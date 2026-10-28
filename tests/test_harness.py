from types import SimpleNamespace

from ttdr import harness


def test_plan_json_is_parsed_leniently():
    text = 'Sure.\n```json\n[{"q": "capex 2018", "sources": "corpus"}]\n```'
    assert harness._json_list(text) == [{"q": "capex 2018", "sources": "corpus"}]
    assert harness._json_list("no json here") == []


def test_only_research_tools_are_offered_to_the_model():
    tools = [
        SimpleNamespace(name=n, description="d", input_schema={"type": "object"})
        for n in ("search", "fetch_page", "get_evidence", "verify")
    ]
    names = [t["function"]["name"] for t in harness._openai_tools(tools)]
    assert names == ["search", "fetch_page", "get_evidence"]


def test_evidence_ids_in_reports():
    report = "Capex was $1,577M [3M_2018_10K#p59.0]; see also [Wdeadbeef.2] and [1]."
    assert harness.EVIDENCE_ID.findall(report) == ["3M_2018_10K#p59.0", "Wdeadbeef.2"]
