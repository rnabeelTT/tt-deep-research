"""AI-Q's web UI on the Deep Agents harness: the chat websocket and job API the UI speaks.

Every chat message becomes a deep-research job. The harness runs in a background task and its
run events (ttdr.harness.Progress) become the job's SSE events, kept in memory so a reconnecting
stream replays from its last event ID. Jobs live as long as the process.
"""

import asyncio
import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from ttdr.harness import EVIDENCE_ID

HEARTBEAT_S = 15.0
DATA_SOURCES = [
    {"id": "web", "name": "Web search", "description": "Search the public web."},
    {"id": "documents", "name": "Our documents", "description": "The private document corpus."},
]
SCOPES = {frozenset({"web"}): "web", frozenset({"documents"}): "corpus"}
PASSAGE = re.compile(
    r"^\[([^\]\s]+)\] (.*)\nURL: (\S+)\n((?:(?!^\[[^\]\s]+\] ).*\n?)*)", re.MULTILINE
)


def scope(selected: list[str]) -> str:
    """The harness's search scope for the UI's data-source selection (none or both: all)."""
    return SCOPES.get(frozenset(selected) & {"web", "documents"}, "all")


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Job:
    id: str
    question: str
    sources: str
    status: str = "running"
    error: str | None = None
    report: str | None = None
    created: str = field(default_factory=_now)
    started: float = field(default_factory=time.monotonic)
    events: list[tuple[str, dict]] = field(default_factory=list)
    """SSE events in order; an event's ID is its position + 1."""
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    urls: dict[str, str] = field(default_factory=dict)
    """Evidence ID -> URL, for every passage the tools returned."""

    @property
    def done(self) -> bool:
        return self.status != "running"

    def add(self, event: str, data: dict) -> None:
        self.events.append((event, data))
        self.changed.set()
        self.changed = asyncio.Event()

    def finish(self, status: str, error: str | None = None) -> None:
        self.status, self.error = status, error
        self.add("job.status", {"data": {"status": status, **({"error": error} if error else {})}})


class Translate:
    """Harness run events -> the UI's SSE events for one job."""

    def __init__(self, job: Job):
        self.job = job
        self.researchers: dict[str, str] = {}
        """Researcher ID (its task call) -> subagent name."""

    def __call__(self, kind: str, data: dict) -> None:
        getattr(self, kind.replace(".", "_"), lambda _: None)(data)

    def _event(self, event: str, id: str, name: str, payload: dict, metadata: dict) -> None:
        body = {"id": id, "name": name, "timestamp": _now(), "data": payload}
        self.job.add(event, {**body, "metadata": metadata})

    def _artifact(self, data: dict, workflow: str | None = None) -> None:
        body = {"id": uuid.uuid4().hex, "timestamp": _now(), "data": data}
        self.job.add(
            "artifact.update",
            {**body, **({"metadata": {"workflow": workflow}} if workflow else {})},
        )

    def plan(self, d: dict) -> None:
        kind = "report" if d["report"] else "short answer"
        budget = f"up to {d['subquestions']} sub-questions, {d['tool_calls']} tool calls each"
        meta = {"agent_id": "planner"}
        self._event("workflow.start", "planner", "planner", {"input": self.job.question}, meta)
        self._event("workflow.end", "planner", "planner", {"output": f"{kind}: {budget}"}, meta)

    def todos(self, d: dict) -> None:
        items = [
            {"id": str(i), "content": t.get("content", ""), "status": t.get("status", "pending")}
            for i, t in enumerate(d["todos"])
        ]
        self._artifact({"type": "todo", "content": items})

    def researcher_start(self, d: dict) -> None:
        self.researchers[d["id"]] = d["subagent"]
        meta = {"agent_id": d["id"]}
        self._event("workflow.start", d["id"], d["subagent"], {"input": d["question"]}, meta)

    def researcher_end(self, d: dict) -> None:
        name = self.researchers.get(d["id"], "researcher")
        meta = {"agent_id": d["id"]}
        self._event("workflow.end", d["id"], name, {"output": d["notes"]}, meta)

    def _tool_meta(self, d: dict) -> dict:
        agent = d["agent"] or "planner"
        return {"workflow": self.researchers.get(agent, agent), "agent_id": agent}

    def tool_start(self, d: dict) -> None:
        self._event("tool.start", d["id"], d["name"], {"input": d["input"]}, self._tool_meta(d))

    def tool_end(self, d: dict) -> None:
        self._event("tool.end", d["id"], d["name"], {"output": d["output"]}, self._tool_meta(d))
        workflow = self._tool_meta(d)["workflow"]
        for eid, title, url, text in PASSAGE.findall(d["output"]):
            if eid not in self.job.urls:
                self.job.urls[eid] = url
                content = f"[{eid}] {title.strip()}\n{text.strip()}"
                self._artifact(
                    {"type": "citation_source", "url": url, "content": content}, workflow
                )

    def report(self, d: dict) -> None:
        for eid in dict.fromkeys(EVIDENCE_ID.findall(d["report"])):
            if url := self.job.urls.get(eid):
                self._artifact({"type": "citation_use", "url": url, "content": ""})
        self.job.report = d["report"]
        self._artifact(
            {"type": "output", "content": d["report"], "output_category": "final_report"}
        )


