"""Claim verification with the decision model: does a cited passage support the claim?

Laya reads at most 512 tokens per state, so a passage is checked in windows and the
strongest window decides.
"""

from dataclasses import dataclass

from ttdr.clients import Decider
from ttdr.corpus.chunk import split

WINDOW_CHARS = 1200
LABELS = ("supported", "contradicted", "not_enough_info")
QUESTION = {
    "support": {
        "type": "choice",
        "instructions": (
            "Does the passage support the claim? Treat rounding, units (million vs billion) and "
            "equivalent wording as the same fact."
        ),
        "criteria": {
            "supported": "The passage states the claim's facts, or they follow directly from it",
            "contradicted": "The passage states a different value or fact for the same thing",
            "not_enough_info": "The passage does not address what the claim says",
        },
    }
}


@dataclass
class Verdict:
    label: str
    supported: float
    contradicted: float
    window: int


def decide(probs: list[dict[str, float]], min_support: float = 0.5, min_contra: float = 0.5):
    """Supported if any window supports; contradicted if none does and one contradicts."""
    best_s = max(range(len(probs)), key=lambda i: probs[i]["supported"])
    best_c = max(range(len(probs)), key=lambda i: probs[i]["contradicted"])
    s, c = probs[best_s]["supported"], probs[best_c]["contradicted"]
    if s >= min_support:
        return Verdict("supported", s, c, best_s)
    if c >= min_contra:
        return Verdict("contradicted", s, c, best_c)
    return Verdict("not_enough_info", s, c, best_s)


class Verifier:
    def __init__(self, decider: Decider, min_support: float = 0.5, min_contra: float = 0.5):
        self.decider = decider
        self.min_support = min_support
        self.min_contra = min_contra

    async def probs(self, claim: str, passage: str) -> list[dict[str, float]]:
        windows = split(passage, WINDOW_CHARS, 200) or [""]
        states = [f"Passage: {w}\nClaim: {claim}" for w in windows]
        answers = await self.decider.ask_batch(states, QUESTION)
        return [a["support"]["probabilities"] for a in answers]

    async def check(self, claim: str, passage: str) -> Verdict:
        return decide(await self.probs(claim, passage), self.min_support, self.min_contra)
