"""OCR worker: pops pages from Redis, renders them, OCRs them, caches the text.

Backends: `paddle` (PaddleOCR-VL on the CPU, the default: keeps the chips for research) or
`llm` (any OpenAI-compatible vision model, e.g. Ornith, for an idle box).
"""

import argparse
import base64
import io
import json
import logging
import os
import time

import httpx
import pypdfium2 as pdfium
import redis

QUEUE, PENDING, TEXT = "ocr:queue", "ocr:pending", "ocr:text:{page}"
PROCESSING = "ocr:processing"  # jobs taken but not finished; requeued if the worker restarts
MAX_ATTEMPTS = 3
PROMPT = (
    "Transcribe all text on this page as Markdown. Keep tables as Markdown tables. "
    "Output only the transcription."
)
log = logging.getLogger("ttdr-ocr")


def render(pdf: str, page: int, scale: float = 2.0) -> bytes:
    doc = pdfium.PdfDocument(pdf)
    try:
        buf = io.BytesIO()
        doc[page].render(scale=scale).to_pil().save(buf, format="PNG")
        return buf.getvalue()
    finally:
        doc.close()


class Paddle:
    def __init__(self):
        from paddleocr import PaddleOCRVL

        self.model = PaddleOCRVL(device="cpu")

    def __call__(self, png: bytes) -> str:
        import numpy as np
        from PIL import Image

        image = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
        return "\n".join(r.markdown["markdown_texts"] for r in self.model.predict(image))


class LLM:
    def __init__(self, url: str, model: str):
        self.url, self.model = url, model

    def __call__(self, png: bytes) -> str:
        img = base64.b64encode(png).decode()
        r = httpx.post(
            f"{self.url}/chat/completions",
            timeout=900,
            json={
                "model": self.model,
                "max_tokens": 4096,
                "temperature": 0,
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}},
                    {"type": "text", "text": PROMPT},
                ]}],
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"] or ""


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--redis", default=os.environ.get("TTDR_REDIS", "redis://localhost:6379"))
    env = os.environ.get
    p.add_argument("--backend", choices=["paddle", "llm"], default=env("TTDR_OCR_BACKEND", "paddle"))
    p.add_argument("--llm-url", default=env("TTDR_OCR_URL", "http://localhost:8000/v1"))
    p.add_argument("--llm-model", default=env("TTDR_OCR_MODEL", "ornith-ai/Ornith-1.5-35B-A3B"))
    p.add_argument("--corpus-dir", default=os.environ.get("TTDR_CORPUS_DIR", "data/corpus"))
    p.add_argument("--once", action="store_true", help="Exit when the queue is empty")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    r = redis.Redis.from_url(args.redis, decode_responses=True, socket_timeout=60)
    ocr = Paddle() if args.backend == "paddle" else LLM(args.llm_url, args.llm_model)
    requeued = 0
    while r.lmove(PROCESSING, QUEUE, "LEFT", "RIGHT"):
        requeued += 1
    log.info("ready (%s), %d unfinished jobs requeued", args.backend, requeued)
    while True:
        raw = r.blmove(QUEUE, PROCESSING, 5, "RIGHT", "LEFT")
        if raw is None:
            if args.once:
                return
            continue
        work(r, ocr, args.corpus_dir, raw)
        r.lrem(PROCESSING, 1, raw)


def work(r, ocr, corpus_dir: str, raw: str) -> None:
    """OCR one job; on failure requeue it, up to MAX_ATTEMPTS."""
    job = json.loads(raw)
    key = f"{job['doc']}#p{job['page']}"
    pdf = os.path.join(corpus_dir, job["corpus"], f"{job['doc']}.pdf")
    start = time.perf_counter()
    try:
        text = ocr(render(pdf, job["page"]))
        r.set(TEXT.format(page=key), text)
        r.srem(PENDING, key)
        log.info("%s: %d chars in %.0fs", key, len(text), time.perf_counter() - start)
    except Exception:
        log.exception("%s failed (attempt %d)", key, job.get("attempt", 0) + 1)
        if job.get("attempt", 0) + 1 < MAX_ATTEMPTS:
            r.rpush(QUEUE, json.dumps({**job, "attempt": job.get("attempt", 0) + 1}))
        else:
            r.srem(PENDING, key)


if __name__ == "__main__":
    main()
