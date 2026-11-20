"""Genuine stdio MCP client used by the Researcher role.

The server is started as a subprocess and spoken to over the MCP protocol; no
corpus file is read directly here or anywhere in the Researcher path.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "mcp_server.py"
CORPUS_URI = "corpus://market/corpus.csv"
GLOSSARY_URI = "corpus://market/glossary.md"


def _run(coro):
    """Run a coroutine from sync LangGraph nodes, even under an active loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _items_to_snippets(content: list[Any]) -> list[dict]:
    """Decode FastMCP TextContent payloads that wrap one or more snippet dicts."""
    snippets: list[dict] = []
    for item in content:
        raw = getattr(item, "text", None)
        if raw is None and isinstance(item, dict):
            raw = item.get("text")
        if not isinstance(raw, str):
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        entries = parsed if isinstance(parsed, list) else [parsed]
        for entry in entries:
            if (isinstance(entry, dict) and isinstance(entry.get("text"), str)
                    and isinstance(entry.get("source"), str)):
                snippets.append({"text": entry["text"].strip(),
                                 "source": entry["source"].strip()})
    return snippets


def _resource_text(result: Any) -> str:
    return "\n".join(
        item.text for item in getattr(result, "contents", [])
        if isinstance(getattr(item, "text", None), str)
    )


async def _search_async(queries: list[str], limit: int) -> dict:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = [tool.name for tool in (await session.list_tools()).tools]
            if "search_corpus" not in tools:
                raise RuntimeError("MCP server does not expose search_corpus")
            resources = [str(resource.uri) for resource in (await session.list_resources()).resources]

            candidates: list[dict] = []
            calls = 0
            for query in queries:
                result = await session.call_tool("search_corpus", {"query": query, "limit": limit})
                calls += 1
                if result.isError:
                    raise RuntimeError("MCP search_corpus returned an error")
                candidates.extend(_items_to_snippets(result.content))

            glossary = ""
            if GLOSSARY_URI in resources:
                glossary = _resource_text(await session.read_resource(GLOSSARY_URI))

            return {
                "candidates": candidates,
                "tool_calls": calls,
                "tools": tools,
                "resources": resources,
                "glossary": glossary,
                "queries": queries,
            }


def search_corpus(queries: list[str], limit: int = 12) -> dict:
    """Open one stdio session, call search_corpus for each query, read the glossary."""
    return _run(asyncio.wait_for(_search_async(queries, limit), timeout=180))
