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
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from mcp import Client

from ttdr.recipe import Recipe

RESEARCH_TOOLS = ("search", "fetch_page", "get_evidence")
MAIN_TOOLS = ("write_todos", "task", "verify")
EVIDENCE_ID = re.compile(r"\[((?:W[0-9a-f]{8}(?:\.\d+)?)|(?:[^\[\]\s]+#p\d+\.\d+))\]")
PASSAGE = re.compile(
    r"^\[([^\]\s]+)\] (.*)\n(?:URL: .*\n)?((?:(?!^\[[^\]\s]+\] ).*\n?)*)", re.MULTILINE
)
FIGURE = re.compile(r"\d[\d,.]*\d|\b[A-Z][a-z]{3,}\b")
CITATION = re.compile(r" ?\[([^\[\]]*(?:#|\bW[0-9a-f]{8})[^\[\]]*)\]")

INSTRUCTIONS = """You write cited research reports using the research engine's tools.
Every factual sentence in your final report must end with the evidence IDs it rests on, in
square brackets, copied character for character from the researchers' notes. Evidence IDs are
issued by the tools: never make one up, shorten it, give a range, or build one from a source's
name. State only facts the notes support; don't add figures, dates or names from memory. If no
note supports a sentence, leave it out.

You have no search tools yourself: the researcher subagent does (web search, the private document
corpus and page fetching). Always delegate research to it before answering, even for simple
questions.

1. Plan: break the request into 2-{subs} sub-questions with write_todos. Note for each whether it
   needs private documents (search sources="corpus", e.g. company 10-K filings), the web
   (sources="web") or both ("all").
2. Delegate each sub-question to the researcher subagent with the task tool. Run them in parallel.
   Ask each researcher for bullet facts that end with evidence IDs.
3. Write the final answer yourself from the researchers' notes: answer the request directly
   first, then supporting detail. Every factual sentence ends with the evidence IDs it rests on,
   in square brackets, exactly as the notes print them (web passages look like [W1a2b3c4d.2],
   filings like [3M_2018_10K#p59.0]). If something wasn't found, say so ("I don't know" is fine).
   Always write the report from what the notes do cover; never reply with a request for other
   tools or sources.
4. If the verify tool is available, check the sentences whose figures matter most and fix or
   drop any that come back contradicted or not_enough_info.
Reply with the final report only; a Sources list is added for you."""

RESEARCHER = """You research one sub-question. Use search with short keyword queries and
fetch_page (with focus set to the sub-question) on the most promising URLs. You have web search:
use sources="web" for anything outside the private filings, and rephrase if a search finds
nothing useful. Every passage has an
evidence ID in square brackets. Never repeat a search. When you have enough, or after about
{calls} tool calls, stop and reply with research notes: short bullet facts, each ending with the
evidence IDs it comes from, e.g.
- 3M's 2018 capital expenditure was $1,577 million [3M_2018_10K#p59.2]
Copy IDs exactly as the tools print them; never make one up. Say what you could not find."""

FIX_CITATIONS = """Some citations in this report are not real evidence IDs: {invented}

For every sentence that cites one of them, replace it with the real ID below whose passage
supports the sentence, or delete the sentence if none does. Change nothing else and reply with
the whole report.

Real evidence IDs from the research:
{listing}"""


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


