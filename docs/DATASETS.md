# Datasets

Every eval set is pinned to a revision and has its licence recorded before we use it.

| Suite | Source | Revision | Licence | Use approved | Size used |
|---|---|---|---|---|---|
| `financebench` | [PatronusAI/financebench](https://huggingface.co/datasets/PatronusAI/financebench) | `e04404e` | CC BY-NC 4.0 | Internal eval only (2026-10-27). **Needs legal review before any external or commercial use.** | All 150 (open sample) |
| `simpleqa` | [basicv8vc/SimpleQA](https://huggingface.co/datasets/basicv8vc/SimpleQA) (mirror of OpenAI simple-evals) | `e319282` | MIT | Yes | 150 (seeded sample of 4,326) |
| `unidoc` | [Salesforce/UniDoc-Bench](https://huggingface.co/datasets/Salesforce/UniDoc-Bench) (QA parquet and domain PDF archives) | `2e22c59` | CC BY-NC 4.0; the card adds that the data was generated with GPT-4.1 and must not be used to develop models that compete with OpenAI | Internal eval only (2026-10-31); we evaluate, never train on it. **Needs legal review before any external or commercial use.** | 200: energy and construction, 25 per answer type each, against both domains' 1,491 PDFs |
| `deepresearch_bench` | [Ayanami0730/deep_research_bench](https://github.com/Ayanami0730/deep_research_bench) (prompts, RACE criteria, reference reports) | `f2735b5` | Apache-2.0 | Yes | The 50 English tasks (51–100) |

The suites are built locally with `ttdr eval build` into `data/suites/`, which is gitignored. Only the builder is committed. It pins the revision and seed, so every machine builds the same suites.

## Fields kept

- **FinanceBench:** question, answer, company and doc name, question type (metrics-generated, domain-relevant or novel-generated) and evidence pages. Evidence pages are kept for the citation-validity checks in later stages.
- **SimpleQA:** problem, answer, topic and answer type.
- **UniDoc-Bench:** question, answer, domain, question type, answer type (text only, table required, image only, image plus text) and gold pages, from the gold page-image paths (`<doc>_page_0001.png` is page 0). The corpus is `ttdr corpus fetch unidoc`: regular PDF files only from the archives, flattened to their names.
- **DeepResearch Bench:** prompt and topic. Grading (`ttdr eval race-export` / `race-import`) reads the per-task RACE criteria and the reference reports from the same pinned commit. Scores come from our Claude judge, not the paper's Gemini judge, so they aren't comparable with the public leaderboard.