def _frame(event: str, data: dict, id: int | None = None) -> str:
    head = f"id: {id}\n" if id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stream(job: Job, after: int):
    """Events after AFTER, then live ones until the job ends (its final job.status closes it)."""
    yield _frame("stream.start", {"job_id": job.id})
    cursor, live = after, False
    while True:
        waiter = job.changed
        while cursor < len(job.events):
            cursor += 1
            yield _frame(*job.events[cursor - 1], cursor)
        if job.done:
            if after >= len(job.events):
                yield _frame(*job.events[-1], len(job.events))
            return
        if not live:
            live = True
            yield _frame("stream.mode", {"mode": "live"})
        try:
            await asyncio.wait_for(waiter.wait(), HEARTBEAT_S)
        except TimeoutError:
            uptime = round(time.monotonic() - job.started)
            yield _frame("job.heartbeat", {"data": {"uptime_seconds": uptime}})


def build(make_harness: Callable[[str], object]) -> Starlette:
    """MAKE_HARNESS(sources) gives a harness whose search is limited to SOURCES ("web",
    "corpus" or "all"); its run(question, events=...) is awaited per job."""
    jobs: dict[str, Job] = {}

    async def research(job: Job) -> None:
        job.add("job.status", {"data": {"status": "running"}})
        try:
            await make_harness(job.sources).run(job.question, events=Translate(job))
        except asyncio.CancelledError:
            job.finish("interrupted", "cancelled by user")
            raise
        except Exception as e:  # noqa: BLE001 - the UI shows it on the job
            job.finish("failure", f"{type(e).__name__}: {e}")
        else:
            job.finish("success")

    def submit(question: str, selected: list[str]) -> Job:
        job = Job(uuid.uuid4().hex, question, scope(selected))
        jobs[job.id] = job
        job.task = asyncio.create_task(research(job))
        return job

    def find(request: Request) -> Job | None:
        return jobs.get(request.path_params["job_id"])

    def missing(request: Request) -> JSONResponse:
        detail = f"Job not found: {request.path_params['job_id']}"
        return JSONResponse({"detail": detail}, status_code=404)

    async def chat(ws: WebSocket) -> None:
        await ws.accept()
        try:
            while True:
                msg = await ws.receive_json()
                if msg.get("type") != "user_message":
                    continue
                try:
                    text = msg["content"]["messages"][-1]["content"][0]["text"]
                    query = json.loads(text) if text.lstrip().startswith("{") else {"query": text}
                    question, selected = query["query"].strip(), query.get("data_sources") or []
                    if not question:
                        raise ValueError("empty query")
                except (KeyError, IndexError, TypeError, ValueError) as e:
                    await ws.send_json(
                        {
                            "type": "error_message",
                            "id": uuid.uuid4().hex,
                            "conversation_id": msg.get("conversation_id"),
                            "content": {"code": "invalid_message", "message": str(e)},
                            "status": "error",
                            "timestamp": _now(),
                        }
                    )
                    continue
                job = submit(question, selected)
                escalation = {"type": "job_escalation", "kind": "deep_research", "job_id": job.id}
                await ws.send_json(
                    {
                        "type": "system_response_message",
                        "id": uuid.uuid4().hex,
                        "parent_id": msg.get("id"),
                        "conversation_id": msg.get("conversation_id"),
                        "content": json.dumps(escalation),
                        "status": "complete",
                        "timestamp": _now(),
                    }
                )
        except WebSocketDisconnect:
            pass

    async def stream(request: Request):
        if not (job := find(request)):
            return missing(request)
        after = int(request.path_params.get("last_event_id", 0))
        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(
            _stream(job, after), media_type="text/event-stream", headers=headers
        )

    async def status(request: Request):
        if not (job := find(request)):
            return missing(request)
        body = {"job_id": job.id, "status": job.status, "error": job.error}
        return JSONResponse({**body, "created_at": job.created})

    async def report(request: Request):
        if not (job := find(request)):
            return missing(request)
        return JSONResponse(
            {"job_id": job.id, "has_report": bool(job.report), "report": job.report}
        )

    async def state(request: Request):
        if not (job := find(request)):
            return missing(request)
        tools = [
            {"name": d["name"], "output": d["data"]["output"], "timestamp": d["timestamp"]}
            for e, d in job.events
            if e == "tool.end"
        ]
        outputs = (
            [{"type": "output", "content": job.report, "output_category": "final_report"}]
            if job.report
            else []
        )
        artifacts = {"tools": tools, "outputs": outputs}
        return JSONResponse(
            {"job_id": job.id, "has_state": False, "state": None, "artifacts": artifacts}
        )

    async def cancel(request: Request):
        if not (job := find(request)):
            return missing(request)
        if job.done:
            return JSONResponse({"detail": f"Job not running: {job.id}"}, status_code=400)
        job.task.cancel()
        return JSONResponse({"job_id": job.id, "status": "interrupted", "cancelled": True})

    async def data_sources(_: Request):
        return JSONResponse(
            [{**s, "default_enabled": True, "requires_auth": False} for s in DATA_SOURCES]
        )

    async def health(_: Request):
        return JSONResponse({"status": "healthy"})

    job_path = "/v1/jobs/async/job/{job_id}"
    return Starlette(
        routes=[
            WebSocketRoute("/websocket", chat),
            Route(f"{job_path}/stream", stream),
            Route(f"{job_path}/stream/{{last_event_id:int}}", stream),
            Route(job_path, status),
            Route(f"{job_path}/report", report),
            Route(f"{job_path}/state", state),
            Route(f"{job_path}/cancel", cancel, methods=["POST"]),
            Route("/v1/data_sources", data_sources),
            Route("/health", health),
        ]
    )


def serve(recipe, host: str, port: int) -> None:
    import uvicorn

    from ttdr.harness import DeepAgentsHarness

    uvicorn.run(
        build(lambda sources: DeepAgentsHarness(recipe, sources=sources)), host=host, port=port
    )