class OnlyTools(AgentMiddleware):
    """Hide Deep Agents' built-in file tools: researchers used `grep` as if it searched the corpus."""

    def __init__(self, names):
        super().__init__()
        self.names = set(names)

    def _keep(self, request):
        tools = [t for t in request.tools if getattr(t, "name", None) in self.names]
        return request.override(tools=tools)

    def wrap_model_call(self, request, handler):
        return handler(self._keep(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._keep(request))


class EvidenceNotes(AgentMiddleware):
    """Researchers often drop evidence IDs from their notes; append the passages that back them.

    Ranks the passages the researcher's tools returned by how many of the notes' figures and
    names they contain, so the writer has real IDs to cite.
    """

    def __init__(self, limit: int = 8):
        super().__init__()
        self.limit = limit

    def after_agent(self, state, runtime):
        messages = state["messages"]
        reply = messages[-1]
        notes = _content(reply)
        if EVIDENCE_ID.search(notes):
            return None
        figures = set(FIGURE.findall(notes))
        seen: dict[str, tuple[int, str]] = {}
        for msg in messages:
            if getattr(msg, "type", "") != "tool":
                continue
            for eid, title, text in PASSAGE.findall(_content(msg)):
                score = sum(f in text for f in figures)
                if score and score >= seen.get(eid, (0, ""))[0]:
                    seen[eid] = (score, f"{title.strip()}: {' '.join(text.split())[:200]}")
        if not seen:
            return None
        best = sorted(seen.items(), key=lambda kv: -kv[1][0])[: self.limit]
        listing = "\n".join(f"- [{eid}] {line}" for eid, (_, line) in best)
        reply = reply.model_copy(
            update={"content": f"{notes}\n\nEvidence for these notes (cite these IDs):\n{listing}"}
        )
        return {"messages": [reply]}


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
                *(
                    ToolCallLimitMiddleware(tool_name=t.name, run_limit=self.max_tool_calls)
                    for t in research
                ),
                EvidenceNotes(),
                OnlyTools(RESEARCH_TOOLS),
            ],
        }
        return create_deep_agent(
            model=chat_model(self.recipe, self.thinking, 8192),
            tools=[t for t in tools if t.name == "verify"],
            system_prompt=INSTRUCTIONS.format(subs=self.max_subquestions),
            subagents=[researcher],
            middleware=[
                ModelCallLimitMiddleware(run_limit=self.max_model_calls),
                OnlyTools(MAIN_TOOLS),
            ],
        )

    async def run(self, question: str, log=lambda _: None) -> Result:
        stats: Counter = Counter()
        start = time.perf_counter()
        async with Client(self.recipe.services.mcp) as client:
            agent = self.agent(mcp_tools(client, (await client.list_tools()).tools))
            final, seen = None, {}
            async for namespace, update in agent.astream(
                {"messages": [{"role": "user", "content": question}]},
                stream_mode="updates",
                subgraphs=True,
                config={"recursion_limit": 500},
            ):
                for node in update.values():
                    for msg in node.get("messages", []) if isinstance(node, dict) else []:
                        _count(msg, stats, log)
                        _collect(msg, seen)
                        if not namespace and _is_answer(msg):
                            final = msg
            report = _clean(_content(final))
            report = await self.check_citations(client, report, seen, stats)
            report += await _sources(client, report)
        stats["seconds"] = round(time.perf_counter() - start)
        return Result(report, stats)

    async def check_citations(self, client: Client, report: str, seen: dict, stats: Counter) -> str:
        """Every cited ID must exist in the evidence store. Invented ones get one rewrite with the
        real IDs the research returned; any left after it are removed."""
        known = await _known(client, citations(report))
        invented = citations(report) - known
        stats["invented ids"] = len(invented)
        if invented and seen:
            listing = "\n".join(f"- [{eid}] {line}" for eid, line in list(seen.items())[:200])
            prompt = FIX_CITATIONS.format(invented=", ".join(sorted(invented)), listing=listing)
            fixed = await chat_model(self.recipe, False, 8192).ainvoke(
                [{"role": "system", "content": prompt}, {"role": "user", "content": report}]
            )
            report = _clean(_content(fixed)) or report
            known = await _known(client, citations(report))
            stats["invented after rewrite"] = len(citations(report) - known)
        stats["cited ids"] = len(known)
        return strip_citations(report, known)


def citations(report: str) -> set[str]:
    """Every ID-like token in the report's citation brackets, real or not."""
    return {t for group in CITATION.findall(report) for t in _tokens(group)}


def strip_citations(report: str, keep: set[str]) -> str:
    """Remove cited IDs not in KEEP (and brackets left empty)."""

    def sub(m: re.Match) -> str:
        kept = [t for t in _tokens(m.group(1)) if t in keep]
        return f" [{', '.join(kept)}]" if kept else ""

    return CITATION.sub(sub, report)


def _tokens(group: str) -> list[str]:
    return [t.strip() for t in re.split(r"[;,]", group) if t.strip()]


async def _known(client: Client, ids: set[str]) -> set[str]:
    if not ids:
        return set()
    found = _text(await client.call_tool("get_evidence", {"ids": sorted(ids)}))
    return set(re.findall(r"^\[([^\]]+)\] ", found, re.MULTILINE)) & ids


def _collect(msg, seen: dict) -> None:
    if getattr(msg, "type", "") == "tool":
        for eid, title, text in PASSAGE.findall(_content(msg)):
            seen.setdefault(eid, f"{title.strip()}: {' '.join(text.split())[:150]}")


def _clean(report: str) -> str:
    return _without_sources(_without_preamble(report))


def _without_preamble(report: str) -> str:
    """Drop the "Let me write the report now" lines before the report's first heading."""
    m = re.search(r"^# ", report, re.MULTILINE)
    return report[m.start() :] if m else report


def _without_sources(report: str) -> str:
    """Drop the model's own Sources list; the one built from the evidence store replaces it."""
    return re.split(r"^#+\s*(?:Sources|References)\b", report, flags=re.MULTILINE | re.IGNORECASE)[
        0
    ].rstrip()


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
