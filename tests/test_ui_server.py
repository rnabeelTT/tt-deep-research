import asyncio
import json

import pytest
from starlette.testclient import TestClient

from ttdr import ui_server

PASSAGES = (
    "[W1a2b3c4d.1] Capex news\nURL: https://x.org/a\nCapex was $1.5B.\n\n"
    "[3M_2018_10K#p59.0] 3M 2018 10-K\nURL: corpus://3M_2018_10K#page=60\nPurchases of PP&E"
)
REPORT = "# Capex\nCapex was $1.5B [W1a2b3c4d.1].\n\n## Sources\n- [W1a2b3c4d.1] Capex news"
RUN = [
    ("plan", {"report": True, "subquestions": 2, "tool_calls": 4}),
    ("todos", {"todos": [{"content": "Find capex", "status": "in_progress"}]}),
    ("researcher.start", {"id": "tA", "subagent": "researcher", "question": "Find capex"}),
    ("tool.start", {"id": "s1", "agent": "tA", "name": "search", "input": {"query": "capex"}}),
    ("tool.end", {"id": "s1", "agent": "tA", "name": "search", "output": PASSAGES}),
    ("researcher.end", {"id": "tA", "notes": "- Capex $1.5B [W1a2b3c4d.1]"}),
    ("report", {"report": REPORT}),
]


class FakeHarness:
    def __init__(self, sources, gate=None):
        self.sources, self.gate = sources, gate

    async def run(self, question, events):
        for kind, data in RUN:
            if self.gate and kind == "report":
                await self.gate.wait()
            events(kind, data)
            await asyncio.sleep(0)


def message(query, sources=("web", "documents")):
    text = json.dumps({"query": query, "data_sources": list(sources)})
    return {
        "type": "user_message",
        "schema_type": "chat_stream",
        "id": "m1",
        "conversation_id": "c1",
        "content": {"messages": [{"role": "user", "content": [{"type": "text", "text": text}]}]},
        "timestamp": "2026-11-03T00:00:00Z",
    }


def sse(client, path):
    events, frame = [], {}
    with client.stream("GET", path) as r:
        for line in r.iter_lines():
            if not line:
                if frame:
                    events.append(frame)
                frame = {}
            elif line.startswith("id: "):
                frame["id"] = int(line[4:])
            elif line.startswith("event: "):
                frame["event"] = line[7:]
            elif line.startswith("data: "):
                frame["data"] = json.loads(line[6:])
    return events


@pytest.fixture
def made():
    return []


@pytest.fixture
def client(made):
    def make(sources, corpora):
        made.append(FakeHarness(sources))
        made[-1].corpora = corpora
        return made[-1]

    with TestClient(ui_server.build(make)) as c:
        yield c


def submit(client, query="What was capex?", sources=("web", "documents")):
    with client.websocket_connect("/websocket") as ws:
        ws.send_json(message(query, sources))
        reply = ws.receive_json()
    assert reply["type"] == "system_response_message" and reply["parent_id"] == "m1"
    escalation = json.loads(reply["content"])
    assert escalation["type"] == "job_escalation" and escalation["kind"] == "deep_research"
    return escalation["job_id"]


def test_chat_message_escalates_to_a_job_that_streams_the_run(client):
    job = submit(client)
    events = sse(client, f"/v1/jobs/async/job/{job}/stream")
    names = [e["event"] for e in events]
    assert names[0] == "stream.start" and events[0]["data"] == {"job_id": job}
    assert events[-1] == {
        **events[-1],
        "event": "job.status",
        "data": {"data": {"status": "success"}},
    }
    by = {}
    for e in events:
        by.setdefault(e["event"], []).append(e["data"])

    agents = [w for w in by["workflow.start"] if w["metadata"]["agent_id"] == "tA"]
    assert agents[0]["name"] == "researcher" and agents[0]["data"] == {"input": "Find capex"}
    [end] = [w for w in by["workflow.end"] if w["id"] == "tA"]
    assert end["data"]["output"].startswith("- Capex")
    [tool_end] = by["tool.end"]
    assert tool_end["data"]["output"] == PASSAGES
    assert tool_end["metadata"] == {"workflow": "researcher", "agent_id": "tA"}
    assert by["tool.start"][0]["data"]["input"] == {"query": "capex"}

    artifacts = [a["data"] for a in by["artifact.update"]]
    [todo] = [a for a in artifacts if a["type"] == "todo"]
    assert todo["content"] == [{"id": "0", "content": "Find capex", "status": "in_progress"}]
    sources = {a["url"]: a["content"] for a in artifacts if a["type"] == "citation_source"}
    assert sources == {
        "https://x.org/a": "[W1a2b3c4d.1] Capex news\nCapex was $1.5B.",
        "corpus://3M_2018_10K#page=60": "[3M_2018_10K#p59.0] 3M 2018 10-K\nPurchases of PP&E",
    }
    assert [a["url"] for a in artifacts if a["type"] == "citation_use"] == ["https://x.org/a"]
    [output] = [a for a in artifacts if a["type"] == "output"]
    assert output == {"type": "output", "content": REPORT, "output_category": "final_report"}

    ids = [e["id"] for e in events if "id" in e]
    assert ids == list(range(1, len(ids) + 1))
    report = client.get(f"/v1/jobs/async/job/{job}/report").json()
    assert report == {"job_id": job, "has_report": True, "report": REPORT}
    assert client.get(f"/v1/jobs/async/job/{job}").json()["status"] == "success"


