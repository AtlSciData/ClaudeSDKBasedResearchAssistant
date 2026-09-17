# Build Guide: "AIG GenAI Research Assistant" — Claude Agent SDK + a hand-built MCP Server

**Why this project:** the AIG posting names three things by title — an "Agentic AI framework such as CrewAI, Claude Agent SDK, and LangChain," "Model Context Protocol (MCP) server design and implementation," and "RAG." Your CV currently has strong GenAI agent work (Claude 3.5 Sonnet extraction agent, multi-LLM validation, RAG chatbot) but nothing naming these specific tools. This project closes that gap directly, using only public AIG documents. **Updated per your request: the model calls route through your own AWS Bedrock account** rather than a direct Anthropic API key — see section 2.

**What you'll end up with:** a small CLI agent, "the AIG GenAI Research Assistant," that answers questions about AIG's business (segments, results, strategy) by retrieving grounded passages from AIG's own public investor documents and citing them — built on a real hand-written MCP server (not just the SDK's convenience decorator) talking to a Claude Agent SDK client.

**Time budget (2–3 hrs):**
| Phase | Time |
|---|---|
| 1. Get public source docs | 10 min |
| 2. Project scaffold + deps | 10 min |
| 3. Ingestion (chunk PDFs, build search index) | 30–40 min |
| 4. Write the MCP server | 30–40 min |
| 5. Write the Claude Agent SDK client | 25–35 min |
| 6. Run, test, iterate | 20–30 min |
| 7. Interview talking points | 10 min |

All commands below are things **you** run on your own machine — I'm not executing anything here.

---

## 0. What's verified vs. what to double-check yourself

Everything in this guide reflects the actual installed `claude-agent-sdk` package API that I introspected directly (function signatures, dataclass fields, TypedDict shapes) — not documentation claims. An earlier research pass I ran surfaced a suspicious/likely-fabricated claim that Bedrock routing was broken (citing a nonexistent `AnthropicBedrockMantle` class). **Update: I've since verified real Bedrock support directly against the official docs at [code.claude.com/docs/en/amazon-bedrock](https://code.claude.com/docs/en/amazon-bedrock)** — it's a real, current, documented feature (`CLAUDE_CODE_USE_BEDROCK=1`), and I also confirmed in the installed SDK's own source (`_internal/transport/subprocess_cli.py`) that it merges your shell's environment into the subprocess it spawns, which is why setting the Bedrock env vars in your shell/`.env` is enough — no SDK code changes needed. Section 2 below now uses this path.

---

## 1. Get the public source documents

Download these into a folder — they're official public investor-relations PDFs from AIG's own site:

- 4Q 2025 & Full-Year AIG Financial Results Presentation — https://www.aig.com/content/dam/aig/america-canada/us/documents/investor-relations/financial-result-presentation/aig-financial-results-presentation-4q25-and-full-year.pdf
- 3Q 2025 AIG Financial Results Presentation — https://www.aig.com/content/dam/aig/america-canada/us/documents/investor-relations/financial-result-presentation/aig-financial-results-presentation-3q25.pdf
- AIG Investor Day 2025 Presentation — https://www.aig.com/content/dam/aig/america-canada/us/documents/investor-relations/aig_investor_day_2025__presentation.pdf

Optional (heavier, but adds real 10-K narrative text rather than just slide decks): AIG's latest 10-K from SEC EDGAR — browse filings at https://www.sec.gov/edgar/browse/?CIK=0000005272 or AIG's own filings page https://www.aig.com/home/investor-relations/sec-filings, download the most recent 10-K as PDF or HTML.

Save these into:
```
aig-genai-agent/
  data/
    raw/
      aig-4q25-results.pdf
      aig-3q25-results.pdf
      aig-investor-day-2025.pdf
```

---

## 2. Project scaffold (using AWS Bedrock instead of an Anthropic API key)

