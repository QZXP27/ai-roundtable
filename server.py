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

"""AI Roundtable server — local multi-model debate chatroom.

Run:  python3 server.py   ->  http://localhost:8500
Deps: pip install fastapi uvicorn
"""

import asyncio
import json
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile
from fastapi.responses import FileResponse, Response

from clients import PROVIDERS, LOGIN_HINTS, check_status
from engine import DebateEngine, render_markdown, session_from_dict
from pdf_export import to_pdf_bytes

ROOT = Path(__file__).parent
# Presets are split in two on purpose. The shipped starter set is tracked in
# git; anything you save while using the app goes to the .local file, which is
# gitignored. Without the split, saving a preset would stage a personal
# lineup into a tracked file — an easy way to publish something private by
# accident, and it means your own presets also survive a `git pull`.
PERSONAS_DEFAULT_FILE = ROOT / "personas.default.json"
PERSONAS_LOCAL_FILE = ROOT / "personas.local.json"
LEGACY_PERSONAS_FILE = ROOT / "personas.json"
TRANSCRIPTS_DIR = ROOT / "transcripts"
OBSIDIAN_DIR = Path.home() / "Obsidian" / "Ai Chat Room"
REFERENCE_TEXT_CAP = 20_000

app = FastAPI()
sockets: set[WebSocket] = set()


@app.on_event("startup")
async def _startup():
    _migrate_legacy_presets()


async def broadcast(event: dict):
    dead = []
    for ws in sockets:
        try:
            await ws.send_text(json.dumps(event, ensure_ascii=False))
        except Exception:
            dead.append(ws)
    for ws in dead:
        sockets.discard(ws)


engine = DebateEngine(emit=broadcast)


def _read_presets(path: Path) -> list:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    # Legacy files were a flat array of participants rather than of presets.
    # Detect that by a participant-only field: testing for a missing
    # "participants" key would also match a delete tombstone.
    if raw and isinstance(raw[0], dict) and "provider" in raw[0]:
        return [{"name": "Default", "participants": raw}]
    return raw


def _migrate_legacy_presets():
    """Older versions kept everything in a single tracked personas.json. Move
    it to the local file once, so upgrading doesn't silently lose presets."""
    if LEGACY_PERSONAS_FILE.exists() and not PERSONAS_LOCAL_FILE.exists():
        legacy = _read_presets(LEGACY_PERSONAS_FILE)
        shipped = {p["name"] for p in _read_presets(PERSONAS_DEFAULT_FILE)}
        mine = [p for p in legacy if p["name"] not in shipped]
        if mine:
            _write_local_presets(mine)


def load_presets() -> list:
    """Shipped starter presets, overlaid with the user's own. A local preset
    with the same name wins, so the defaults can be customised without
    editing a tracked file."""
    by_name = {p["name"]: p for p in _read_presets(PERSONAS_DEFAULT_FILE)}
    for p in _read_presets(PERSONAS_LOCAL_FILE):
        # A tombstone lets you delete a shipped preset without editing the
        # tracked file it lives in.
        if p.get("deleted"):
            by_name.pop(p["name"], None)
        else:
            by_name[p["name"]] = p
    return list(by_name.values())


def _write_local_presets(presets: list):
    PERSONAS_LOCAL_FILE.write_text(json.dumps(presets, ensure_ascii=False, indent=1))


def list_sessions() -> list:
    if not TRANSCRIPTS_DIR.exists():
        return []
    out = []
    for f in TRANSCRIPTS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        out.append({
            "id": f.name,
            "topic": data.get("topic", "(untitled)"),
            "state": data.get("state", "unknown"),
            "participants": [p.get("name") for p in data.get("participants", [])],
            "message_count": len(data.get("messages", [])),
            "mtime": f.stat().st_mtime,
        })
    out.sort(key=lambda s: s["mtime"], reverse=True)
    return out


def load_session(session_id: str):
    path = (TRANSCRIPTS_DIR / session_id).resolve()
    if path.parent != TRANSCRIPTS_DIR.resolve() or not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _meta_models_label(eng) -> str:
    """Confirmation text for a mid-debate model change. Goes into the
    transcript so it's visible later why round 3 reads unlike round 2."""
    def fmt(spec):
        return f"{spec['provider']}/{spec['model']}" if spec else "auto"
    parts = [f"Summary model: {fmt(eng.summary_model)}", f"Verdict model: {fmt(eng.verdict_model)}"]
    if eng.summary_instruction or eng.verdict_instruction:
        parts.append("custom instructions applied")
    return " · ".join(parts)


async def send_provider_status(ws: WebSocket):
    providers = list(PROVIDERS)
    # check_status writes clients.LAST_STATUS, which is what auto-resolution
    # reads — so the Accounts panel refresh doubles as the availability probe.
    results = await asyncio.gather(*(check_status(p) for p in providers))
    await ws.send_text(json.dumps({
        "type": "provider_status",
        "status": dict(zip(providers, results)),
    }, ensure_ascii=False))


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.post("/upload_reference")
async def upload_reference(file: UploadFile):
    data = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(data)
        tmp.flush()
        try:
            proc = subprocess.run(["pdftotext", tmp.name, "-"], capture_output=True, timeout=60)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return {"filename": file.filename, "chars": 0, "text": "", "error": str(e)}
    if proc.returncode != 0:
        return {"filename": file.filename, "chars": 0, "text": "", "error": proc.stderr.decode()[:300]}
    text = proc.stdout.decode(errors="replace").strip()[:REFERENCE_TEXT_CAP]
    return {"filename": file.filename, "chars": len(text), "text": text}


