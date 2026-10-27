# Datasets

Every eval set is pinned to a revision and has its licence recorded before we use it.

| Suite | Source | Revision | Licence | Use approved | Size used |
|---|---|---|---|---|---|
| `financebench` | [PatronusAI/financebench](https://huggingface.co/datasets/PatronusAI/financebench) | `e04404e` | CC BY-NC 4.0 | Internal eval only (2026-10-27). **Needs legal review before any external or commercial use.** | All 150 (open sample) |
| `simpleqa` | [basicv8vc/SimpleQA](https://huggingface.co/datasets/basicv8vc/SimpleQA) (mirror of OpenAI simple-evals) | `e319282` | MIT | Yes | 150 (seeded sample of 4,326) |

The suites are built locally with `ttdr eval build` into `data/suites/`, which is gitignored. Only the builder is committed. It pins the revision and seed, so every machine builds the same suites.

## Fields kept

- **FinanceBench:** question, answer, company and doc name, question type (metrics-generated, domain-relevant or novel-generated) and evidence pages. Evidence pages are kept for the citation-validity checks in later stages.
- **SimpleQA:** problem, answer, topic and answer type.