```bash
mkdir -p aig-genai-agent/data/raw aig-genai-agent/data/index
cd aig-genai-agent
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

`requirements.txt` — **unchanged, nothing Bedrock-specific to add here.** This surprised me too, so worth understanding why: the Claude Agent SDK doesn't make model calls itself. It bundles and shells out to the actual Claude Code CLI binary (a compiled executable inside the `claude-agent-sdk` package), and *that* binary does its own AWS SigV4 signing and credential resolution when `CLAUDE_CODE_USE_BEDROCK` is set. You don't need `boto3` or `anthropic[bedrock]` in your Python code for this to work — the routing happens entirely below your code, at the subprocess level.

```
claude-agent-sdk
mcp
pypdf
rank-bm25
python-dotenv
```

```bash
pip install -r requirements.txt
```

### One-time AWS Bedrock console step (do this once per AWS account)

Before your first call, you must request access to Anthropic models in Bedrock:

1. Open the [Amazon Bedrock console](https://console.aws.amazon.com/bedrock/) → **Model catalog**.
2. Select an Anthropic model (e.g. Claude Sonnet), submit the use-case access form. Access is granted immediately.
3. Note which region you did this in — Bedrock model access is per-region.

### IAM permissions

Attach this policy to whichever IAM user/role holds the credentials you'll use locally (your existing AWS CLI user is fine for a demo):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowModelAndInferenceProfileAccess",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock:ListInferenceProfiles",
        "bedrock:GetInferenceProfile"
      ],
      "Resource": [
        "arn:aws:bedrock:*:*:inference-profile/*",
        "arn:aws:bedrock:*:*:application-inference-profile/*",
        "arn:aws:bedrock:*:*:foundation-model/*"
      ]
    },
    {
      "Sid": "AllowMarketplaceSubscription",
      "Effect": "Allow",
      "Action": ["aws-marketplace:ViewSubscriptions", "aws-marketplace:Subscribe"],
      "Resource": "*",
      "Condition": { "StringEquals": { "aws:CalledViaLast": "bedrock.amazonaws.com" } }
    }
  ]
}
```

