"""A claim checker on the CPU: MiniCheck-Flan-T5-Large behind a small HTTP API (the `check` kind).

POST /check {"pairs": [{"passage": ..., "claim": ...}]} -> {"support": [p, ...]}, the probability
that each passage supports its claim. Scoring follows MiniCheck's reference inference: the input is
"predict: <passage></s><claim>", one decoder step, softmax over the tokens for "0" and "1". The
passage is cut to fit, never the claim.

Run with `uv run --extra checker python -m ttdr.checker --port 8003`.
"""

import asyncio
import threading

import torch
import typer
import uvicorn
from huggingface_hub import snapshot_download
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

MODEL = "lytang/MiniCheck-Flan-T5-Large"
REVISION = "96eafd01cee2d16cf81aaa2fb226b14f422a37b3"
MAX_TOKENS = 2048
LABEL_TOKENS = [3, 209]  # "0", "1"
BATCH = 8


class MiniCheck:
    def __init__(self):
        path = snapshot_download(
            MODEL, revision=REVISION, allow_patterns=["*.json", "*.bin", "spiece.model"]
        )
        self.tok = AutoTokenizer.from_pretrained(path)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(path).eval()
        self.lock = threading.Lock()

    def _ids(self, passage: str, claim: str) -> list[int]:
        claim_ids = self.tok(claim).input_ids
        budget = MAX_TOKENS - len(claim_ids) - 1
        doc = self.tok("predict: " + passage, add_special_tokens=False).input_ids[:budget]
        return [*doc, self.tok.eos_token_id, *claim_ids]

    def support(self, pairs: list[tuple[str, str]]) -> list[float]:
        out: list[float] = []
        with self.lock, torch.inference_mode():
            for i in range(0, len(pairs), BATCH):
                ids = [self._ids(p, c) for p, c in pairs[i : i + BATCH]]
                x = self.tok.pad({"input_ids": ids}, return_tensors="pt")
                start = torch.zeros((len(ids), 1), dtype=torch.long)
                logits = self.model(**x, decoder_input_ids=start).logits[:, 0, LABEL_TOKENS]
                out += logits.softmax(-1)[:, 1].tolist()
        return out


def app(checker: MiniCheck) -> Starlette:
    async def check(request: Request) -> JSONResponse:
        pairs = [(p["passage"], p["claim"]) for p in (await request.json())["pairs"]]
        return JSONResponse({"support": await asyncio.to_thread(checker.support, pairs)})

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"model": MODEL, "revision": REVISION})

    return Starlette(routes=[Route("/check", check, methods=["POST"]), Route("/health", health)])


def main(host: str = "0.0.0.0", port: int = 8003) -> None:
    uvicorn.run(app(MiniCheck()), host=host, port=port)


if __name__ == "__main__":
    typer.run(main)
