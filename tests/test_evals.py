import json

import pytest

from ttdr.evals import report
from ttdr.evals.grading import match
from ttdr.evals.schema import Answer, Graded, Item


def _item(answer: str) -> Item:
    return Item(id="q", suite="s", question="?", answer=answer)


def _ans(text: str, error: str | None = None) -> Answer:
    return Answer(id="q", text=text, latency_s=1.0, error=error)


@pytest.mark.parametrize(
    ("gold", "text", "method"),
    [
        ("Michio Sugeno", "michio sugeno.", "exact"),
        ("$1577.00", "Capital expenditure was $1,577 million.", "numeric"),
        ("0.66", "The ratio is about 0.664.", "numeric"),
        ("-12.5%", "It fell -12.5%.", "numeric"),
    ],
)
def test_deterministic_correct(gold, text, method):
    g = match(_item(gold), _ans(text))
    assert g.grade == "correct" and g.method == method


@pytest.mark.parametrize(
    ("gold", "text"),
    [
        ("$1577.00", "In FY2018 capex was $1,577 million."),
        ("$1577.00", "It was $1,700 million."),
        ("Michio Sugeno", "Yoshua Bengio"),
    ],
)
def test_ambiguous_or_wrong_defers_to_judge(gold, text):
    assert match(_item(gold), _ans(text)) is None


def test_errors_are_graded_incorrect():
    assert match(_item("x"), _ans("", error="Timeout")).method == "error"


def _write_run(path, grades: list[str], latency: float):
    path.mkdir()
    ids = [f"q{i}" for i in range(len(grades))]
    rows = {
        "items.jsonl": [Item(id=i, suite="s", question="?", answer="a") for i in ids],
        "answers.jsonl": [Answer(id=i, text="a", latency_s=latency) for i in ids],
        "grades.jsonl": [Graded(id=i, grade=g, method="judge") for i, g in zip(ids, grades)],
    }
    for name, models in rows.items():
        (path / name).write_text("".join(m.model_dump_json() + "\n" for m in models))


