"""LangChain Deep Agents as a research harness over the engine's MCP tools.

The design's fallback harness (and the stage 11 comparison with AI-Q). Deep Agents does the
planning (write_todos), the research subagents (task) and the tool loop; this module only wires
in the recipe's LLM, the engine's MCP tools, call limits, and a Sources list for cited IDs.
No structured output is requested, so no request forces a tool call (that would push the whole
batch off Blackhole's fast decode path).
"""

import re
import time
from collections import Counter
from dataclasses import dataclass, field

from deepagents import create_deep_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from mcp import Client

from ttdr.recipe import Recipe

RESEARCH_TOOLS = ("search", "fetch_page", "get_evidence")
EVIDENCE_ID = re.compile(r"\[((?:W[0-9a-f]{8}(?:\.\d+)?)|(?:[^\[\]\s]+#p\d+\.\d+))\]")

INSTRUCTIONS = """You write cited research reports using the research engine's tools.
Every factual sentence in your final report must end with the evidence IDs it rests on, in
square brackets, copied from the researchers' notes. A sentence without an ID is not allowed.

1. Plan: break the request into 2-{subs} sub-questions with write_todos. Note for each whether it
   needs private documents (search sources="corpus", e.g. company 10-K filings), the web
   (sources="web") or both ("all").
2. Delegate each sub-question to the researcher subagent with the task tool. Run them in parallel.
3. Write the final answer yourself from the researchers' notes: answer the request directly
   first, then supporting detail. Every factual sentence ends with the evidence IDs it rests on,
   in square brackets, e.g. [3M_2018_10K#p59.0] or [W1a2b3c4d.2]. Only cite IDs from the notes.
   If something wasn't found, say so ("I don't know" is fine).
4. If the verify tool is available, check the sentences whose figures matter most and fix or
   drop any that come back contradicted or not_enough_info.
Reply with the final report only; a Sources list is added for you."""

RESEARCHER = """You research one sub-question. Use search with short keyword queries and
fetch_page (with focus set to the sub-question) on the most promising URLs. Every passage has an
evidence ID in square brackets. Never repeat a search. When you have enough, or after about
{calls} tool calls, stop and reply with research notes: short bullet facts, each ending with the
evidence IDs it comes from. Only cite IDs the tools returned. Say what you could not find."""


def _text(result) -> str:
    return "".join(c.text for c in result.content if getattr(c, "type", "") == "text")


def mcp_tools(client: Client, tools) -> list[StructuredTool]:
    """The engine's MCP tools as LangChain tools (langchain-mcp-adapters lacks MCP 2.x support)."""

    def wrap(t) -> StructuredTool:
        async def call(**kwargs) -> str:
            return _text(await client.call_tool(t.name, kwargs))

        return StructuredTool(
            name=t.name,
            description=t.description or "",
            args_schema=t.input_schema,
            coroutine=call,
        )

    return [wrap(t) for t in tools]


def chat_model(recipe: Recipe, thinking: bool, max_tokens: int) -> ChatOpenAI:
    m = recipe.llm
    return ChatOpenAI(
        model=m.model,
        base_url=m.url,
        api_key="none",
        max_tokens=max_tokens,
        timeout=1800,
        **m.request_settings(thinking),
    )


@dataclass
class Result:
    report: str
    stats: Counter = field(default_factory=Counter)


@dataclass
class DeepAgentsHarness:
    recipe: Recipe
    max_subquestions: int = 5
    max_tool_calls: int = 12
    """Per researcher run and tool; past it the tool errors and the researcher wraps up."""
    max_model_calls: int = 60
    thinking: bool = False

    def agent(self, tools: list[StructuredTool]):
        research = [t for t in tools if t.name in RESEARCH_TOOLS]
        researcher = {
            "name": "researcher",
            "description": "Researches one sub-question with the engine's search tools and "
            "returns bullet notes with evidence IDs.",
            "system_prompt": RESEARCHER.format(calls=self.max_tool_calls),
            "tools": research,
            "middleware": [
                ToolCallLimitMiddleware(tool_name=t.name, run_limit=self.max_tool_calls)
                for t in research
            ],
        }
        return create_deep_agent(
            model=chat_model(self.recipe, self.thinking, 8192),
            tools=[t for t in tools if t.name == "verify"],
            system_prompt=INSTRUCTIONS.format(subs=self.max_subquestions),
            subagents=[researcher],
            middleware=[ModelCallLimitMiddleware(run_limit=self.max_model_calls)],
        )

    async def run(self, question: str, log=lambda _: None) -> Result:
        stats: Counter = Counter()
        start = time.perf_counter()
        async with Client(self.recipe.services.mcp) as client:
            agent = self.agent(mcp_tools(client, (await client.list_tools()).tools))
            final = None
            async for namespace, update in agent.astream(
                {"messages": [{"role": "user", "content": question}]},
                stream_mode="updates",
                subgraphs=True,
                config={"recursion_limit": 500},
            ):
                for node in update.values():
                    for msg in node.get("messages", []) if isinstance(node, dict) else []:
                        _count(msg, stats, log)
                        if not namespace and _is_answer(msg):
                            final = msg
            report = _content(final)
            report += await _sources(client, report)
        stats["seconds"] = round(time.perf_counter() - start)
        return Result(report, stats)


def _is_answer(msg) -> bool:
    return getattr(msg, "type", "") == "ai" and not getattr(msg, "tool_calls", None)


def _content(msg) -> str:
    if msg is None:
        return ""
    c = msg.content
    return c if isinstance(c, str) else "".join(p.get("text", "") for p in c if isinstance(p, dict))


def _count(msg, stats: Counter, log) -> None:
    if getattr(msg, "type", "") != "ai":
        return
    stats["llm calls"] += 1
    usage = getattr(msg, "usage_metadata", None) or {}
    stats["input tokens"] += usage.get("input_tokens", 0)
    stats["output tokens"] += usage.get("output_tokens", 0)
    for call in getattr(msg, "tool_calls", None) or []:
        stats[call["name"]] += 1
        a = call.get("args", {})
        log(
            f"  {call['name']}: {str(a.get('query') or a.get('url') or a.get('description') or '')[:90]}"
        )


async def _sources(client: Client, report: str) -> str:
    ids = sorted(set(EVIDENCE_ID.findall(report)))
    if not ids:
        return ""
    found = _text(await client.call_tool("get_evidence", {"ids": ids}))
    lines = [
        f"- [{m[1]}] {m[2].strip()} - {m[3].strip()}"
        for m in re.finditer(r"^\[([^\]]+)\] (.*)\nURL: (\S+)", found, re.MULTILINE)
    ]
    return "\n\n## Sources\n" + "\n".join(lines) if lines else ""
