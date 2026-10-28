"""Verify a finished report: every cited sentence is checked against the passages it cites.

Works on AI-Q reports (numbered citations with a Sources list) and on reports that cite
evidence IDs directly. Weak claims can go back to the writer for one revision.
"""

import asyncio
import re
from dataclasses import dataclass, field

from ttdr.clients import LLM
from ttdr.engine import Engine
from ttdr.engine.evidence import Evidence
from ttdr.engine.verify import Verdict, Verifier

NUMBERED = re.compile(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]")
EVIDENCE = re.compile(r"\[((?:W[0-9a-f]{8}(?:\.\d+)?)|(?:[^\[\]\s]+#p\d+\.\d+))\]")
SOURCE_LINE = re.compile(r"^\s*(?:[-*]\s*)?\[(\d+)\].*?(\S+://\S+?)[)\].,]*\s*$")
CORPUS_URL = re.compile(r"corpus://([^#\s]+)#page=(\d+)")
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[*])")
BADGE = {"supported": "✓", "contradicted": "✗", "not_enough_info": "?"}
FETCH_PASSAGES = 3


@dataclass
class Claim:
    text: str
    sentence: str
    refs: list[str]


@dataclass
class Checked:
    claim: Claim
    verdict: Verdict | None
    evidence: list[str] = field(default_factory=list)


def sources(report: str) -> dict[str, str]:
    """Citation number -> URL, from the report's Sources list."""
    return {m[1]: m[2] for line in report.splitlines() if (m := SOURCE_LINE.match(line))}


def _body(report: str) -> str:
    return re.split(r"^#+\s*(?:Sources|References)\b", report, flags=re.MULTILINE | re.IGNORECASE)[
        0
    ]


def claims(report: str) -> list[Claim]:
    out = []
    for line in _body(report).splitlines():
        for sentence in SENTENCE.split(line.strip()):
            refs = [r.strip() for g in NUMBERED.findall(sentence) for r in re.split(r"[,–-]", g)]
            refs += EVIDENCE.findall(sentence)
            if not refs:
                continue
            text = EVIDENCE.sub("", NUMBERED.sub("", sentence)).strip(" -*#>")
            if len(text) > 15:
                out.append(Claim(text, sentence, refs))
    return out


async def passages(engine: Engine, ref: str, urls: dict[str, str], claim: str) -> list[Evidence]:
    if not ref.isdigit():
        return await engine.get([ref])
    url = urls.get(ref)
    if not url:
        return []
    if m := CORPUS_URL.match(url):
        hits = await engine.clients.corpus.store.page(m[1], int(m[2]) - 1)
        return [Evidence(h.id, "corpus", h.doc, url, h.text) for h in hits]
    try:
        return await engine.fetch(url, claim, FETCH_PASSAGES)
    except Exception:  # noqa: BLE001 - an unreachable source leaves the claim unverified
        return []


async def check(engine: Engine, verifier: Verifier, report: str, concurrency: int = 8):
    urls = sources(report)
    sem = asyncio.Semaphore(concurrency)

    async def one(c: Claim) -> Checked:
        async with sem:
            found = [e for r in c.refs for e in await passages(engine, r, urls, c.text)]
            if not found:
                return Checked(c, None)
            verdict = await verifier.check(c.text, "\n\n".join(e.text for e in found))
            return Checked(c, verdict, [e.id for e in found])

    return await asyncio.gather(*(one(c) for c in claims(report)))


def summary(checked: list[Checked]) -> dict:
    labels = [c.verdict.label if c.verdict else "unreachable" for c in checked]
    n = len(labels)
    return {
        "claims": n,
        **{k: labels.count(k) for k in (*BADGE, "unreachable")},
        "citation_validity": round(labels.count("supported") / n, 3) if n else None,
    }


def annotate(report: str, checked: list[Checked]) -> str:
    """Badge each checked sentence: ✓ supported, ✗ contradicted, ? not enough info."""
    for c in checked:
        badge = BADGE[c.verdict.label] if c.verdict else "?"
        report = report.replace(c.claim.sentence, f"{c.claim.sentence} {badge}", 1)
    return report


REVISE_PROMPT = """You are revising a research report after fact-checking.
Each flagged sentence below was checked against the sources it cites.
- CONTRADICTED: the source says something different. Fix the sentence to match the source if the
  source text shows the right value; otherwise remove the sentence.
- UNSUPPORTED: the cited source does not state it. Remove the sentence, or weaken it to what the
  source does say.
Change nothing else: keep all other text, headings, citations and the Sources list exactly as they are.
Return only the revised report.

Flagged sentences:
{flags}

Report:
{report}"""


async def revise(llm: LLM, report: str, checked: list[Checked], max_tokens: int = 16384) -> str:
    weak = [c for c in checked if c.verdict and c.verdict.label != "supported"]
    if not weak:
        return report
    flags = "\n".join(
        f"- {'CONTRADICTED' if c.verdict.label == 'contradicted' else 'UNSUPPORTED'}: "
        f"{c.claim.sentence}"
        for c in weak
    )
    r = await llm.chat(
        [{"role": "user", "content": REVISE_PROMPT.format(flags=flags, report=report)}],
        max_tokens=max_tokens,
        chat_template_kwargs={"enable_thinking": False},
    )
    return r["choices"][0]["message"]["content"] or report