def test_summary_and_paired_compare(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _write_run(a, ["correct", "incorrect", "incorrect", "not_attempted"], 2.0)
    _write_run(b, ["correct", "correct", "correct", "incorrect"], 1.0)

    s = report.summarize(a)["s"]
    assert s["accuracy"] == 0.25 and s["not_attempted"] == 1
    assert json.loads((a / "summary.json").read_text())["s"]["n"] == 4

    c = report.compare(a, b)["s"]
    assert c["diff"] == 0.5 and c["flips"] == {"gained": 2, "lost": 0}
    assert c["ci95"][0] <= 0.5 <= c["ci95"][1]


def test_tool_check():
    from ttdr.bench import _check

    call = {
        "tool_calls": [{"function": {"name": "web_search", "arguments": '{"query": "FDA GLP-1"}'}}]
    }
    assert _check("web_search", "fda", call)[1]
    assert not _check(None, "", call)[1]
    assert _check(None, "", {"content": "391"})[1]
    bad = {"tool_calls": [{"function": {"name": "web_search", "arguments": "{query"}}]}
    assert not _check("web_search", "", bad)[1]


def test_reasoning_params():
    from ttdr.clients import reasoning_params

    assert reasoning_params("low") == {"reasoning_effort": "low"}
    assert reasoning_params(thinking=False) == {"chat_template_kwargs": {"enable_thinking": False}}


def test_external_judge_roundtrip(tmp_path):
    from ttdr.evals import grading

    run = tmp_path / "run"
    run.mkdir()
    items = [
        Item(id="a", suite="s", question="Q1", answer="Paris"),
        Item(id="b", suite="s", question="Q2", answer="Michio Sugeno"),
    ]
    answers = [
        Answer(id="a", text="paris", latency_s=1),
        Answer(id="b", text="Bengio", latency_s=1),
    ]
    (run / "items.jsonl").write_text("".join(i.model_dump_json() + "\n" for i in items))
    (run / "answers.jsonl").write_text("".join(a.model_dump_json() + "\n" for a in answers))

    q = tmp_path / "q"
    assert grading.export_queue([run], q, chunks=2) == 1
    queued = [json.loads(x) for f in q.glob("queue-*.jsonl") for x in f.read_text().splitlines()]
    assert set(queued[0]) == {"key", "question", "gold", "predicted"}
    (q / "verdicts-00.jsonl").write_text(
        json.dumps({"key": queued[0]["key"], "grade": "B", "reason": "wrong"}) + "\n"
    )

    grading.import_verdicts(q, "test-judge")
    grades = {
        g.id: g
        for g in (
            Graded.model_validate_json(x) for x in (run / "grades.jsonl").read_text().splitlines()
        )
    }
    assert grades["a"].method == "exact" and grades["b"].grade == "incorrect"
    assert json.loads((run / "judge.json").read_text())["model"] == "test-judge"


def test_race_total_and_relative():
    from ttdr.evals import race

    criteria = {
        "dimension_weight": {d: 0.25 for d in race.DIMENSIONS},
        "criterions": {d: [{"weight": 0.75}, {"weight": 0.25}] for d in race.DIMENSIONS},
    }
    t = race.total({d: [8, 4] for d in race.DIMENSIONS}, criteria)
    assert t["overall"] == pytest.approx(7.0)
    ref = race.total({d: [7, 7] for d in race.DIMENSIONS}, criteria)
    assert race.relative(t, ref)["overall"] == pytest.approx(0.5)
    with pytest.raises(ValueError):
        race.total({d: [8] for d in race.DIMENSIONS}, criteria)


def test_chat_decider_answers_typed_questions():
    import asyncio

    from ttdr.clients import ChatDecider
    from ttdr.engine.verify import QUESTION

    class FakeLLM:
        async def chat(self, messages, **params):
            said = "Not_enough_info" if "Paris" in messages[1]["content"] else "supported"
            return {"choices": [{"message": {"content": said}}]}

    d = ChatDecider(FakeLLM())
    a, b = asyncio.run(d.ask_batch(["Passage: x\nClaim: Paris", "Passage: y\nClaim: z"], QUESTION))
    assert a["support"]["probabilities"]["not_enough_info"] == 1.0
    assert b["support"]["choice"] == "supported"
    yes = asyncio.run(d.ask("s", {"q": {"type": "noul", "instructions": "?"}}))
    assert yes["q"]["noul"] == 0.0


def test_checker_verifies_through_windows():
    import asyncio

    import httpx

    from ttdr.clients import Checker
    from ttdr.engine.verify import Verifier

    def handler(request):
        pairs = json.loads(request.content)["pairs"]
        return httpx.Response(
            200, json={"support": [0.9 if "42" in p["passage"] else 0.1 for p in pairs]}
        )

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            v = Verifier(Checker(http, "http://checker"), window=200)
            passage = " ".join(["Filler sentence about nothing."] * 20) + " Revenue was 42."
            return await v.check("Revenue was 42.", passage), await v.check("x", "nothing here")

    hit, miss = asyncio.run(go())
    assert hit.label == "supported" and hit.window > 0
    assert miss.label == "not_enough_info" and miss.contradicted == 0.0


def test_jsonl_lines_keep_unicode_separators_inside_strings(tmp_path):
    import json

    from ttdr import jsonl

    path = tmp_path / "answers.jsonl"
    path.write_text(json.dumps({"text": "a b"}, ensure_ascii=False) + "\n\n")
    assert [json.loads(x)["text"] for x in jsonl.lines(path)] == ["a b"]


def test_published_drb_reports_become_a_run(tmp_path):
    from ttdr.evals import race

    src = tmp_path / "x.jsonl"
    src.write_text(
        '{"id": "51", "prompt": "p", "article": "report"}\n{"id": "52", "prompt": "q"}\n'
    )
    assert race.import_published(src, tmp_path / "run") == 2
    answers = race._answers(tmp_path / "run")
    assert answers["drb_51"].text == "report" and answers["drb_52"].text == ""


def test_runs_started_in_the_same_second_get_their_own_directory(tmp_path, monkeypatch):
    from ttdr.evals import runner

    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path)
    assert runner._new_run_dir("x").name == "x"
    assert runner._new_run_dir("x").name == "x-2"


def test_deep_agents_search_only_what_each_suite_tests(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from ttdr import harness
    from ttdr.evals.schema import Item
    from ttdr.evals.systems import DeepAgents

    scopes = []

    class Harness:
        def __init__(self, *a, sources="all", corpora=(), **kw):
            scopes.append((sources, corpora))

        async def run(self, question):
            stats = {"input tokens": 0, "output tokens": 0}
            return SimpleNamespace(report="", seen=[], stats=stats)

    monkeypatch.setattr(harness, "DeepAgentsHarness", Harness)
    monkeypatch.setattr("ttdr.evals.systems.recipe.load", lambda: None)
    for suite in ("deepresearch_bench", "frames", "financebench", "unidoc", "other"):
        asyncio.run(DeepAgents().answer(Item(id="i", suite=suite, question="q", answer=""), None))
    asyncio.run(
        DeepAgents(scoped=False).answer(Item(id="i", suite="hle", question="q", answer=""), None)
    )
    assert scopes == [
        ("web", ()),
        ("web", ()),
        ("corpus", ("financebench",)),
        ("corpus", ("unidoc",)),
        ("all", ()),
        ("all", ()),
    ]
