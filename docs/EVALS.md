# Evals

```bash
uv run ttdr eval build                                   # pinned suites → data/suites/*.jsonl
uv run ttdr eval run b0 --concurrency 32                 # answer → runs/<ts>-b0/ (no grading)
uv run ttdr eval run b0 -s simpleqa --limit 5 --grade    # quick check with the on-box judge
uv run ttdr eval judge-export runs/<A> runs/<B> --chunks 6   # blinded queue for Claude graders
uv run ttdr eval judge-import data/judge/queue --judge claude-opus-5-5
uv run ttdr eval compare runs/<A> runs/<B>               # paired bootstrap, B minus A
uv run ttdr eval retry runs/<A>                          # re-answer only the items that errored
uv run ttdr eval ab b1 --a recipe.toml --b recipes/x.toml -s financebench   # both runs + one blinded queue
uv run ttdr eval check runs/<A> --min financebench.accuracy=0.6             # CI floor; exit 1 below it
uv run ttdr eval verify-run runs/<A> --revise            # check cited claims; write <run>-revised to grade
uv run ttdr eval claims-build && uv run ttdr eval claims-run   # decision model on claim-passage pairs
```

## Systems

| System | What it answers with |
|---|---|
| `b0` | The LLM alone, closed book |
| `b1` | Naive RAG: top-k corpus chunks, cite evidence IDs |
| `b1mcp` | B1 with retrieval through the MCP `search` tool |
| `aiq` | AI-Q's job API (`-p agent=shallow_researcher` or `deep_researcher`) |
| `deepagents` | LangChain Deep Agents over the engine's MCP tools |

Every system reads the same recipe, so `--recipe recipes/<overlay>.toml` (or `ttdr eval ab`) compares configurations: another LLM, another decision model, a feature switched on.

## Pieces

| Module | Job |
|---|---|
| `evals/datasets.py` | Builds suites from pinned sources ([DATASETS.md](DATASETS.md)) |
| `evals/systems.py` | Systems under test. Each one is `answer(item, clients) -> Answer` and is registered in `SYSTEMS` |
| `evals/runner.py` | Runs items concurrently; writes `config.json` (system params, recipe, git sha), `items.jsonl` and `answers.jsonl` |
| `evals/grading.py` | Deterministic matching, the on-box judge, and the external-judge export and import |
| `evals/report.py` | Writes `summary.json` per suite and tag; `compare` does the paired bootstrap |

## Grading

1. **Deterministic first.** An exact match after normalizing case, punctuation and articles counts as correct. So does a numeric match within 1%, but only when the answer contains exactly one number.
2. **Claude judges everything else.** `judge-export` writes every undecided answer from the given runs into one queue. The queue is shuffled across runs and keyed by an opaque hash, so the grader can't tell which system wrote an answer. It's split into chunks, each graded by a Claude subagent against `rubric.md` (`A` correct, `B` incorrect, `C` not attempted). `judge-import` merges the verdicts back and records the judge in each run's `judge.json`.
3. **The on-box judge** (`--grade`, or `ttdr eval grade`) uses the recipe's `judge` role (the LLM if unset). It's only for quick checks and CI: the model would be grading its own family in model A/Bs.
4. **Grading is its own pass**, so any run can be re-graded without answering again. Per the design doc, the judge should still be checked against about 30 hand-graded answers.

## Comparing

`compare` scores both runs on the same questions (1 if correct, else 0) and bootstraps the per-question difference 10,000 times for a 95% CI. A change is kept if the CI is above zero and the gain beats the noise floor (the gap between two runs of the same config). See the design doc for the guardrails.
