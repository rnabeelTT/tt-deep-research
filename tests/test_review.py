from ttdr.engine import review
from ttdr.engine.verify import Verdict, decide, window_chars

REPORT = """# Capex

3M spent $1,577 million on capex in 2018 [1]. It was up from 2017 [1, 2].
Uncited context sentence here.
- The cash flow statement lists PP&E purchases [3M_2018_10K#p59.0].

## Sources
- [1] 3M 10-K - corpus://3M_2018_10K#page=60
- [2] Press release - https://example.com/3m.
"""


def test_claims_and_sources_are_parsed():
    found = review.claims(REPORT)
    assert [c.refs for c in found] == [["1"], ["1", "2"], ["3M_2018_10K#p59.0"]]
    assert found[0].text == "3M spent $1,577 million on capex in 2018 ."
    assert review.sources(REPORT) == {
        "1": "corpus://3M_2018_10K#page=60",
        "2": "https://example.com/3m",
    }


def test_annotate_and_summary():
    found = review.claims(REPORT)
    checked = [
        review.Checked(found[0], Verdict("supported", 0.9, 0.0, 0)),
        review.Checked(found[1], Verdict("contradicted", 0.1, 0.8, 0)),
        review.Checked(found[2], None),
    ]
    out = review.annotate(REPORT, checked)
    assert "[1]. ✓" in out and "[1, 2]. ✗" in out
    assert review.summary(checked)["citation_validity"] == round(1 / 3, 3)


def test_verdict_rules_and_windows():
    probs = [
        {"supported": 0.2, "contradicted": 0.7, "not_enough_info": 0.1},
        {"supported": 0.6, "contradicted": 0.1, "not_enough_info": 0.3},
    ]
    assert decide(probs).label == "supported"
    assert decide(probs[:1]).label == "contradicted"
    assert window_chars(512) < 1200 < window_chars(65536)


def test_triage_blends_embedding_and_usefulness():
    import asyncio
    from types import SimpleNamespace

    from ttdr.engine.triage import Triage

    class Decider:
        async def ask_batch(self, states, questions):
            return [{"useful": {"noul": u}} for u in (0.0, 1.0, 0.1)]

    items = [SimpleNamespace(text=t, score=s) for t, s in (("a", 0.9), ("b", 0.85), ("c", 0.5))]
    ranked = asyncio.run(Triage(Decider(), weight=0.5).rerank("q", items))
    assert [i.text for i in ranked] == ["b", "a", "c"]


def test_corpus_fallback_is_reported_apart_from_citation_validity():
    found = review.claims(REPORT)
    checked = [
        review.Checked(found[0], Verdict("supported", 0.9, 0.0, 0)),
        review.Checked(found[1], Verdict("supported", 0.9, 0.0, 0), fallback=True),
    ]
    s = review.summary(checked)
    assert s["citation_validity"] == 0.5
    assert s["fallback_checked"] == 1 and s["fallback_supported"] == 1
