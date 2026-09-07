from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agent import Agent, MSG

app = FastAPI(title="Payment Collection Agent")

sessions: dict[str, Agent] = {}


class MessageRequest(BaseModel):
    message: str


@app.post("/session")
def create_session():
    session_id = str(uuid.uuid4())
    sessions[session_id] = Agent()
    return {"session_id": session_id, "message": MSG["greet"]}


@app.post("/session/{session_id}/message")
def send_message(session_id: str, req: MessageRequest):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    agent = sessions[session_id]
    response = agent.next(req.message)
    return {"session_id": session_id, **response}


@app.get("/health")
def health():
    return {"status": "ok"}
