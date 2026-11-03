"""LangChain Deep Agents as a research harness over the engine's MCP tools.

The design's fallback harness (and the stage 11 comparison with AI-Q). Deep Agents does the
planning (write_todos), the research subagents (task) and the tool loop; this module only wires
in the recipe's LLM, the engine's MCP tools, call limits, and a Sources list for cited IDs.
No structured output is requested, so no request forces a tool call (that would push the whole
batch off Blackhole's fast decode path).
"""

import asyncio
import re
import time
from collections import Counter
from collections.abc import Callable
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

from ttdr import writer
from ttdr.recipe import Recipe

RESEARCH_TOOLS = ("search", "fetch_page", "read_page", "get_evidence")
DOC_TOOLS = ("find_in_document", "open_pages")
MAIN_TOOLS = ("write_todos", "task", "verify")
EVIDENCE_ID = re.compile(r"\[((?:W[0-9a-f]{8}(?:\.\d+)?)|(?:[^\[\]\s]+#p\d+\.\d+))\]")
PASSAGE = re.compile(
    r"^\[([^\]\s]+)\] (.*)\n(?:URL: .*\n)?((?:(?!^\[[^\]\s]+\] ).*\n?)*)", re.MULTILINE
)
FIGURE = re.compile(r"\d[\d,.]*\d|\b[A-Z][a-z]{3,}\b")
MIXED_WEB_ID = re.compile(r"\b(W[0-9a-f]{8})#p(\d+)\b")  # web ID in filing style: Wab12cd34#p5
HEADING = re.compile(r"\n(?=## )")
# Web IDs are W + 8 hex digits; invented ones also come as W + 8 letters and digits (W5e6f7g8h).
CITATION = re.compile(r" ?\[([^\[\]]*(?:#|\bW(?=[a-z]*\d)[0-9a-z]{8}\b)[^\[\]]*)\]")

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
   needs the private document collections listed below (search sources="corpus"), the web
   (sources="web") or both ("all"). Prefer the collections for anything they cover.
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
Reply with the final report only; a Sources list is added for you.

Private document collections:
{corpora}"""

RESEARCHER = """You research one sub-question. Use search with short keyword queries and
fetch_page (with focus set to the sub-question) on the most promising URLs. You have web search:
search sources="corpus" first for anything the private collections cover:
{corpora}
Use sources="web" for anything outside them, and rephrase if a search finds
nothing useful. When a document passage mentions a figure, chart or table, or lacks the number
you need, call read_page with its evidence ID to see the page itself.{doc_tools} Every passage
has an evidence ID in square brackets. Never repeat a search. When you have enough, or after about
{calls} tool calls, stop and reply with research notes: short bullet facts, each ending with the
evidence IDs it comes from, e.g.
- 3M's 2018 capital expenditure was $1,577 million [3M_2018_10K#p59.2]
Copy IDs exactly as the tools print them; never make one up. Say what you could not find."""

DOC_TOOLS_HINT = """ Inside a document you
found, find_in_document looks up exact words (a line item, a defined term, a note number) and
open_pages reads the whole page around a passage and the pages after it, for tables and notes
that continue."""

SCOPE = {"web": "web search", "corpus": "the private document collections"}
SCOPE_NOTE = """

Search is limited to {scope} for this request: search returns only that, whatever sources you
pass."""

FIX_CITATIONS = """Some citations in this report section are not real evidence IDs: {invented}

For every sentence that cites one of them, replace it with the real ID below whose passage
supports the sentence, or delete the sentence if none does. Change nothing else and reply with
the whole section.

