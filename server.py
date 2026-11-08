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

from agent import Agent
from context import CONFIG_DIR, ROOT

app = FastAPI(title="mini-claude")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

SESSIONS: dict[str, Agent] = {}


class ChatRequest(BaseModel):
    session_id: str
    message: str


def get_agent(session_id: str) -> Agent:
    if session_id not in SESSIONS:
        path = CONFIG_DIR / "sessions" / f"web-{session_id}.json"
        SESSIONS[session_id] = Agent(session_path=path)
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


@app.get("/api/state")
def state(session_id: str):
    """What the model would be sent right now -- the 'show your working' endpoint."""
    agent = get_agent(session_id)
    return {
        "system_prompt": agent.system,
        "message_count": len(agent.messages),
        "messages": agent.messages,
    }
