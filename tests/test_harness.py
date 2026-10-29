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
