import asyncio
import io
import zipfile

from starlette.testclient import TestClient

from ttdr import ui_server
from ttdr.corpus.library import Collection, Library, doc_name, docx_text


class FakeCorpus:
    def __init__(self, fail=(), scanned=(), images=True):
        self.indexed, self.deleted, self.fail = [], [], fail
        self.scanned, self.images, self.ocr_pages = list(scanned), images, []

    async def index_document(self, path, corpus, pool=None):
        if path.stem.startswith(self.fail):
            raise ValueError("broken PDF")
        self.indexed.append((corpus, path.name))
        return 7, self.scanned

    async def index_ocr_page(self, doc, page, text, corpus):
        self.ocr_pages.append((doc, page, text))
        return 2

    async def delete_document(self, doc):
        self.deleted.append(doc)


def docx(*paragraphs):
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"<w:document {w}><w:body>{body}</w:body></w:document>")
    return buf.getvalue()


def test_doc_names_are_readable_unique_and_safe():
    assert doc_name("Q3 board memo (final).pdf", "3f2a1c99") == "Q3_board_memo_final-3f2a1c"
    assert doc_name("../../etc/passwd", "abcdef12") == "passwd-abcdef"
    assert doc_name("???.pdf", "abcdef12") == "document-abcdef"


def test_word_documents_read_as_paragraphs():
    assert docx_text(docx("Revenue rose 4%.", "Costs fell.")) == "Revenue rose 4%.\nCosts fell."


def test_uploads_index_in_the_background_and_survive_a_restart(tmp_path):
    corpus = FakeCorpus(fail=("broken",))

    async def go():
        lib = Collection(corpus, "library", tmp_path)
        job = lib.add(
            [
                ("memo.md", b"# Memo\nRevenue rose."),
                ("spec.docx", docx("Rated at 40 kW.")),
                ("broken.pdf", b"%PDF-"),
                ("sheet.xlsx", b"x"),
            ]
        )
        assert job.info()["status"] == "pending"
        await job.task
        return lib, job

    lib, job = asyncio.run(go())
    info = job.info()
    assert info["status"] == "completed" and info["processed_files"] == 4
    status = {d["file_name"]: d["status"] for d in info["file_details"]}
    assert status == {
        "memo.md": "success",
        "spec.docx": "success",
        "broken.pdf": "failed",
        "sheet.xlsx": "failed",
    }
    assert [n for _, n in corpus.indexed] == [f"{f.doc}.txt" for f in job.files[:2]] and {
        c for c, _ in corpus.indexed
    } == {"library"}
    assert "Rated at 40 kW." in (tmp_path / "library" / f"{job.files[1].doc}.txt").read_text()
    broken = job.files[2]
    assert (
        corpus.deleted == [broken.doc] and not (tmp_path / "library" / f"{broken.doc}.pdf").exists()
    )
    assert "unsupported" in job.files[3].error
    assert lib.info()["chunk_count"] == 14

    again = Collection(FakeCorpus(), "library", tmp_path)
    assert {f.file_name: f.status for f in again.files.values()} == status


def test_deleting_a_file_removes_it_from_the_index_and_disk(tmp_path):
    corpus = FakeCorpus()

    async def go():
        lib = Collection(corpus, "library", tmp_path)
        job = lib.add([("a.txt", b"alpha"), ("b.txt", b"beta")])
        await job.task
        return lib, job, await lib.delete([job.files[0].file_id, "nope"])

    lib, job, n = asyncio.run(go())
    a, b = job.files
    assert n == 1 and corpus.deleted == [a.doc] and list(lib.files) == [b.file_id]
    assert not (tmp_path / "library" / f"{a.doc}.txt").exists()
    assert (tmp_path / "library" / f"{b.doc}.txt").exists()


class FakeHarness:
    def __init__(self, sources, corpora=()):
        self.sources, self.corpora = sources, corpora


CHAT_1, CHAT_2 = "s_" + "1" * 32, "s_" + "2" * 32


def upload(client, collection, name, text):
    r = client.post(
        f"/v1/collections/{collection}/documents",
        files=[("files", (name, text, "text/plain"))],
    )
    job_id = r.json()["job_id"]
    for _ in range(100):
        status = client.get(f"/v1/documents/{job_id}/status").json()
        if status["status"] == "completed":
            return r.json()["file_ids"][0], status
        asyncio.run(asyncio.sleep(0.01))
    raise AssertionError(status)