Real evidence IDs from the research:
{listing}"""


def _text(result) -> str:
    return "".join(c.text for c in result.content if getattr(c, "type", "") == "text")


@dataclass
class Engine:
    """The engine's MCP tools, one short connection per call (the server keeps no session).

    A call that can't reach the engine is retried with backoff, so a restart of the engine
    container (about 10 s) doesn't fail every research run in flight.
    """

    url: str
    retries: int = 5

    async def _with_client(self, fn):
        for attempt in range(self.retries + 1):
            try:
                async with Client(self.url) as c:
                    return await fn(c)
            except Exception:  # connection loss surfaces as several exception types
                if attempt == self.retries:
                    raise
                await asyncio.sleep(2**attempt)
        raise AssertionError

    async def list_tools(self):
        return await self._with_client(lambda c: _tools(c))

    async def call_tool(self, name: str, args: dict):
        return await self._with_client(lambda c: c.call_tool(name, args))


async def _tools(c: Client):
    return (await c.list_tools()).tools


def mcp_tools(client, tools, fixed: dict[str, dict] | None = None) -> list[StructuredTool]:
    """The engine's MCP tools as LangChain tools (langchain-mcp-adapters lacks MCP 2.x support).

    FIXED maps a tool name to arguments that override whatever the model passes."""
    fixed = fixed or {}

    def wrap(t) -> StructuredTool:
        async def call(**kwargs) -> str:
            return _text(await client.call_tool(t.name, {**kwargs, **fixed.get(t.name, {})}))

        return StructuredTool(
            name=t.name,
            description=t.description or "",
            args_schema=t.input_schema,
            coroutine=call,
        )

    return [wrap(t) for t in tools]


def chat_model(
    recipe: Recipe, thinking: bool, max_tokens: int, context_tokens: int | None = None
) -> ChatOpenAI:
    """context_tokens is the budget Deep Agents sizes its summarization from (85% triggers it,
    the last 10% is kept); unset, it summarizes only past 170k tokens."""
    m = recipe.llm
    return ChatOpenAI(
        model=m.model,
        base_url=m.url,
        api_key="none",
        max_tokens=max_tokens,
        timeout=1800,
        profile={"max_input_tokens": context_tokens} if context_tokens else None,
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
    seen: list[str] = field(default_factory=list)
    """Evidence IDs the researchers' tools returned, in order."""


