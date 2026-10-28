"""Triage: rank candidate passages by embedding, then rerank the top ones with the decision model.

The final score blends the min-max-normalized embedding score with the decision model's
usefulness. Laya alone ranks worse than embeddings (recall@10 75% -> 48% on FinanceBench);
blended at 0.2 it is neutral, so triage is off by default (docs/LOG.md).
"""

from ttdr.clients import Decider

QUESTION = {
    "useful": {
        "type": "noul",
        "instructions": (
            "Does this passage contain information that helps answer the question "
            "(a fact, figure or statement the answer would use)?"
        ),
    }
}
OVERFETCH = 3


class Triage:
    def __init__(self, decider: Decider, window: int = 1200, weight: float = 0.2):
        self.decider = decider
        self.window = window
        self.weight = weight

    async def scores(self, question: str, texts: list[str]) -> list[float]:
        states = [f"Question: {question}\nPassage: {t[: self.window]}" for t in texts]
        answers = await self.decider.ask_batch(states, QUESTION)
        return [a["useful"]["noul"] for a in answers]

    async def rerank(self, question: str, items: list) -> list:
        """Items (with .text and .score) reordered by the blended score."""
        if len(items) < 2:
            return items
        useful = await self.scores(question, [i.text for i in items])
        emb = [i.score for i in items]
        lo, hi = min(emb), max(emb)
        blended = [
            (1 - self.weight) * (e - lo) / (hi - lo or 1) + self.weight * u
            for e, u in zip(emb, useful, strict=True)
        ]
        order = sorted(range(len(items)), key=lambda i: (-blended[i], i))
        return [items[i] for i in order]
