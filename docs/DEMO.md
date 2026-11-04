# Demo plan

A slide deck with screen recordings: what the QB2 deep-research stack does, how it works, and how it compares with published systems. Every number in the deck comes from a run in `runs/`, graded by blinded Claude judges, with its command and run ID in the appendix.

## Story

1. **The problem.** Deep research products (OpenAI, Gemini, Perplexity, Kimi, NVIDIA AI-Q) run in someone else's cloud. Ours runs on one QB2: four Blackhole chips, all models local, private documents never leave the box, and web search is optional and pluggable.
2. **What it does** (recording 1): a question over private filings and the web, then a cited report whose citations open the exact page.
3. **How it works:** the stack diagram (chips, engine, harness, UI) and the decisions behind it, each backed by an A/B from [LOG.md](LOG.md).
4. **Us vs them:** one slide per benchmark, our score next to published scores with sources and caveats.
5. **Transparency** (recording 2): the research panel showing the plan, each researcher, every search with its results, and the intent decision.
6. **Speed and cost:** time per report and per question, tokens, chips in use.
7. **What's next.**

## Benchmarks

Ours vs published numbers. "Fair?" records what differs between our run and theirs (judge, search backend, subset); each slide states it.

| Benchmark | What it tests | Our suite | Competitors with published numbers | Fair? / caveats | Search needed |
|---|---|---|---|---|---|
| DeepResearch Bench (RACE) | Long cited reports | `deepresearch_bench` (50 English tasks) | Gemini, OpenAI, Perplexity, Kimi, Claude Research and others publish their **reports**, which we re-judge with the same Claude graders | Same judge and criteria for everyone: the fairest comparison we have | Web (final run: Tavily) |
| FRAMES | Multi-hop factual questions (Wikipedia) | `frames` (150) | Google's paper baselines, and open agents (QUEST, MiroThinker, WebThinker…) | Their numbers use their own judges; we grade with Claude | Web (free search works) |
| HLE, text-only | Expert-level questions | `hle` (150) | OpenAI deep research, Perplexity, Gemini, Kimi, open agents | Subset of the text-only split; published numbers are on the full set | Web |
| BrowseComp-Plus | Hard browsing over a fixed corpus | `browsecomp-plus` (150), dev 40; `recipes/browsecomp-plus.toml` (offline) | QUEST, Tongyi, OpenResearcher, GPT-5 and others, on the same corpus | Same corpus, so retrieval is comparable. Needs the corpus indexed (several hours of embedder time) | None: fixed corpus |
| SimpleQA | Short factual questions | `simpleqa` (150) | OpenAI, Perplexity Sonar, others | Sample, not the full 4,326 | Web |
| FinanceBench | Questions over 10-K filings | `financebench` (150) | Patronus paper baselines (GPT-4 RAG etc.), vendor reports | Same open-book task | None |
| UniDoc-Bench | Questions over figures and tables in PDFs | `unidoc` (200) | The paper's RAG baselines | Same task; licence allows internal evaluation only | None |
| AI-Q vs Deep Agents, same stack | Harness choice | dev sets | NVIDIA AI-Q, run by us on our engine | Identical models and engine | — |
| GAIA, text-only | General assistant tasks | blocked: GAIA access not granted to this HF account | OpenAI, Manus, open agents | — | Web |

Not planned: BrowseComp (live web; most questions need many searches, which the Tavily free tier can't cover), xbench (Chinese), GAIA until access is granted.

## Runs, in order

1. **Lock the stack** after the current A/Bs (thinking on/off). Every final run uses one recipe and one commit.
2. **Offline suites first** (no search budget): FinanceBench 150, UniDoc 200, then BrowseComp-Plus once the corpus is indexed.
3. **Free-search suites:** FRAMES 150, SimpleQA 150 and HLE 150 on the free engines (Yandex + Bing News + OpenAlex).
4. **Final web runs on Tavily** (free tier, about 1,000 searches a month; see recipes/tavily.toml): DRB, 50 English tasks. Measure searches per task on 2 tasks first; if 50 tasks don't fit, run as many as fit plus the remaining tasks on free search, labelled.
5. **Competitor re-judging** for DRB: published reports from the DRB repo, graded by the same Claude judges with the same rubric.

## Recordings

| # | What | UI | Length |
|---|---|---|---|
| 1 | Private-document question (3M capex): answer, Sources, click a citation, page image opens | Deep Agents (:3000) | ~1 min |
| 2 | Web + documents report: research panel with the plan, researchers, every search and result | :3000 | ~2 min, sped up |
| 3 | Same question in both UIs side by side: AI-Q (:3001) vs Deep Agents (:3000) | both | ~1 min, sped up |
| 4 | The chips at work: `tt-smi` or the serving dashboards during a run | terminal | ~20 s |
| 5 | A figure-only answer: the model reading a page image (`read_page` in Tool calls) | :3000 | ~40 s |

Record at 1080p with a clean browser profile, after a warm-up query so prefix caches are hot, and keep the terminal font large.

## Open items

- Competitor numbers with sources: collected in [COMPETITORS.md](COMPETITORS.md) (2026-11-04). DRB's official judge changed to GPT-5.5 in May 2026, so leaderboard RACE scores (Gemini-2.5-Pro or GPT-5.5 judge) can't sit beside our Claude-judged ones; the re-judging step puts competitors' published reports and ours under one judge. Check Tongyi's numbers against the paper PDF before the slides.
- BrowseComp-Plus corpus indexing (lead chunk per page, 100k embeddings): after the thinking A/B.
- GAIA: request access on its Hub page.
- Decide the deck format (Slides artifact or Google Slides) and who presents.
