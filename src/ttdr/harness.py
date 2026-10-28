"""A lean research harness over the engine's MCP tools: plan, research in parallel, write, verify.

Orchestration is plain code, so loops are bounded by construction, and no request forces a tool
call or a JSON schema (both push the whole batch off Blackhole's fast decode path). Talks to the
engine only through MCP, like any other top layer.
"""

import asyncio
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field

from mcp import Client

from ttdr.clients import LLM
from ttdr.engine import review
from ttdr.engine.verify import Verdict

PLAN_PROMPT = """Break this research request into 2 to {n} sub-questions that together answer it.
For each, say where to look: "corpus" (private documents such as company 10-K filings), "web",
or "all". Reply with JSON only: [{{"q": "...", "sources": "corpus|web|all"}}, ...]

Request: {question}"""

RESEARCH_PROMPT = """You are researching one sub-question for a report.
Use the tools: search with short keyword queries, fetch_page on the most promising URLs with
focus set to the sub-question. Every passage has an evidence ID in square brackets.
When you have enough, stop calling tools and reply with research notes: short bullet facts, each
ending with the evidence IDs it comes from, e.g. "- Revenue was $5.2B in 2022 [AMD_2022_10K#p48.0]".
Only cite IDs the tools returned. Say what you could not find.

Overall request: {question}
Sub-question: {sub} (look in: {sources})"""

NOTES_NOW = "Stop using tools. Write your research notes now, citing evidence IDs."

WRITE_PROMPT = """Write the answer to the request from the research notes below.
- Answer the request directly first, then give supporting detail; use headings for a long report.
- Every factual sentence ends with the evidence IDs it rests on, in square brackets, copied from
  the notes. Use no facts that aren't in the notes.
- If the notes don't answer part of the request, say so plainly ("I don't know" is fine).
Don't write a Sources list; it is added for you.

Request: {question}

Notes:
{notes}"""

EVIDENCE_ID = re.compile(r"\[((?:W[0-9a-f]{8}(?:\.\d+)?)|(?:[^\[\]\s]+#p\d+\.\d+))\]")
VERDICT = re.compile(r"^(supported|contradicted|not_enough_info)\b")


def _json_list(text: str) -> list:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    try:
        return json.loads(m.group()) if m else []
    except json.JSONDecodeError:
        return []


def _text(result) -> str:
    return "".join(c.text for c in result.content if getattr(c, "type", "") == "text")


def _openai_tools(tools) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.input_schema},
        }
        for t in tools
        if t.name in ("search", "fetch_page", "get_evidence")
    ]


@dataclass
class Result:
    report: str
    notes: list[str]
    stats: Counter = field(default_factory=Counter)
    verification: dict | None = None


@dataclass
class Lite:
    llm: LLM
    mcp_url: str
    max_subquestions: int = 5
    max_rounds: int = 6
    concurrency: int = 4
    thinking: bool = False
    verify: bool = True
    revise: bool = True
    tool_result_chars: int = 6000

    def _params(self, **kw) -> dict:
        return {"chat_template_kwargs": {"enable_thinking": self.thinking}, **kw}

    async def _chat(self, messages, stats: Counter, **kw) -> dict:
        r = await self.llm.chat(messages, **self._params(**kw))
        stats["llm calls"] += 1
        stats["input tokens"] += r["usage"]["prompt_tokens"]
        stats["output tokens"] += r["usage"]["completion_tokens"]
        return r["choices"][0]["message"]

    async def plan(self, question: str, stats: Counter) -> list[dict]:
        prompt = PLAN_PROMPT.format(n=self.max_subquestions, question=question)
        msg = await self._chat([{"role": "user", "content": prompt}], stats, max_tokens=2048)
        subs = [
            s for s in _json_list(msg.get("content") or "") if isinstance(s, dict) and s.get("q")
        ]
        return subs[: self.max_subquestions] or [{"q": question, "sources": "all"}]

    async def research(self, mcp: Client, tools, question: str, sub: dict, stats, log) -> str:
        messages = [
            {
                "role": "user",
                "content": RESEARCH_PROMPT.format(
                    question=question, sub=sub["q"], sources=sub.get("sources", "all")
                ),
            }
        ]
        for _ in range(self.max_rounds):
            msg = await self._chat(messages, stats, tools=tools, max_tokens=4096)
            calls = msg.get("tool_calls") or []
            if not calls:
                return msg.get("content") or ""
            messages.append(
                {"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls}
            )
            for call in calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                    out = _text(await mcp.call_tool(name, args))
                except Exception as e:  # noqa: BLE001 - the model sees the error and moves on
                    args, out = {}, f"Tool error: {type(e).__name__}: {e}"
                stats[name] += 1
                log(f"  {name}: {str(args.get('query') or args.get('url') or args)[:90]}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": out[: self.tool_result_chars],
                    }
                )
        messages.append({"role": "user", "content": NOTES_NOW})
        return (await self._chat(messages, stats, max_tokens=4096)).get("content") or ""

    async def check(self, mcp: Client, report: str) -> list[review.Checked]:
        async def one(c: review.Claim) -> review.Checked:
            ids = [r for r in c.refs if not r.isdigit()]
            out = _text(await mcp.call_tool("verify", {"claim": c.text, "evidence_ids": ids}))
            m = VERDICT.match(out)
            return review.Checked(c, Verdict(m[1], 0.0, 0.0, 0) if m else None, ids)

        return list(await asyncio.gather(*(one(c) for c in review.claims(report))))

    async def sources(self, mcp: Client, report: str) -> str:
        ids = sorted(set(EVIDENCE_ID.findall(report)))
        if not ids:
            return ""
        found = _text(await mcp.call_tool("get_evidence", {"ids": ids}))
        lines = [
            f"- [{m[1]}] {m[2].strip()} - {m[3].strip()}"
            for m in re.finditer(r"^\[([^\]]+)\] (.*)\nURL: (\S+)", found, re.MULTILINE)
        ]
        return "\n\n## Sources\n" + "\n".join(lines) if lines else ""

    async def run(self, question: str, log=lambda _: None) -> Result:
        stats: Counter = Counter()
        start = time.perf_counter()
        async with Client(self.mcp_url) as mcp:
            listed = await mcp.list_tools()
            tools = _openai_tools(listed.tools)
            can_verify = any(t.name == "verify" for t in listed.tools)
            subs = await self.plan(question, stats)
            log(f"plan: {len(subs)} sub-questions")
            for s in subs:
                log(f"  - {s['q']} ({s.get('sources', 'all')})")
            sem = asyncio.Semaphore(self.concurrency)

            async def one(sub):
                async with sem:
                    return await self.research(mcp, tools, question, sub, stats, log)

            notes = await asyncio.gather(*(one(s) for s in subs))
            log("writing")
            joined = "\n\n".join(f"### {s['q']}\n{n}" for s, n in zip(subs, notes, strict=True))
            msg = await self._chat(
                [{"role": "user", "content": WRITE_PROMPT.format(question=question, notes=joined)}],
                stats,
                max_tokens=8192,
            )
            report = msg.get("content") or ""
            verification = None
            if self.verify and can_verify:
                checked = await self.check(mcp, report)
                verification = {"before": review.summary(checked)}
                if self.revise:
                    report = await review.revise(self.llm, report, checked)
                    verification["after"] = review.summary(await self.check(mcp, report))
                log(f"verified: {verification}")
            report += await self.sources(mcp, report)
        stats["seconds"] = round(time.perf_counter() - start)
        return Result(report, list(notes), stats, verification)
