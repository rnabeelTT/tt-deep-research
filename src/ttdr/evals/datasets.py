import ast
import csv
import json
import random
from collections.abc import Callable
from pathlib import Path

import httpx
from huggingface_hub import hf_hub_download

from ttdr.evals.schema import Evidence, Item

SUITES_DIR = Path("data/suites")
SEED = 0


def _fetch(repo: str, filename: str, revision: str) -> Path:
    return Path(hf_hub_download(repo, filename, repo_type="dataset", revision=revision))


def _fetch_github(repo: str, path: str, revision: str) -> Path:
    out = SUITES_DIR / "raw" / repo.replace("/", "__") / revision / path
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://raw.githubusercontent.com/{repo}/{revision}/{path}"
        r = httpx.get(url, follow_redirects=True, timeout=120)
        r.raise_for_status()
        out.write_bytes(r.content)
    return out


DRB = ("Ayanami0730/deep_research_bench", "f2735b5c3636759c22f9e936c0232de8bf545b67")


def drb_file(path: str) -> dict[int, dict]:
    """A DeepResearch Bench data file (prompts, RACE criteria or reference reports) by task id."""
    rows = map(json.loads, _fetch_github(DRB[0], path, DRB[1]).read_text().splitlines())
    return {r["id"]: r for r in rows}


def deepresearch_bench() -> list[Item]:
    """The English half (tasks 51-100). Graded with RACE against the reference reports."""
    tasks = drb_file("data/prompt_data/query.jsonl")
    return [
        Item(
            id=f"drb_{t['id']}",
            suite="deepresearch_bench",
            question=t["prompt"],
            answer="",
            tags={"topic": t["topic"]},
        )
        for t in tasks.values()
        if t["language"] == "en"
    ]


def financebench() -> list[Item]:
    path = _fetch(
        "PatronusAI/financebench",
        "financebench_merged.jsonl",
        "e04404e3a97f69f79c14d42f24981a1c9c3bcd18",
    )
    items = []
    for row in map(json.loads, path.read_text().splitlines()):
        items.append(
            Item(
                id=row["financebench_id"],
                suite="financebench",
                question=row["question"],
                answer=row["answer"],
                tags={
                    "type": row["question_type"],
                    "company": row["company"],
                    "doc": row["doc_name"],
                },
                evidence=[
                    Evidence(doc=e["doc_name"], page=int(e["evidence_page_num"]))
                    for e in row["evidence"]
                ],
            )
        )
    return items


def simpleqa(n: int = 150) -> list[Item]:
    path = _fetch(
        "basicv8vc/SimpleQA", "simple_qa_test_set.csv", "e319282ab125c3dbd0c7fd00be2e4dd54e7e8f94"
    )
    rows = list(csv.DictReader(path.open()))
    picked = sorted(random.Random(SEED).sample(range(len(rows)), n))
    items = []
    for i in picked:
        meta = ast.literal_eval(rows[i]["metadata"])
        items.append(
            Item(
                id=f"simpleqa_{i:04d}",
                suite="simpleqa",
                question=rows[i]["problem"],
                answer=rows[i]["answer"],
                tags={"topic": meta["topic"], "answer_type": meta["answer_type"]},
            )
        )
    return items


BUILDERS: dict[str, Callable[[], list[Item]]] = {
    "financebench": financebench,
    "simpleqa": simpleqa,
    "deepresearch_bench": deepresearch_bench,
}


def build(name: str) -> Path:
    out = SUITES_DIR / f"{name}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(i.model_dump_json() + "\n" for i in BUILDERS[name]()))
    return out


def load(name: str) -> list[Item]:
    path = SUITES_DIR / f"{name}.jsonl"
    if not path.exists():
        build(name)
    return [Item.model_validate_json(line) for line in path.read_text().splitlines()]
