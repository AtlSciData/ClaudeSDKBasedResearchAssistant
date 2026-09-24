import asyncio
import os
from dotenv import load_dotenv
from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
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

async def ask(session_id: str, message: str) -> dict:
    client = await get_or_create_client(session_id)
    await client.query(message)
    reply_parts = []
    tool_calls = []
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    reply_parts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_calls.append({"name": block.name, "input": block.input})
    return {"reply": "\n".join(reply_parts), "tool_calls": tool_calls}