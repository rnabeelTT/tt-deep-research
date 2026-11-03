"""Outline-then-sections report writing, after WebWeaver (arXiv 2509.13312).

The planner's one-pass report is replaced by: an outline whose sections each name the evidence
IDs they draw on, then one call per section that sees only the notes and passages it cites, then
an opening summary written from the finished sections. Requests with a specific answer keep the
planner's direct reply: on short-answer questions the long form cost accuracy (docs/LOG.md).
"""

import asyncio
import re

KIND = """Classify this research request. ANSWER: it asks for a specific answer (a figure, a
date, a name, a yes/no, a calculation or a short list) that a paragraph or two settles. REPORT: it
asks for analysis, a survey, a comparison across many items, a review, a plan or a report, or asks
how or why something happened in a way that needs a full explanation. Reply with one word, ANSWER
or REPORT.

Request:
{question}"""

OUTLINE = """You plan a long, analytical research report. Below are the request and the
researchers' notes; every fact in the notes ends with evidence IDs in square brackets.

Write an outline of {low}-{high} sections that together answer the request completely and in the
order a reader needs. Don't include an introduction, summary or conclusion: those are added for
you. For each section write exactly one line:

## <heading> | <what the section must establish, compare or explain> | <evidence IDs, comma-separated>

List every evidence ID from the notes that the section will use, copied exactly. Reply with the
outline lines only.

Request:
{question}

Notes:
{notes}"""

SECTION = """You write one section of a research report. The full outline is below so you know
what the other sections cover; write only your section.

Go beyond listing facts: compare, explain causes and consequences, quantify, and say what the
evidence implies for the request. Use tables where they help comparison. Every factual sentence
ends with the evidence IDs it rests on, in square brackets, copied exactly from the material below
(e.g. [W1a2b3c4d.2] or [3M_2018_10K#p59.0]). Use only facts from this material; never invent an
ID. Write as the report itself: never mention the material, notes, sources or evidence you were
given, or what they lack; where something isn't known, say so about the subject. Start with the
line "## {heading}" and write nothing after the section.

Request:
{question}

Outline:
{outline}

Your section: {heading}
It must: {goal}

Material:
{material}"""

OPENING = """Below are a research request and the body of the report written for it. Write the
report's title (a "# " line) and an opening section "## Summary" that answers the request directly
in a few paragraphs, with the key findings and figures. Cite evidence IDs exactly as the body
does; add no facts the body doesn't contain. Reply with the title and the summary only.

Request:
{question}

Body:
{body}"""

LINE = re.compile(r"^#*\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.*)$", re.MULTILINE)
ID = re.compile(r"[^\s,\[\]]+(?:#p\d+\.\d+)|W[0-9a-f]{8}(?:\.\d+)?")


def outline(text: str) -> list[tuple[str, str, list[str]]]:
    return [(h.strip("# "), goal, ID.findall(ids)) for h, goal, ids in LINE.findall(text)]


def material(ids: list[str], notes: list[str], passages: dict[str, str]) -> str:
    """The note lines citing the section's IDs, then those passages' text."""
    wanted = set(ids)
    lines = [ln for n in notes for ln in n.splitlines() if wanted & set(ID.findall(ln))]
    found = [f"[{i}] {passages[i]}" for i in ids if i in passages]
    return "\n".join(lines) + ("\n\nPassages:\n" + "\n\n".join(found) if found else "")


async def is_report(model, question: str) -> bool:
    """REPORT vs ANSWER, at temperature 0: at the recipe's 0.6 the same request flipped."""
    reply = await model.bind(temperature=0).ainvoke(
        [{"role": "user", "content": KIND.format(question=question)}]
    )
    return isinstance(reply.content, str) and "REPORT" in reply.content.upper()


async def write(model, question: str, notes: list[str], passages: dict[str, str]) -> str | None:
    """None when no usable outline comes back (the caller keeps the planner's report)."""

    async def ask(prompt: str) -> str:
        reply = await model.ainvoke([{"role": "user", "content": prompt}])
        return reply.content if isinstance(reply.content, str) else ""

    joined = "\n\n".join(notes)
    plan = outline(await ask(OUTLINE.format(low=4, high=8, question=question, notes=joined)))
    if len(plan) < 2:
        return None
    listing = "\n".join(f"- {h}: {goal}" for h, goal, _ in plan)
    body = await asyncio.gather(
        *(
            ask(
                SECTION.format(
                    question=question,
                    outline=listing,
                    heading=h,
                    goal=goal,
                    material=material(ids, notes, passages) if ids else joined,
                )
            )
            for h, goal, ids in plan
        )
    )
    body_text = "\n\n".join(s.strip() for s in body)
    opening = await ask(OPENING.format(question=question, body=body_text))
    return f"{opening.strip()}\n\n{body_text}"
