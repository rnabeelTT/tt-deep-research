"""RACE grading for DeepResearch Bench, with Claude subagents as the judge.

Each task becomes one blinded file: the task, its weighted criteria, and the reports to score
(every run's report plus the benchmark's reference report) under shuffled letters. A judge
scores every criterion 0-10 for every report. A report's score is the criterion-weighted mean
per dimension, then the dimension-weighted total; RACE reports it relative to the reference,
target / (target + reference), so 0.5 means on par with the reference.
"""

import json
import random
import statistics
import string
from pathlib import Path

from ttdr import jsonl
from ttdr.evals.datasets import drb_file
from ttdr.evals.report import BOOTSTRAP, _pct
from ttdr.evals.schema import Answer

DIMENSIONS = ("comprehensiveness", "insight", "instruction_following", "readability")
REFERENCE = "reference"

RUBRIC = """# RACE judging

You are an expert reviewer of research reports. Each task file holds one research task, its
evaluation criteria grouped into four dimensions (comprehensiveness, insight,
instruction_following, readability), and several reports labelled A, B, C...

For every report, score every criterion from 0 to 10:
0-2 very poor, 3-4 poor, 5-6 average, 7-8 good, 9-10 excellent.

- Judge each report against the criterion and its explanation, comparing the reports with each
  other so the scores rank them consistently.
- Length is not quality: reward substance, specific evidence and analysis, not padding.
- Citation markers in any style ([1], [ID#p3.0], [W1a2b3c4d]) are fine; don't penalize the style.
- A missing or empty report scores 0 on everything.
- You can't check facts against the web; do mark down claims that are internally inconsistent or
  implausible.

Write one JSON line per task to your verdicts file:

{"task": "drb_51", "scores": {"A": {"comprehensiveness": [7, 6, ...], "insight": [...],
"instruction_following": [...], "readability": [...]}, "B": {...}, ...}}

Each list has one integer per criterion, in the order listed in the task file.
"""


def _criteria() -> dict[int, dict]:
    return drb_file("data/criteria_data/criteria.jsonl")


def _references() -> dict[int, str]:
    return {
        i: r["article"] for i, r in drb_file("data/test_data/cleaned_data/reference.jsonl").items()
    }


def _task_number(item_id: str) -> int:
    return int(item_id.removeprefix("drb_"))


def _answers(run_dir: Path) -> dict[str, Answer]:
    lines = jsonl.lines(run_dir / "answers.jsonl")
    return {a.id: a for a in map(Answer.model_validate_json, lines)}


def _task_file(item_id: str, prompt: str, criteria: dict, reports: dict[str, str]) -> str:
    parts = [f"# Task {item_id}\n\n{prompt}\n\n## Criteria\n"]
    for dim in DIMENSIONS:
        parts.append(f"\n### {dim}\n")
        for n, c in enumerate(criteria["criterions"][dim], 1):
            parts.append(f"{n}. **{c['criterion']}**: {c['explanation']}\n")
    for letter, text in reports.items():
        parts.append(f"\n\n---\n\n## Report {letter}\n\n{text.strip() or '(no report)'}\n")
    return "".join(parts)


def export(run_dirs: list[Path], out_dir: Path, per_chunk: int = 5, seed: int = 0) -> int:
    """One task file per task of the first run: every run's report for it plus the reference, under
    shuffled letters. A run without a report for a task (a competitor's missing one) is left out."""
    criteria, references = _criteria(), _references()
    runs = {str(r): _answers(r) for r in run_dirs}
    ids = sorted(runs[str(run_dirs[0])], key=_task_number)
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    keymap = {}
    for item_id in ids:
        n = _task_number(item_id)
        sources = [(r, a[item_id].text) for r, a in runs.items() if item_id in a]
        sources.append((REFERENCE, references[n]))
        rng.shuffle(sources)
        letters = string.ascii_uppercase[: len(sources)]
        keymap[item_id] = {letter: src for letter, (src, _) in zip(letters, sources, strict=True)}
        reports = {letter: text for letter, (_, text) in zip(letters, sources, strict=True)}
        text = _task_file(item_id, criteria[n]["prompt"], criteria[n], reports)
        (out_dir / f"task-{item_id}.md").write_text(text)
    chunks = [ids[i : i + per_chunk] for i in range(0, len(ids), per_chunk)]
    (out_dir / "chunks.json").write_text(json.dumps(chunks, indent=1))
    (out_dir / "keymap.json").write_text(json.dumps(keymap, indent=1))
    (out_dir / "rubric.md").write_text(RUBRIC)
    return len(ids)