def test_chats_upload_to_the_library_and_named_collections_are_data_sources(tmp_path):
    corpus = FakeCorpus()
    present = {"financebench"}

    class Store:
        async def corpora(self):
            return set(present)

    corpus.store = Store()
    (tmp_path / "financebench").mkdir()
    lib = Library(corpus, tmp_path)
    about = {"financebench": "10-K filings"}
    with TestClient(ui_server.build(FakeHarness, lib, about)) as client:
        assert client.post("/v1/collections", json={"name": CHAT_1}).status_code == 201
        file_id, status = upload(client, CHAT_1, "notes.txt", b"Pump P-101 is rated at 40 kW.")
        assert status["file_details"][0]["status"] == "success"
        assert status["collection_name"] == "library"

        files = client.get(f"/v1/collections/{CHAT_2}/documents").json()["files"]
        assert [f["file_name"] for f in files] == ["notes.txt"]
        client.delete(f"/v1/collections/{CHAT_1}")
        assert len(lib.collections["library"].files) == 1

        r = client.post("/v1/collections", json={"name": "contracts", "description": "Leases"})
        assert r.status_code == 201 and r.json()["description"] == "Leases"
        upload(client, "contracts", "lease.md", b"Rent is 4,000 EUR a month.")
        assert client.post("/v1/collections", json={"name": "Bad Name!"}).status_code == 400
        assert client.post("/v1/collections", json={"name": "financebench"}).status_code == 400
        assert client.get("/v1/collections/nope/documents").status_code == 404

        sources = {s["id"]: s for s in client.get("/v1/data_sources").json()}
        assert list(sources) == [
            "web",
            "docs:contracts",
            "docs:financebench",
            "docs:library",
            "knowledge_layer",
        ]
        assert sources["docs:contracts"]["description"] == "Leases"
        assert sources["docs:financebench"]["description"] == "10-K filings"
        assert sources["docs:library"]["name"] == "My uploads"

        r = client.request(
            "DELETE", f"/v1/collections/{CHAT_2}/documents", json={"file_ids": [file_id]}
        )
        assert r.json() == {"deleted": 1} and not lib.collections["library"].files
        assert client.get("/v1/documents/nope/status").status_code == 404

    reopened = Library(FakeCorpus(), tmp_path)
    assert set(reopened.collections) == {"contracts", "library"}


def test_no_library_no_uploads():
    with TestClient(ui_server.build(FakeHarness)) as client:
        assert "knowledge_layer" not in [s["id"] for s in client.get("/v1/data_sources").json()]
        assert client.get("/v1/collections").status_code == 404


def test_an_uploaded_text_document_embeds_every_chunk(tmp_path):
    from ttdr.corpus import Corpus

    upserts, keywords = [], []

    class Embedder:
        async def queries(self, texts):
            return [[0.0] * 4 for _ in texts]

        async def documents(self, docs):
            return [[1.0] * 4 for _ in docs]

    class Store:
        async def ensure(self, dim):
            pass

        async def upsert(self, corpus, chunks, vecs):
            upserts.extend(c.id for c in chunks)

    class Keywords:
        async def ensure(self):
            pass

        async def upsert(self, corpus, chunks):
            keywords.extend(c.id for c in chunks)

    path = tmp_path / "memo-abc123.txt"
    path.write_text("\n".join(f"line {i} " * 20 for i in range(60)))
    corpus = Corpus(Embedder(), Store(), root=tmp_path, keywords=Keywords())
    n, scanned = asyncio.run(corpus.index_document(path, "library"))
    assert n == len(upserts) > 3 and upserts == keywords and scanned == []
    assert upserts[:2] == ["memo-abc123#p0.0", "memo-abc123#p0.1"]


def test_scanned_pages_are_read_by_ocr_when_the_embedder_takes_no_images(tmp_path, monkeypatch):
    from ttdr.corpus import library

    monkeypatch.setattr(library, "OCR_WAIT_S", 0.05)
    monkeypatch.setattr(library, "OCR_POLL_S", 0.01)

    class Ocr:
        def __init__(self):
            self.queued = []

        async def backfill(self, pages):
            self.queued.extend(pages)
            return len(pages)

        async def text(self, doc, page):
            return "Invoice 8812, total 18,400 EUR" if page == 0 else None

    async def go(corpus, ocr):
        lib = Collection(corpus, "library", tmp_path, ocr)
        job = lib.add([("scan.pdf", b"%PDF-")])
        await job.task
        return job.files[0]

    text_only, ocr = FakeCorpus(scanned=[0, 3], images=False), Ocr()
    f = asyncio.run(go(text_only, ocr))
    assert ocr.queued == [("library", f.doc, 0), ("library", f.doc, 3)]
    assert text_only.ocr_pages == [(f.doc, 0, "Invoice 8812, total 18,400 EUR")]
    assert f.status == "success" and f.chunks == 9 and f.unread_pages == [3]
    assert f.info("library")["metadata"]["unread_pages"] == [3]

    multimodal, ocr = FakeCorpus(scanned=[0], images=True), Ocr()
    f = asyncio.run(go(multimodal, ocr))
    assert ocr.queued == [] and multimodal.ocr_pages == [] and f.unread_pages == []
