import asyncio
from pathlib import Path
from types import SimpleNamespace

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from ttdr import harness, recipe

RECIPE = recipe.load(Path(__file__).parents[1] / "recipe.toml")


class FakeClient:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=f"{name} ok")])


def test_mcp_tools_become_langchain_tools():
    client = FakeClient()
    schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    [tool] = harness.mcp_tools(
        client, [SimpleNamespace(name="search", description="d", input_schema=schema)]
    )
    assert tool.name == "search" and tool.args_schema == schema
    assert asyncio.run(tool.ainvoke({"query": "capex"})) == "search ok"
    assert client.calls == [("search", {"query": "capex"})]


def test_chat_model_uses_the_recipe_llm_and_thinking_switch():
    m = harness.chat_model(RECIPE, thinking=False, max_tokens=100)
    assert m.model_name == RECIPE.llm.model and m.temperature == 0.6
    assert m.extra_body == {"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}}


def test_deep_agent_builds_with_a_researcher_subagent():
    schema = {"type": "object", "properties": {}}
    tools = harness.mcp_tools(
        FakeClient(),
        [
            SimpleNamespace(name=n, description="d", input_schema=schema)
            for n in ("search", "fetch_page", "get_evidence", "verify")
        ],
    )
    agent = harness.DeepAgentsHarness(RECIPE).agent(tools)
    assert agent is not None


def test_a_model_without_vision_gets_no_read_page(monkeypatch):
    schema = {"type": "object", "properties": {}}
    tools = harness.mcp_tools(
        FakeClient(),
        [
            SimpleNamespace(name=n, description="d", input_schema=schema)
            for n in ("search", "read_page", "get_evidence")
        ],
    )
    seen = {}
    monkeypatch.setattr(harness, "create_deep_agent", lambda **kw: seen.update(kw))
    blind = RECIPE.model_copy(deep=True)
    blind.llm.vision = False
    for r, read in ((RECIPE, True), (blind, False)):
        harness.DeepAgentsHarness(r).agent(tools)
        researcher = seen["subagents"][0]
        assert ("read_page" in {t.name for t in researcher["tools"]}) is read
        assert ("read_page" in researcher["system_prompt"]) is read


def test_evidence_ids_in_reports():
    report = "Capex was $1,577M [3M_2018_10K#p59.0]; see also [Wdeadbeef.2] and [1]."
    assert harness.EVIDENCE_ID.findall(report) == ["3M_2018_10K#p59.0", "Wdeadbeef.2"]


def test_researcher_notes_without_ids_get_the_passages_behind_them():
    from langchain_core.messages import AIMessage, ToolMessage

    found = (
        "[3M_2018_10K#p59.2] 3M 2018 10-K\nURL: file://3M\nPurchases of PP&E (1,577) (1,373)\n\n"
        "[Wdeadbeef] Unrelated\nURL: https://x\nNothing relevant here"
    )
    state = {
        "messages": [
            ToolMessage(found, tool_call_id="1", name="search"),
            AIMessage("- 3M's 2018 capex was $1,577 million"),
        ]
    }
    [reply] = harness.EvidenceNotes().after_agent(state, None)["messages"]
    assert "- [3M_2018_10K#p59.2] 3M 2018 10-K: Purchases of PP&E" in reply.content
    assert "Wdeadbeef" not in reply.content
    state["messages"][-1] = AIMessage("- capex $1,577 million [3M_2018_10K#p59.2]")
    assert harness.EvidenceNotes().after_agent(state, None) is None


def test_only_tools_hides_the_built_in_file_tools():
    from types import SimpleNamespace as NS

    request = NS(
        tools=[NS(name=n) for n in ("grep", "search", "read_file", "fetch_page")],
        override=lambda tools: NS(tools=tools),
    )
    seen = harness.OnlyTools(harness.RESEARCH_TOOLS).wrap_model_call(request, lambda r: r)
    assert [t.name for t in seen.tools] == ["search", "fetch_page"]


def test_report_starts_at_its_first_heading():
    assert (
        harness._without_preamble("Let me write it.\n\n---\n\n# Report\nText") == "# Report\nText"
    )
    assert harness._without_preamble("Short answer [W1a2b3c4d]") == "Short answer [W1a2b3c4d]"


def test_the_models_sources_list_is_replaced():
    assert (
        harness._without_sources("# R\nFact [W1a2b3c4d].\n\n## Sources\n- [1] x")
        == "# R\nFact [W1a2b3c4d]."
    )


def test_citations_finds_real_and_invented_ids():
    report = (
        "Capex was $1,577 million [3M_2018_10K#p59.2]. Benchmarks vary [Dakota_10K#p3–p5; W1a2b3c4d.2]."
        " See [the docs](https://x.org/a#b)."
    )
    assert harness.citations(report) == {"3M_2018_10K#p59.2", "Dakota_10K#p3–p5", "W1a2b3c4d.2"}


def test_invented_web_ids_need_not_be_hex():
    report = "A [W5e6f7g8h.2]. B [Wikipedia]. C [W1a2b3c4d.1]."
    assert harness.citations(report) == {"W5e6f7g8h.2", "W1a2b3c4d.1"}


def test_strip_citations_keeps_only_known_ids():
    report = "A [W1a2b3c4d.2; Fake_10K#p1]. B [Made_Up#p2]. C."
    assert harness.strip_citations(report, {"W1a2b3c4d.2"}) == "A [W1a2b3c4d.2]. B. C."


class EvidenceClient(FakeClient):
    async def call_tool(self, name, args):
        found = [i for i in args["ids"] if i.startswith("W")]
        text = "\n\n".join(f"[{i}] Title\nURL: https://x.org\nbody" for i in found)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def test_check_citations_counts_and_drops_invented_ids_when_nothing_to_rewrite_with():
    from collections import Counter

    h = harness.DeepAgentsHarness(RECIPE)
    stats = Counter()
    report = "A [W1a2b3c4d.2]. B [Fake_10K#p1]."
    out = asyncio.run(h.check_citations(EvidenceClient(), report, {}, stats))
    assert out == "A [W1a2b3c4d.2]. B."
    assert stats["invented ids"] == 1 and stats["cited ids"] == 1


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply

    async def ainvoke(self, messages):
        return SimpleNamespace(content=self.reply)


def test_rewrite_that_loses_real_citations_is_dropped(monkeypatch):
    from collections import Counter

    monkeypatch.setattr(harness, "chat_model", lambda *a: FakeLLM("A. B."))
    h = harness.DeepAgentsHarness(RECIPE)
    seen = {"W1a2b3c4d.2": "Title: body"}
    report = "A [W1a2b3c4d.2]. B [Fake_10K#p1]."
    out = asyncio.run(h.check_citations(EvidenceClient(), report, seen, Counter()))
    assert out == "A [W1a2b3c4d.2]. B."


def test_rewrite_that_fixes_citations_is_kept(monkeypatch):
    from collections import Counter

    fixed = "A [W1a2b3c4d.2]. B [W1a2b3c4d.3]."
    monkeypatch.setattr(harness, "chat_model", lambda *a: FakeLLM(fixed))
    h = harness.DeepAgentsHarness(RECIPE)
    stats = Counter()
    report = "A [W1a2b3c4d.2]. B [Fake_10K#p1]."
    out = asyncio.run(h.check_citations(EvidenceClient(), report, {"W1a2b3c4d.3": "x"}, stats))
    assert out == fixed and stats["invented after rewrite"] == 0


def test_only_sections_with_invented_ids_are_rewritten(monkeypatch):
    from collections import Counter

    asked = []

    class Echo(FakeLLM):
        async def ainvoke(self, messages):
            asked.append(messages[1]["content"])
            return SimpleNamespace(
                content=messages[1]["content"].replace("Fake_10K#p1", "W1a2b3c4d.3")
            )

    monkeypatch.setattr(harness, "chat_model", lambda *a: Echo(""))
    h = harness.DeepAgentsHarness(RECIPE)
    report = (
        "# T\n\n## One\nA [W1a2b3c4d.2].\n\n## Two\nB [Fake_10K#p1].\n\n## Three\nC [W1a2b3c4d.2]."
    )
    out = asyncio.run(h.check_citations(EvidenceClient(), report, {"W1a2b3c4d.3": "x"}, Counter()))
    assert asked == ["## Two\nB [Fake_10K#p1].\n"]
    assert out == report.replace("Fake_10K#p1", "W1a2b3c4d.3")


def test_web_ids_in_filing_style_are_normalized():
    from collections import Counter

    h = harness.DeepAgentsHarness(RECIPE)
    stats = Counter()
    out = asyncio.run(h.check_citations(EvidenceClient(), "A [W1a2b3c4d#p2].", {}, stats))
    assert out == "A [W1a2b3c4d.2]." and stats["invented ids"] == 0


def test_engine_retries_until_the_server_is_back(monkeypatch):
    attempts = []

    class Flaky:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("engine restarting")
            return FakeClient()

        async def __aexit__(self, *exc):
            return False

    async def no_sleep(_):
        pass

    monkeypatch.setattr(harness, "Client", Flaky)
    monkeypatch.setattr(harness.asyncio, "sleep", no_sleep)
    r = asyncio.run(harness.Engine("http://x").call_tool("search", {"query": "q"}))
    assert r.content[0].text == "search ok" and len(attempts) == 3


def test_writer_outline_and_material():
    from ttdr import writer

    plan = writer.outline(
        "## Market size | quantify growth | W1a2b3c4d.2, 3M_2018_10K#p59.0\n"
        "Risks | compare risks | [W0000beef]\nnot a section line"
    )
    assert plan == [
        ("Market size", "quantify growth", ["W1a2b3c4d.2", "3M_2018_10K#p59.0"]),
        ("Risks", "compare risks", ["W0000beef"]),
    ]
    notes = ["- Sales grew 5% [3M_2018_10K#p59.0]\n- Unrelated [W9999aaaa.1]"]
    m = writer.material(["3M_2018_10K#p59.0"], notes, {"3M_2018_10K#p59.0": "3M: sales table"})
    assert "Sales grew" in m and "Unrelated" not in m and "sales table" in m


class ScriptedModel(GenericFakeChatModel):
    """The planner writes todos, starts two researchers, then answers; each researcher searches
    once and replies with notes."""

    messages: object = iter(())

    def bind_tools(self, tools, **kw):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        ask = next(m for m in messages if m.type == "human").content
        turn = sum(m.type == "ai" for m in messages)
        if ask.startswith("sub"):
            search = {"name": "search", "args": {"query": ask, "sources": "web"}, "id": f"s{ask}"}
            replies = [AIMessage("", tool_calls=[search]), AIMessage(f"- {ask} fact [W1a2b3c4d.1]")]
        else:
            todos = {"todos": [{"content": "sub A", "status": "in_progress"}]}
            tasks = [
                {"name": "task", "args": {"description": d, "subagent_type": "researcher"}, "id": i}
                for d, i in (("sub A", "tA"), ("sub B", "tB"))
            ]
            replies = [
                AIMessage("", tool_calls=[{"name": "write_todos", "args": todos, "id": "td"}]),
                AIMessage("", tool_calls=tasks),
                AIMessage("# Report\nFact [W1a2b3c4d.1]."),
            ]
        return ChatResult(generations=[ChatGeneration(message=replies[turn])])


class ResearchEngine:
    def __init__(self, url=None):
        self.calls = []

    async def list_tools(self):
        schema = {"type": "object", "properties": {"query": {"type": "string"}}}
        return [SimpleNamespace(name="search", description="d", input_schema=schema)]

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "get_evidence":
            text = "[W1a2b3c4d.1] Title\nURL: https://x.org\nbody"
        else:
            text = f"[W1a2b3c4d.1] Title\nURL: https://x.org\nresult for {args['query']}"
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def _run_scripted(monkeypatch, **kw):
    engines = []

    def engine(url):
        engines.append(ResearchEngine())
        return engines[-1]

    monkeypatch.setattr(harness, "chat_model", lambda *a, **k: ScriptedModel())
    monkeypatch.setattr(harness, "Engine", engine)
    events = []
    h = harness.DeepAgentsHarness(RECIPE, sections=False, **kw)
    result = asyncio.run(h.run("Q", events=lambda kind, data: events.append((kind, data))))
    return result, events, engines[0]


def test_run_reports_plan_todos_researchers_tools_and_report(monkeypatch):
    result, events, _ = _run_scripted(monkeypatch)
    kinds = [k for k, _ in events]
    assert kinds[0] == "plan" and kinds[-1] == "report"
    assert events[0][1] == {"report": True, "subquestions": 5, "tool_calls": 12}
    assert ("todos", {"todos": [{"content": "sub A", "status": "in_progress"}]}) in events
    starts = {d["id"]: d["question"] for k, d in events if k == "researcher.start"}
    assert starts == {"tA": "sub A", "tB": "sub B"}
    ends = {d["id"]: d["notes"] for k, d in events if k == "researcher.end"}
    assert ends["tA"].startswith("- sub A fact") and ends["tB"].startswith("- sub B fact")
    tools = [d for k, d in events if k == "tool.end"]
    assert {(t["agent"], t["output"].splitlines()[-1]) for t in tools} == {
        ("tA", "result for sub A"),
        ("tB", "result for sub B"),
    }
    assert kinds.index("researcher.start") < kinds.index("tool.start") < kinds.index("tool.end")
    assert events[-1][1]["report"] == result.report and "## Sources" in result.report


def test_sources_pins_the_search_scope(monkeypatch):
    _, _, engine = _run_scripted(monkeypatch, sources="corpus")
    searches = [a for n, a in engine.calls if n == "search"]
    assert searches and all(a["sources"] == "corpus" for a in searches)
    _, _, engine = _run_scripted(monkeypatch)
    assert all(a["sources"] == "web" for n, a in engine.calls if n == "search")


def test_quest_scaffold_formats_results_and_parses_calls():
    from ttdr import quest

    raw = "[W1a2b3c4d.2] Reuters\nURL: https://r.com/x\nsnippet one\n\n[3M_2018_10K#p59.0] 3M\nURL: http://ui/pages/3M_2018_10K/60\ncapex"
    out = quest._search_text("3M capex", raw)
    assert out.startswith("A Google search for '3M capex' found 2 results:")
    assert "Title: Reuters [W1a2b3c4d.2]\nLink: https://r.com/x\nSnipptes: snippet one" in out
    assert quest.EVIDENCE_ID.findall(out) == ["W1a2b3c4d.2", "3M_2018_10K#p59.0"]
    text = 'Let me search.\n<tool_call>\n{"name": "search", "arguments": {"query": ["a"]}}\n</tool_call>'
    assert quest.CALL.findall(text) == ['{"name": "search", "arguments": {"query": ["a"]}}']
    assert quest.ANSWER.search("x <answer>$1,577M</answer>").group(1) == "$1,577M"


def test_writer_drops_lines_a_looping_section_repeats():
    from ttdr.writer import without_repeats

    loop = "Live-service games keep players engaged through regular content updates."
    text = f"## Trends\n\n{loop}\n\n{loop}\n\n| a | b |\n| a | b |\n\n{loop}\nEnd."
    assert without_repeats(text) == f"## Trends\n\n{loop}\n\n| a | b |\n| a | b |\n\nEnd."


def test_thinking_fallback_retries_a_call_that_ran_out_of_tokens():
    from types import SimpleNamespace as NS

    from langchain_core.messages import AIMessage

    def reply(finish, calls=()):
        tool_calls = [{"name": "task", "args": {}, "id": "1"}] if calls else []
        return NS(
            result=[
                AIMessage("", tool_calls=tool_calls, response_metadata={"finish_reason": finish})
            ]
        )

    fallback = harness.ThinkingFallback("plain")
    request = NS(model="thinking", override=lambda model: NS(model=model))
    answers = {"thinking": reply("length"), "plain": reply("stop")}
    out = fallback.wrap_model_call(request, lambda r: answers[r.model])
    assert out is answers["plain"]
    answers["thinking"] = reply("length", calls=True)
    assert fallback.wrap_model_call(request, lambda r: answers[r.model]) is answers["thinking"]


def test_agents_hear_only_of_collections_that_hold_documents(monkeypatch, tmp_path):
    import asyncio

    from ttdr import recipe
    from ttdr.corpus import library, store
    from ttdr.harness import DeepAgentsHarness

    class Store:
        def __init__(self, url, collection):
            pass

        async def corpora(self):
            return {"financebench", "contracts"}

    monkeypatch.setattr(store, "Store", Store)
    monkeypatch.setattr(library, "described", lambda: {"library": "uploads", "contracts": "Leases"})
    r = recipe.load(None)
    everything = asyncio.run(DeepAgentsHarness(r).catalog())
    assert "- contracts: Leases" in everything and "- financebench:" in everything
    assert "unidoc" not in everything and "library" not in everything
    picked = asyncio.run(DeepAgentsHarness(r, corpora=("contracts", "unidoc")).catalog())
    assert picked == "- contracts: Leases"
