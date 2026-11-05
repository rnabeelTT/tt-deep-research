---
name: qb2-deep-research
description: "Cited research reports from the web and private documents, using the QB2 research engine (ttdr MCP tools)."
version: 0.1.0
author: tt-deep-research
license: Apache-2.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [Research, Reports, Citations, RAG, Tenstorrent]
    related_skills: []
---

# QB2 Deep Research

Answer a research question with a report where every factual sentence cites an evidence ID that the tools returned. Uses the `ttdr` MCP server (`hermes mcp add ttdr --url http://localhost:9901/mcp`): `search`, `fetch_page`, `find_in_document`, `open_pages`, `read_page`, `get_evidence`, and `verify` when the server offers it.

## When to Use

- "Research …", "write a report on …", "compare … and cite sources".
- Questions about the private document collections (e.g. company 10-K filings: use `sources="corpus"`).

Don't use for questions answerable from one search; call `search` directly.

## Procedure

1. **Plan.** Split the question into 3–6 sub-questions. Note for each whether it needs private documents (`sources="corpus"`), the web (`sources="web"`) or both (`"all"`).
2. **Research each sub-question.**
   - `search` with short keyword queries, not sentences.
   - For the 1–2 most promising results, `fetch_page(url, focus=<sub-question>)` to get the passages that matter. Corpus results already contain their passage.
   - Inside a document you found: `find_in_document` for exact words (a line item, a defined term), `open_pages` for the whole page around a passage and the pages after it (tables that continue).
   - When a document passage mentions a figure, chart or table, or lacks the number you need, `read_page(evidence_id, question)` shows you the page itself.
   - Write down the evidence IDs (`[W1a2b3c4d.3]`, `[3M_2018_10K#p59.0]`) next to each fact you'll use.
   - Stop a sub-question after about 6 tool calls. Never repeat a search: if the tool says you already ran it, search for something different or move on.
3. **Write.** One section per sub-question, then a short conclusion. Every factual sentence ends with the evidence IDs it rests on, in square brackets. Use only IDs a tool returned to you. Say plainly when the evidence doesn't answer something.
4. **Verify** (if the `verify` tool is available). For each cited sentence, `verify(claim, evidence_ids)`:
   - `contradicted`: fix the sentence to match the source, or remove it.
   - `not_enough_info`: re-read with `get_evidence`; keep it only if the passage really says it, else weaken or remove it.
5. **Deliver.** The report, then a **Sources** list mapping each evidence ID to its title and URL.

## Pitfalls

- Search results are snippets; numbers and quotes need `fetch_page` or `get_evidence` before you cite them.
- Corpus page numbers in IDs are 0-based (`#p59` is the 60th page of the PDF).
- Scanned pages may come back marked "OCR pending" or unverified; read them with `read_page` instead of citing that text.
- Use these tools, not Hermes' own web tools, so every fact has an evidence ID.
