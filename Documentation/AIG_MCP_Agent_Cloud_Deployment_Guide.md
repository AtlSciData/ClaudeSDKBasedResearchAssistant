# Phase 2: Ship It — Docker, AWS Deployment, Login, GitHub, Video

**Budget:** ~10 hours over 2 days. Given that's genuinely tight for a first-time full-stack cloud deploy, here's what I assumed to keep this achievable — flagging these up front so you can override any of them:

- **App Runner, not ECS Fargate.** Both are legitimate Docker deployment targets, but ECS Fargate needs you to hand-configure a VPC, ALB, target groups, and security groups. App Runner takes a container image and gives you a running, HTTPS, auto-scaled service in a few clicks. For a 10-hour demo, that time is better spent on the app itself. (In the interview, you can honestly say "I used App Runner for speed; in an enterprise setting with more complex networking/compliance needs I'd use ECS Fargate behind an ALB" — that's a real, sensible tradeoff statement, not a weakness.)
- **One container, not two.** The FastAPI backend serves both the chat API *and* the built static frontend (single-page app) from the same process. One Dockerfile, one image, one App Runner service, one URL. The "textbook" split (S3+CloudFront for frontend, separate API service) is worth naming as the production alternative, but building it doubles your infra surface area for no functional gain in a demo.
- **Cognito with a custom login form, not the Hosted UI redirect.** Your frontend POSTs a username/password to your own `/api/login` endpoint, which calls Cognito server-side. This avoids OAuth redirect/PKCE complexity in the frontend while still being genuine Cognito-backed authentication.
- **Plain HTML/JS frontend, no React build step.** One `index.html` with vanilla JS. This is a demo, not a product — skip the Node toolchain.

If any of these don't sit right with you, say so and I'll adjust before you're deep into building.

---

## Day 1 (~5 hrs): Web app + Docker, running locally

### 1. Architecture

```
Browser (index.html + JS)
   |  POST /api/login (username, password)
   |  POST /api/chat   (message, session_id)  -- Authorization: Bearer <Cognito ID token>
   v
FastAPI backend (single container)
   |-- verifies JWT against Cognito JWKS
   |-- holds one ClaudeSDKClient per active session_id (in-memory dict)
   |-- ClaudeSDKClient talks to mcp_server.py (subprocess, stdio) -- from Phase 1
   v
Claude API (Anthropic) <-- MCP tool calls --> aig-research MCP server --> data/index/chunks.json
```

Project layout, extending what you already have:
```
aig-genai-agent/
  data/                     # from Phase 1 (unchanged)
  mcp_server.py             # from Phase 1 (unchanged)
  ingest.py                 # from Phase 1 (unchanged)
  backend/
    app/
      main.py
      auth.py
      agent_session.py
    requirements.txt
    Dockerfile
  frontend/
    index.html
    app.js
    style.css
  .env                      # local only, never committed
  .gitignore
  README.md
```

### 2. Cognito setup (AWS Console, ~15 min)

1. AWS Console → Cognito → **Create user pool**.
2. Sign-in options: email. Leave MFA off for the demo (note in your README that you'd turn it on for production).
3. App integration → create an **app client** with **no client secret** (simplifies server-side calls) and enable the **`USER_PASSWORD_AUTH`** auth flow (you'll need to explicitly enable this flow on the app client — it's off by default).
4. Note down: **User Pool ID**, **App Client ID**, and your **AWS region**.
5. Create one test user (Console → Users → Create user), set a permanent password so you're not stuck in the "force change password" flow during your demo/recording.

### 3. Backend: `backend/app/auth.py`

```python
"""
Cognito authentication: login (USER_PASSWORD_AUTH) and JWT verification.
"""
import os
import time
import boto3
import requests
from jose import jwk, jwt
from jose.utils import base64url_decode
from fastapi import HTTPException, Header

AWS_REGION = os.environ["AWS_REGION"]
COGNITO_POOL_ID = os.environ["COGNITO_POOL_ID"]
COGNITO_CLIENT_ID = os.environ["COGNITO_CLIENT_ID"]

_cognito = boto3.client("cognito-idp", region_name=AWS_REGION)

_JWKS_URL = f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{COGNITO_POOL_ID}/.well-known/jwks.json"
_jwks_cache = {"keys": None, "fetched_at": 0}


def _get_jwks():
    if _jwks_cache["keys"] is None or time.time() - _jwks_cache["fetched_at"] > 3600:
        resp = requests.get(_JWKS_URL, timeout=5)
        resp.raise_for_status()
        _jwks_cache["keys"] = resp.json()["keys"]
        _jwks_cache["fetched_at"] = time.time()
    return _jwks_cache["keys"]


def login(username: str, password: str) -> str:
    """Authenticate against Cognito and return the ID token."""
    try:
        resp = _cognito.initiate_auth(
            ClientId=COGNITO_CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )
    except _cognito.exceptions.NotAuthorizedException:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return resp["AuthenticationResult"]["IdToken"]


def verify_token(authorization: str = Header(...)) -> dict:
    """FastAPI dependency: verifies the Bearer ID token, returns its claims."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ")

    try:
        headers = jwt.get_unverified_header(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Malformed token")

    key_data = next((k for k in _get_jwks() if k["kid"] == headers["kid"]), None)
    if key_data is None:
        raise HTTPException(status_code=401, detail="Unknown signing key")

    public_key = jwk.construct(key_data)
    message, encoded_sig = token.rsplit(".", 1)
    sig = base64url_decode(encoded_sig.encode())
    if not public_key.verify(message.encode(), sig):
        raise HTTPException(status_code=401, detail="Invalid token signature")

    claims = jwt.get_unverified_claims(token)
    if time.time() > claims["exp"]:
        raise HTTPException(status_code=401, detail="Token expired")
    if claims.get("aud") != COGNITO_CLIENT_ID and claims.get("client_id") != COGNITO_CLIENT_ID:
        raise HTTPException(status_code=401, detail="Token not issued for this app")

    return claims
```

> Verify the exact `jose`/`boto3` call signatures against current docs as you type this in — the shapes above are the standard, long-stable Cognito JWT-verification pattern, but library versions drift.

### 4. Backend: `backend/app/agent_session.py`

Wraps a `ClaudeSDKClient` per logged-in session so multi-turn context survives across HTTP requests. **Demo-only caveat to say out loud in your video**: this is in-memory, single-instance state — fine for a demo on one App Runner instance, not how you'd do multi-instance production session state (that would be Redis or DynamoDB-backed).

```python
import asyncio
import os
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, AssistantMessage, TextBlock

SYSTEM_PROMPT = """You are the AIG GenAI Research Assistant... (reuse the Phase 1 system prompt)"""

_sessions: dict[str, ClaudeSDKClient] = {}
_lock = asyncio.Lock()


def _build_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"aig_research": {"type": "stdio", "command": "python", "args": ["mcp_server.py"]}},
        allowed_tools=[
            "mcp__aig_research__list_documents",
            "mcp__aig_research__search_documents",
            "mcp__aig_research__get_page_text",
            "mcp__aig_research__extract_financial_metrics",
        ],
        permission_mode="bypassPermissions",
        # Same pin as Phase 1's agent_app.py — without this, you'll hit the exact
        # "Opus 5 not available" startup warning again (harmless, but no reason to
        # reintroduce it). Match whatever you confirmed works via
        # `aws bedrock list-inference-profiles`.
        model=os.environ.get("ANTHROPIC_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
        max_turns=8,
    )


async def get_or_create_client(session_id: str) -> ClaudeSDKClient:
    async with _lock:
        if session_id not in _sessions:
            client = ClaudeSDKClient(options=_build_options())
            await client.connect()
            _sessions[session_id] = client
        return _sessions[session_id]


async def ask(session_id: str, message: str) -> str:
    client = await get_or_create_client(session_id)
    await client.query(message)
    reply_parts = []
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    reply_parts.append(block.text)
    return "\n".join(reply_parts)
```

### 5. Backend: `backend/app/main.py`

```python
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
    reply = await ask(body.session_id, body.message)
    return {"reply": reply}


# Serve the static frontend last, so /api/* routes above take priority.
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
```

### 6. Frontend: `frontend/index.html` + `frontend/app.js`

Keep this minimal — a login form, then a chat box. Sketch:

```html
<!-- frontend/index.html -->
<!DOCTYPE html>
<html>
<head><title>AIG GenAI Research Assistant</title><link rel="stylesheet" href="style.css"></head>
<body>
  <div id="login-view">
    <h2>Sign in</h2>
    <input id="username" placeholder="username" />
    <input id="password" type="password" placeholder="password" />
    <button onclick="login()">Sign in</button>
    <p id="login-error"></p>
  </div>
  <div id="chat-view" style="display:none">
    <h2>AIG GenAI Research Assistant</h2>
    <div id="messages"></div>
    <input id="message-input" placeholder="Ask about AIG's business..." />
    <button onclick="sendMessage()">Send</button>
  </div>
  <script src="app.js"></script>
</body>
</html>
```

```javascript
// frontend/app.js
let idToken = null;
const sessionId = crypto.randomUUID();

async function login() {
  const username = document.getElementById("username").value;
  const password = document.getElementById("password").value;
  const resp = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!resp.ok) {
    document.getElementById("login-error").textContent = "Login failed";
    return;
  }
  const data = await resp.json();
  idToken = data.id_token;
  document.getElementById("login-view").style.display = "none";
  document.getElementById("chat-view").style.display = "block";
}

async function sendMessage() {
  const input = document.getElementById("message-input");
  const text = input.value;
  input.value = "";
  appendMessage("You", text);

  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${idToken}`,
    },
    body: JSON.stringify({ session_id: sessionId, message: text }),
  });
  const data = await resp.json();
  appendMessage("Assistant", data.reply);
}

