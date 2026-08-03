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

"""Model clients for the round-table participants.

Claude, Gemini and OpenAI are reached through their official CLIs, which
authenticate via subscription login (Claude Pro/Max, Google account OAuth,
ChatGPT account) — no API key involved. DeepSeek is the exception: it ships
no subscription-auth CLI, so it goes over its OpenAI-compatible HTTP API with
a key from $DEEPSEEK_API_KEY. Its per-token price is low enough (~1-2% of the
others) that this stays cheap in practice.

Each call is stateless and carries the full debate transcript, so every
participant sees everything.
"""

import asyncio
import json
import os
import shutil
from pathlib import Path

import httpx

CALL_TIMEOUT = 300  # seconds; reasoning models can be slow on long transcripts
RETRIES = 1

# Resolved off PATH; the fallbacks are only so a missing CLI produces a clear
# "not found" status instead of an obscure error.
CLAUDE_BIN = shutil.which("claude") or "claude"
# Google deprecated Gemini CLI's individual login (June 2026); the subscription
# path is now the Antigravity CLI (`agy`), same headless -p mode.
AGY_BIN = shutil.which("agy") or "agy"
# OpenAI's Codex CLI, authenticated by signing in with a ChatGPT account
# (works on the free tier too) rather than an API key.
OPENAI_BIN = shutil.which("codex") or "codex"

# Models offered in the UI, per provider.
CLAUDE_MODELS = ["sonnet", "opus", "haiku"]
GEMINI_MODELS = ["gemini-3.6-flash-high", "gemini-3.1-pro-high",
                 "gemini-3.1-pro-low", "gemini-3.6-flash-medium"]
# Taken from the Codex CLI's own models cache (~/.codex/models_cache.json),
# listed in its priority order; gpt-5.6-terra is the CLI default.
OPENAI_MODELS = ["gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.4-mini"]
# HTTP, not a CLI. The v4 pair replaced the deepseek-chat/deepseek-reasoner
# aliases, which were retired on 2026-07-24.
DEEPSEEK_MODELS = ["deepseek-v4-flash", "deepseek-v4-pro"]

DEEPSEEK_BASE = "https://api.deepseek.com/v1"
DEEPSEEK_KEY_FILE = Path.home() / ".config" / "ai-roundtable" / "deepseek.key"
# v4 are reasoning models: the thinking tokens count against this budget, so a
# small cap truncates mid-reasoning and yields empty or stub content.
DEEPSEEK_MAX_TOKENS = 8000

# The one place that knows which providers exist and what each one offers.
# Everything else (the dispatcher, status checks, the UI payload) reads this,
# so adding a provider means adding a line here plus its call_* function.
PROVIDERS = {
    "claude": CLAUDE_MODELS,
    "gemini": GEMINI_MODELS,
    "openai": OPENAI_MODELS,
    "deepseek": DEEPSEEK_MODELS,
}

# The CLI providers' logins are interactive TUI flows, which don't survive being
# proxied through a browser — so the UI just tells the user what to run in
# their own terminal, then re-checks status. DeepSeek has no login flow, so its
# hint writes the key file instead. It must be the file and not `export`:
# exporting in the user's shell can't reach this already-running process, so
# "Recheck status" would stay red until a restart.
LOGIN_HINTS = {
    "claude": "claude setup-token",
    "gemini": "agy",
    "openai": "codex login",
    "deepseek": (
        f"mkdir -p {DEEPSEEK_KEY_FILE.parent} && "
        f"echo sk-YOUR-KEY > {DEEPSEEK_KEY_FILE} && "
        f"chmod 600 {DEEPSEEK_KEY_FILE}"
    ),
}


class ModelCallError(Exception):
    pass


class ModelAuthError(ModelCallError):
    """Bad or missing credentials. Separate from ModelCallError so call_model
    can skip the retry — a 401 will fail identically the second time."""


