"""QUEST's own inference scaffold over the engine's tools.

After OSU-NLP-Group/QUEST (MIT, inference/react_agent.py and prompt.py): one ReAct agent that
thinks, calls `search` (a list of queries) and `visit` (URLs and a goal) as <tool_call> JSON, reads
<tool_response> blocks, and ends with <answer>. The model was trained on this loop, so it plans
and stops by itself; our planner-and-researchers harness kept it searching until the call limits.
Search and visit run on the engine (web and private collections); the condenser and the LLM page
summarizer of the original are left out.
"""

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from ttdr.harness import Engine, Result, _text
from ttdr.recipe import Recipe

SYSTEM = """You are a deep research assistant. Your core function is to conduct thorough, multi-source investigations into any topic. You must handle both broad, open-domain inquiries and queries within specialized academic fields. For every request, synthesize information from credible, diverse sources to deliver a comprehensive, accurate, and objective response. When you have gathered sufficient information and are ready to provide the definitive response, you must enclose the entire final answer within <answer></answer> tags.

# Tools

You may call one or more functions to assist with the user query.

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{"type": "function", "function": {"name": "search", "description": "Perform Google web searches then returns a string of the top search results. Accepts multiple queries.", "parameters": {"type": "object", "properties": {"query": {"type": "array", "items": {"type": "string", "description": "The search query."}, "minItems": 1, "description": "The list of search queries."}}, "required": ["query"]}}}
{"type": "function", "function": {"name": "visit", "description": "Visit webpage(s) and return the summary of the content.", "parameters": {"type": "object", "properties": {"url": {"type": "array", "items": {"type": "string"}, "description": "The URL(s) of the webpage(s) to visit. Can be a single URL or an array of URLs."}, "goal": {"type": "string", "description": "The specific information goal for visiting webpage(s)."}}, "required": ["url", "goal"]}}}
</tools>

Search also covers these private document collections; their results link to document pages, which visit reads:
{catalog}

For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{"name": <function-name>, "arguments": <args-json-object>}
</tool_call>

Current date: {today}"""

FORCE = (
    "You have now reached the maximum number of reasoning turns you can use. You must stop making "
    "tool calls and, based on all the information above, think again and provide what you "
    "consider the most likely answer in the following format:<think>your final thinking</think>\n"
    "<answer>your answer</answer>"
)
CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
ANSWER = re.compile(r"<answer>(.*?)(?:</answer>|$)", re.DOTALL)
BLOCK = re.compile(
    r"^\[([^\]\s]+)\] *([^\n]*)\nURL: *(\S+)\n(.*?)(?=\n\n\[[^\]\s]+\] |\Z)",
    re.MULTILINE | re.DOTALL,
)
EVIDENCE_ID = re.compile(r"\[([^\]\s]+#p\d+\.\d+|W[0-9a-f]{8}(?:\.\d+)?)\]")


def _search_text(query: str, raw: str) -> str:
    """The engine's passages in the shape of QUEST's Google results (tool_search.py)."""
    hits = BLOCK.findall(raw)
    entries = [
        f"Title: {title} [{eid}]\nLink: {url}\nSnipptes: {text.strip()}"
        for eid, title, url, text in hits
    ]
    return f"A Google search for '{query}' found {len(hits)} results:\n\n" + "\n\n".join(entries)


def _visit_text(url: str, goal: str, raw: str) -> str:
    """The engine's passages for the goal, in the shape of QUEST's visit results (tool_visit.py)."""
    return (
        f"The useful information in {url} for user goal {goal} as follows: \n\n"
        f"Evidence in page: \n{raw}\n\nSummary: \nThe evidence above is the page's text most "
        "relevant to the goal.\n\n"
    )