def import_published(path: Path, out_dir: Path) -> int:
    """A system's published DRB reports (the benchmark repo's {id, prompt, article} lines) as a run,
    so race-export judges them beside ours with the same graders."""
    out_dir.mkdir(parents=True, exist_ok=True)
    answers = [
        Answer(id=f"drb_{r['id']}", text=r.get("article") or "", latency_s=0.0)
        for r in map(json.loads, jsonl.lines(path))
    ]
    (out_dir / "answers.jsonl").write_text("".join(a.model_dump_json() + "\n" for a in answers))
    (out_dir / "config.json").write_text(json.dumps({"system": "published", "source": str(path)}))
    return len(answers)


def total(scores: dict[str, list[float]], criteria: dict) -> dict[str, float]:
    """Criterion-weighted score per dimension and the dimension-weighted total."""
    out = {}
    for dim in DIMENSIONS:
        weights = [c["weight"] for c in criteria["criterions"][dim]]
        if len(scores[dim]) != len(weights):
            raise ValueError(f"{dim}: {len(scores[dim])} scores for {len(weights)} criteria")
        out[dim] = sum(w * s for w, s in zip(weights, scores[dim], strict=True)) / sum(weights)
    out["overall"] = sum(criteria["dimension_weight"][d] * out[d] for d in DIMENSIONS)
    return out


def relative(target: dict[str, float], reference: dict[str, float]) -> dict[str, float]:
    return {
        k: target[k] / (target[k] + reference[k]) if target[k] + reference[k] else 0.0
        for k in target
    }


def score(queue_dir: Path, judge: str) -> dict[str, dict]:
    """Merge verdicts-*.jsonl into RACE scores per run; writes race.json into each run."""
    criteria = _criteria()
    keymap = json.loads((queue_dir / "keymap.json").read_text())
    verdicts = {
        v["task"]: v["scores"]
        for path in sorted(queue_dir.glob("verdicts-*.jsonl"))
        for v in map(json.loads, jsonl.lines(path))
    }
    missing = set(keymap) - set(verdicts)
    if missing:
        raise ValueError(f"{len(missing)} tasks without verdicts, e.g. {sorted(missing)[:3]}")
    per_run: dict[str, dict[str, dict]] = {}
    for item_id, letters in keymap.items():
        c = criteria[_task_number(item_id)]
        totals = {src: total(verdicts[item_id][letter], c) for letter, src in letters.items()}
        for src, t in totals.items():
            if src != REFERENCE:
                per_run.setdefault(src, {})[item_id] = relative(t, totals[REFERENCE])
    results = {}
    for run, tasks in per_run.items():
        mean = {
            k: round(statistics.fmean(t[k] for t in tasks.values()), 4)
            for k in ("overall", *DIMENSIONS)
        }
        results[run] = {"judge": judge, "n": len(tasks), "mean": mean, "tasks": tasks}
        (Path(run) / "race.json").write_text(json.dumps(results[run], indent=2))
    return results


def compare(a: dict, b: dict, seed: int = 0) -> dict:
    """Paired bootstrap of B minus A on the RACE overall score."""
    ids = sorted(set(a["tasks"]) & set(b["tasks"]))
    diffs = [b["tasks"][i]["overall"] - a["tasks"][i]["overall"] for i in ids]
    rng = random.Random(seed)
    boots = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(BOOTSTRAP))
    return {
        "n": len(ids),
        "diff": round(statistics.fmean(diffs), 4),
        "ci95": [round(_pct(boots, 0.025), 4), round(_pct(boots, 0.975), 4)],
        "wins": sum(d > 0 for d in diffs),
        "losses": sum(d < 0 for d in diffs),
    }