function appendMessage(sender, text) {
  const div = document.createElement("div");
  div.textContent = `${sender}: ${text}`;
  document.getElementById("messages").appendChild(div);
}
```

### 7. Dockerfile: `backend/Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_server.py .
COPY data/ ./data/
COPY backend/app ./app
COPY frontend/ ./frontend/

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

`backend/requirements.txt` (adds to Phase 1's list):
```
claude-agent-sdk
mcp<2
pypdf
rank-bm25
python-dotenv
fastapi
uvicorn[standard]
boto3
python-jose[cryptography]
requests
```

### 8. Build and test locally, before touching AWS

```bash
docker build -t aig-genai-agent -f backend/Dockerfile .
docker run -p 8080:8080 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e AWS_REGION=us-east-1 \
  -e AWS_ACCESS_KEY_ID=... \
  -e AWS_SECRET_ACCESS_KEY=... \
  -e COGNITO_POOL_ID=us-east-1_xxxxxxx \
  -e COGNITO_CLIENT_ID=xxxxxxxxxxxxxxxxxxxxxxxxxx \
  aig-genai-agent
```

Open `http://localhost:8080`, log in with your test Cognito user, send a message. **Get this fully working locally in Docker before you touch AWS deployment** — debugging is far faster locally than through App Runner's deploy cycle.

---

## Day 2 (~5 hrs): AWS deployment, GitHub, video

### 1. Push the image to ECR

```bash
aws ecr create-repository --repository-name aig-genai-agent --region us-east-1

aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin <your-account-id>.dkr.ecr.us-east-1.amazonaws.com

docker tag aig-genai-agent:latest <your-account-id>.dkr.ecr.us-east-1.amazonaws.com/aig-genai-agent:latest
docker push <your-account-id>.dkr.ecr.us-east-1.amazonaws.com/aig-genai-agent:latest
```

### 2. Create the App Runner service (AWS Console)

1. App Runner → Create service → **Container registry** → **Amazon ECR** → select the image you just pushed.
2. Deployment trigger: manual (simplest for a demo — you control exactly when it redeploys).
3. Port: `8080` (must match your Dockerfile's `EXPOSE`/uvicorn port).
4. Environment variables: `AWS_REGION`, `COGNITO_POOL_ID`, `COGNITO_CLIENT_ID`. For `ANTHROPIC_API_KEY`, use App Runner's **secret** environment variable option backed by **Secrets Manager** rather than a plain env var — put the key in Secrets Manager first (`aws secretsmanager create-secret --name anthropic-api-key --secret-string sk-ant-...`), then reference it. This is worth doing properly and mentioning in your video: "secrets aren't stored as plaintext env vars."
5. Instance role: create an IAM role for the App Runner service with a policy granting only `cognito-idp:InitiateAuth` on your specific user pool ARN (and `secretsmanager:GetSecretValue` on that one secret) — least privilege, not `AdministratorAccess`. This is a good thing to narrate in the interview: you scoped the IAM role deliberately rather than reaching for a broad policy.
6. Create & deploy. App Runner gives you a public HTTPS URL when it's done (a few minutes).

### 3. Test the live deployment

Open the App Runner URL, log in with your Cognito test user, run through the same prompts from Phase 1. Check **CloudWatch Logs** (App Runner service → Logs) to see your MCP tool-call print statements — this is a nice moment for the video: showing the logs proving the agent actually called `search_documents` rather than hallucinating.

### 4. GitHub

`.gitignore`:
```
.venv/
__pycache__/
.env
*.pyc
```

The public AIG PDFs in `data/raw/` are fine to commit — they're already public, no confidentiality concern. If they push the repo size up uncomfortably, an alternative is a `data/README.md` with the download links from Phase 1 and a `.gitignore` entry for `data/raw/*.pdf`, so anyone cloning the repo runs `ingest.py` after downloading the PDFs themselves.

`README.md` — structure it as:
1. One paragraph: what this is and why you built it (naming the AIG role explicitly is fine and normal for a portfolio project).
2. Architecture diagram (even ASCII, like the one earlier in this guide, is enough).
3. Setup/run instructions (local + Docker).
4. **A section titled something like "Relevance to this role"** that's essentially the talking-points table from the Phase 1 guide — an interviewer or hiring manager skimming your repo should immediately see the connection to MCP, Claude Agent SDK, RAG, and AWS.
5. Known limitations (BM25 not embeddings, in-memory session state, no RL/knowledge graph) — stated plainly. This reads as engineering maturity, not weakness.

```bash
git init
git add .
git commit -m "AIG GenAI Research Assistant: Claude Agent SDK + MCP server over public AIG investor docs"
git remote add origin https://github.com/<your-username>/aig-genai-agent.git
git push -u origin main
```

### 5. Record the video

Aim for 5–8 minutes, not longer — structure it so each segment maps to a JD line, same as the talking-points table from Phase 1:

1. **(30s) Framing**: "I built this to demonstrate hands-on experience with the specific technologies this role calls out — Claude Agent SDK, MCP server design, RAG — using AIG's own public investor materials as the dataset."
2. **(1-2 min) Architecture walkthrough**: screen-share the README diagram, narrate the flow (frontend → FastAPI → Cognito auth → Claude Agent SDK → MCP server → BM25 index → public AIG PDFs).
3. **(2-3 min) Live demo**: open the deployed App Runner URL (not localhost — showing it's actually running in AWS matters), log in, ask 2-3 questions, point out the citations in the answers.
4. **(1 min) Show the MCP server code directly**: pull up `mcp_server.py`, briefly explain the tools — this is the part that most directly answers "MCP server design and implementation."
5. **(1 min) Show CloudWatch logs**: prove the tool calls are real, not scripted.
6. **(30s) Close with the honest gaps**: BM25 vs. embeddings, no RL/knowledge graph yet, and how you'd extend it — this is the single highest-leverage 30 seconds for credibility.

Practical recording notes: use a fresh browser profile/incognito window so no saved passwords or personal bookmarks show; never show `.env` contents, terminal history with real API keys, or your AWS account ID on screen (crop or blur if needed); record at 1080p; a simple tool like OBS Studio (free) or QuickTime screen recording (Mac) is enough — no need for editing software beyond trimming the start/end.

### 6. Afterward: tear down AWS resources

App Runner, ECR image storage, and Secrets Manager all cost small amounts continuously. After your interview cycle is done (or once you've recorded the video), delete the App Runner service, the ECR repository, and the Secrets Manager secret to stop charges. Cognito's free tier is generous enough that a User Pool with one test user costs nothing meaningful, but delete it too if you want a clean account.

---

## Time-budget checkpoint

If you're running behind partway through Day 1, the thing to cut first is Cognito/login — a hardcoded single shared password check in the backend (clearly labeled as a placeholder in your README, with "would use Cognito/proper IAM-backed auth in production" as the stated intent) still lets you demo Docker + AWS deployment + the agent, which are the parts most directly tied to the JD. Login is the least JD-relevant piece of this whole build — don't let it eat time you need for the MCP/agent/AWS-deployment story.
