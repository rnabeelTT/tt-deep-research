from ttdr.corpus.chunk import Chunk, split
from ttdr.corpus.pdf import flags_for
from ttdr.evals.report import evidence_hits
from ttdr.evals.schema import Evidence, Item


def test_split_overlaps_and_covers_text():
    text = "\n".join(f"line {i} " + "x" * 80 for i in range(60))
    parts = split(text, size=500, overlap=100)
    assert all(len(p) <= 500 for p in parts)
    assert parts[0].startswith("line 0") and parts[-1].endswith("x")
    assert len(parts) > len(text) // 500


def test_page_flags():
    assert flags_for("", 0.9) == ["image_only"]
    assert flags_for("Revenue grew 4% " * 20, 0.0) == ["text_ok"]
    assert flags_for("Revenue grew 4% " * 20, 0.6) == ["large_figure"]
    assert "bad_text" in flags_for("�\x01" * 200, 0.0)


def test_evidence_ids_match_gold_pages():
    assert Chunk("3M_2018_10K", 59, 1, "t", []).id == "3M_2018_10K#p59.1"
    item = Item(
        id="q", suite="s", question="", answer="", evidence=[Evidence(doc="3M_2018_10K", page=59)]
    )
    assert evidence_hits(item, ["X#p1.0", "3M_2018_10K#p59.3"])
    assert not evidence_hits(item, ["3M_2018_10K#p58.0"])


def test_indexing_refuses_a_doc_name_another_corpus_has(tmp_path):
    import asyncio

    import pytest

    from ttdr.corpus import Corpus

    class Embedder:
        async def queries(self, texts):
            return [[0.0] * 4 for _ in texts]

    class Store:
        async def ensure(self, dim):
            pass

        async def docs(self, corpus=None):
            return {"ACME_2020_10K", "OTHER"} if corpus is None else {"OTHER"}

    (tmp_path / "ACME_2020_10K.pdf").write_bytes(b"")
    with pytest.raises(ValueError, match="ACME_2020_10K"):
        asyncio.run(Corpus(Embedder(), Store()).index_dir(tmp_path, "mine"))
