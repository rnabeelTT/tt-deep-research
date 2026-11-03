import ast
import csv
import json
import random
import re
from collections.abc import Callable
from pathlib import Path

import httpx
import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download

from ttdr import jsonl
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
    rows = map(json.loads, jsonl.lines(_fetch_github(DRB[0], path, DRB[1])))
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
    for row in map(json.loads, jsonl.lines(path)):
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


FRAMES = ("google/frames-benchmark", "58d9fb6330f3ab1316d1eca12e5e8ef23dcc22ef")


def frames(n: int = 150) -> list[Item]:
    """FRAMES (Google, Apache-2.0): multi-hop questions answerable from Wikipedia; a fixed sample.
    Tagged with the first reasoning type (multiple constraints, numerical, temporal, tabular...)."""
    rows = list(csv.DictReader(_fetch(FRAMES[0], "test.tsv", FRAMES[1]).open(), delimiter="\t"))
    picked = sorted(random.Random(SEED).sample(range(len(rows)), n))
    return [
        Item(
            id=f"frames_{i:03d}",
            suite="frames",
            question=rows[i]["Prompt"],
            answer=rows[i]["Answer"],
            tags={"type": rows[i]["reasoning_types"].split("|")[0].strip()},
        )
        for i in picked
    ]


HLE = ("cais/hle", "5a81a4c7271a2a2a312b9a690f0c2fde837e4c29")


def hle(n: int = 150) -> list[Item]:
    """Humanity's Last Exam (gated; internal use, don't redistribute): a fixed sample of the
    text-only questions (2,158 of 2,500 have no image)."""
    columns = ["id", "question", "image", "answer", "answer_type", "category"]
    rows = pq.read_table(
        _fetch(HLE[0], "data/test-00000-of-00001.parquet", HLE[1]), columns=columns
    )
    text = [r for r in rows.to_pylist() if not r["image"]]
    return [
        Item(
            id=f"hle_{r['id']}",
            suite="hle",
            question=r["question"],
            answer=r["answer"],
            tags={"category": r["category"], "answer_type": r["answer_type"]},
        )
        for r in sorted(random.Random(SEED).sample(text, n), key=lambda r: r["id"])
    ]


UNIDOC = ("Salesforce/UniDoc-Bench", "2e22c592438d1e52c39bd769aab2901d596af746")
UNIDOC_PER_TYPE = 25
PAGE_PNG = re.compile(r"images/\w+/(\w+)/\1_page_(\d+)\.png")


def unidoc() -> list[Item]:
    """UniDoc-Bench, energy and construction: per domain 25 questions per answer type (text only,
    table required, image only, image plus text), against both domains' ~1,490 PDFs. Gold pages
    come from the page images (`_page_0001` is page 0)."""
    return [i for d in ("energy", "construction") for i in _unidoc_domain(d)]


def _unidoc_domain(domain: str) -> list[Item]:
    import pyarrow.parquet as pq

    path = _fetch(UNIDOC[0], f"data/{domain}-00000-of-00001.parquet", UNIDOC[1])
    rows = pq.read_table(path).to_pylist()
    rng = random.Random(SEED)
    picked = []
    for t in sorted({r["answer_type"] for r in rows}):
        of_type = [i for i, r in enumerate(rows) if r["answer_type"] == t]
        picked += rng.sample(of_type, min(UNIDOC_PER_TYPE, len(of_type)))
    items = []
    for i in sorted(picked):
        r = rows[i]
        evidence = [
            Evidence(doc=d, page=int(n) - 1) for d, n in PAGE_PNG.findall(str(r["gt_image_paths"]))
        ]
        items.append(
            Item(
                id=f"unidoc_{domain}_{i:03d}",
                suite="unidoc",
                question=r["question"],
                answer=r["answer"],
                tags={
                    "domain": domain,
                    "answer_type": r["answer_type"],
                    "type": r["question_type"],
                    "doc": evidence[0].doc,
                },
                evidence=evidence,
            )
        )
    return items


def dev(suite: str, n: int, strata: tuple[str, ...], seed: int = 0) -> Callable[[], list[Item]]:
    """A fixed sample for quick A/Bs: n items spread evenly over the strata's tag combinations.
    Items keep their suite, so they search the same corpus."""

    def build_dev() -> list[Item]:
        groups: dict[tuple, list[Item]] = {}
        for i in load(suite):
            groups.setdefault(tuple(i.tags.get(k, "") for k in strata), []).append(i)
        rng = random.Random(seed)
        for g in groups.values():
            rng.shuffle(g)
        picked: list[Item] = []
        while len(picked) < n and any(groups.values()):
            for key in sorted(groups):
                if groups[key] and len(picked) < n:
                    picked.append(groups[key].pop())
        return sorted(picked, key=lambda i: i.id)

    return build_dev


BUILDERS: dict[str, Callable[[], list[Item]]] = {
    "financebench": financebench,
    "simpleqa": simpleqa,
    "deepresearch_bench": deepresearch_bench,
    "unidoc": unidoc,
    "frames": frames,
    "hle": hle,
    "financebench-dev": dev("financebench", 40, ("type",)),
    "unidoc-dev": dev("unidoc", 40, ("answer_type", "domain")),
    "deepresearch_bench-dev": dev("deepresearch_bench", 8, ("topic",)),
    "frames-dev": dev("frames", 40, ("type",)),
    "hle-dev": dev("hle", 40, ("category",)),
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
    return [Item.model_validate_json(line) for line in jsonl.lines(path)]
