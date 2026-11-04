# Competitor scores: published numbers for the demo comparison

Collected 2026-11-04. I opened every URL listed below. A score is marked **unverified** when I could not open its primary source (for example, openai.com returned 403) and it rests on a secondary report or a citation in another paper. "LB" means leaderboard. The DRB and BrowseComp-Plus leaderboard rows were read straight from the CSV files behind the official HF spaces.

---

## 1. DeepResearch Bench (DRB I; Du et al. 2025, arXiv 2506.11763)

The benchmark has 100 PhD-level tasks. RACE scores report quality relative to a reference report, on a 0-100 scale. FACT reports C.Acc (citation accuracy, %) and E.Cit (effective, i.e. supported, citations per task).

### 1a. Paper, Table 1 (RACE judge Gemini-2.5-Pro; FACT judge Gemini-2.5-Flash)

Source: https://arxiv.org/html/2506.11763 (v1, June 2025)

| System | RACE | C.Acc % | E.Cit | Subset | Judge | Date |
|---|---|---|---|---|---|---|
| Gemini-2.5-Pro Deep Research | 48.88 | 81.44 | 111.21 | full 100 | Gemini-2.5-Pro / 2.5-Flash | 2025-06 |
| OpenAI Deep Research | 46.98 | 77.96 | 40.79 | full 100 | same | 2025-06 |
| Perplexity Deep Research | 42.25 | 90.24 | 31.26 | full 100 | same | 2025-06 |
| Grok Deeper Search | 40.24 | 83.59 | 8.15 | full 100 | same | 2025-06 |
| Claude-3.7-Sonnet w/ Search | 40.67 | 93.68 | 32.48 | full 100 | same | 2025-06 |
| Perplexity Sonar-Reasoning-Pro (high) | 40.22 | 39.36 | 8.35 | full 100 | same | 2025-06 |
| Gemini-2.5-Pro Grounding | 35.12 | 81.81 | 32.88 | full 100 | same | 2025-06 |
| GPT-4o-Search-Preview (high) | 35.10 | 88.41 | 4.79 | full 100 | same | 2025-06 |

### 1b. Official HF leaderboard, Gemini-2.5-Pro judge board (the legacy board)

Source: https://huggingface.co/spaces/muset-ai/DeepResearch-Bench-Leaderboard, file `data/leaderboard.csv`, space last modified 2026-11-03. The leaderboard re-scored the paper systems, so its numbers differ slightly from the paper. FACT is blank ("-") for most newer entries.

| System | RACE | C.Acc % | E.Cit | Judge | Source |
|---|---|---|---|---|---|
| qianfan_deepresearch_0430 (Baidu), #1 | 58.03 | - | - | Gemini-2.5-Pro | HF LB csv |
| ZTE-Nebula-DeepResearch | 57.27 | - | - | same | HF LB csv |
| zhipu_deep_research | 57.06 | - | - | same | HF LB csv |
| cellcog-max | 56.67 | - | - | same | HF LB csv |
| **nvidia-aiq-nemotron-gpt52-updated (NVIDIA AI-Q, 2026)** | **55.95** | - | - | same | HF LB csv |
| onyx | 54.54 | - | - | same | HF LB csv |
| tavily-research | 52.44 | - | - | same | HF LB csv |
| salesforce-air-deep-research | 50.65 | - | - | same | HF LB csv |
| gensee-search-gpt-5 | 50.60 | 32.94 | 21.06 | same | HF LB csv |
| **gemini-2.5-pro-deepresearch** | **49.71** | 78.30 | 165.34 | same | HF LB csv |
| langchain-open-deep-research-gpt-5 | 49.33 | 34.74 | 22.44 | same | HF LB csv |
| **openai-deepresearch** | **46.45** | 75.01 | 39.79 | same | HF LB csv |
| dr-tulu (AI2, open) | 45.49 | - | - | same | HF LB csv |
| **claude-research** | **45.00** | - | - | same | HF LB csv |
| **kimi-researcher** | **44.64** | - | - | same | HF LB csv |
| doubao-deepresearch | 44.34 | 52.86 | 52.62 | same | HF LB csv |
| langchain-open-deep-research (orig) | 43.44 | 49.10 | 29.49 | same | HF LB csv |
| **nvidia-aiq-research-assistant (AI-Q, Aug 2025)** | **40.52** | - | - | same | HF LB csv |
| **tongyi-deepresearch-30B-A3B** | **40.46** | - | - | same | HF LB csv |
| **perplexity-Research** | **40.46** | 82.63 | 31.20 | same | HF LB csv |
| **grok-deeper-search** | **38.22** | 73.08 | 8.58 | same | HF LB csv |
| claude-3-7-sonnet-with-search | 36.63 | 87.32 | 24.51 | same | HF LB csv |
| gemini-2.5-pro-preview-05-06 (no search) | 31.90 | - | - | same | HF LB csv |
| gpt-4o-search-preview | 30.74 | 86.63 | 5.05 | same | HF LB csv |

