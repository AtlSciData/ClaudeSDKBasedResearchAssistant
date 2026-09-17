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