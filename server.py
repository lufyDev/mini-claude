"""
Web renderer: the same Agent.run() event stream, pushed to the browser as SSE.

    uvicorn server:app --reload

With DEMO_MODE=1 this is also the enforcement point for a public deployment:
per-session workspaces, a per-IP throttle, a per-session turn cap and a global
daily spend cap. See limits.py for the policy and why each one is there.
"""
import json
import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import limits
from agent import MODEL, Agent
from context import CONFIG_DIR, ROOT, WORKSPACE
from tools import safe_path
from usage import BUDGET_USD, LEDGER

app = FastAPI(title="mini-claude")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

WORKSPACES = ROOT / "workspaces"
SESSIONS: dict[str, Agent] = {}
TURNS: dict[str, int] = {}
rate = limits.RateLimiter()

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ChatRequest(BaseModel):
    session_id: str
    message: str


def workspace_for(session_id: str) -> Path:
    """One directory per visitor in demo mode; the shared one locally."""
    if not limits.DEMO_MODE:
        return WORKSPACE
    ws = WORKSPACES / session_id
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def get_agent(session_id: str) -> Agent:
    if session_id not in SESSIONS:
        SESSIONS[session_id] = Agent(
            session_path=CONFIG_DIR / "sessions" / f"web-{session_id}.json",
            session_name=f"web-{session_id}",
            workspace=workspace_for(session_id),
        )
    return SESSIONS[session_id]


def sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def refusal(message: str) -> StreamingResponse:
    """Refusals go back down the same event stream, so the UI needs no new path."""
    body = sse({"type": "error", "message": message}) + sse({"type": "done", "usage": {}})
    return StreamingResponse(iter([body]), media_type="text/event-stream")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.post("/api/chat")
def chat(req: ChatRequest, request: Request):
    if not SAFE_ID.match(req.session_id):
        return refusal("bad session id")
    if not req.message.strip():
        return refusal("empty message")
    if len(req.message) > 2000:
        return refusal("message too long")

    client = request.client.host if request.client else "unknown"
    if not rate.allow(client):
        return refusal(f"slow down - {limits.RATE_PER_MIN} messages a minute on the demo")

    turns = TURNS.get(req.session_id, 0)
    if limits.DEMO_MODE and turns >= limits.SESSION_MSG_CAP:
        return refusal(
            f"demo limit: {limits.SESSION_MSG_CAP} messages per session. "
            "Clone the repo and run it with your own key to keep going."
        )

    # The cap that actually protects the bill: a per-session limit alone is
    # defeated by reloading the page for a fresh id.
    if limits.DEMO_MODE and limits.spent_today(LEDGER) >= limits.DAILY_USD_CAP:
        return refusal("the demo's daily budget is spent - try again tomorrow, or run it locally.")

    TURNS[req.session_id] = turns + 1
    agent = get_agent(req.session_id)

    def events():
        try:
            for ev in agent.run(req.message):
                yield sse(ev)
        except Exception as exc:                      # never hang the browser
            yield sse({"type": "error", "message": str(exc)})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/meta")
def meta(session_id: str = ""):
    out = {"model": MODEL, "budget_usd": BUDGET_USD, "demo": limits.DEMO_MODE}
    if limits.DEMO_MODE:
        out["messages_left"] = max(limits.SESSION_MSG_CAP - TURNS.get(session_id, 0), 0)
        out["daily_left_usd"] = round(max(limits.DAILY_USD_CAP - limits.spent_today(LEDGER), 0), 4)
    return out


@app.get("/api/files")
def files(session_id: str = ""):
    """Everything the agent has created, so you can watch the workspace fill up."""
    ws = workspace_for(session_id) if SAFE_ID.match(session_id or "x") else WORKSPACE
    ws.mkdir(parents=True, exist_ok=True)
    out = []
    for child in sorted(ws.rglob("*")):
        if child.is_dir() or child.name.startswith("."):
            continue
        out.append({"path": str(child.relative_to(ws)), "size": child.stat().st_size})
    return {"files": out}


@app.get("/api/file")
def file(path: str, session_id: str = ""):
    """One file's contents. Same path guard the tools use."""
    ws = workspace_for(session_id) if SAFE_ID.match(session_id or "x") else WORKSPACE
    try:
        target = safe_path(ws, path)
    except ValueError as exc:
        return {"error": str(exc)}
    if not target.is_file():
        return {"error": f"no such file: {path}"}
    if target.stat().st_size > limits.MAX_WRITE_BYTES:
        return {"error": "file too large to preview"}
    try:
        return {"path": path, "content": target.read_text(encoding="utf-8")}
    except UnicodeDecodeError:
        return {"error": f"{path} is not a UTF-8 text file"}


@app.get("/api/state")
def state(session_id: str):
    """What the model would be sent right now -- the 'show your working' endpoint."""
    agent = get_agent(session_id)
    return {
        "system_prompt": agent.system,
        "message_count": len(agent.items),
        "messages": agent.items,
    }
