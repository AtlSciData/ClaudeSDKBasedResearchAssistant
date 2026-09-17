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
        #model=os.environ.get("ANTHROPIC_MODEL", "us.anthropic.claude-sonnet-4-6"),
        model=os.environ.get("ANTHROPIC_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
        max_turns=8,
    )

async def print_response_stream(client: ClaudeSDKClient):
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            print(f"  (model: {message.model})")
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