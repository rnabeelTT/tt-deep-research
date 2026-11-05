import asyncio
import io
import zipfile

from starlette.testclient import TestClient

from ttdr import ui_server
from ttdr.corpus.library import Library, doc_name, docx_text


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
        lib = Library(corpus, tmp_path)
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
    assert "Rated at 40 kW." in (tmp_path / f"{job.files[1].doc}.txt").read_text()
    broken = job.files[2]
    assert corpus.deleted == [broken.doc] and not (tmp_path / f"{broken.doc}.pdf").exists()
    assert "unsupported" in job.files[3].error
    assert lib.collection()["chunk_count"] == 14

    again = Library(FakeCorpus(), tmp_path)
    assert {f.file_name: f.status for f in again.files.values()} == status


def test_deleting_a_file_removes_it_from_the_index_and_disk(tmp_path):
    corpus = FakeCorpus()

    async def go():
        lib = Library(corpus, tmp_path)
        job = lib.add([("a.txt", b"alpha"), ("b.txt", b"beta")])
        await job.task
        return lib, job, await lib.delete([job.files[0].file_id, "nope"])

    lib, job, n = asyncio.run(go())
    a, b = job.files
    assert n == 1 and corpus.deleted == [a.doc] and list(lib.files) == [b.file_id]
    assert not (tmp_path / f"{a.doc}.txt").exists() and (tmp_path / f"{b.doc}.txt").exists()


class FakeHarness:
    def __init__(self, sources):
        self.sources = sources


def test_the_ui_uploads_into_one_shared_library(tmp_path):
    corpus = FakeCorpus()
    lib = Library(corpus, tmp_path)
    harnesses = []

    def make(sources):
        harnesses.append(FakeHarness(sources))
        return harnesses[-1]

    with TestClient(ui_server.build(make, lib)) as client:
        ids = [s["id"] for s in client.get("/v1/data_sources").json()]
        assert "knowledge_layer" in ids
        assert client.post("/v1/collections", json={"name": "chat-1"}).status_code == 201
        r = client.post(
            "/v1/collections/chat-1/documents",
            files=[("files", ("notes.txt", b"Pump P-101 is rated at 40 kW.", "text/plain"))],
        )
        job_id, [file_id] = r.json()["job_id"], r.json()["file_ids"]
        for _ in range(50):
            status = client.get(f"/v1/documents/{job_id}/status").json()
            if status["status"] == "completed":
                break
            asyncio.run(asyncio.sleep(0.01))
        assert status["file_details"][0]["status"] == "success"

        files = client.get("/v1/collections/chat-2/documents").json()["files"]
        assert [f["file_name"] for f in files] == ["notes.txt"]
        assert client.get("/v1/collections/chat-2").json()["file_count"] == 1
        client.delete("/v1/collections/chat-1")
        assert len(lib.files) == 1
        r = client.request("DELETE", "/v1/collections/x/documents", json={"file_ids": [file_id]})
        assert r.json() == {"deleted": 1} and not lib.files
        assert client.get("/v1/documents/nope/status").status_code == 404

    assert ui_server.scope(["web", "knowledge_layer"]) == "all"
    assert ui_server.scope(["knowledge_layer"]) == "corpus"


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
        lib = Library(corpus, tmp_path, ocr)
        job = lib.add([("scan.pdf", b"%PDF-")])
        await job.task
        return job.files[0]

    text_only, ocr = FakeCorpus(scanned=[0, 3], images=False), Ocr()
    f = asyncio.run(go(text_only, ocr))
    assert ocr.queued == [("library", f.doc, 0), ("library", f.doc, 3)]
    assert text_only.ocr_pages == [(f.doc, 0, "Invoice 8812, total 18,400 EUR")]
    assert f.status == "success" and f.chunks == 9 and f.unread_pages == [3]
    assert f.info()["metadata"]["unread_pages"] == [3]

    multimodal, ocr = FakeCorpus(scanned=[0], images=True), Ocr()
    f = asyncio.run(go(multimodal, ocr))
    assert ocr.queued == [] and multimodal.ocr_pages == [] and f.unread_pages == []