@dataclass
class QuestHarness:
    recipe: Recipe
    max_turns: int = 60
    max_context: int = 200_000
    """Past this many prompt tokens, the next turn must answer (QUEST's own cap is 256k)."""
    max_tokens: int = 10_000
    sampling: dict = field(
        default_factory=lambda: {"temperature": 0.6, "top_p": 0.95, "presence_penalty": 1.1}
    )

    async def _tool(self, engine: Engine, name: str, args: dict, stats: Counter) -> str:
        if name == "search":
            queries = args.get("query") or []
            queries = [queries] if isinstance(queries, str) else queries
            out = []
            for q in queries[:5]:
                stats["search"] += 1
                out.append(_search_text(q, _text(await engine.call_tool("search", {"query": q}))))
            return "\n\n".join(out) or "No query given."
        if name == "visit":
            urls = args.get("url") or []
            urls = [urls] if isinstance(urls, str) else urls
            goal = args.get("goal", "")
            out = []
            for u in urls[:5]:
                stats["visit"] += 1
                raw = _text(await engine.call_tool("fetch_page", {"url": u, "focus": goal}))
                out.append(_visit_text(u, goal, raw))
            return "\n=======\n".join(out) or "No URL given."
        return f"Unknown tool {name!r}: use search or visit."

    async def _complete(self, http, messages: list[dict]) -> dict:
        """One turn through the completions API on the server-rendered chat prompt: the chat API's
        tool parser expects Qwen3.5's XML calls and drops QUEST's JSON ones from the content."""
        m = self.recipe.llm
        base = m.url.removesuffix("/v1")
        prompt = await http.post(
            f"{base}/tokenize",
            json={"model": m.model, "messages": messages, "add_generation_prompt": True},
        )
        prompt.raise_for_status()
        body = {
            "model": m.model,
            "prompt": prompt.json()["tokens"],
            "max_tokens": self.max_tokens,
            "stop": ["\n<tool_response>", "<tool_response>"],
            "skip_special_tokens": False,
            **self.sampling,
        }
        r = await http.post(f"{m.url}/completions", json=body)
        r.raise_for_status()
        return r.json()

    async def run(self, question: str, log=lambda _: None) -> Result:
        import httpx

        stats: Counter = Counter()
        start = time.perf_counter()
        engine = Engine(self.recipe.services.mcp)
        catalog = self.recipe.corpus_catalog() or "- (none)"
        messages = [
            {
                "role": "system",
                "content": SYSTEM.replace("{catalog}", catalog).replace(
                    "{today}", date.today().isoformat()
                ),
            },
            {"role": "user", "content": question},
        ]
        answer, seen = "", {}
        async with httpx.AsyncClient(timeout=1800) as http:
            force = False
            for turn in range(self.max_turns + 1):
                if turn == self.max_turns or force:
                    messages.append({"role": "user", "content": FORCE})
                    force = True
                r = await self._complete(http, messages)
                usage = r["usage"]
                stats["llm calls"] += 1
                stats["input tokens"] += usage["prompt_tokens"]
                stats["output tokens"] += usage["completion_tokens"]
                content = r["choices"][0]["text"].split("</think>")[-1]
                messages.append({"role": "assistant", "content": content})
                calls = CALL.findall(content)
                if (m := ANSWER.search(content)) or not calls or force:
                    answer = (m.group(1) if m else content).strip()
                    break
                try:
                    call = json.loads(calls[-1])
                    name = call.get("name", "")
                    result = await self._tool(engine, name, call.get("arguments") or {}, stats)
                except (ValueError, AttributeError) as e:
                    name, result = "?", f"Could not parse the tool call: {e}"
                seen.update(dict.fromkeys(EVIDENCE_ID.findall(result), ""))
                log(f"turn {turn}: {name}")
                messages.append(
                    {"role": "user", "content": f"<tool_response>\n{result}\n</tool_response>"}
                )
                force = usage["prompt_tokens"] > self.max_context
        stats["seconds"] = round(time.perf_counter() - start)
        return Result(answer, stats, list(seen))
