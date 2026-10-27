# Journey

Findings and friction, logged at the end of each stage. **Friction** marks something worth raising with the owning team. Eval numbers and A/B decisions go in [LOG.md](LOG.md).

## Stage 0a: model bring-up (2026-10-27)

**Result:** gpt-oss-20b (2 chips), embeddinggemma-2 and Laya serve at the same time through `tt serve`, which assigns chips itself.

- **Friction:** tt 1.0.1's catalog listed gpt-oss-20b for t3k and galaxy only, not QB2. We serve it as a community bundle, `jashansinghTT/gpt-oss-20b-blackhole-fast`. Finding the right bundle took `tt model search`, plus the team knowing which bundle is the fast one.
- **Friction:** `tt-hous/laya` doesn't serve as shipped. Its pull leaves out the repo's `multilingual/` folder, but the server checks for the full snapshot and runs offline, so it exits with code 3. Workaround: one `hf download` at the pinned revision ([SETUP.md](SETUP.md)). It should be fixed in the bundle.
- **Friction:** Kev-9B isn't in the community catalog listing, but `tt model search kev` finds `tt-hous/kev-9b`. The two discovery paths disagree.
- `tt-hous/laya-typed-decisions` is a specialist (invoices, security incidents, customer service, agent traces). On claim support it called a clearly supported claim "contradicted". Base Laya got 3 of 3 right at about 10 ms per call.
- Laya's API is custom (`/v1/systemone`: a state plus typed questions) and its documentation lives only in the model card. Each state is capped at 512 tokens, so passages must be chunked.
- First-pull boot times: gpt-oss 12.5 min, embedder 6 min, Laya 2.5 min. A cached restart of gpt-oss takes 5.4 min.
- Every serve needs `--yes`, because these are unverified community bundles.

## Stage 0b: skeleton (2026-10-27)

**Result:** `ttdr health` checks all 6 endpoints with real requests.

- Dropped LiteLLM. It needed a master key to boot (new in 1.104) and added nothing with only two OpenAI-compatible endpoints. It was also one more hop that could mangle gpt-oss tool calls. It's deferred until we need replicas, live A/B routing or many external clients.
- gpt-oss reasons before every answer, so even "OK" takes about 1.8 s. `reasoning_effort` needs to be chosen per step.

## Stage 1: eval harness and B0 (2026-10-27)

**Result:** `ttdr eval` builds pinned suites, runs a system, grades it (deterministic match, then an LLM judge) and compares runs with a paired bootstrap. B0 scores 12% on FinanceBench and 10% on SimpleQA. The noise floor is about ±5 points at n = 150 ([LOG.md](LOG.md)).

- **Friction (performance):** our first measurement of gpt-oss-20b fast (34 tok/s on one stream, 162 tok/s total) was **wrong by 5–9×**. The default sampling (`temperature=1.0`, no `top_k`) silently drops off the bundle's fused decode path. With `top_k ≤ 32` it runs at 135–170 tok/s on one stream and 970 tok/s total at 32 users. Nothing warns you: no error, no log line. The card's numbers are greedy. Every client (AI-Q, Hermes, TT-Studio) needs `top_k` set, or it gets a fifth of the speed.
- The bundle's `p150x2` (interactive) profile beats `p150x2-throughput` on a single stream (34 vs 20 tok/s) and ties it when batched. We switched to `p150x2`.
- The reasoning parser comes from the bundle manifest. `content` comes back clean, and `reasoning_effort` is honored (296 vs 1,790 reasoning tokens at low vs high).
- FinanceBench is CC BY-NC 4.0. It's approved for internal eval only; anything external needs legal review ([DATASETS.md](DATASETS.md)).
- Not yet done: AI-Q's `nat eval`. It's needed at Stage 4, for DeepResearch Bench.

## Stage 1.5: LLM A/B, Ornith vs gpt-oss (2026-10-27)

**Result:** with both models on their fast paths, Ornith 1.5 35B-A3B (thinking) ties gpt-oss-20b on accuracy, makes 37% fewer confident wrong answers, and has 3.5× the KV cache. gpt-oss decodes about 1.4× faster at the peak. We recommend Ornith ([LOG.md](LOG.md)). The 3.5–4.2× speed gap we first saw came from gpt-oss's sampling settings, not the models. The results were confirmed by an independent Claude judge.

- Bundle and **request settings** matter as much as model choice on Blackhole. The fused fast paths only cover some sampling configurations.
- With thinking off, Ornith answers in about 2 s but declines far more often. It could serve cheap steps such as routing and query rewriting, not answering.
- **The judge is now Claude:** blinded subagents grading an exported queue (`judge-export` / `judge-import`). It agrees with gpt-oss 88–94% of the time, and the conclusions held. They differ only on long-form answers, which ~30 hand-graded answers should settle.
- Added: recipe overlays (`extends`), a judge entry pinned in the recipe, `ttdr bench decode|tools`, and a model-agnostic reasoning control (`reasoning_effort` for gpt-oss, `enable_thinking` for Ornith).
- Per-model sampling now lives in the recipe (`sampling = {...}`), and every client inherits it, so fast-path settings can't be forgotten.
