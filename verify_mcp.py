"""Verify the supplied MCP server over genuine stdio and the Researcher path.

Run: python verify_mcp.py
Writes evidence/mcp_verification.json; exits non-zero on any mismatch.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import mcp_client
import team

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "evidence" / "mcp_verification.json"


async def check() -> dict:
    params = StdioServerParameters(command=sys.executable, args=[str(mcp_client.SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resources = [str(resource.uri) for resource in (await session.list_resources()).resources]
            tools = [tool.name for tool in (await session.list_tools()).tools]
            assert mcp_client.CORPUS_URI in resources, resources
            assert mcp_client.GLOSSARY_URI in resources, resources
            assert "search_corpus" in tools, tools

            corpus = await session.read_resource(mcp_client.CORPUS_URI)
            glossary = await session.read_resource(mcp_client.GLOSSARY_URI)
            assert corpus.contents and glossary.contents

            result = await session.call_tool("search_corpus", {"query": "solar", "limit": 3})
            assert not result.isError, "search_corpus returned an error"
            snippets = mcp_client._items_to_snippets(result.content)
            assert snippets, "search_corpus returned no snippets"
            assert all(snippet["source"].startswith("P") for snippet in snippets)

    # The Researcher must reach the same server through the same client module.
    state = team._initial_state("Assess rooftop solar installation trends in Northstar.", "T01")
    command = team.researcher(state)
    researcher_update = command.update
    assert command.goto == "supervisor"
    assert researcher_update["mcp_calls"] >= 1
    evidence = researcher_update["mcp_evidence"][-1]
    assert evidence["transport"] == "stdio"
    assert "search_corpus" in evidence["tools"]
    assert mcp_client.CORPUS_URI in evidence["resources"]

    return {
        "server": str(mcp_client.SERVER),
        "transport": "stdio",
        "resources": resources,
        "tools": tools,
        "resource_reads": {"corpus_items": len(corpus.contents),
                           "glossary_items": len(glossary.contents)},
        "sample_search": {"query": "solar", "returned": len(snippets),
                          "sources": [snippet["source"] for snippet in snippets]},
        "researcher": {
            "goto": command.goto,
            "mcp_calls": researcher_update["mcp_calls"],
            "snippets": [snippet["source"] for snippet in researcher_update["snippets"]],
            "transport": evidence["transport"],
        },
        "no_direct_corpus_access": {
            "team.researcher": not any(
                marker in inspect.getsource(team.researcher)
                for marker in ("corpus.csv", "market_corpus", "open(", "csv.")),
            "mcp_client": not any(
                marker in inspect.getsource(mcp_client)
                for marker in ("data/corpus.csv", "DictReader", "read_text(")),
        },
    }


def main() -> int:
    evidence = asyncio.run(check())
    assert all(evidence["no_direct_corpus_access"].values()), evidence["no_direct_corpus_access"]
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    print(f"\nMCP verification passed; wrote {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