def _slug(topic: str) -> str:
    return "".join(c for c in topic[:50] if c.isalnum() or c in " -_").strip() or "debate"


def _content_disposition(filename: str) -> str:
    """HTTP headers are latin-1 only, so a CJK topic can't go in `filename=`
    directly. Send an ASCII fallback plus an RFC 5987 UTF-8 variant."""
    ascii_name = filename.encode("ascii", "ignore").decode().strip() or "debate"
    quoted = urllib.parse.quote(filename)
    return f'attachment; filename="{ascii_name}.pdf"; filename*=UTF-8\'\'{quoted}.pdf'


@app.get("/export.pdf")
async def export_pdf(session: str | None = None):
    """Exports the live debate, or a stored one from history when ?session= is given."""
    target = engine
    if session:
        data = load_session(session)
        if data is None:
            raise HTTPException(status_code=404, detail="session not found")
        target = session_from_dict(data)
    return Response(
        content=to_pdf_bytes(target),
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition(_slug(target.topic))},
    )


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    sockets.add(ws)
    # Sync current session state to the (re)connecting client.
    await ws.send_text(json.dumps({
        "type": "init",
        "state": engine.state,
        "round": engine.round_num,
        "topic": engine.topic,
        "participants": [vars(p) for p in engine.participants],
        "messages": [m.to_dict() for m in engine.messages],
        "presets": load_presets(),
        "sessions": list_sessions(),
        "models": PROVIDERS,
        "summary_model": engine.summary_model,
        "verdict_model": engine.verdict_model,
        "summary_instruction": engine.summary_instruction,
        "verdict_instruction": engine.verdict_instruction,
        "login_hints": LOGIN_HINTS,
    }, ensure_ascii=False))
    asyncio.create_task(send_provider_status(ws))
    try:
        while True:
            data = json.loads(await ws.receive_text())
            await handle(ws, data)
    except WebSocketDisconnect:
        sockets.discard(ws)


async def handle(ws: WebSocket, data: dict):
    action = data.get("action")
    try:
        if action == "start":
            engine.start(data["topic"], data["participants"], data.get("exchanges_per_round", 2),
                         data.get("force", False), data.get("reference_material", ""),
                         summary_model=data.get("summary_model"), verdict_model=data.get("verdict_model"),
                         summary_instruction=data.get("summary_instruction", ""),
                         verdict_instruction=data.get("verdict_instruction", ""))
        elif action == "interject":
            await engine.interject(data["text"])
        elif action == "continue":
            engine.continue_round()
        elif action == "set_meta_models":
            engine.set_meta_models(data.get("summary_model"), data.get("verdict_model"),
                                   data.get("summary_instruction", ""),
                                   data.get("verdict_instruction", ""))
            await broadcast({"type": "system", "text": _meta_models_label(engine)})
        elif action == "wrap_up":
            engine.wrap_up()
            await broadcast({"type": "system", "text": "Wrap-up requested — final verdict after current step."})
        elif action == "save_preset":
            # Only ever writes the gitignored local file — never the tracked
            # defaults, so a saved lineup can't end up staged for commit.
            local = [p for p in _read_presets(PERSONAS_LOCAL_FILE) if p["name"] != data["name"]]
            local.append({"name": data["name"], "participants": data["participants"],
                          "summary_model": data.get("summary_model"),
                          "verdict_model": data.get("verdict_model"),
                          "summary_instruction": data.get("summary_instruction", ""),
                          "verdict_instruction": data.get("verdict_instruction", "")})
            _write_local_presets(local)
            await broadcast({"type": "presets", "presets": load_presets()})
            await broadcast({"type": "system", "text": f"Saved preset \"{data['name']}\""})
        elif action == "delete_preset":
            name = data["name"]
            local = [p for p in _read_presets(PERSONAS_LOCAL_FILE) if p["name"] != name]
            if any(p["name"] == name for p in _read_presets(PERSONAS_DEFAULT_FILE)):
                local.append({"name": name, "deleted": True})
            _write_local_presets(local)
            await broadcast({"type": "presets", "presets": load_presets()})
            await broadcast({"type": "system", "text": f"Deleted preset \"{name}\""})
        elif action == "list_sessions":
            await ws.send_text(json.dumps({"type": "sessions", "sessions": list_sessions()}, ensure_ascii=False))
        elif action == "get_session":
            sess = load_session(data.get("id", ""))
            if sess is None:
                await ws.send_text(json.dumps({"type": "system", "text": "Session not found or unreadable."}, ensure_ascii=False))
            else:
                await ws.send_text(json.dumps({"type": "session", "id": data.get("id", ""), "session": sess}, ensure_ascii=False))
        elif action == "get_provider_status":
            await send_provider_status(ws)
        elif action == "export":
            target = engine
            if data.get("session"):
                stored = load_session(data["session"])
                if stored is None:
                    await ws.send_text(json.dumps({"type": "system", "text": "Session not found or unreadable."}, ensure_ascii=False))
                    return
                target = session_from_dict(stored)
            OBSIDIAN_DIR.mkdir(parents=True, exist_ok=True)
            path = OBSIDIAN_DIR / f"{time.strftime('%Y-%m-%d')} - {_slug(target.topic)}.md"
            path.write_text(render_markdown(target))
            await broadcast({"type": "system", "text": f"Exported to Obsidian: {path.name}"})
    except Exception as e:
        await ws.send_text(json.dumps({"type": "system", "text": f"Error: {e}"}))


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8500)