@dataclass
class DeepAgentsHarness:
    recipe: Recipe
    max_subquestions: int = 5
    max_tool_calls: int = 12
    """Per researcher run and tool; past it the tool errors and the researcher wraps up."""
    max_model_calls: int = 60
    thinking: bool = False
    doc_tools: bool = True
    """In-document tools for researchers: exact-term find and reading neighbouring pages."""
    context_tokens: int | None = None
    """Condense agent histories past 85% of this many tokens (see chat_model)."""
    sections: bool = True
    """Rewrite report requests outline-first, one section at a time (ttdr.writer)."""
    quick: tuple[int, int] | None = None
    """(sub-questions, tool calls) for short-answer requests; None plans them like reports."""
    sources: str = "all"
    """Search scope: "web" or "corpus" pins search's sources argument; "all" lets agents choose."""

    def agent(self, tools: list[StructuredTool], subs: int | None = None, calls: int | None = None):
        subs, calls = subs or self.max_subquestions, calls or self.max_tool_calls
        allowed = RESEARCH_TOOLS + (DOC_TOOLS if self.doc_tools else ())
        research = [t for t in tools if t.name in allowed]
        scope = SCOPE_NOTE.format(scope=SCOPE[self.sources]) if self.sources in SCOPE else ""
        researcher = {
            "name": "researcher",
            "description": "Researches one sub-question with the engine's search tools and "
            "returns bullet notes with evidence IDs.",
            "system_prompt": RESEARCHER.format(
                calls=calls,
                corpora=self.recipe.corpus_catalog(),
                doc_tools=DOC_TOOLS_HINT if self.doc_tools else "",
            )
            + scope,
            "tools": research,
            "middleware": [
                *(ToolCallLimitMiddleware(tool_name=t.name, run_limit=calls) for t in research),
                # Past its tool limits a researcher can retry the blocked call forever (DRB: 100+
                # model calls on one ~98k-token context); end it, EvidenceNotes keeps its passages.
                ModelCallLimitMiddleware(run_limit=3 * calls, exit_behavior="end"),
                EvidenceNotes(),
                OnlyTools(allowed),
            ],
        }
        # The planner sometimes picks Deep Agents' built-in general-purpose subagent, which has
        # no search tools and reports "no web search"; under that name it gets the researcher's.
        general = {**researcher, "name": "general-purpose", "description": "Same as researcher."}
        return create_deep_agent(
            model=chat_model(self.recipe, self.thinking, 8192, self.context_tokens),
            tools=[t for t in tools if t.name == "verify"],
            system_prompt=INSTRUCTIONS.format(subs=subs, corpora=self.recipe.corpus_catalog())
            + scope,
            subagents=[researcher, general],
            middleware=[
                ModelCallLimitMiddleware(run_limit=self.max_model_calls),
                OnlyTools(MAIN_TOOLS),
            ],
        )

    async def run(
        self, question: str, log=lambda _: None, events: Callable[[str, dict], None] | None = None
    ) -> Result:
        """EVENTS, if given, hears the run as it happens (see Progress)."""
        stats: Counter = Counter()
        progress = Progress(events or (lambda *_: None))
        start = time.perf_counter()
        client = Engine(self.recipe.services.mcp)
        writer_model = chat_model(self.recipe, self.thinking, 8192)
        report_kind = not (self.sections or self.quick) or await writer.is_report(
            writer_model, question
        )
        stats["report request"] = int(report_kind)
        budget = self.quick if self.quick and not report_kind else ()
        subs, calls = budget or (self.max_subquestions, self.max_tool_calls)
        progress.emit("plan", {"report": report_kind, "subquestions": subs, "tool_calls": calls})
        fixed = {"search": {"sources": self.sources}} if self.sources in SCOPE else None
        agent = self.agent(mcp_tools(client, await client.list_tools(), fixed), *budget)
        final, seen, passages, notes = None, {}, {}, []
        async for namespace, mode, update in agent.astream(
            {"messages": [{"role": "user", "content": question}]},
            stream_mode=["updates", "tasks"],
            subgraphs=True,
            config={"recursion_limit": 500},
        ):
            if mode == "tasks":
                progress.task(namespace, update)
                continue
            for node in update.values():
                for msg in node.get("messages", []) if isinstance(node, dict) else []:
                    progress.message(namespace, msg)
                    _count(msg, stats, log)
                    _collect(msg, seen)
                    _collect(msg, passages, 800)
                    if not namespace and getattr(msg, "type", "") == "tool" and msg.name == "task":
                        notes.append(_content(msg))
                    if not namespace and _is_answer(msg):
                        final = msg
        report = _clean(_content(final))
        if self.sections and report_kind and notes:
            report = _clean(await writer.write(writer_model, question, notes, passages) or report)
        report = await self.check_citations(client, report, seen, stats)
        report += await _sources(client, report)
        stats["seconds"] = round(time.perf_counter() - start)
        progress.emit("report", {"report": report})
        return Result(report, stats, list(seen))

    async def check_citations(self, client, report: str, seen: dict, stats: Counter) -> str:
        """Every cited ID must exist in the evidence store. Each section citing invented ones gets
        one rewrite with the real IDs the research returned (kept only if it doesn't lose real
        ones); any left are removed. Per section, so a rewrite can't truncate or drop the report."""
        report = MIXED_WEB_ID.sub(r"\1.\2", report)
        known = await _known(client, citations(report))
        stats["invented ids"] = len(citations(report) - known)
        if stats["invented ids"] and seen:
            listing = "\n".join(f"- [{eid}] {line}" for eid, line in list(seen.items())[:200])
            fixed = await asyncio.gather(
                *(self._fix_section(s, known, listing) for s in HEADING.split(report))
            )
            report = "\n\n".join(s.strip() for s in fixed)
            known = await _known(client, citations(report))
            stats["invented after rewrite"] = len(citations(report) - known)
        stats["cited ids"] = len(known)
        return strip_citations(report, known)

    async def _fix_section(self, section: str, known: set[str], listing: str) -> str:
        invented = citations(section) - known
        if not invented:
            return section
        prompt = FIX_CITATIONS.format(invented=", ".join(sorted(invented)), listing=listing)
        fixed = await chat_model(self.recipe, False, 8192).ainvoke(
            [{"role": "system", "content": prompt}, {"role": "user", "content": section}]
        )
        rewrite = _clean(_content(fixed))
        lost = (citations(section) & known) - citations(rewrite)
        if lost or len(rewrite) < len(section) // 2:  # drops real citations or most of the text
            return section
        return rewrite


