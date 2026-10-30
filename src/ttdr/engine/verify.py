"""Claim verification with the decision model: does a cited passage support the claim?

Laya reads at most 512 tokens per state, so a passage is checked in windows and the
strongest window decides. A claim checker (MiniCheck) scores support only, so it never
reports a contradiction.
"""

from dataclasses import dataclass

from ttdr.clients import Checker, Decider
from ttdr.corpus.chunk import split

WINDOW_CHARS = 1200
MAX_WINDOW_CHARS = 24_000
CHARS_PER_TOKEN = 3
QUESTION_TOKENS = 150
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


def window_chars(max_state_tokens: int | None) -> int:
    """Passage window that fits the model's state limit, leaving room for the claim."""
    if not max_state_tokens:
        return WINDOW_CHARS
    return min(MAX_WINDOW_CHARS, (max_state_tokens - QUESTION_TOKENS) * CHARS_PER_TOKEN)


def from_recipe(recipe, decider) -> "Verifier":
    """The recipe's decide model as a verifier; verification needs one."""
    if decider is None or recipe.decide is None:
        raise RuntimeError("the recipe has no decide role, so claims can't be verified")
    return Verifier(decider, window=window_chars(recipe.decide.max_state_tokens))


class Verifier:
    def __init__(
        self,
        decider: Decider | Checker,
        min_support: float = 0.5,
        min_contra: float = 0.5,
        window: int = WINDOW_CHARS,
    ):
        self.decider = decider
        self.min_support = min_support
        self.min_contra = min_contra
        self.window = window

    async def probs(self, claim: str, passage: str) -> list[dict[str, float]]:
        windows = split(passage, self.window, 200) or [""]
        if isinstance(self.decider, Checker):
            support = await self.decider.support([(w, claim) for w in windows])
            return [
                {"supported": s, "contradicted": 0.0, "not_enough_info": 1 - s} for s in support
            ]
        states = [f"Passage: {w}\nClaim: {claim}" for w in windows]
        answers = await self.decider.ask_batch(states, QUESTION)
        return [a["support"]["probabilities"] for a in answers]

    async def check(self, claim: str, passage: str) -> Verdict:
        return decide(await self.probs(claim, passage), self.min_support, self.min_contra)
