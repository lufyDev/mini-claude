"""
Web renderer: the same Agent.run() event stream, pushed to the browser as SSE.

    uvicorn server:app --reload

One agent per browser session id, held in memory -- swap the dict for Redis if
you ever run more than one worker.
"""
import json
from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import MODEL, Agent
from context import CONFIG_DIR, ROOT, WORKSPACE
from tools import safe_path
from usage import BUDGET_USD

app = FastAPI(title="mini-claude")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

SESSIONS: dict[str, Agent] = {}


class ChatRequest(BaseModel):
    session_id: str
    message: str


def get_agent(session_id: str) -> Agent:
    if session_id not in SESSIONS:
        path = CONFIG_DIR / "sessions" / f"web-{session_id}.json"
        SESSIONS[session_id] = Agent(session_path=path, session_name=f"web-{session_id}")
    return SESSIONS[session_id]


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.post("/api/chat")
def chat(req: ChatRequest):
    agent = get_agent(req.session_id)

    def events():
        try:
            for ev in agent.run(req.message):
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as exc:                      # never hang the browser
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/meta")
def meta():
    return {"model": MODEL, "budget_usd": BUDGET_USD}


@app.get("/api/files")
def files():
    """Everything the agent has created, so you can watch the workspace fill up."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    out = []
    for child in sorted(WORKSPACE.rglob("*")):
        if child.is_dir() or child.name.startswith("."):
            continue
        out.append({
            "path": str(child.relative_to(WORKSPACE)),
            "size": child.stat().st_size,
        })
    return {"files": out}


@app.get("/api/file")
def file(path: str):
    """One file's contents. Same path guard the tools use."""
    try:
        target = safe_path(WORKSPACE, path)
    except ValueError as exc:
        return {"error": str(exc)}
    if not target.is_file():
        return {"error": f"no such file: {path}"}
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
        "message_count": len(agent.messages),
        "messages": agent.messages,
    }