(You can scope `Resource` down to specific inference-profile ARNs once you know which model you're pinning, for tighter least-privilege — a good detail to mention in the interview.)

### `.env` (never commit this)

```
# No ANTHROPIC_API_KEY needed at all when using Bedrock.

# Enable Bedrock routing
CLAUDE_CODE_USE_BEDROCK=1
AWS_REGION=us-east-1

# Pin a specific model via a cross-region inference profile ID
# (check the Bedrock console's Model catalog for exact IDs your account can invoke)
ANTHROPIC_MODEL=us.anthropic.claude-sonnet-4-6

# AWS credentials: pick ONE of these mechanisms
# Option A — you already ran `aws configure` locally: leave this section
# out entirely; Claude Code reads your default AWS profile automatically.
# Option B — explicit access key on this machine only:
# AWS_ACCESS_KEY_ID=...
# AWS_SECRET_ACCESS_KEY=...
# AWS_SESSION_TOKEN=...
# Option C — a named profile:
# AWS_PROFILE=your-profile-name
```

Since your earlier message confirmed you already have live AWS credentials set up, Option A (your existing default profile via `aws configure`) is almost certainly already in place — you likely only need the first three lines (`CLAUDE_CODE_USE_BEDROCK`, `AWS_REGION`, `ANTHROPIC_MODEL`) plus `python-dotenv`'s `load_dotenv()` call at the top of your scripts (already in the Phase 1/2 code) to pick these up, since `load_dotenv()` writes them into `os.environ`, and the SDK's subprocess transport merges your process's environment into the CLI subprocess it spawns automatically — no extra plumbing needed.

**Pick your model ID deliberately, don't guess.** The `us.` prefix is a *cross-region inference profile* — it only resolves if your account actually has that profile enabled in the region you set. Run this once to see what your account can actually invoke before hardcoding an ID:
```bash
aws bedrock list-inference-profiles --region us-east-1
```
If `claude-sonnet-4-6` isn't available in your account/region, use whatever current Sonnet-class ID the console's Model catalog shows you instead — model naming shifts over time, so verify against your own account rather than copying the example above verbatim.

**One real limitation to know about, in case it matters for anything you extend later:** Anthropic's documentation states the SDK's `WebSearch` tool isn't available when running on Bedrock. Doesn't affect this project (we don't use it), but worth knowing.

---

## 3. Ingestion: turn the PDFs into a searchable chunk index

This is the "R" in RAG — retrieval. We keep it dependency-light: `pypdf` for text extraction, `rank-bm25` for keyword-ranked retrieval (no embedding model, no vector DB, nothing that needs a network call). It's a legitimate, explainable retrieval method, and you can honestly say in the interview "I used BM25 for retrieval and would swap in embeddings-based retrieval for production" — that's a normal, defensible engineering tradeoff, not a shortcut you need to hide.

`ingest.py`:
```python
"""
Chunk the public AIG PDFs into overlapping text windows and save them
as a flat JSON index. Run this once (and again whenever data/raw/ changes).
"""
import json
import re
from pathlib import Path
from pypdf import PdfReader

RAW_DIR = Path("data/raw")
INDEX_PATH = Path("data/index/chunks.json")

CHUNK_SIZE = 900       # characters
CHUNK_OVERLAP = 150


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def chunk_page(text: str, doc_id: str, page_num: int) -> list[dict]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunk_text = text[start:end]
        if chunk_text.strip():
            chunks.append({
                "id": f"{doc_id}:p{page_num}:{start}",
                "doc_id": doc_id,
                "page": page_num,
                "text": chunk_text,
            })
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def main():
    all_chunks = []
    doc_manifest = []

    pdf_paths = sorted(RAW_DIR.glob("*.pdf"))
    if not pdf_paths:
        raise SystemExit(f"No PDFs found in {RAW_DIR.resolve()} — add some first.")

    for pdf_path in pdf_paths:
        doc_id = pdf_path.stem
        reader = PdfReader(str(pdf_path))
        num_pages = len(reader.pages)
        doc_manifest.append({"doc_id": doc_id, "title": doc_id, "num_pages": num_pages})

        for page_num, page in enumerate(reader.pages, start=1):
            raw_text = page.extract_text() or ""
            text = clean_text(raw_text)
            if text:
                all_chunks.extend(chunk_page(text, doc_id, page_num))

        print(f"Ingested {pdf_path.name}: {num_pages} pages")

    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(
        {"documents": doc_manifest, "chunks": all_chunks}, indent=2
    ))
    print(f"\nWrote {len(all_chunks)} chunks from {len(doc_manifest)} documents to {INDEX_PATH}")


if __name__ == "__main__":
    main()
```

Run it:
```bash
python ingest.py
```

You should see chunk counts printed and a `data/index/chunks.json` file appear.

---

## 4. Write the MCP server (the part the JD names explicitly)

This is a **standalone MCP server** built with the `mcp` Python package's `FastMCP` — a real, from-scratch server process, not just the SDK's in-process tool shortcut. This is the artifact you can point to and say "I designed and implemented an MCP server" — it's a separate process speaking the MCP protocol over stdio, independently testable, and reusable by *any* MCP-compatible client (Claude Agent SDK, Claude Desktop, or anything else), which is the actual point of MCP as a protocol.

`mcp_server.py`:
```python
"""
AIG Research MCP Server.

A standalone MCP server (stdio transport) exposing tools for retrieving
and searching AIG's public investor documents. Run standalone for testing,
or launched as a subprocess by a Claude Agent SDK client.
"""
import json
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from rank_bm25 import BM25Okapi

INDEX_PATH = Path("data/index/chunks.json")

mcp = FastMCP("aig-research")

# --- Load the chunk index and build a BM25 retriever at startup ---
_data = json.loads(INDEX_PATH.read_text())
_documents = _data["documents"]
_chunks = _data["chunks"]


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


_tokenized_corpus = [_tokenize(c["text"]) for c in _chunks]
_bm25 = BM25Okapi(_tokenized_corpus)


@mcp.tool()
def list_documents() -> list[dict]:
    """List the AIG public documents that have been ingested and are searchable."""
    return _documents


@mcp.tool()
def search_documents(query: str, top_k: int = 5) -> list[dict]:
    """
    Search across all ingested AIG documents for passages relevant to a query.
    Returns the top matching passages with their source document, page number,
    and a relevance score, so answers can be grounded and cited.
    """
    scores = _bm25.get_scores(_tokenize(query))
    ranked = sorted(range(len(_chunks)), key=lambda i: scores[i], reverse=True)[:top_k]
    results = []
    for i in ranked:
        if scores[i] <= 0:
            continue
        chunk = _chunks[i]
        results.append({
            "doc_id": chunk["doc_id"],
            "page": chunk["page"],
            "snippet": chunk["text"],
            "score": round(float(scores[i]), 3),
        })
    return results


@mcp.tool()
def get_page_text(doc_id: str, page: int) -> str:
    """Fetch the full extracted text of a specific page from a specific document."""
    matches = [c["text"] for c in _chunks if c["doc_id"] == doc_id and c["page"] == page]
    if not matches:
        return f"No text found for {doc_id} page {page}."
    return " ".join(matches)


@mcp.tool()
def extract_financial_metrics(text: str) -> dict:
    """
    Extract candidate financial figures (dollar amounts and percentages) from a
    block of text, with surrounding context, as structured candidates for the
    agent to reason over rather than restating numbers from memory.
    """
    dollar_pattern = re.compile(r"\$[\d,]+(?:\.\d+)?\s?(?:million|billion|M|B)?", re.IGNORECASE)
    percent_pattern = re.compile(r"\d+(?:\.\d+)?%")

    candidates = []
    for match in dollar_pattern.finditer(text):
        start = max(0, match.start() - 60)
        end = min(len(text), match.end() + 60)
        candidates.append({"type": "dollar_amount", "value": match.group(), "context": text[start:end]})
    for match in percent_pattern.finditer(text):
        start = max(0, match.start() - 60)
        end = min(len(text), match.end() + 60)
        candidates.append({"type": "percentage", "value": match.group(), "context": text[start:end]})

    return {"candidate_count": len(candidates), "candidates": candidates}


if __name__ == "__main__":
    mcp.run(transport="stdio")
```

**Test the MCP server on its own before wiring it to the agent** — this is good practice and worth mentioning in the interview (you validated the server in isolation before integration):

```bash
python -c "
from mcp_server import list_documents, search_documents
print(list_documents())
print(search_documents('General Insurance underwriting income', top_k=3))
"
```

If that prints sensible results, the server logic is sound before you ever involve the agent loop.

---

## 5. Write the Claude Agent SDK client

This is the actual "Claude Agent SDK" usage the JD names. We use `ClaudeSDKClient` (stateful, multi-turn) rather than the one-shot `query()` function, since a research assistant that remembers context across follow-up questions is the more realistic and more impressive demo.

`agent_app.py`:
```python
"""
AIG GenAI Research Assistant — a Claude Agent SDK client wired to the
standalone aig-research MCP server (mcp_server.py).
"""
import asyncio
import os

from dotenv import load_dotenv
from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ResultMessage,
)

load_dotenv()

SYSTEM_PROMPT = """You are the AIG GenAI Research Assistant, an internal research
tool for analyzing AIG's public investor communications (earnings presentations,
investor day materials).

Rules:
- Always use the search_documents tool to find relevant passages before answering
  substantive questions about AIG's business, financials, or strategy.
- Ground every factual claim in a retrieved passage. Cite the source as
  (doc_id, page X) inline.
- If the retrieved passages don't support an answer, say so explicitly rather
  than filling in from general knowledge.
- Use extract_financial_metrics when a user asks about specific figures, to pull
  structured candidates rather than eyeballing numbers from raw text.
"""


def build_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={
            "aig_research": {
                "type": "stdio",
                "command": "python",
                "args": ["mcp_server.py"],
            }
        },
        allowed_tools=[
            "mcp__aig_research__list_documents",
            "mcp__aig_research__search_documents",
            "mcp__aig_research__get_page_text",
            "mcp__aig_research__extract_financial_metrics",
        ],
        permission_mode="bypassPermissions",  # all tools here are local & read-only
        # With CLAUDE_CODE_USE_BEDROCK=1 set in the environment (via .env / load_dotenv()),
        # `model` should be the Bedrock inference profile ID, not a bare Anthropic model name.
        # Leaving this unset also works — the SDK falls back to ANTHROPIC_MODEL from the
        # environment, which is what section 2's .env sets — but being explicit here means
        # this script's behavior doesn't silently change if someone edits .env later.
        model=os.environ.get("ANTHROPIC_MODEL", "us.anthropic.claude-sonnet-4-6"),
        max_turns=8,
    )


async def print_response_stream(client: ClaudeSDKClient):
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"\nAssistant: {block.text}")
                elif isinstance(block, ToolUseBlock):
                    print(f"  [tool call] {block.name}({block.input})")
        elif isinstance(message, ResultMessage):
            if message.total_cost_usd:
                print(f"  (turn cost: ${message.total_cost_usd:.4f})")


async def main():
    options = build_options()
    print("AIG GenAI Research Assistant — type a question, or 'quit' to exit.\n")

    async with ClaudeSDKClient(options=options) as client:
        while True:
            user_input = input("You: ").strip()
            if user_input.lower() in {"quit", "exit"}:
                break
            if not user_input:
                continue

            await client.query(user_input)
            await print_response_stream(client)


if __name__ == "__main__":
    asyncio.run(main())
```

Run it:
```bash
python agent_app.py
```

Try prompts like:
- "What were AIG's General Insurance underwriting results in the most recent quarter?"
- "Summarize AIG's stated strategic priorities from the investor day presentation."
- "What did AIG say about combined ratio? Cite your sources."
- Ask a follow-up without repeating context, e.g. "How does that compare to the prior quarter?" — this exercises the multi-turn state.

Watch the `[tool call]` lines print — that's your visible proof the agent is actually calling `search_documents` / `get_page_text` on the MCP server rather than hallucinating, which is exactly the auditability story from your CV (citation tracking, hallucination mitigation) carried into this new stack.

---

## 6. If something doesn't work

- **"No module named mcp_server"** when running `agent_app.py`: the subprocess is launched with `cwd` matching wherever you run `python agent_app.py` from — make sure you run it from inside `aig-genai-agent/` so the relative path `mcp_server.py` resolves. If you need to run from elsewhere, use an absolute path in the `args` list.
- **Tool never gets called / agent answers from general knowledge**: double check `allowed_tools` names exactly match `mcp__<server_name>__<tool_name>` — the server key you chose (`aig_research`) has to match what's in `allowed_tools`.
- **BM25 returns nothing**: check `data/index/chunks.json` actually has chunks (rerun `ingest.py`), and that your query shares vocabulary with the source text — BM25 is literal keyword matching, not semantic.
- **Permission prompt hangs the script**: confirm `permission_mode="bypassPermissions"` is set in options.

---

## 7. Interview talking points — mapping this build to the JD

| JD language | What you built |
|---|---|
| "Experience implementing Agentic AI frameworks... Claude Agent SDK" | Built a stateful multi-turn agent with `ClaudeSDKClient`, custom tool wiring via `allowed_tools`, and a real system-prompt-driven grounding policy. |
| "Model Context Protocol (MCP) server design and implementation" | Designed and implemented a standalone `FastMCP` server (not the SDK's in-process shortcut) exposing 4 tools over stdio — independently testable, reusable by any MCP client. |
| "RAG (Retrieval-Augmented Generation)" | Built the retrieval half yourself (chunking, BM25 indexing) rather than calling a managed RAG service — you understand what's actually happening under the hood, and can speak to the embeddings-vs-BM25 tradeoff. |
| "Manage data quality to reduce bias" | The `extract_financial_metrics` tool returns structured *candidates* rather than letting the model free-associate numbers — same pattern as your SHAP/structured-extraction work at Jazz, applied here. |
| "Communicate research findings... translating complex concepts into actionable insights" | You can walk through this build end-to-end, in plain language, to a non-technical interviewer — chunking → retrieval → grounded generation → citation. |

One honest gap to name if asked: this demo uses keyword (BM25) retrieval, not embeddings/vector search or reinforcement learning or knowledge graphs — all three are called out in the JD and aren't in your CV either. Naming the gap and describing how you'd extend it (embeddings for semantic recall, a knowledge graph over entities like business segments/subsidiaries, RL for agent policy refinement) reads as more credible than pretending the demo covers everything.

---

## Appendix: optional AWS extension (design-only, not built here)

If you want to speak to AWS specifically (the JD doesn't actually require it, but your CV's AWS Bedrock/SageMaker experience is worth connecting), you could describe — without building it — how you'd extend this:

- Store `data/raw/` PDFs and the `chunks.json` index in **S3** instead of local disk, so the MCP server reads from a shared bucket rather than a laptop filesystem — makes it usable by a team, not just you.
- Swap BM25 for embeddings computed via **Bedrock Titan Embeddings** (or any embedding model), stored alongside the chunks, with cosine-similarity retrieval — this is the "production RAG" upgrade path.
- If AIG standardizes on Bedrock-hosted Claude models for data-residency/compliance reasons (common in regulated industries), the model call in the agent would route through Bedrock instead of the direct Anthropic API — verify current SDK support for this before claiming it's a solved problem; treat it as a design point, not a demoed feature.

This section is intentionally description-only — nothing here needs AWS credentials or execution to be a credible thing to say in an interview, since you're describing an extension path, not overstating what you built in three hours.
