from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .auth import login as cognito_login, verify_token
from .agent_session import ask

app = FastAPI(title="AIG GenAI Research Assistant")


class LoginRequest(BaseModel):
    username: str
    password: str


class ChatRequest(BaseModel):
    session_id: str
    message: str


@app.post("/api/login")
def api_login(body: LoginRequest):
    token = cognito_login(body.username, body.password)
    return {"id_token": token}

@app.post("/api/chat")
async def api_chat(body: ChatRequest, claims: dict = Depends(verify_token)):
    return await ask(body.session_id, body.message)

# Serve the static frontend last, so /api/* routes above take priority.
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")