### 1c. Official HF leaderboard, new GPT-5.5 judge board (official since 2026-05-11)

Source: the same space, file `data_gpt55/leaderboard.csv`. Gemini-2.5-Pro was deprecated, so GPT-5.5 is now the RACE judge, GPT-5.4-mini is the FACT judge, and GPT-5.6 Luna does article cleaning (repo README: https://github.com/Ayanami0730/deep_research_bench).

| System | RACE | Judge |
|---|---|---|
| cellcog-max (#1) | 56.72 | GPT-5.5 |
| ATH-Voicepica-DeepResearch | 55.99 | GPT-5.5 |
| gemini-2.5-pro-deepresearch | 49.98 | GPT-5.5 |
| openai-deepresearch | 47.84 | GPT-5.5 |
| perplexity-Research | 43.05 | GPT-5.5 |
| grok-deeper-search | 41.22 | GPT-5.5 |

### 1d. DRB scores reported in papers, blogs and model cards

| System | Score | Metric | Subset | Judge | Date | Source |
|---|---|---|---|---|---|---|
| NVIDIA AI-Q (fine-tuned Nemotron-3-Super-120B-A12B) | 55.95 | DRB I RACE (claimed #1) | full | not stated (matches the Gemini-judge LB row) | 2026-03-12 | https://huggingface.co/blog/nvidia/how-nvidia-won-deepresearch-bench |
| NVIDIA AI-Q | 54.50 | DRB II total (claimed #1) | full | not stated | 2026-03-12 | same |
| QUEST-35B | 48.2 | DRB (RACE, presumably) | full | not stated in excerpt | 2026-05 | https://arxiv.org/html/2605.24218 (Table 3) |
| QUEST-30B | 45.3 | DRB | full | not stated | 2026-05 | same |
| Claude Opus 4.5 / Gemini 3 Pro / OpenAI-DR (as cited by QUEST) | 50.6 / 49.6 / 47.0 | DRB | full | not stated | 2026-05 | same |
| Tongyi-DR / DR Tulu / OpenResearcher (as cited by QUEST) | 40.5 / 45.5 / 35.4 | DRB | full | not stated | 2026-05 | same |
| QUEST-4B (SFT) | 22.0 | "DeepResearch Bench avg@3" | ? | not stated | 2026 | https://huggingface.co/osunlp/QUEST-4B (looks like a different metric or scale; treat with care) |

DRB II leaderboard (`data_drb2/leaderboard.csv`, same space): Xiaoyi DeepResearch 7.0 61.91, OpenAI o3 Deep Research 43.12, Gemini 3 Pro DR 41.52, Gemini 2.5 Pro DR 39.89, Perplexity Research 33.57. AI-Q's 54.50 is **not** in the current DRB II CSV.

**Caveats (DRB)**
- There are now three scoring regimes: the paper (Gemini-2.5-Pro judge), the legacy LB (also Gemini-2.5-Pro, but re-run), and the new GPT-5.5 LB. Do not mix them in one column. If we score ourselves with a different judge (we cannot use Gemini-2.5-Pro after its June 2026 deprecation), say so on the slide.
- FACT is missing for almost every system submitted after mid-2025. Claude Research, Kimi Researcher, Tongyi and AI-Q have no FACT numbers on the LB.
- I found no DRB score for MiroThinker or WebThinker. WebThinker was evaluated on Glaive report generation, not DRB.
- RACE is relative to a reference report, so a score near 50 means "on par with the reference".

---

## 2. FRAMES (Krishna et al. 2024, arXiv 2409.12941; 824 questions)

| System | Score | Metric | Subset | Judge | Date | Source |
|---|---|---|---|---|---|---|
| Gemini-Pro-1.5 naive (no retrieval) | 40.8 | accuracy | full 824 | Gemini-Pro-1.5 autorater | 2024-09 (v3 2025-01) | https://arxiv.org/html/2409.12941 |
| Gemini-Pro-1.5 + BM25, 2 / 4 docs | 45.2 / 47.4 | accuracy | full | same | same | same |
| Gemini-Pro-1.5 multi-step retrieval (5 steps x 5 queries x 10 docs) | 66.0 | accuracy | full | same | same | same |
| Gemini-Pro-1.5 oracle (gold articles) | 72.9 | accuracy | full | same | same | same |
| Gemini-Flash-1.5 naive / oracle | 26.3 / 66.5 | accuracy | full | same | same | same |
| Gemma2-27B naive | 30.8 | accuracy | full | same | same | same |
| **Tongyi DeepResearch 30B-A3B** | **90.6** | Avg@3 | full | not stated | 2025-10 | https://arxiv.org/html/2510.24701v1 (Table 1) |
| OpenAI o3 (ReAct agent, run by Tongyi) | 84.0 | Avg@3 | full | not stated | 2025-10 | same |
| DeepSeek-V3.1 (ReAct) | 83.7 | Avg@3 | full | not stated | 2025-10 | same |
| Claude-4-Sonnet (ReAct) | 80.7 | Avg@3 | full | not stated | 2025-10 | same |
| GLM 4.5 (ReAct) / Kimi K2 (ReAct) | 78.9 / 72.0 | Avg@3 | full | not stated | 2025-10 | same |
| **Kimi-Researcher** | **78.8** | as cited | ? | o3-mini (per Kimi blog) | 2025-06 | cited in Tongyi Table 1 and MiroThinker README; the Kimi blog (https://moonshotai.github.io/Kimi-Researcher/) gives no FRAMES number in text, so **unverified on primary** |
| MiroThinker v0.2 | 74.8 | accuracy | ? | not stated | 2025-09-08 | https://github.com/MiroMindAI/MiroThinker |
| MiroThinker v0.1 32B-DPO | 71.7 | accuracy | ? | not stated | 2025-08-08 | same |

**Caveats (FRAMES)**
- The column placement in Tongyi's Table 1 came from HTML extraction, which aligns values by position. The 90.6 / 84.0 / 83.7 figures match the paper's headline text, but check the PDF before putting them on a slide.
- I found no published FRAMES number for OpenAI Deep Research, Perplexity, WebThinker or QUEST. QUEST does not evaluate on FRAMES.
- The paper's oracle result (72.9) is far below what modern agents score, because it predates current models. Treat it as a historical anchor, not a ceiling.

---

## 3. Humanity's Last Exam (HLE; 2,500 questions full, about 2,154 text-only)

### Deep-research agents (with tools)

| System | Score % | Metric | Subset | Judge | Date | Source |
|---|---|---|---|---|---|---|
| Gemini Deep Research Max (Gemini 3.1 Pro) | 54.6 | accuracy | ? | ? | 2026-04-21 | **unverified**. Secondary source: https://officechai.com/ai/google-releases-deep-research-max-tops-hle-browsecomp-deepsearchqa-benchmarks/. The Google post https://blog.google/innovation-and-ai/models-and-research/gemini-models/next-generation-gemini-deep-research/ shows the numbers only in a chart image. |
| Grok 4 Heavy (with tools) | 50.7 | accuracy | text-only | not stated | 2025-07-09 | https://x.ai/news/grok-4 |
| Gemini Deep Research (Gemini 3 Pro, Dec 2025) | 46.4 | accuracy | **full set** | not stated | 2025-12-11 | https://blog.google/technology/developers/deep-research-agent-gemini-api/ |
| Claude Opus 4.5 agent (as cited by QUEST) | 43.2 | accuracy | text | not stated | 2026-05 | https://arxiv.org/html/2605.24218 |
| MiroThinker v1.7 (235B) | 42.9 | accuracy | HLE-Text | not stated | 2026-03-11 | https://github.com/MiroMindAI/MiroThinker |
| MiroThinker v1.5 / v1.0 | 39.2 / 37.7 | accuracy | HLE-Text | not stated | 2026-01 / 2025-11 | same |
| Tongyi DeepResearch Heavy mode | 38.3 | accuracy | text-only 2,154 | o3-mini | 2025-10 | https://arxiv.org/html/2510.24701v1 |
| QUEST-35B | 37.2 | accuracy | HLE-Text | not stated | 2026-05 | https://arxiv.org/html/2605.24218 |
| QUEST-4B (SFT) | 36.2 | avg@3 | HLE | not stated | 2026 | https://huggingface.co/osunlp/QUEST-4B |
| GPT-5 agent (as cited by QUEST) | 35.2 | accuracy | text | not stated | 2026-05 | https://arxiv.org/html/2605.24218 |
| **Tongyi DeepResearch 30B-A3B** | **32.9** | Avg@3 | text-only 2,154 | o3-mini | 2025-10 | https://arxiv.org/html/2510.24701v1 |
| DeepSeek-V3.1 (ReAct) | 29.8 | Avg@3 | text-only | o3-mini | 2025-10 | same |
| **Kimi-Researcher** | **26.9** | Pass@1 (40.17 Pass@4) | not stated | o3-mini | 2025-06-17 | https://moonshotai.github.io/Kimi-Researcher/ |
| Gemini Deep Research (2025, as cited) | 26.9 | accuracy | ? | ? | 2025 | cited in Tongyi Table 1 and MiroThinker README. **Primary not found.** |
| **OpenAI deep research** | **26.6** | accuracy | full set (per OpenAI chart) | o3-mini | 2025-02-02 | **unverified on primary** (openai.com/index/introducing-deep-research returns 403). Cited in Tongyi Table 1 and WebThinker Table 2; press: https://www.techradar.com/computing/artificial-intelligence/openais-deep-research-smashes-records-for-the-worlds-hardest-ai-exam-with-chatgpt-o3-mini-and-deepseek-left-in-its-wake |
| OpenAI o3 (ReAct w/ tools, run by Tongyi) | 24.9 | Avg@3 | text-only | o3-mini | 2025-10 | https://arxiv.org/html/2510.24701v1 |
| QUEST-30B | 24.6 | accuracy | HLE-Text | not stated | 2026-05 | https://arxiv.org/html/2605.24218 |
| **Perplexity Deep Research** | **21.1** | accuracy | not stated | not stated | 2025-02-14 | https://www.perplexity.ai/hub/blog/introducing-perplexity-deep-research |
| MiroThinker v0.2 | 19.1 (17.8 full) | accuracy | text-only | not stated | 2025-09-08 | https://github.com/MiroMindAI/MiroThinker |
| WebThinker-32B-RL | 15.8 | Pass@1 | 500-question text-only sample | Qwen2.5-72B-Instruct | 2025-05 | https://arxiv.org/html/2504.21776 |
| WebThinker-32B-Base | 13.0 | Pass@1 | 500 sample | same | 2025-05 | same |

### Plain frontier models, no tools (official Scale/CAIS LB)

Source (text-only): https://labs.scale.com/leaderboard/humanitys_last_exam_text_only. Source (full): https://labs.scale.com/leaderboard/humanitys_last_exam. Both pages were fetched 2026-11-04; their latest notice is dated 2026-09-17 (the HLE-Rolling fork). Grading is automatic, with temperature 0 and o3-mini as the judge (per lastexam.ai).

| Model | Text-only % | Full % |
|---|---|---|
| GPT 6 Astra (#1) | 54.17 | 54.80 |
| gemini-3.1-pro-preview (thinking high) | 47.31 | 46.44 |
| gpt-5.4-pro | 45.32 | 44.32 |
| gemini-3-pro-preview | 37.72 | 37.52 |
| claude-opus-4-6-thinking-max | 36.24 | 34.44 |
| gpt-5 (2025-08-07) | 26.32 | 25.32 |
| claude-opus-4-5-thinking | 26.32 | 25.20 |
| gemini-2.5-pro-preview-06-05 | 22.06 | 21.64 |
| o3 (high) | 20.57 | 20.32 |
| gpt-oss-120b | 15.48 | - |
| Qwen3-235B-A22B-Thinking-2507 | 15.43 | - |
| Qwen3-235B-A22B | 11.75 | - |
| GPT-4o (Nov 2024) | 2.32 | - |

**Caveats (HLE)**
- The full set and the text-only subset are different denominators, and vendors often do not say which one they used. Only Google (full set) and Grok 4 Heavy, Tongyi, MiroThinker and QUEST (text-only) say so explicitly.
- Agent numbers come with tools such as search and code; the Scale LB numbers come without tools. Put them on separate rows of the slide.
- The 26.6 / 26.9 figures for OpenAI DR and Gemini DR from early 2025 are what every open-agent paper cites. Gemini's current product scores much higher (46.4 in Dec 2025, about 54.6 claimed for Max).
- HLE text-only is our suite (150-question sample, 40-question dev set). A sample of 150 has a 95% CI of roughly ±7 points.

---

## 4. BrowseComp-Plus (Chen et al. 2025, arXiv 2508.06600; 830 queries, fixed ~100K-doc corpus)

Official LB source: https://huggingface.co/datasets/Tevatron/BrowseComp-Plus-results (`agent_results.csv`, read by the LB space https://huggingface.co/spaces/Tevatron/BrowseComp-Plus). The LB's primary accuracy column is judged by **Qwen3-32B**. The paper used GPT-4.1, and the LB keeps a GPT-4.1 column for the original rows. In the table below, "Standard" means the default scaffold, which gives the agent only `search` with the top 5 results, 512-token snippets.

| System | Retriever | Acc % (Qwen3-32B judge) | Acc % (GPT-4.1 judge, paper) | Scaffold | Date |
|---|---|---|---|---|---|
| GPT-5 (AI21 multi-agent, up to 8 agents) | Hybrid Qwen2-7B + Reason-ModernColBERT | 95.18 | - | custom | 2026-04-28 |
| GLM-5.1 (Sail Research swarm) | Hybrid BM25 + Qwen3-Embed-8B | 90.72 | - | custom | 2026-04-17 |
| GPT-5 | Mixedbread Search v3 | 90.48 | - | search + get_doc | 2026-03-19 |
| GPT-5 | Reason-ModernColBERT | 87.59 | - | search + get_doc | 2026-03-16 |
| gpt-oss-120b-high | Agent-ModernColBERT | 72.53 | - | search + get_doc | 2026-04-28 |
| **GPT-5** | **Qwen3-Embed-8B** | **71.69** | **70.12** | Standard | 2025-08-08 |
| Tongyi-DR-30B-A3B | AgentIR-4B | 68.07 | - | native search + visit | 2026-03-04 |
| **o3** | **Qwen3-Embed-8B** | **65.90** | **63.49** | Standard | 2025-08-08 |
| OpenResearcher-30B | Qwen3-Embed-8B | 60.92 | - | native toolset | 2026-02-12 |
| **GPT-5** | **BM25** | **57.59** | **55.90** | Standard | 2025-08-08 |
| oss-120b-high | Qwen3-Embed-8B | 56.99 | - | native search/open/find | 2025-12-31 |
| Tongyi-DR-30B-A3B | Qwen3-Embed-8B | 51.93 | - | native search + visit | 2025-12-27 |
| o3 | BM25 | 50.48 | 49.28 | Standard | 2025-08-08 |
| **Tongyi-DR-30B-A3B** | **Qwen3-Embed-8B** | **44.46** | 44.10 | Standard | 2025-09-28 |
| oss-120b-high | Qwen3-Embed-8B | 44.10 | 42.89 | Standard | 2025-08-08 |
| Sonnet 4 / Opus 4 | Qwen3-Embed-8B | 37.35 / 36.75 | 36.75 / 36.14 | Standard | 2025-08-08 |
| GPT-4.1 | Qwen3-Embed-8B | 36.39 | 35.42 | Standard | 2025-08-08 |
| kimi-k2-0711 | Qwen3-Embed-8B | 35.42 | 34.22 | Standard | 2025-08-16 |
| oss-20b-high | Qwen3-Embed-8B | 35.06 | 34.58 | Standard | 2025-08-08 |
| Tongyi-DR-30B-A3B | BM25 | 33.98 | 33.37 | Standard | 2025-09-28 |
| Gemini 2.5 Pro | Qwen3-Embed-8B | 29.52 | 28.67 | Standard | 2025-08-08 |
| WebSailor-32B | Qwen3-Embed-8B | 27.59 | 25.78 | Standard | 2025-09-28 |
| SearchR1-32B | Qwen3-Embed-8B / BM25 | 11.08 / 4.11 | 10.36 / 3.86 | Standard | 2025-08-08 |
| Qwen3-32B | Qwen3-Embed-8B / BM25 | 10.72 / 3.61 | 10.36 / 3.49 | Standard | 2025-08-08 |
| DeepSeek-R1-0528 | Qwen3-Embed-8B | 16.39 | 16.02 | Standard | 2025-08-16 |

Paper Table 1 (https://arxiv.org/html/2508.06600, GPT-4.1 judge) matches the GPT-4.1 column above.

Results reported in papers, not on the LB:

| System | Score | Metric | Retriever | Date | Source |
|---|---|---|---|---|---|
| QUEST-35B | 61.0 (69.5 with discard-all context) | accuracy | not stated | 2026-05 | https://arxiv.org/html/2605.24218 |
| QUEST-30B | 48.2 | accuracy | not stated | 2026-05 | same |
| QUEST-4B SFT | 52.1 | avg@3 | not stated | 2026 | https://huggingface.co/osunlp/QUEST-4B |
| Claude Opus 4.5 / GPT-5 (as cited by QUEST) | 83.0 / 71.7 | accuracy | not stated | 2026-05 | https://arxiv.org/html/2605.24218 |
| Tongyi-DR / OpenResearcher (as cited by QUEST) | 44.5 / 54.8 | accuracy | not stated | 2026-05 | same |

**Caveats (BrowseComp-Plus)**
- Accuracy depends heavily on the retriever and the scaffold. Always show the retriever next to the score, and compare like with like (Standard scaffold + Qwen3-Embed-8B is the canonical row).
- The LB judge (Qwen3-32B) and the paper judge (GPT-4.1) differ by less than about 1.5 points.
- QUEST does not name the retriever in the excerpt I read. Its 44.5 for Tongyi matches the LB's Standard + Qwen3-Embed-8B row (44.46), which suggests QUEST used that setup.

---

## 5. SimpleQA (Wei et al. 2024, arXiv 2411.04368; 4,326 questions)

| System | Score | Metric | Subset | Judge | Date | Source |
|---|---|---|---|---|---|---|
| **Perplexity Deep Research** | **93.9** | accuracy | full? | not stated | 2025-02-14 | https://www.perplexity.ai/hub/blog/introducing-perplexity-deep-research |
| Perplexity Sonar Pro (API) | 85.8 | F-score | full? | not stated | 2025-01-21 | https://www.perplexity.ai/hub/blog/introducing-the-sonar-pro-api |
| Perplexity Sonar (API) | 77.3 | F-score | full? | not stated | 2025-01-21 | same |
| GPT-4o search preview | 90 | accuracy | ? | ? | 2025-03 | **unverified** (openai.com "new tools for building agents" returns 403; the figure is from memory/press only) |
| GPT-4o-mini search preview | 88 | accuracy | ? | ? | 2025-03 | **unverified** (same) |
| GPT-4o (no browsing) | 38.2 correct / 38.4 F | correct %, F-score | full 4,326 | prompted ChatGPT grader | 2024-11 | https://arxiv.org/html/2411.04368 |
| o1-preview (no browsing) | 42.7 / 44.8 | correct %, F | full | same | 2024-11 | same |
| Claude-3-opus (no browsing) | 23.5 / 29.3 | correct %, F | full | same | 2024-11 | same |
| Kimi-Researcher | n/a | - | - | o3-mini | 2025-06 | https://moonshotai.github.io/Kimi-Researcher/ says only "strong performance" and gives no number |
| Tongyi DeepResearch | n/a (figure only) | - | - | - | 2025-10 | https://arxiv.org/html/2510.24701v1 (Fig. 11, values not in the text) |

**Caveats (SimpleQA)**
- Search-enabled systems saturate this benchmark (90%+). It shows factual grounding but does not separate strong systems well.
- Vendors do not say whether they used the full 4,326 questions, and some report F-score while others report accuracy.
- I could not verify any number for OpenAI deep research on SimpleQA.

---

## 6. FinanceBench (Islam et al. 2023, arXiv 2311.11944; 150-case open sample)

Paper results are human-graded (correct / incorrect / refusal). Source: https://arxiv.org/html/2311.11944 (2023-11-20).

| System | Config | Correct % | Incorrect % | Refusal % |
|---|---|---|---|---|
| GPT-4-Turbo | Closed book | 9 | 3 | 88 |
| GPT-4-Turbo | **Shared vector store** | **19** | 13 | 68 |
| Llama2 | Shared vector store | 19 | 70 | 11 |
| GPT-4-Turbo | Single (per-doc) vector store | 50 | 11 | 39 |
| Llama2 | Single vector store | 41 | 54 | 5 |
| Claude 2 | Long context (context-first) | 76 | 21 | 3 |
| GPT-4-Turbo | **Long context** (context-first) | **79** | 17 | 4 |
| GPT-4-Turbo | **Oracle** (context-first) | **85** | 15 | 0 |
| GPT-4-Turbo | Oracle (context-last) | 89 | - | - |
| GPT-4-Turbo / Claude 2 | Long context, context-last | 25 / 37 | - | - |

Later vendor claims, all self-reported:

| System | Score | Metric | Notes | Source |
|---|---|---|---|---|
| Mafin 2.5 (VectifyAI / PageIndex) | 98.7 | accuracy | full public set; grading method not described; no date | https://github.com/VectifyAI/Mafin2.5-FinanceBench |
| Fintool / Quantly | 98 / 94 | accuracy | cited by Vectify; Fintool covered only 66.7% of questions; results not public | same |
| Perplexity / ChatGPT-4o + Search | 45 / 31 | accuracy | cited by Vectify; 66.7% coverage | same |

**Caveats (FinanceBench)**
- The paper used human grading, so our LLM-judged numbers are not directly comparable. Disclose that.
- "Shared vector store" (one index over all filings) is the setup closest to our corpus-RAG product. "Single vector store" gives the oracle document index, which is easier.
- I could not open the docs.patronus.ai FinanceBench pages. I found no official Patronus leaderboard for later models.

---

## 7. UniDoc-Bench (Peng et al., Salesforce AI Research, arXiv 2510.03663)

The benchmark has 1,600 human-verified QA pairs, 200 per domain, across 8 domains, over a corpus of about 70k PDF pages. Metrics are completeness and faithfulness, scored by an LLM judge whose model is **not named** in the paper.

| System | Score | Metric | Subset | Generator | Date | Source |
|---|---|---|---|---|---|---|
| Text+Image fusion (T+I) RAG | 68.4 (top-20) / 65.4 (top-10) | completeness | 8-domain avg | GPT-4.1 | 2025-10-04 (v1) | https://arxiv.org/html/2510.03663v1 |
| Text-only RAG | 65.3 / 61.9 | completeness | same | GPT-4.1 | same | same |
| Multimodal joint (GME-Qwen2-VL-7B) | 64.1 / 63.0 | completeness | same | GPT-4.1 | same | same |
| Vertex AI RAG | 63.9 / 61.0 | completeness | same | gemini-2.5-flash | same | same |
| Image-only RAG / VRAG | 54.5 / 54.6 (top-20) | completeness | same | GPT-4.1 | same | same |
| T+I with Claude-4.5-Sonnet / GPT-5 / Gemini-2.5-Pro | 77.0 / 69.0 / 68.5 | completeness (top-10) | same | as named | 2026-01-05 (v3) | https://arxiv.org/html/2510.03663v3 (column mapping inferred; headers garbled) |
| Claude-4.5-Sonnet on ground-truth evidence (upper bound) | 84.0 | completeness | same | Claude-4.5-Sonnet | v3 | same |
| Retrieval T+I | P@10 0.430, R@10 0.864 | retrieval | same | - | v3 | same |

**Caveats (UniDoc-Bench)**
- Faithfulness is reported only by question type (e.g. T+I 0.880 on text-only answers), not as an overall average.
- The judge model is unnamed, so our numbers (Claude-subagent judge) are comparable only loosely. Say so.

---

## 8. NVIDIA AI-Q blueprint

| System | Score | Metric | Benchmark | Judge | Date | Source |
|---|---|---|---|---|---|---|
| AI-Q Research Assistant (Llama-3.3-Nemotron-Super-49B-v1.5) | 40.52 | RACE | DRB I (top "LLM with search" open entry at the time) | Gemini-2.5-Pro (LB) | 2025-08-04 | LB csv row `nvidia-aiq-research-assistant`; announcement (no numbers): https://forums.developer.nvidia.com/t/nvidia-ai-q-achieves-top-score-for-open-portable-ai-researcher/341232 |
| AI-Q v2 (fine-tuned Nemotron-3-Super-120B-A12B + GPT-5.2 per LB name; Tavily + Serper) | 55.95 | RACE | DRB I | Gemini-2.5-Pro (LB) | 2026-03-12 | https://huggingface.co/blog/nvidia/how-nvidia-won-deepresearch-bench ; LB row `nvidia-aiq-nemotron-gpt52-updated` |
| AI-Q v2 | 54.50 | total | DRB II | not stated | 2026-03-12 | same blog (not present in current DRB II CSV) |

**Caveats (AI-Q)**
- The AI-Q docs (https://docs.nvidia.com/aiq-blueprint/2.2.0/evaluation/benchmarks/deep-research-bench.html) describe how to run DRB but publish no scores.
- The 55.95 entry's LB name ("nemotron-gpt52") suggests GPT-5.2 is in the loop, so it is not a pure open-model result.
- I found no FACT, FRAMES or HLE numbers for AI-Q.

---

## Gaps and unverified items

1. OpenAI deep research HLE 26.6 and GPT-4o-search SimpleQA 90/88: openai.com returns 403, so these rest on secondary sources only.
2. Gemini Deep Research Max HLE 54.6 / BrowseComp 85.9: the Google post shows them only in a chart image; the numbers come from press.
3. Gemini DR HLE 26.9 (early 2025) and Kimi-Researcher FRAMES 78.8: cited by Tongyi and MiroThinker; I did not find these numbers in the primary sources' text.
4. I found no published FRAMES number for OpenAI DR, Perplexity, WebThinker or QUEST, no SimpleQA number for OpenAI DR, Kimi or Tongyi (figure only), and no DRB number for MiroThinker or WebThinker.
5. The Tongyi Table 1 column mapping was inferred from HTML; cross-check it against the PDF.
