import json
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from ttdr import jsonl
from ttdr.evals.schema import Answer, Graded, Item

BOOTSTRAP = 10_000


def _load(run_dir: Path) -> tuple[dict[str, Item], dict[str, Answer], dict[str, Graded]]:
    def read(name, model):
        lines = jsonl.lines(run_dir / name)
        return {r.id: r for r in (model.model_validate_json(x) for x in lines)}

    return read("items.jsonl", Item), read("answers.jsonl", Answer), read("grades.jsonl", Graded)


def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0.0


def _page(eid: str) -> tuple[str, int] | None:
    """Corpus evidence ids are doc#pPAGE.N; web ids have no page."""
    if "#p" not in eid:
        return None
    doc, rest = eid.split("#p", 1)
    return doc, int(rest.split(".", 1)[0])


def evidence_hits(item: Item, eids: list[str]) -> bool:
    gold = {(e.doc, e.page) for e in item.evidence}
    return any(_page(e) in gold for e in eids)


def summarize(run_dir: Path) -> dict:
    """Accuracy per suite and per tag, plus latency and token use. Written to summary.json."""
    items, answers, grades = _load(run_dir)
    groups: dict[str, list[str]] = defaultdict(list)
    for i in items.values():
        groups[i.suite].append(i.id)
        for k in ("type", "answer_type"):
            if k in i.tags:
                groups[f"{i.suite}/{i.tags[k]}"].append(i.id)

    def stats(ids: list[str]) -> dict:
        c = Counter(grades[i].grade for i in ids)
        lat = [answers[i].latency_s for i in ids if not answers[i].error]
        return {
            "n": len(ids),
            "accuracy": round(c["correct"] / len(ids), 4),
            "incorrect": c["incorrect"],
            "not_attempted": c["not_attempted"],
            "errors": sum(1 for i in ids if answers[i].error),
            "truncated": sum(1 for i in ids if answers[i].finish_reason == "length"),
            "latency_p50_s": round(_pct(lat, 0.5), 2),
            "latency_p95_s": round(_pct(lat, 0.95), 2),
            "completion_tokens_mean": round(
                statistics.mean(answers[i].completion_tokens for i in ids), 1
            ),
            **_evidence_stats([items[i] for i in ids], answers),
        }

    summary = {name: stats(ids) for name, ids in sorted(groups.items())}
    methods = Counter(g.method for g in grades.values())
    summary["_grading"] = dict(methods)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def _evidence_stats(items: list[Item], answers: dict[str, Answer]) -> dict:
    """Retrieval hit: a gold page among retrieved chunks. Citation hit: among answers that cite."""
    scored = [i for i in items if i.evidence and answers[i.id].retrieved]
    if not scored:
        return {}
    citing = [i for i in scored if answers[i.id].cited]
    return {
        "retrieval_hit": round(
            sum(evidence_hits(i, answers[i.id].retrieved) for i in scored) / len(scored), 4
        ),
        "citing": len(citing),
        "citation_hit": round(
            sum(evidence_hits(i, answers[i.id].cited) for i in citing) / len(citing), 4
        )
        if citing
        else None,
    }


def compare(a_dir: Path, b_dir: Path, seed: int = 0) -> dict:
    """Paired bootstrap of B minus A accuracy per suite, on the questions both runs answered."""
    a_items, a_ans, a_g = _load(a_dir)
    _, b_ans, b_g = _load(b_dir)
    by_suite: dict[str, list[str]] = defaultdict(list)
    for i in a_items.values():
        if i.id in b_g:
            by_suite[i.suite].append(i.id)

    rng = random.Random(seed)
    out = {}
    for suite, ids in sorted(by_suite.items()):
        diffs = [(b_g[i].grade == "correct") - (a_g[i].grade == "correct") for i in ids]
        boots = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(BOOTSTRAP))
        a_lat = [a_ans[i].latency_s for i in ids if not a_ans[i].error]
        b_lat = [b_ans[i].latency_s for i in ids if not b_ans[i].error]
        out[suite] = {
            "n": len(ids),
            "a_accuracy": round(statistics.fmean(a_g[i].grade == "correct" for i in ids), 4),
            "b_accuracy": round(statistics.fmean(b_g[i].grade == "correct" for i in ids), 4),
            "diff": round(statistics.fmean(diffs), 4),
            "ci95": [round(_pct(boots, 0.025), 4), round(_pct(boots, 0.975), 4)],
            "flips": {
                "gained": sum(d == 1 for d in diffs),
                "lost": sum(d == -1 for d in diffs),
            },
            "a_latency_p50_s": round(_pct(a_lat, 0.5), 2),
            "b_latency_p50_s": round(_pct(b_lat, 0.5), 2),
        }
    return out
