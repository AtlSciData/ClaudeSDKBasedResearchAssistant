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


@app.get("/healthz")
def healthz():
    """Liveness probe for App Runner/ECS and the Docker HEALTHCHECK.

    Deliberately unauthenticated and dependency-free: it only confirms the
    process is up and serving requests. It does not (yet) check that the
    MCP subprocess or the chunk index are healthy -- see the README's
    "Known limitations" section.
    """
    return {"status": "ok"}


@app.post("/api/login")
def api_login(body: LoginRequest):
    token = cognito_login(body.username, body.password)
    return {"id_token": token}

@app.post("/api/chat")
async def api_chat(body: ChatRequest, claims: dict = Depends(verify_token)):
    return await ask(body.session_id, body.message)

# Serve the static frontend last, so /api/* routes above take priority.
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")