# AI Roundtable — a local multi-model debate chatroom.
# Copyright (C) 2026 QZXP27
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or (at your
# option) any later version. Distributed WITHOUT ANY WARRANTY.
# See <https://www.gnu.org/licenses/agpl-3.0.html> for the full terms.
#
# Note the "Affero" part: if you run a modified version of this program and
# let other people use it over a network, you must offer them its source.
# Source: https://github.com/QZXP27/ai-roundtable

"""Debate state machine.

Flow:
  configuring -> in_round -> round_summary -> moderator_checkpoint
      -> (continue) in_round ... -> (wrap up) wrapping_up -> done

A round = each participant speaks `exchanges_per_round` times, in turn order.
After each round a summary lists agreements / discrepancies / eurekas /
open questions, then the debate pauses for the moderator. The summary and
verdict models are chosen per debate (None = auto-pick a connected one).
Interjections can arrive at any time and are inserted into the transcript
before the next turn.
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from clients import call_model, call_with_fallback, resolve_model, ModelCallError

TRANSCRIPTS_DIR = Path(__file__).parent / "transcripts"

# Optional, gitignored. Any of the keys "turn", "summary", "verdict" replaces
# the corresponding built-in prompt template below. The shipped defaults are
# complete and good; this exists so you can tune wording for your own use
# without carrying a permanent diff against the repo, and so your tuning
# isn't published when you push. Templates are .format()-ed with the same
# named fields the defaults use — see each _*_prompt method.
PROMPTS_LOCAL_FILE = Path(__file__).parent / "prompts.local.json"


def _load_prompt_overrides() -> dict:
    if not PROMPTS_LOCAL_FILE.exists():
        return {}
    try:
        data = json.loads(PROMPTS_LOCAL_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}   # a malformed override must not take the app down


PROMPT_OVERRIDES = _load_prompt_overrides()

# Who writes the round summaries and the final verdict. Both are per-debate
# settings chosen in the UI, not constants: hardcoding Claude here meant that
# anyone without a Claude subscription got a debate with no summaries and no
# verdict — i.e. everything this app exists to produce. None means "auto",
# resolved against whichever providers are actually connected.
DEFAULT_SUMMARY_MODEL = None
DEFAULT_VERDICT_MODEL = None

WORD_LIMIT = 250


@dataclass
class Participant:
    name: str
    provider: str  # any key of clients.PROVIDERS
    model: str
    persona: str


# How settled the round was, parsed off the summary. Drives the badge that
# tells the moderator at a glance whether this still needs their judgement.
CONVERGENCE_LEVELS = ("unanimous", "leaning", "split", "contested")
_CONVERGENCE_RE = re.compile(
    r"\*{0,2}Convergence:?\*{0,2}\s*:?\s*\**\s*(" + "|".join(CONVERGENCE_LEVELS) + r")\b",
    re.IGNORECASE,
)


def parse_convergence(text: str) -> str | None:
    """Pulls the convergence verdict out of a summary. Returns None when the
    model didn't emit one — the label is a nicety, so a miss must degrade to
    'no badge' rather than break a round that otherwise went fine."""
    if not text:
        return None
    m = _CONVERGENCE_RE.search(text)
    return m.group(1).lower() if m else None


@dataclass
class Message:
    speaker: str          # participant name, "Moderator", "Round Summary", "Final Verdict", "System"
    content: str
    kind: str             # "turn" | "moderator" | "summary" | "verdict" | "system" | "error"
    round: int
    ts: float = field(default_factory=time.time)
    # Optional extras (currently just {"convergence": ...} on summaries).
    # Defaulted so transcripts written before this field existed still load.
    meta: dict | None = None

    def to_dict(self):
        d = {"speaker": self.speaker, "content": self.content,
             "kind": self.kind, "round": self.round, "ts": self.ts}
        if self.meta:
            d["meta"] = self.meta
        return d


class DebateEngine:
    def __init__(self, emit):
        """`emit` is an async callback(dict) that pushes events to the UI."""
        self.emit = emit
        self.state = "configuring"
        self.topic = ""
        self.participants: list[Participant] = []
        self.exchanges_per_round = 2
        self.round_num = 0
        self.messages: list[Message] = []
        self.wrap_requested = False
        self._task: asyncio.Task | None = None
        self.session_file: Path | None = None
        self.reference_material = ""
        self.summary_model = DEFAULT_SUMMARY_MODEL
        self.verdict_model = DEFAULT_VERDICT_MODEL

    # ---------- public API (called from the websocket handler) ----------

    def start(self, topic: str, participants: list[dict], exchanges_per_round: int, force: bool = False,
              reference_material: str = "", summary_model=None, verdict_model=None):
        if self.state not in ("configuring", "done"):
            if not force:
                raise RuntimeError("debate already running")
            if self._task and not self._task.done():
                self._task.cancel()
            self.state = "abandoned"
            self._save()
        self.__init__(self.emit)
        self.topic = topic.strip()
        self.participants = [Participant(**p) for p in participants]
        self.exchanges_per_round = max(1, int(exchanges_per_round))
        self.reference_material = reference_material.strip()
        # Assigned after the __init__ reset above, which wipes every attribute.
        self.summary_model = summary_model
        self.verdict_model = verdict_model
        TRANSCRIPTS_DIR.mkdir(exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H%M")
        safe_topic = "".join(c for c in self.topic[:40] if c.isalnum() or c in " -_").strip() or "debate"
        self.session_file = TRANSCRIPTS_DIR / f"{stamp} {safe_topic}.json"
        self._task = asyncio.create_task(self._run_round())

    async def interject(self, text: str):
        """Moderator message, any time. It enters the transcript immediately,
        so every subsequent model turn (which rebuilds the full prompt) sees it."""
        text = text.strip()
        if text:
            await self._add(Message("Moderator", text, "moderator", self.round_num))

    def continue_round(self):
        if self.state != "moderator_checkpoint":
            raise RuntimeError("not at a checkpoint")
        self._task = asyncio.create_task(self._run_round())

    def set_meta_models(self, summary_model=None, verdict_model=None):
        """Changeable mid-debate: the meta-turns resolve their model when they
        run, so a change at a checkpoint applies from the next round on."""
        self.summary_model = summary_model
        self.verdict_model = verdict_model
        self._save()

    def wrap_up(self):
        """From a checkpoint: synthesize now. Mid-round: finish round then synthesize."""
        self.wrap_requested = True
        if self.state == "moderator_checkpoint":
            self._task = asyncio.create_task(self._run_wrap_up())

    # ---------- internals ----------

    async def _add(self, msg: Message):
        self.messages.append(msg)
        await self.emit({"type": "message", **msg.to_dict()})
        self._save()

    async def _set_state(self, state: str):
        self.state = state
        await self.emit({"type": "state", "state": state, "round": self.round_num})
        self._save()

    def _save(self):
        if not self.session_file:
            return
        data = {
            "topic": self.topic,
            "participants": [vars(p) for p in self.participants],
            "exchanges_per_round": self.exchanges_per_round,
            "state": self.state,
            "messages": [m.to_dict() for m in self.messages],
            "reference_material": self.reference_material,
            "summary_model": self.summary_model,
            "verdict_model": self.verdict_model,
        }
        self.session_file.write_text(json.dumps(data, ensure_ascii=False, indent=1))

    def _meta_candidates(self, spec):
        """Models to try for a summary/verdict turn. An 'auto' spec prefers the
        providers already in this debate, since those are known-configured."""
        return resolve_model(spec, prefer_providers=[p.provider for p in self.participants])

    def _reference_block(self) -> str:
        if not self.reference_material:
            return ""
        return f"Reference material (uploaded by the moderator):\n{self.reference_material}\n\n"

    def _transcript_text(self) -> str:
        lines = []
        for m in self.messages:
            if m.kind in ("turn", "moderator", "summary"):
                label = {"summary": f"Round {m.round} Summary"}.get(m.kind, m.speaker)
                lines.append(f"[{label}]: {m.content}")
        return "\n\n".join(lines) if lines else "(no discussion yet)"

    def _override(self, key: str, **fields) -> str | None:
        """Renders a user-supplied prompt template, if one exists for `key`.
        A template referencing a field that doesn't exist falls back to the
        built-in rather than crashing a debate mid-round."""
        tpl = PROMPT_OVERRIDES.get(key)
        if not tpl:
            return None
        try:
            return tpl.format(**fields)
        except (KeyError, IndexError, ValueError):
            return None

    def _turn_prompt(self, p: Participant) -> str:
        others = ", ".join(x.name for x in self.participants if x.name != p.name)
        custom = self._override(
            "turn", name=p.name, persona=p.persona, topic=self.topic,
            reference=self._reference_block(), others=others,
            transcript=self._transcript_text(), word_limit=WORD_LIMIT,
        )
        if custom:
            return custom
        return f"""You are {p.name}, one participant in a moderated round-table debate.

Your assigned perspective/persona: {p.persona}

Debate topic: {self.topic}

{self._reference_block()}Other participants: {others}. The human Moderator may interject; treat moderator messages as steering that you must take into account.

Discussion so far:
{self._transcript_text()}

Now write your next contribution as {p.name}. Rules:
- Stay in your assigned perspective; argue it seriously, but concede points when genuinely warranted.
- Engage directly with the most recent points from other participants — quote or reference them; do not monologue past them.
- Bring at least one new argument, piece of evidence, or probing question; do not repeat yourself.
- Under {WORD_LIMIT} words. Reply with ONLY your message text (no name prefix, no preamble)."""

    def _summary_prompt(self) -> str:
        custom = self._override(
            "summary", topic=self.topic, reference=self._reference_block(),
            transcript=self._transcript_text(), round_num=self.round_num,
        )
        if custom:
            return custom
        return f"""You are the neutral scribe of a round-table debate on: {self.topic}

{self._reference_block()}Discussion so far:
{self._transcript_text()}

Write a summary of round {self.round_num} ONLY (the latest exchanges), for the human moderator. Use exactly these markdown sections:
**Agreements** — points where participants now converge.
**Discrepancies** — live disagreements, stated as opposing positions with who holds each.
**Eureka insights** — genuinely novel ideas or reframings that emerged, if any (say "none" if none).
**Open questions** — what the moderator could steer toward next.
**Crux** — the SINGLE sub-question that, if answered, would settle the disagreement. One sentence. Pick the one that actually decides the matter, not the most-discussed one. If the participants genuinely agree, say what would have to be true for them to be wrong.

Be concrete and cite participants by name. Under 200 words total.

Then end your reply with this as the final line, and nothing after it:
**Convergence:** one of unanimous | leaning | split | contested
(unanimous = they agree on substance; leaning = one position is clearly winning; split = two coherent camps; contested = still genuinely unresolved.)"""

    def _verdict_prompt(self) -> str:
        custom = self._override(
            "verdict", topic=self.topic, reference=self._reference_block(),
            transcript=self._transcript_text(),
        )
        if custom:
            return custom
        return f"""You are the synthesizer concluding a round-table debate on: {self.topic}

{self._reference_block()}Full discussion:
{self._transcript_text()}

Produce the final output for the human moderator. It must weigh ALL participants' perspectives — not adopt one side.

You must COMMIT. The moderator came here to stop deliberating, so a balanced restatement of both sides is a failed verdict. If the question is close, pick the better answer anyway and mark your confidence low — that is far more useful than a hedge.

Use exactly these markdown sections, in this order:
**Decision** — the single recommended action, in ONE sentence, stated first. No "it depends", no listing of options, no deferring back to the moderator.
**Confidence:** high | medium | low — followed by one line on why, grounded in whether the participants actually converged.
**Why** — the surviving arguments that support the decision, i.e. the strongest form of each perspective after challenge.
**What would change this** — the specific evidence or condition that would flip the decision. Be concrete enough that the moderator could go check it."""

    async def _speak(self, p: Participant):
        await self.emit({"type": "thinking", "speaker": p.name})
        try:
            text = await call_model(p.provider, self._turn_prompt(p), p.model)
            await self._add(Message(p.name, text, "turn", self.round_num))
        except ModelCallError as e:
            await self._add(Message("System", f"{p.name} failed to respond: {e}", "error", self.round_num))

    async def _run_round(self):
        self.round_num += 1
        await self._set_state("in_round")
        for _ in range(self.exchanges_per_round):
            for p in self.participants:
                await self._speak(p)

        await self._set_state("round_summary")
        await self.emit({"type": "thinking", "speaker": "Round Summary"})
        try:
            text, provider, model = await call_with_fallback(
                self._meta_candidates(self.summary_model), self._summary_prompt())
            level = parse_convergence(text)
            meta = {"model": f"{provider}/{model}"}
            if level:
                meta["convergence"] = level
            await self._add(Message("Round Summary", text, "summary", self.round_num, meta=meta))
        except ModelCallError as e:
            await self._add(Message("System", f"summary failed: {e}", "error", self.round_num))

        if self.wrap_requested:
            await self._run_wrap_up()
        else:
            await self._set_state("moderator_checkpoint")

    async def _run_wrap_up(self):
        await self._set_state("wrapping_up")
        await self.emit({"type": "thinking", "speaker": "Final Verdict"})
        try:
            text, provider, model = await call_with_fallback(
                self._meta_candidates(self.verdict_model), self._verdict_prompt())
            await self._add(Message("Final Verdict", text, "verdict", self.round_num,
                                    meta={"model": f"{provider}/{model}"}))
        except ModelCallError as e:
            await self._add(Message("System", f"verdict failed: {e}", "error", self.round_num))
        await self._set_state("done")

    # ---------- export ----------

    def to_markdown(self) -> str:
        return render_markdown(self)


@dataclass
class StoredSession:
    """A session loaded back off disk, exposing the same attribute surface the
    exporters read off a live DebateEngine."""
    topic: str
    exchanges_per_round: int
    participants: list[Participant]
    messages: list[Message]
    summary_model: dict | None = None
    verdict_model: dict | None = None


def session_from_dict(data: dict) -> StoredSession:
    """Rehydrate a transcript JSON file (as written by DebateEngine._save)
    into something the markdown/PDF exporters can render."""
    return StoredSession(
        topic=data.get("topic", ""),
        exchanges_per_round=data.get("exchanges_per_round", 2),
        participants=[Participant(**p) for p in data.get("participants", [])],
        messages=[Message(**m) for m in data.get("messages", [])],
        summary_model=data.get("summary_model"),
        verdict_model=data.get("verdict_model"),
    )


def render_markdown(session) -> str:
    """Works on either a live DebateEngine or a StoredSession."""
    lines = [f"# AI Roundtable — {session.topic}", ""]
    lines.append(f"*{time.strftime('%Y-%m-%d %H:%M')} · {session.exchanges_per_round} exchanges/round*")
    lines.append("")
    lines.append("## Participants")
    for p in session.participants:
        lines.append(f"- **{p.name}** ({p.provider}/{p.model}): {p.persona}")
    current_round = 0
    for m in session.messages:
        if m.kind in ("turn", "moderator") and m.round != current_round:
            current_round = m.round
            lines += ["", f"## Round {current_round}", ""]
        if m.kind == "turn":
            lines += [f"**{m.speaker}:** {m.content}", ""]
        elif m.kind == "moderator":
            lines += [f"> **Moderator:** {m.content}", ""]
        elif m.kind == "summary":
            by = f" *(by {m.meta['model']})*" if m.meta and m.meta.get("model") else ""
            lines += [f"### Round {m.round} Summary{by}", "", m.content, ""]
        elif m.kind == "verdict":
            by = f" *(by {m.meta['model']})*" if m.meta and m.meta.get("model") else ""
            lines += ["", f"## Final Verdict{by}", "", m.content, ""]
    return "\n".join(lines)
