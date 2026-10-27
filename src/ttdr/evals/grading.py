import asyncio
import hashlib
import json
import random
import re
import string
from pathlib import Path

import httpx

from ttdr.clients import LLM
from ttdr.evals.schema import Answer, Graded, Item
from ttdr.recipe import Recipe

NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")
GOLD_NUMBER = re.compile(r"^\s*\$?\s*(-?\d[\d,]*\.?\d*)\s*%?\s*$")

JUDGE_PROMPT = """You grade answers against a gold target.

Question: {question}
Gold target: {gold}
Predicted answer: {predicted}

Reply with one letter on the first line, then a one-sentence reason:
A = CORRECT: the predicted answer contains the gold target's essential facts and contradicts \
none of them. Formatting, rounding within 1%, and extra correct detail are fine.
B = INCORRECT: it states a wrong fact, contradicts the gold target, or hedges between \
several candidate answers.
C = NOT_ATTEMPTED: it gives no definitive answer (for example "I don't know") and does not \
contradict the gold target."""

LETTERS = {"A": "correct", "B": "incorrect", "C": "not_attempted"}


def _norm(s: str) -> str:
    s = s.lower().translate(str.maketrans("", "", string.punctuation))
    return " ".join(w for w in s.split() if w not in {"a", "an", "the"})


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def match(item: Item, ans: Answer) -> Graded | None:
    """Deterministic grade when unambiguous; None defers to the judge."""
    if ans.error:
        return Graded(id=ans.id, grade="incorrect", method="error", judge_reason=ans.error)
    if _norm(ans.text) == _norm(item.answer):
        return Graded(id=ans.id, grade="correct", method="exact")
    gold = GOLD_NUMBER.match(item.answer)
    found = {_num(n) for n in NUMBER.findall(ans.text) if any(c.isdigit() for c in n)}
    if gold and len(found) == 1:
        g, (a,) = _num(gold.group(1)), found
        if abs(a - g) <= max(abs(g) * 0.01, 1e-9):
            return Graded(id=ans.id, grade="correct", method="numeric")
    return None


async def _judge(llm: LLM, item: Item, ans: Answer) -> Graded:
    prompt = JUDGE_PROMPT.format(question=item.question, gold=item.answer, predicted=ans.text)
    for _ in range(2):
        r = await llm.chat(
            [{"role": "user", "content": prompt}], max_tokens=2048, reasoning_effort="medium"
        )
        text = (r["choices"][0]["message"]["content"] or "").strip()
        if text[:1] in LETTERS:
            return Graded(
                id=ans.id, grade=LETTERS[text[0]], method="judge", judge_reason=text[1:].strip()
            )
    return Graded(id=ans.id, grade="incorrect", method="error", judge_reason=f"unparsed: {text}")


def judge_info(recipe: Recipe) -> dict:
    return {"model": recipe.models.judge.model, "prompt_sha": judge_info_sha()}


async def _grade(items: dict[str, Item], answers: list[Answer], recipe: Recipe, concurrency: int):
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=1800) as http:
        j = recipe.models.judge
        llm = LLM(http, j.url, j.model, j.sampling)

        async def one(a: Answer) -> Graded:
            if g := match(items[a.id], a):
                return g
            async with sem:
                return await _judge(llm, items[a.id], a)

        return await asyncio.gather(*(one(a) for a in answers))


def grade(run_dir: Path, recipe: Recipe, concurrency: int = 16) -> Path:
    items = {i.id: i for i in _read(run_dir / "items.jsonl", Item)}
    answers = _read(run_dir / "answers.jsonl", Answer)
    graded = asyncio.run(_grade(items, answers, recipe, concurrency))
    (run_dir / "judge.json").write_text(json.dumps(judge_info(recipe), indent=2))
    out = run_dir / "grades.jsonl"
    out.write_text("".join(g.model_dump_json() + "\n" for g in graded))
    return out


def export_queue(run_dirs: list[Path], out_dir: Path, chunks: int, seed: int = 0) -> int:
    """Blind queue for an external judge: undecided answers from every run, shuffled, opaque keys."""
    queue, keymap = [], {}
    for run_dir in run_dirs:
        items = {i.id: i for i in _read(run_dir / "items.jsonl", Item)}
        for a in _read(run_dir / "answers.jsonl", Answer):
            if match(items[a.id], a) is None:
                key = hashlib.sha256(f"{run_dir.name}/{a.id}".encode()).hexdigest()[:12]
                keymap[key] = [str(run_dir), a.id]
                q = items[a.id]
                queue.append(
                    {"key": key, "question": q.question, "gold": q.answer, "predicted": a.text}
                )
    random.Random(seed).shuffle(queue)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "keymap.json").write_text(json.dumps(keymap))
    (out_dir / "rubric.md").write_text(JUDGE_PROMPT)
    size = -(-len(queue) // chunks)
    for n in range(chunks):
        part = queue[n * size : (n + 1) * size]
        (out_dir / f"queue-{n:02d}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in part))
    return len(queue)


def import_verdicts(queue_dir: Path, judge: str) -> list[Path]:
    """Merge verdict-*.jsonl ({key, grade: A|B|C, reason}) with deterministic grades, per run."""
    keymap = json.loads((queue_dir / "keymap.json").read_text())
    verdicts = {}
    for path in sorted(queue_dir.glob("verdicts-*.jsonl")):
        for v in map(json.loads, path.read_text().splitlines()):
            verdicts[v["key"]] = v
    missing = set(keymap) - set(verdicts)
    if missing:
        raise ValueError(
            f"{len(missing)} queued answers have no verdict, e.g. {sorted(missing)[:3]}"
        )
    by_run: dict[str, dict[str, dict]] = {}
    for key, (run, item_id) in keymap.items():
        by_run.setdefault(run, {})[item_id] = verdicts[key]
    info = {
        "model": judge,
        "prompt_sha": judge_info_sha(),
        "via": "external",
        "queue": str(queue_dir),
    }
    out = []
    for run, judged in by_run.items():
        run_dir = Path(run)
        items = {i.id: i for i in _read(run_dir / "items.jsonl", Item)}
        graded = []
        for a in _read(run_dir / "answers.jsonl", Answer):
            g = match(items[a.id], a)
            if g is None:
                v = judged[a.id]
                g = Graded(
                    id=a.id, grade=LETTERS[v["grade"]], method="judge", judge_reason=v["reason"]
                )
            graded.append(g)
        (run_dir / "grades.jsonl").write_text("".join(g.model_dump_json() + "\n" for g in graded))
        (run_dir / "judge.json").write_text(json.dumps(info, indent=2))
        out.append(run_dir)
    return out


def judge_info_sha() -> str:
    return hashlib.sha256(JUDGE_PROMPT.encode()).hexdigest()[:12]


def _read(path: Path, model):
    return [model.model_validate_json(line) for line in path.read_text().splitlines()]
