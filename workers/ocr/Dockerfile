# Lazy OCR worker: PaddleOCR-VL on the CPU (or --backend llm for a vision model on the chips).
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.0 /uv /usr/local/bin/uv
WORKDIR /opt/ocr
ENV UV_PROJECT_ENVIRONMENT=/opt/ocr/.venv UV_LINK_MODE=copy
COPY pyproject.toml worker.py ./
RUN uv sync --extra paddle --no-dev
ENV PATH=/opt/ocr/.venv/bin:$PATH
CMD ["ttdr-ocr", "--backend", "paddle"]