def test_reconnect_replays_from_the_last_event_id(client):
    job = submit(client)
    full = [e for e in sse(client, f"/v1/jobs/async/job/{job}/stream") if "id" in e]
    rest = [e for e in sse(client, f"/v1/jobs/async/job/{job}/stream/5") if "id" in e]
    assert rest == full[5:]
    tail = sse(client, f"/v1/jobs/async/job/{job}/stream/{full[-1]['id']}")
    assert tail[-1]["event"] == "job.status" and tail[-1]["data"]["data"]["status"] == "success"


def test_live_stream_switches_mode_and_follows_new_events():
    gate = {}

    def make(sources, corpora):
        gate["event"] = asyncio.Event()
        return FakeHarness(sources, gate["event"])

    with TestClient(ui_server.build(make)) as client:
        job = submit(client)
        client.portal.start_task_soon(_release, gate["event"])
        events = sse(client, f"/v1/jobs/async/job/{job}/stream")
    live = [e["event"] for e in events].index("stream.mode")
    assert events[live]["data"] == {"mode": "live"}
    after = [
        e["data"]["data"].get("type") for e in events[live:] if e["event"] == "artifact.update"
    ]
    assert "output" in after and events[-1]["data"]["data"]["status"] == "success"


async def _release(event):
    await asyncio.sleep(0.1)
    event.set()


@pytest.mark.parametrize(
    "selected, scope, corpora",
    [
        (["web", "documents"], "all", ()),
        (["web"], "web", ()),
        (["documents"], "corpus", ()),
        ([], "all", ()),
        (["docs:contracts"], "corpus", ("contracts",)),
        (["web", "docs:contracts", "docs:library"], "all", ("contracts", "library")),
        (["web", "knowledge_layer"], "all", ("library",)),
        (["docs:contracts", "knowledge_layer"], "corpus", ("contracts", "library")),
    ],
)
def test_data_sources_choose_the_search_scope(client, made, selected, scope, corpora):
    submit(client, sources=selected)
    assert (made[-1].sources, made[-1].corpora) == (scope, corpora)


def test_data_sources_lists_web_and_documents(client):
    sources = client.get("/v1/data_sources").json()
    assert [(s["id"], s["name"], s["default_enabled"]) for s in sources] == [
        ("web", "Web search", True),
        ("documents", "Our documents", True),
    ]
    assert client.get("/health").status_code == 200


def test_cancel_interrupts_the_job():
    def make(sources, corpora):
        return FakeHarness(sources, asyncio.Event())

    with TestClient(ui_server.build(make)) as client:
        job = submit(client)
        r = client.post(f"/v1/jobs/async/job/{job}/cancel")
        assert r.json() == {"job_id": job, "status": "interrupted", "cancelled": True}
        events = sse(client, f"/v1/jobs/async/job/{job}/stream")
        assert events[-1]["data"]["data"] == {"status": "interrupted", "error": "cancelled by user"}
        assert client.post(f"/v1/jobs/async/job/{job}/cancel").status_code == 400
        assert client.get("/v1/jobs/async/job/nope").status_code == 404


def test_bad_message_gets_an_error(client):
    with client.websocket_connect("/websocket") as ws:
        ws.send_json({**message(""), "content": {"messages": []}})
        assert ws.receive_json()["type"] == "error_message"
