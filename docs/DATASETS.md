# Datasets

Every eval set is pinned to a revision and has its licence recorded before we use it.

| Suite | Source | Revision | Licence | Use approved | Size used |
|---|---|---|---|---|---|
| `financebench` | [PatronusAI/financebench](https://huggingface.co/datasets/PatronusAI/financebench) | `e04404e` | CC BY-NC 4.0 | Internal eval only (2026-10-27). **Needs legal review before any external or commercial use.** | All 150 (open sample) |
| `simpleqa` | [basicv8vc/SimpleQA](https://huggingface.co/datasets/basicv8vc/SimpleQA) (mirror of OpenAI simple-evals) | `e319282` | MIT | Yes | 150 (seeded sample of 4,326) |
| `deepresearch_bench` | [Ayanami0730/deep_research_bench](https://github.com/Ayanami0730/deep_research_bench) (prompts, RACE criteria, reference reports) | `f2735b5` | Apache-2.0 | Yes | The 50 English tasks (51–100) |

The suites are built locally with `ttdr eval build` into `data/suites/`, which is gitignored. Only the builder is committed. It pins the revision and seed, so every machine builds the same suites.

## Fields kept

- **FinanceBench:** question, answer, company and doc name, question type (metrics-generated, domain-relevant or novel-generated) and evidence pages. Evidence pages are kept for the citation-validity checks in later stages.
- **SimpleQA:** problem, answer, topic and answer type.
- **DeepResearch Bench:** prompt and topic. Grading (`ttdr eval race-export` / `race-import`) reads the per-task RACE criteria and the reference reports from the same pinned commit. Scores come from our Claude judge, not the paper's Gemini judge, so they aren't comparable with the public leaderboard.
