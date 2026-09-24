# AIG GenAI Research Assistant

A small, authenticated RAG agent for analyzing AIG's public investor
communications (quarterly results presentations and investor day materials).
Built to demonstrate a production-shaped agentic architecture: a real
authentication flow, a tool-using agent over the Claude Agent SDK, and a
retrieval layer exposed through the Model Context Protocol (MCP) rather than
hand-rolled inside the agent.

## Architecture

```
                     ┌──────────────────┐
   Browser  ───────► │  Frontend (SPA)  │
                     │  static HTML/JS  │
                     └────────┬─────────┘
                              │ HTTPS
                              ▼
                     ┌──────────────────────────────┐
                     │   FastAPI backend             │
                     │   backend/app/main.py         │
                     │                                │
                     │   POST /api/login ────────────┼───► Amazon Cognito
                     │     (USER_PASSWORD_AUTH)      │      (user pool)
                     │                                │
                     │   POST /api/chat               │
                     │     (Bearer JWT, verified      │◄──── JWKS
                     │      against Cognito JWKS)     │
                     │                                │
                     │   GET  /healthz                │
                     └────────┬───────────────────────┘
                              │ per-session
                              ▼
                     ┌──────────────────────────────┐
                     │  Claude Agent SDK session     │
                     │  backend/app/agent_session.py │
                     │  (one ClaudeSDKClient per      │
                     │   session_id, kept in memory) │──────► AWS Bedrock
                     └────────┬───────────────────────┘        (Claude model)
                              │ spawns as subprocess, talks stdio
                              ▼
                     ┌──────────────────────────────┐
                     │  MCP server (mcp_server.py)   │
                     │  tools: list_documents,       │
                     │  search_documents (BM25),     │
                     │  get_page_text,                │
                     │  extract_financial_metrics    │
                     └────────┬───────────────────────┘
                              │ reads at startup
                              ▼
                     ┌──────────────────────────────┐
                     │  data/index/chunks.json       │
                     │  built offline by ingest.py    │
                     │  from data/raw/*.pdf (pypdf,   │
                     │  900-char chunks, 150 overlap) │
                     └──────────────────────────────┘
```

The agent never answers from memory: the system prompt requires it to call
`search_documents` before any substantive claim, and to cite `(doc_id, page)`
inline for every fact — retrieval is a hard requirement, not a suggestion.

## Setup

Requires Python 3.11+ and an AWS account with Bedrock model access, a
Cognito user pool (app client using `USER_PASSWORD_AUTH`), and credentials
available to the process (e.g. via `aws configure` or an IAM role).

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment (see `.env.example` — copy it to `.env` and fill
#    in your own AWS_REGION, COGNITO_POOL_ID, COGNITO_CLIENT_ID)
cp .env.example .env

# 3. Ingest the source PDFs into a searchable index (run once, and again
#    whenever data/raw/ changes)
python ingest.py

# 4. Run the backend
uvicorn backend.app.main:app --reload --port 8080
# Windows note: use `python backend/run_server.py` instead -- uvicorn's
# --reload defaults to an event loop that can't spawn subprocesses on
# Windows, which the agent needs to launch mcp_server.py. See
# backend/run_server.py for why.

# 5. Open http://127.0.0.1:8080 and log in with a Cognito user
```

To run the standalone CLI agent instead of the web app:

```bash
python agent_app.py
```

To run the test suite:

```bash
pip install -r requirements-dev.txt
pytest
```

## Design decisions and trade-offs

- **MCP as the tool boundary, not a library call.** `mcp_server.py` is a
  standalone process talking stdio, not a Python module the agent imports
  directly. That's slightly more moving parts locally, but it means the
  retrieval layer has a stable, inspectable interface that doesn't change
  if the agent's internals do — and it's directly reusable from a second,
  differently-orchestrated agent without touching this code at all.
- **BM25 over embeddings, for now.** Lexical search (`rank-bm25`) was fast to
  build and, for a three-document demo corpus, its gap against dense
  retrieval isn't the bottleneck. The real cost is queries phrased with
  different vocabulary than the source text ("cost of claims" vs. "loss
  ratio") — hybrid lexical + semantic retrieval is the natural next step,
  not a rewrite.
- **Single agent, not multi-agent.** Retrieve-then-answer doesn't decompose
  into independent sub-problems, so a second agent would add coordination
  overhead and cost without an accuracy gain. Multi-agent is the right call
  when a task genuinely needs independent specialization or verification —
  see the design notes on the companion Agentic Documents Assistant project
  for a case where a two-agent extract/validate split measurably helped.
- **Routing through Bedrock, not a direct model API key.** This gives
  centralized cost visibility and IAM-based access control, at the cost of
  Bedrock's own model lifecycle and cross-region inference-profile handling.

## Known limitations

- **Conversation state is in-memory and unbounded.** `agent_session.py`
  keeps one `ClaudeSDKClient` per `session_id` in a module-level dict with
  no eviction and no persistence. Two consequences: (1) this breaks the
  moment more than one backend instance is running (a request can land on
  an instance that's never seen that session), and (2) even with a single
  instance, the dict grows without bound over the app's lifetime — it's a
  slow memory leak, not just a scaling limitation. Both are the same root
  cause (state that should be externalized isn't), and both get fixed
  together by moving to a persisted, checkpointed session store.
- **`/healthz` is a liveness check only.** It confirms the process is up,
  not that the MCP subprocess can spawn or that the chunk index loaded
  successfully. A readiness check that verifies those is a natural next
  addition.
- **No retry/backoff on Bedrock throttling or transient failures.** A
  request that hits a rate limit today just fails.
- **Ingestion is a manual, full-rebuild step.** `ingest.py` re-processes
  every PDF in `data/raw/` from scratch; it has no notion of "unchanged, skip
  it" or "removed, delete it from the index."
- **Local-only.** There is currently no container image, CI pipeline, or
  deployed environment — see the roadmap below.

## Roadmap

This repo is being extended in phases to close out a broader set of
production-agent capabilities: containerized deployment, hybrid retrieval
with reranking, LangGraph orchestration with human-in-the-loop approval,
Bedrock Guardrails, an automated evaluation/CI pipeline, and full
observability. Each phase is built and demonstrated working before the next
one starts.
