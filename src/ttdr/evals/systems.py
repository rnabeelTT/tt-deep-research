import time
from dataclasses import dataclass
from typing import Protocol

from ttdr.clients import Clients, reasoning_params
from ttdr.evals.schema import Answer, Item


class System(Protocol):
    async def answer(self, item: Item, clients: Clients) -> Answer: ...


@dataclass
class B0:
    """Closed book: the LLM alone, no retrieval. The floor every later stage must beat."""

    reasoning_effort: str | None = "medium"
    thinking: bool | None = None
    max_tokens: int = 4096
    prompt: str = (
        "Answer the question concisely and directly. "
        'If you are not confident in the answer, say "I don\'t know".'
    )

    async def answer(self, item: Item, clients: Clients) -> Answer:
        start = time.perf_counter()
        r = await clients.llm.chat(
            [
                {"role": "system", "content": self.prompt},
                {"role": "user", "content": item.question},
            ],
            max_tokens=self.max_tokens,
            **reasoning_params(self.reasoning_effort, self.thinking),
        )
        usage, choice = r["usage"], r["choices"][0]
        return Answer(
            id=item.id,
            text=choice["message"]["content"] or "",
            finish_reason=choice.get("finish_reason"),
            latency_s=time.perf_counter() - start,
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            reasoning_tokens=(usage.get("completion_tokens_details") or {}).get(
                "reasoning_tokens", 0
            ),
        )


SYSTEMS: dict[str, type] = {"b0": B0}
