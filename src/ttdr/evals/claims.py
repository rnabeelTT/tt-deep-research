"""Claim-passage pairs for choosing and tuning the decision model (design doc: ~100 labeled pairs).

Built from FinanceBench, labeled by construction: the gold answer against its evidence page is
supported; the same answer with its first figure changed is contradicted; the gold answer
against a far-away page of the same filing is not_enough_info.
"""

import asyncio
import json
import random
import re
import time
from collections import Counter
from pathlib import Path

from ttdr.corpus import pdf
from ttdr.corpus.sources import CORPORA_DIR
from ttdr.engine.verify import LABELS, Verifier, decide

PAIRS = Path("data/suites/claims.jsonl")
NUMBER = re.compile(r"\d[\d,]*\.?\d*")


def _perturb(text: str, rng: random.Random) -> str | None:
    m = NUMBER.search(text)
    if not m:
        return None
    value = float(m.group().replace(",", "").rstrip("."))
    new = value * rng.choice([0.6, 0.75, 1.4, 1.8]) + 1
    shown = f"{new:,.2f}" if "." in m.group() else f"{round(new):,}"
    return text[: m.start()] + shown + text[m.end() :]


def build(n_questions: int = 40, seed: int = 0) -> Path:
    rng = random.Random(seed)
    items = [json.loads(x) for x in Path("data/suites/financebench.jsonl").read_text().splitlines()]
    items = [i for i in items if len(i["evidence"]) == 1 and len(i["answer"]) < 400]
    rng.shuffle(items)
    pages: dict[str, list[pdf.Page]] = {}
    pairs = []
    for item in items[:n_questions]:
        ev = item["evidence"][0]
        doc = pages.setdefault(ev["doc"], pdf.read(CORPORA_DIR / "financebench" / f"{ev['doc']}.pdf"))
        gold = doc[ev["page"]].text
        claim = item["answer"]
        far = [p for p in doc if abs(p.page - ev["page"]) > 5 and len(p.text) > 500]
        pairs.append({"claim": claim, "passage": gold, "label": "supported"})
        if wrong := _perturb(claim, rng):
            pairs.append({"claim": wrong, "passage": gold, "label": "contradicted"})
        if far:
            pairs.append({"claim": claim, "passage": rng.choice(far).text, "label": "not_enough_info"})
    PAIRS.write_text(
        "".join(
            json.dumps({"id": f"pair_{n:03d}", "question_of": "financebench", **p}) + "\n"
            for n, p in enumerate(pairs)
        )
    )
    return PAIRS


async def score(verifier: Verifier, concurrency: int = 8) -> dict:
    pairs = [json.loads(x) for x in PAIRS.read_text().splitlines()]
    sem = asyncio.Semaphore(concurrency)

    async def one(p):
        async with sem:
            start = time.perf_counter()
            probs = await verifier.probs(p["claim"], p["passage"])
            return probs, time.perf_counter() - start

    results = await asyncio.gather(*(one(p) for p in pairs))
    sweep = {}
    for s in (0.3, 0.4, 0.5, 0.6, 0.7):
        for c in (0.3, 0.5, 0.7):
            preds = [decide(probs, s, c).label for probs, _ in results]
            sweep[(s, c)] = sum(p == q["label"] for p, q in zip(preds, pairs, strict=True)) / len(pairs)
    best = max(sweep, key=sweep.get)
    preds = [decide(probs, *best).label for probs, _ in results]
    confusion = Counter((q["label"], p) for p, q in zip(preds, pairs, strict=True))
    lat = sorted(t for _, t in results)
    return {
        "n": len(pairs),
        "labels": dict(Counter(q["label"] for q in pairs)),
        "accuracy_default": sweep[(0.5, 0.5)],
        "best_thresholds": best,
        "accuracy_best": sweep[best],
        "per_label_recall": {
            lab: round(confusion[(lab, lab)] / max(1, sum(v for (g, _), v in confusion.items() if g == lab)), 3)
            for lab in LABELS
        },
        "confusion": {f"{g}->{p}": v for (g, p), v in sorted(confusion.items())},
        "latency_p50_ms": round(1000 * lat[len(lat) // 2]),
    }