@dataclass
class Progress:
    """Turns the agent's stream into run events, emitted as (kind, data):

    - plan {report, subquestions, tool_calls}: the request's kind and the plan budget
    - todos {todos}: the planner's write_todos list
    - researcher.start {id, subagent, question} / researcher.end {id, notes}: one task call
    - tool.start {id, agent, name, input} / tool.end {id, agent, name, output}: agent is the
      researcher's task call ID (None for the planner's own tools); output is the full text
    - report {report}: the final report, Sources list included
    """

    emit: Callable[[str, dict], None]
    agents: dict[str, str] = field(default_factory=dict)
    """Subgraph namespace (tools:<task id>) -> the task tool call that runs it."""

    def task(self, namespace: tuple, task: dict) -> None:
        if namespace or task.get("name") != "tools" or not isinstance(task.get("input"), list):
            return
        calls = [c for c in task["input"] if isinstance(c, dict) and c.get("name") == "task"]
        if calls:
            self.agents[f"tools:{task['id']}"] = calls[0]["id"]

    def message(self, namespace: tuple, msg) -> None:
        agent = self.agents.get(namespace[0]) if namespace else None
        kind = getattr(msg, "type", "")
        if kind == "ai":
            for call in getattr(msg, "tool_calls", None) or []:
                name, args = call["name"], call.get("args", {})
                if name == "write_todos" and not namespace:
                    self.emit("todos", {"todos": args.get("todos", [])})
                elif name == "task" and not namespace:
                    self.emit(
                        "researcher.start",
                        {
                            "id": call["id"],
                            "subagent": args.get("subagent_type", "researcher"),
                            "question": args.get("description", ""),
                        },
                    )
                else:
                    tool = {"id": call["id"], "agent": agent, "name": name}
                    self.emit("tool.start", {**tool, "input": args})
        elif kind == "tool" and msg.name == "task" and not namespace:
            self.emit("researcher.end", {"id": msg.tool_call_id, "notes": _content(msg)})
        elif kind == "tool" and msg.name != "write_todos":
            tool = {"id": msg.tool_call_id, "agent": agent, "name": msg.name}
            self.emit("tool.end", {**tool, "output": _content(msg)})


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


async def _known(client, ids: set[str]) -> set[str]:
    if not ids:
        return set()
    found = _text(await client.call_tool("get_evidence", {"ids": sorted(ids)}))
    return set(re.findall(r"^\[([^\]]+)\] ", found, re.MULTILINE)) & ids


def _collect(msg, seen: dict, chars: int = 150) -> None:
    if getattr(msg, "type", "") == "tool":
        for eid, title, text in PASSAGE.findall(_content(msg)):
            seen.setdefault(eid, f"{title.strip()}: {' '.join(text.split())[:chars]}")


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


async def _sources(client, report: str) -> str:
    ids = sorted(set(EVIDENCE_ID.findall(report)))
    if not ids:
        return ""
    found = _text(await client.call_tool("get_evidence", {"ids": ids}))
    lines = [
        f"- [{m[1]}] {m[2].strip()} - {m[3].strip()}"
        for m in re.finditer(r"^\[([^\]]+)\] (.*)\nURL: (\S+)", found, re.MULTILINE)
    ]
    return "\n\n## Sources\n" + "\n".join(lines) if lines else ""
