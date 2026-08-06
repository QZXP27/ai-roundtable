# AI Roundtable

*[中文说明](README.zh-CN.md) · [项目主页](https://qzxp27.github.io/ai-roundtable/)*

A local, self-hosted debate chatroom where **Claude, Gemini, ChatGPT and
DeepSeek argue a topic from assigned personas**, moderated by you in real
time. The first three run on the official Claude Code, Antigravity (Gemini)
and Codex (OpenAI) CLIs, so those turns cost nothing beyond the
subscriptions you already have. DeepSeek is the one exception — it ships no
subscription CLI, so it goes over its API, at roughly 1% of frontier
per-token pricing.

![status](https://img.shields.io/badge/status-personal%20project-blue)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![providers](https://img.shields.io/badge/providers-4-brightgreen)
![license](https://img.shields.io/badge/license-AGPL--3.0-green)

## Why

Asking one model a question gets you one perspective. This spins up several
models, each locked into a distinct persona, and makes them respond directly
to each other's strongest points — with a neutral summary after every round
and a synthesized final verdict at the end. Useful for stress-testing a
decision, exploring a debate from angles you wouldn't think to argue
yourself, or just watching frontier models disagree.

Every round ends with a convergence verdict (`unanimous` / `leaning` /
`split` / `contested`) and names the single crux that would settle the
disagreement; the debate ends with one committed recommendation and a
confidence level. The point is to finish with *fewer* things to weigh than
you started with, not more.

## How it works

```
Browser (WebSocket)  <->  FastAPI server  <->  DebateEngine  <->  claude -p / agy -p / codex exec
        static/index.html      server.py         engine.py         clients.py
                                     |
                               pdf_export.py  ->  downloadable PDF transcript
```

- **`clients.py`** — thin async wrapper around the `claude`, `agy`
  (Antigravity/Gemini), and `codex` (OpenAI) CLIs in headless/print mode,
  plus a small HTTP client for DeepSeek. For the CLIs, auth is whatever's
  already logged in; DeepSeek reads a key file or `$DEEPSEEK_API_KEY`. The
  `PROVIDERS` table at the top is the single registry — adding a provider
  means adding a line there and a `call_*` function. Also does the cheap
  per-provider status check behind the Accounts panel.
- **`engine.py`** — the debate state machine: rounds, turn order, transcript
  building, round summaries, moderator interjections, final verdict, and
  session autosave. Also renders a session to Markdown.
- **`pdf_export.py`** — renders a live or stored session to a formatted PDF.
- **`server.py`** — FastAPI app serving a single WebSocket connection that
  streams debate events to the browser, plus routes for PDF export and
  reference-material upload.
- **`static/index.html`** — single-file vanilla-JS frontend, no build step.

## Features

- **Multi-model, multi-persona debates** — mix any number of Claude,
  Gemini, OpenAI, and DeepSeek participants, each with its own model tier
  and persona prompt.
- **Live moderation** — interject at any point; your message is inserted
  into the transcript before the next model turn, so every participant sees
  and reacts to it.
- **Round summaries** — after each round, a recap of agreements, live
  disagreements, novel insights, and open questions, then the debate pauses
  for you.
- **Writes in the debate's language** — summaries and verdicts follow the
  language the participants are actually using, section headings included, so
  a Chinese debate doesn't come back with English headings over Chinese text.
  (One deliberate exception: the machine-read `**Convergence:**` line stays in
  English, because the app parses it to draw the badge.)
- **Instructions per judge** — a collapsed box under each of the two model
  pickers takes free-text direction for that role: tone, length, language,
  what to focus on. Editable mid-debate like the model choice, and saved with
  presets. Leave both empty and the defaults above still do the right thing.
- **Pick who judges** — the summary and verdict models are chosen in the UI,
  independently of the debaters, and can be changed mid-debate (a change at a
  checkpoint applies from the next round). They default to **Auto**, which
  resolves to a provider you actually have connected — so the app produces
  summaries and a verdict even with only one provider set up. Every summary
  and verdict records which model wrote it, in the UI and in exports.
- **Decision closure** — every round is tagged `unanimous` / `leaning` /
  `split` / `contested` and names the single crux that would settle the
  disagreement; the wrap-up leads with one committed recommendation and a
  confidence level, rather than a balanced restatement of both sides.
- **Web search** — participants can search the web mid-debate, so arguments
  can cite current information rather than training data alone.
- **Reference material (PDF)** — attach one or more PDFs when starting a
  debate; their text is extracted and injected into every participant's
  context as shared reference material.
- **Start a new debate any time** — starting a fresh debate mid-round asks
  for confirmation, then abandons the running one (its transcript is kept
  and marked `abandoned`) rather than forcing you to sit through a wrap-up.
- **Presets** — save/reuse participant lineups and personas; ships with a
  default Advocate-vs-Skeptic setup.
- **Session history** — every debate autosaves as JSON to `transcripts/`;
  click any past session to read it back.
- **Export** — one click writes a formatted Markdown transcript to Obsidian,
  or downloads a typeset PDF (headings, dividers, real bold, CJK support).
  Both follow whichever session you're currently viewing, live or historical.
- **Light/dark themes and an EN/中文 interface toggle**, both remembered
  across reloads.
- **Accounts panel** — shows each provider's connection status at a glance,
  with the exact terminal command to log in and a re-check button.
- **Tiered model economics** — use cheap/fast models for debate turns and a
  stronger one for the verdict, where synthesis quality matters most. Only
  DeepSeek turns cost money, and at ~1% of frontier pricing.

## Requirements

- **macOS.** The app runs anywhere Python does, but two features shell out
  to macOS-specific paths and degrade silently elsewhere — see
  [Platform notes](#platform-notes).
- Python 3.11+
- At least two providers configured (you only need to set up the ones you
  actually want to debate with — the app degrades a single participant's
  turn to an error message if its provider isn't connected, instead of
  failing the whole session):
  - **Claude**: [Claude Code CLI](https://claude.com/claude-code) — needs a
    Claude Pro/Max subscription.
  - **Gemini**: Antigravity CLI (`brew install antigravity-cli`) — needs a
    Google account with a Gemini subscription. (Google deprecated the
    standalone Gemini CLI's individual login in June 2026; `agy` is the
    successor and exposes the gemini-3.x model family.)
  - **OpenAI**: Codex CLI (`brew install --cask codex`). Signing in with a
    ChatGPT account works, including on the free tier, though free-tier
    usage limits are tight for a format that makes one call per participant
    per exchange. An API key also works, but note that bills per token
    rather than drawing on subscription quota.
  - **DeepSeek**: no CLI. Get a key at
    [platform.deepseek.com](https://platform.deepseek.com), then either write
    it to `~/.config/ai-roundtable/deepseek.key` (mode 600) or set
    `$DEEPSEEK_API_KEY`, which takes precedence. Prefer the file for normal
    use: the key is read on every call, so the Accounts panel picks it up
    without restarting the server — whereas `export` in your own shell can't
    reach an already-running process. Use the env var for headless/cron runs.
    It's the one provider that bills per token, but `deepseek-v4-flash` runs
    $0.14/$0.28 per million in/out, so a full debate lands in fractions of a
    cent. Worth having as a third voice specifically because it isn't a
    Western frontier lab — when it agrees with Claude and Gemini, that
    agreement is worth more.

The three CLIs authenticate via their own login flows, which you run once in
a terminal. DeepSeek is a single key file. There's no `.env` file and no
billing setup beyond that.

## Run

```bash
git clone https://github.com/QZXP27/ai-roundtable.git
cd ai-roundtable
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./.venv/bin/python server.py
# open http://localhost:8500
```

On first load, check the **Accounts** panel in the sidebar. Each provider
shows a status dot (green = connected, amber = installed but not logged in,
red = CLI not found on PATH; DeepSeek only ever shows green or amber, since
a missing key isn't a missing binary). Click **Connect** on any provider to
see the exact command to run in your terminal — copy it, run it, then hit
**Re-check status**.

> Provider logins are interactive terminal UIs, so they're run in your own
> terminal rather than proxied through the browser.

## Using it

1. Connect the providers you want (once — each CLI caches its own login).
2. Set a topic, add participants (name + provider + model + persona),
   pick exchanges per round. Optionally attach reference PDFs.
3. **Start debate** — participants take turns; each sees the full
   transcript so far, including your interjections.
4. After each round: a summary from your chosen summary model (Auto by
   default), then the debate pauses for you.
5. Type in the composer at any time to interject as Moderator; **Continue
   round** or **Wrap up**.
6. Wrap-up produces a final verdict weighing all perspectives. **Export to
   Obsidian** writes Markdown to `~/Obsidian/Ai Chat Room/`; **Export as
   PDF** downloads a typeset transcript.

## Platform notes

Two features depend on macOS specifics and fail soft on other platforms:

- **PDF export fonts** — `pdf_export.py` loads Arial and STHeiti from
  `/System/Library/Fonts`. If those aren't present it falls back to a
  built-in Latin-only font, so exports still work but Chinese text won't
  render. Point the font constants at any local TTF/TTC to fix this.
- **Reference-material extraction** — PDF upload shells out to `pdftotext`
  (from poppler). Without it on PATH, uploads return an error message and
  the debate simply runs without reference material.

Obsidian export writes to `~/Obsidian/Ai Chat Room/`; change `OBSIDIAN_DIR`
in `server.py` if your vault lives elsewhere.

## Project layout

```
clients.py         CLI wrappers for Claude/Gemini/OpenAI, HTTP client for DeepSeek, PROVIDERS registry
engine.py          Debate state machine, prompts, transcript, autosave, Markdown export
pdf_export.py      Typeset PDF rendering of a live or stored session
server.py          FastAPI app + WebSocket event loop, presets, sessions, upload/export routes
static/index.html  Frontend (vanilla JS/CSS, i18n, themes — no build step)
personas.default.json  Starter presets (tracked)
personas.local.json    Your own presets (gitignored, created on save)
prompts.local.json     Optional prompt overrides (gitignored)
requirements.txt   fastapi, uvicorn, websockets, fpdf2, python-multipart, httpx
transcripts/       Autosaved debate sessions (gitignored)
```

## Customising without forking

Two optional files, both gitignored, so your setup survives `git pull` and
can't be committed by accident:

- **`personas.local.json`** — every preset you save from the UI lands here.
  The tracked `personas.default.json` is only the starter set; a local preset
  with the same name overrides it.
- **`prompts.local.json`** — optional. Keys `turn`, `summary`, `verdict`
  replace the built-in prompt templates. Read once at startup, so restart the
  server after editing. For small tweaks prefer the in-UI instruction boxes —
  they apply live and don't fork the whole prompt.
  Replaced templates receive the same named fields as the defaults. Each is `.format()`-ed with the same
  named fields the defaults use (see the `_*_prompt` methods in `engine.py`).
  A malformed template falls back to the built-in rather than breaking a
  debate mid-round.

## License

[AGPL-3.0](LICENSE). You may use, modify, and even sell this — but any
derivative must publish its source and keep the attribution, **including one
you only ever offer to others over a network** (AGPL §13). Fork it, extend
it, make it yours; just keep it open.

> Note: releases before 2026-08-02 were published under MIT. That grant
> can't be revoked for anyone who already has that snapshot; AGPL applies
> from this version forward.
