import asyncio
from pathlib import Path
from types import SimpleNamespace

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
