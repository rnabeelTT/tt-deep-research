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


def test_fused_search_returns_whole_pages_from_chunks_and_images():
    import asyncio

    from ttdr.corpus import Corpus
    from ttdr.corpus.store import Hit

    class Embedder:
        async def queries(self, texts):
            return [[1.0]]

    class Chunks:
        async def search(self, vec, k, corpus=None):
            return [
                Hit("a#p1.2", "a", 1, "x", [], 0.9, "c"),
                Hit("a#p1.0", "a", 1, "y", [], 0.8, "c"),
                Hit("b#p4.1", "b", 4, "z", [], 0.7, "c"),
            ]

        async def page(self, doc, page):
            return [
                Hit(
                    f"{doc}#p{page}.{n}",
                    doc,
                    page,
                    f"{doc}{page}-{n}",
                    ["table"] if n else [],
                    0,
                    "c",
                )
                for n in range(2)
            ]

    class Pages:
        async def search(self, vec, k, corpus=None):
            return [
                Hit("b#p4.0", "b", 4, "", [], 0.6, "c"),
                Hit("c#p0.0", "c", 0, "", [], 0.5, "c"),
            ]

    hits = asyncio.run(Corpus(Embedder(), Chunks(), pages=Pages()).fused("q", 4))
    assert [h.id for h in hits] == ["a#p1.0", "b#p4.0", "c#p0.0"]
    assert hits[0].text == "a1-0 a1-1" and hits[0].flags == ["table"]


def test_fetching_a_corpus_page_link_reads_the_page_from_the_corpus():
    import asyncio

    from ttdr.engine import Engine

    asked = []

    async def open_pages(eid, before, after):
        asked.append((eid, before, after))
        return ["passage"]

    engine = Engine.__new__(Engine)
    engine.open_pages = open_pages
    for url in ("http://ui:3000/pages/3M_2018_10K/60", "corpus://3M_2018_10K#page=60"):
        assert asyncio.run(engine.fetch(url)) == ["passage"]
    assert asked == [("3M_2018_10K#p59.0", 0, 0)] * 2


def test_text_documents_index_their_lead_chunk_and_read_as_pages(tmp_path):
    import asyncio

    from ttdr.corpus import TEXT_PAGE_CHUNKS, Corpus

    upserts = []

    class Embedder:
        async def queries(self, texts):
            return [[0.0] * 4 for _ in texts]

        async def documents(self, docs):
            return [[1.0] * 4 for _ in docs]

    class Store:
        async def ensure(self, dim):
            pass

        async def docs(self, corpus=None):
            return set()

        async def upsert(self, corpus, chunks, vecs):
            upserts.extend((corpus, c.id) for c in chunks)

    web = tmp_path / "web"
    web.mkdir()
    (web / "42.txt").write_text(
        "https://example.org/a\n" + "\n".join(f"line {i} " * 20 for i in range(60))
    )
    (web / "43.txt").write_text("")
    corpus = Corpus(Embedder(), Store(), root=tmp_path)
    asyncio.run(corpus.index_dir(web, "web"))
    assert upserts == [("web", "42#p0.0")]

    page = asyncio.run(corpus.page_range("42", 1, 1))
    assert [h.id for h in page] == [f"42#p1.{i}" for i in range(TEXT_PAGE_CHUNKS)]
    assert all(h.corpus == "web" for h in page)
    found = asyncio.run(corpus.find("42", "LINE 59"))
    assert found and all("line 59" in h.text for h in found)


def test_offline_recipes_fetch_only_corpus_pages():
    import asyncio
    from types import SimpleNamespace

    import pytest

    from ttdr.engine import Engine, Offline

    engine = Engine.__new__(Engine)
    engine.recipe = SimpleNamespace(features=SimpleNamespace(offline=True))
    with pytest.raises(Offline):
        asyncio.run(engine.fetch("https://example.org/"))


def test_keyword_hits_follow_the_embeddings_and_text_documents_index_every_chunk(tmp_path):
    import asyncio

    from ttdr.corpus import Corpus
    from ttdr.corpus.store import Hit

    def hit(i):
        return Hit(i, i.split("#")[0], 0, "t", [], 1.0, "web")

    class Embedder:
        async def queries(self, texts):
            return [[0.0] * 4 for _ in texts]

    class Store:
        async def search(self, vec, k, corpus=None, doc=None):
            return [hit("1#p0.0"), hit("2#p0.0")]

        async def docs(self, corpus=None):
            return {"42"}

    upserts = []

    class Keywords:
        async def search(self, query, k, corpus=None):
            return [hit("2#p0.0"), hit("3#p4.1")]

        async def ensure(self):
            pass

        async def docs(self, corpus):
            return set()

        async def upsert(self, corpus, chunks):
            upserts.extend(c.id for c in chunks)

    corpus = Corpus(Embedder(), Store(), root=tmp_path, keywords=Keywords())
    assert [h.id for h in asyncio.run(corpus.search("q", keywords=5))] == [
        "1#p0.0",
        "2#p0.0",
        "3#p4.1",
    ]
    assert [h.id for h in asyncio.run(corpus.search("q"))] == ["1#p0.0", "2#p0.0"]

    web = tmp_path / "web"
    web.mkdir()
    (web / "42.txt").write_text("\n".join(f"line {i} " * 20 for i in range(60)))
    asyncio.run(corpus.index_keywords("web"))
    assert len(upserts) > 3 and upserts[:2] == ["42#p0.0", "42#p0.1"]