async def _run(cmd: list[str], stdin_text: str | None = None, merge_stderr: bool = False) -> str:
    """Returns the command's stdout. `merge_stderr` appends stderr too, for
    CLIs that report status there (codex login status writes to stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise ModelCallError(f"{cmd[0]} not found — is it installed and on PATH?")
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(stdin_text.encode() if stdin_text is not None else None),
            timeout=CALL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise ModelCallError(f"{cmd[0]} timed out after {CALL_TIMEOUT}s")
    if proc.returncode != 0:
        raise ModelCallError(f"{cmd[0]} exited {proc.returncode}: {err.decode()[:500]}")
    text = out.decode()
    if merge_stderr:
        text += err.decode()
    return text


async def call_claude(prompt: str, model: str = "sonnet") -> str:
    """Headless Claude Code call on the subscription. Tools disabled and
    user settings excluded so it behaves as a pure chat participant."""
    cmd = [
        CLAUDE_BIN, "-p", "--model", model,
        "--output-format", "json",
        "--tools", "WebSearch",
        "--permission-mode", "bypassPermissions",
        "--setting-sources", "",
        "--strict-mcp-config",
    ]
    out = await _run(cmd, stdin_text=prompt)
    try:
        data = json.loads(out.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        raise ModelCallError(f"claude returned unparseable output: {out[:300]}")
    if data.get("is_error"):
        raise ModelCallError(f"claude error: {data.get('result', '')[:500]}")
    return data.get("result", "").strip()


async def call_gemini(prompt: str, model: str = "gemini-3.6-flash-high") -> str:
    """Headless Antigravity CLI call on Google-account OAuth (subscription)."""
    cmd = [AGY_BIN, "-p", prompt, "--model", model, "--sandbox", "--dangerously-skip-permissions"]
    out = await _run(cmd)
    text = out.strip()
    if not text:
        raise ModelCallError("agy returned empty output")
    return text


async def call_openai(prompt: str, model: str = "gpt-5.6-terra") -> str:
    """Headless Codex CLI call on the ChatGPT subscription (OAuth login, not
    an API key). Sandbox is read-only so it behaves as a pure chat
    participant with no shell/filesystem access. Prompt is passed as a
    positional arg rather than via stdin — codex exec has a known hang when
    piped stdin has no writer."""
    cmd = [OPENAI_BIN, "exec", "--model", model, "--sandbox", "read-only",
           "--skip-git-repo-check", prompt]
    out = await _run(cmd)
    text = out.strip()
    if not text:
        raise ModelCallError("codex returned empty output")
    return text


def _deepseek_key() -> str:
    """Read at call time, not import time, so the app still boots (and can
    show DeepSeek as simply not-connected) when no key is configured.

    The key file matters for the Accounts panel: `export` in the user's own
    terminal cannot reach an already-running server, so an env-var-only setup
    would leave "Recheck status" permanently red until a restart. The CLI
    providers avoid that by storing credentials on disk, and this gives
    DeepSeek the same property. The env var still wins when set, since that's
    the better path for headless/cron use."""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key and DEEPSEEK_KEY_FILE.exists():
        try:
            key = DEEPSEEK_KEY_FILE.read_text().strip()
        except OSError:
            key = ""
    if not key:
        # Deliberately avoids the words "not found" — check_status treats that
        # phrase as "binary missing from PATH", which is meaningless for HTTP.
        raise ModelAuthError(
            f"no DeepSeek key — set $DEEPSEEK_API_KEY or write {DEEPSEEK_KEY_FILE}"
        )
    return key


async def call_deepseek(prompt: str, model: str = "deepseek-v4-flash") -> str:
    """DeepSeek over its OpenAI-compatible HTTP API. The only provider here
    billed per token rather than by subscription."""
    key = _deepseek_key()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": DEEPSEEK_MAX_TOKENS,
    }
    try:
        async with httpx.AsyncClient(timeout=CALL_TIMEOUT) as client:
            resp = await client.post(
                f"{DEEPSEEK_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
    except httpx.TimeoutException:
        raise ModelCallError(f"deepseek timed out after {CALL_TIMEOUT}s")
    except httpx.HTTPError as e:
        raise ModelCallError(f"deepseek request failed: {e}")

    if resp.status_code in (401, 403):
        raise ModelAuthError(f"deepseek rejected the API key ({resp.status_code})")
    if resp.status_code != 200:
        raise ModelCallError(f"deepseek returned {resp.status_code}: {resp.text[:500]}")
    try:
        text = resp.json()["choices"][0]["message"]["content"].strip()
    except (ValueError, KeyError, IndexError, AttributeError):
        raise ModelCallError(f"deepseek returned unparseable output: {resp.text[:300]}")
    if not text:
        # Most likely max_tokens was spent entirely on reasoning.
        raise ModelCallError("deepseek returned empty output")
    return text


async def call_model(provider: str, prompt: str, model: str) -> str:
    for attempt in range(RETRIES + 1):
        try:
            if provider == "claude":
                return await call_claude(prompt, model)
            elif provider == "gemini":
                return await call_gemini(prompt, model)
            elif provider == "openai":
                return await call_openai(prompt, model)
            elif provider == "deepseek":
                return await call_deepseek(prompt, model)
            raise ModelCallError(f"unknown provider: {provider}")
        except ModelAuthError:
            raise  # retrying bad credentials just wastes CALL_TIMEOUT twice
        except ModelCallError:
            if attempt == RETRIES:
                raise
            await asyncio.sleep(2)


STATUS_CHECK_PROMPT = "Reply with exactly the word OK and nothing else."


async def check_status(provider: str) -> str:
    """Connectivity check for the Accounts panel, caching into LAST_STATUS so
    auto-resolution can read availability without probing again."""
    status = await _probe_status(provider)
    LAST_STATUS[provider] = status
    return status


async def _probe_status(provider: str) -> str:
    """Returns "connected", "not_connected", or "not_installed". DeepSeek has a free models endpoint
    and Codex a dedicated `login status` subcommand; Claude and Gemini have
    neither, so those fall back to a tiny live call."""
    if provider == "deepseek":
        # A key can't be "not installed", so this only ever reports
        # connected/not_connected. Listing models bills nothing.
        try:
            key = _deepseek_key()
        except ModelAuthError:
            return "not_connected"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{DEEPSEEK_BASE}/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
        except httpx.HTTPError:
            return "not_connected"
        return "connected" if resp.status_code == 200 else "not_connected"

    if provider == "openai":
        try:
            out = await _run([OPENAI_BIN, "login", "status"], merge_stderr=True)
        except ModelCallError as e:
            return "not_installed" if "not found" in str(e) else "not_connected"
        return "connected" if "logged in" in out.lower() and "not logged in" not in out.lower() else "not_connected"

    try:
        if provider == "claude":
            await call_claude(STATUS_CHECK_PROMPT, model="haiku")
        elif provider == "gemini":
            await call_gemini(STATUS_CHECK_PROMPT, model="gemini-3.6-flash-medium")
        else:
            raise ModelCallError(f"unknown provider: {provider}")
    except ModelCallError as e:
        return "not_installed" if "not found" in str(e) else "not_connected"
    return "connected"


# ---------------------------------------------------------------- auto-select

# Last known per-provider status, populated by check_status. Auto-resolution
# reads this instead of probing: the claude and gemini checks each cost a real
# inference call, so resolving a model must never trigger one.
LAST_STATUS: dict[str, str] = {}

# Preference order for meta-turns (round summaries and the final verdict),
# best synthesis first. Only consulted when the user picked "auto".
AUTO_PREFERENCE = [
    ("claude", "opus"),
    ("openai", "gpt-5.6-terra"),
    ("gemini", "gemini-3.1-pro-high"),
    ("deepseek", "deepseek-v4-pro"),
]


def auto_candidates(prefer_providers=()) -> list[tuple[str, str]]:
    """Ordered (provider, model) candidates for an 'auto' choice.

    Prefers providers known to be connected. When nothing has been probed yet
    — a fresh server that hasn't run a status check — falls back to providers
    the user put in the debate, since those are demonstrably configured. The
    full list is always appended so a caller can keep trying rather than fail.
    """
    connected = [c for c in AUTO_PREFERENCE if LAST_STATUS.get(c[0]) == "connected"]
    if connected:
        ordered = connected
    elif not LAST_STATUS:
        ordered = [c for c in AUTO_PREFERENCE if c[0] in prefer_providers]
    else:
        ordered = []
    # Dedupe while preserving order; the tail is the last-resort fallback.
    seen, out = set(), []
    for c in ordered + AUTO_PREFERENCE:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def resolve_model(spec, prefer_providers=()) -> list[tuple[str, str]]:
    """Turns a model spec into the candidates to try, in order.

    `spec` is either None/falsy ("auto") or {"provider": ..., "model": ...}.
    An explicit choice yields exactly one candidate — if the user asked for
    DeepSeek, quietly answering with Claude would be worse than an error.
    """
    if spec and spec.get("provider") and spec.get("model"):
        return [(spec["provider"], spec["model"])]
    return auto_candidates(prefer_providers)


async def call_with_fallback(candidates: list[tuple[str, str]], prompt: str):
    """Tries each candidate in turn, returning (text, provider, model).
    Raises the last ModelCallError if they all fail. A single-candidate list
    (an explicit user choice) therefore behaves exactly like a direct call."""
    last = None
    for provider, model in candidates:
        try:
            return await call_model(provider, prompt, model), provider, model
        except ModelCallError as e:
            last = e
    raise last or ModelCallError("no model available")
