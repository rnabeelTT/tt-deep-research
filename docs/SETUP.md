# Setup

## Prerequisites

- QB2 (4× p300c, detected as `p300x2`), Docker with Compose, uv
- tt ≥ 1.2.1 (`tt self update --yes`)

## Serve the models

tt assigns chips itself, so start the models one at a time, largest first. All three are unverified community bundles, which is why each command needs `--yes`.

| Port | Model | Bundle | Chips | Ready in (first pull) |
|---|---|---|---|---|
| 8000 | gpt-oss-20b | `jashansinghTT/gpt-oss-20b-blackhole-fast`, profile `p150x2-throughput` | 2 | 12m 33s |
| 8001 | embeddinggemma-2 | `tt-hous/embeddinggemma-2` | 1 | 5m 56s |
| 8002 | Laya | `tt-hous/laya` | 1 | 2m 30s |

```bash
tt serve jashansinghTT/gpt-oss-20b-blackhole-fast --yes --port 8000 -- --profile p150x2-throughput
tt serve tt-hous/embeddinggemma-2 --yes --port 8001
tt serve tt-hous/laya --yes --port 8002
```

**Laya workaround.** The bundle's pull leaves out the repo's `multilingual/` folder, but the server checks for the full snapshot and runs offline, so it exits with code 3. Fetch the full snapshot once, before the first serve:

```bash
hf download convaiinnovations/laya --revision 7b928d828b7b0e022f929d9bd2e44165aa270148 \
  --cache-dir /opt/tenstorrent/models/hub
```

To stop a model, run `tt model stop <bundle>`. To see its logs, run `tt model logs <bundle> --follow`.

## Smoke tests

```bash
# LLM
curl -s localhost:8000/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model":"openai/gpt-oss-20b","messages":[{"role":"user","content":"What is a 10-K?"}],"max_tokens":200}'

# Embeddings (768-dim; Matryoshka 512/256/128 also available)
curl -s localhost:8001/v1/embeddings -H 'content-type: application/json' \
  -d '{"model":"google/embeddinggemma-2","input":["query","passage"]}'

# Decision model: a state plus named questions. Types are noul (0..1), choice (criteria) or score.
curl -s localhost:8002/v1/systemone -H 'content-type: application/json' -d '{
  "state": "Passage: ...\nClaim: ...",
  "questions": {
    "support":  {"type": "choice", "instructions": "Does the passage support the claim?",
                 "criteria": {"supported": "...", "contradicted": "...", "not_enough_info": "..."}},
    "relevant": {"type": "noul", "instructions": "Is the passage relevant to the claim?"}
  }}'
```

Laya also has `/v1/systemone/batch` (many states, shared forward passes) and `/v1/forward` (raw tensors). Each state is capped at 512 tokens, so passages must be chunked to fit. Every question shares a 256-token head budget.

## Measured at bring-up (single request, warm)

| Endpoint | Latency |
|---|---|
| LLM, "Say OK.", 32 max tokens | 1.7 s |
| LLM, one-sentence answer (88 tokens, 39 of them reasoning) | 5.1 s |
| Embeddings, 3 short inputs | 19 ms |
| Laya, 2 questions, ~160 tokens | 10 ms |

## Alternatives

- Don't use `tt-hous/laya-typed-decisions`. It's a specialist for invoices, security, customer service and agent traces, and it got 2 of 3 claim-support checks right (base Laya got 3 of 3).
- The decision model can be `tt-hous/kev-9b` (Qwen3.5-9B base, one chip, port 8008) instead of Laya. Which one we keep is decided at Stage 6.
- gpt-oss-20b also has an interactive `p150x2` profile. It's an A/B candidate against `p150x2-throughput`.
