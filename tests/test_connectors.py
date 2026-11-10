import asyncio
from datetime import datetime

import httpx
import pytest
from starlette.testclient import TestClient
from test_library import FakeCorpus

from ttdr import recipe, ui_server
from ttdr.connectors import (
    AuthError,
    Budget,
    ConnectorError,
    Item,
    Limits,
    Resolved,
    Unavailable,
    available,
    gdrive,
    http,
    prepare,
)
from ttdr.corpus.library import Collection, Library, Remote, cited_title, origins
from ttdr.recipe import Connectors

CLIENT = "1234-abc.apps.googleusercontent.com"
TOKEN = "ya29.secret-token"
ENV = {
    "TTDR_GOOGLE_CLIENT_ID": CLIENT,
    "TTDR_GOOGLE_API_KEY": "AIza-key",
    "TTDR_GOOGLE_APP_ID": "123456789012",
}
SHIPPED = recipe.GoogleDrive(client_id=CLIENT, api_key="AIza-key", app_id="123456789012")
CONFIG = gdrive.Config.load(SHIPPED, {})
DOC = "application/vnd.google-apps.document"
PDF = b"%PDF-1.7 report"


class FakeGoogle:
    """Drive v3 and tokeninfo, enough for an import: FILES by ID, and per-URL failures queued in
    FAIL (a status, or "drop" for a connection cut off part-way)."""

    def __init__(self, files=None, scope=gdrive.SCOPE, aud=CLIENT):
        self.files = files or {}
        self.scope, self.aud = scope, aud
        self.fail: dict[str, list] = {}
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        url = request.url
        if str(url).startswith(gdrive.TOKENINFO):
            form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
            if form.get("access_token") != TOKEN:
                return httpx.Response(400, json={"error": "invalid_token"})
            body = {"aud": self.aud, "azp": self.aud, "scope": self.scope, "expires_in": "3599"}
            return httpx.Response(200, json=body)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        path = url.path.removeprefix("/drive/v3/files/")
        key = f"{path}?{url.params.get('alt') or url.params.get('mimeType') or 'meta'}"
        if queued := self.fail.get(key):
            how = queued.pop(0)
            if how == "drop":
                return httpx.Response(200, stream=Dropped())
            if isinstance(how, tuple):
                status, reason, headers = how
                body = {"error": {"errors": [{"reason": reason}], "message": reason}}
                return httpx.Response(status, json=body, headers=headers)
            return httpx.Response(how, json={"error": {"message": "boom"}})
        file_id, _, export = path.partition("/")
        if not (f := self.files.get(file_id)):
            return httpx.Response(404, json={"error": {"message": "File not found"}})
        if export:
            data = f["exports"].get(url.params["mimeType"])
            if data is None:
                body = {"error": {"errors": [{"reason": "exportSizeLimitExceeded"}]}}
                return httpx.Response(403, json=body)
            return httpx.Response(200, content=data)
        if url.params.get("alt") == "media":
            return httpx.Response(200, content=f["data"])
        meta = {k: v for k, v in f.items() if k not in ("data", "exports")}
        return httpx.Response(200, json={"id": file_id, **meta})


class Dropped(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"%PDF-partial"
        raise httpx.ReadError("connection reset")


def drive(google: FakeGoogle):
    sleeps = []

    async def sleep(s):
        sleeps.append(s)

    http = httpx.AsyncClient(transport=httpx.MockTransport(google))
    return gdrive.GoogleDrive(CONFIG, http, sleep), sleeps


FILES = {
    "pdf00000001": {
        "name": "Q3 report.pdf",
        "mimeType": "application/pdf",
        "size": "15",
        "data": PDF,
    },
    "doc00000001": {
        "name": "Board memo",
        "mimeType": DOC,
        "exports": {"application/pdf": PDF, "text/markdown": b"# Memo"},
    },
    "big00000001": {"name": "Huge doc", "mimeType": DOC, "exports": {"text/markdown": b"# Big"}},
    "img00000001": {"name": "photo.png", "mimeType": "image/png", "size": "10"},
    "bin00000001": {"name": "notes.md", "mimeType": "application/octet-stream", "data": b"hi"},
    "cut00000001": {
        "name": "shortcut",
        "mimeType": gdrive.SHORTCUT,
        "shortcutDetails": {"targetId": "pdf00000001"},
    },
    "trash0000001": {"name": "old.pdf", "mimeType": "application/pdf", "trashed": True},
    "lock00000001": {
        "name": "locked.pdf",
        "mimeType": "application/pdf",
        "capabilities": {"canDownload": False},
    },
}


def run(coro):
    return asyncio.run(coro)


def first(d, item_id, mb=2**20):
    found = run(d.resolve(TOKEN, item_id, Limits(max_bytes=mb)))
    assert len(found.items) == 1 and not found.skipped
    return found.items[0]


def test_google_drive_is_off_until_the_recipe_or_environment_configures_it():
    assert gdrive.Config.load(recipe.GoogleDrive(), {}) is None
    assert gdrive.Config.load(SHIPPED.model_copy(update={"api_key": " "}), {}) is None
    assert available(Connectors(), {}) == {}
    assert list(available(Connectors(google_drive=SHIPPED), {})) == ["google-drive"]
    assert list(available(Connectors(), ENV)) == ["google-drive"]


def test_the_shipped_recipe_leaves_google_drive_off_with_localhost_origins(monkeypatch):
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    shipped = recipe.load("recipe.toml").connectors
    assert available(shipped) == {}
    assert shipped.origins == ["http://localhost:3000", "http://127.0.0.1:3000"]
    cohost = recipe.load("recipes/ornith-35b-cohost.toml").connectors
    assert cohost == shipped


def test_the_environment_overrides_the_recipe_value_by_value():
    config = gdrive.Config.load(
        SHIPPED, {"TTDR_GOOGLE_CLIENT_ID": "9-dev.apps.googleusercontent.com"}
    )
    assert (config.client_id, config.api_key) == ("9-dev.apps.googleusercontent.com", "AIza-key")
    assert gdrive.Config.load(recipe.GoogleDrive(), ENV).client_id == CLIENT


def test_a_wrong_value_turns_google_drive_off_and_says_which(caplog):
    with pytest.raises(ValueError, match="OAuth client ID"):
        gdrive.Config.load(SHIPPED.model_copy(update={"client_id": "nope"}), {})
    with pytest.raises(ValueError, match="project number"):
        gdrive.Config.load(SHIPPED, {"TTDR_GOOGLE_APP_ID": "my-project"})
    wrong = Connectors(google_drive=SHIPPED.model_copy(update={"app_id": "my-project"}))
    assert available(wrong, {}) == {}
    assert "Google Drive import is off" in caplog.text and "project number" in caplog.text
    with pytest.raises(ValueError, match="scheme, host and port"):
        Connectors(origins=["localhost:3000"])
    with pytest.raises(ValueError, match="scheme, host and port"):
        Connectors(origins=["http://localhost:3000/app"])
    assert Connectors(origins=["http://localhost:3000/"]).origins == ["http://localhost:3000"]


def test_the_browser_gets_the_public_values_and_the_origins_it_may_sign_in_from():
    custom = Connectors(google_drive=SHIPPED, origins=["http://localhost:8080"])
    public = available(custom, {})["google-drive"].public()
    assert public == {
        "client_id": CLIENT,
        "api_key": "AIza-key",
        "app_id": "123456789012",
        "scope": "https://www.googleapis.com/auth/drive.file",
        "origins": ["http://localhost:8080"],
    }


def test_limits_follow_the_upload_caps():
    assert Limits.from_env({}) == Limits(1000, 2048 * 2**20)
    assert Limits.from_env({"TTDR_UPLOAD_MAX_FILES": "5", "TTDR_UPLOAD_MAX_MB": "1"}) == Limits(
        5, 2**20
    )
    with pytest.raises(ValueError):
        Limits.from_env({"TTDR_UPLOAD_MAX_FILES": "0"})


@pytest.mark.parametrize(
    ("google", "token", "message"),
    [
        (FakeGoogle(), "", "missing"),
        (FakeGoogle(), "has space", "missing"),
        (FakeGoogle(), "ya29.someone-elses", "expired"),
        (FakeGoogle(aud="999-other.apps.googleusercontent.com"), TOKEN, "another app"),
        (FakeGoogle(scope="openid email"), TOKEN, "doesn't allow"),
    ],
)
def test_only_live_tokens_issued_to_this_app_are_accepted(google, token, message):
    d, _ = drive(google)
    with pytest.raises(AuthError, match=message) as e:
        run(d.authorize(token))
    assert TOKEN not in str(e.value)


def test_a_token_is_checked_by_post_so_it_never_lands_in_a_url():
    google = FakeGoogle()
    d, _ = drive(google)
    run(d.authorize(TOKEN))
    assert google.seen[0].method == "POST" and TOKEN not in str(google.seen[0].url)


def test_picked_files_resolve_to_what_the_library_reads():
    d, _ = drive(FakeGoogle(FILES))
    mb = 2**20

    def resolve(i, limit=mb):
        return first(d, i, limit)

    assert resolve("pdf00000001") == Item("pdf00000001", "Q3 report.pdf", 15)
    doc = resolve("doc00000001")
    assert doc.name == "Board memo" and doc.exports[0] == ("application/pdf", ".pdf")
    assert resolve("bin00000001").name == "notes.md"
    assert resolve("cut00000001") == Item("pdf00000001", "Q3 report.pdf", 15)
    for file_id, message in [
        ("img00000001", "image/png can't be read"),
        ("trash0000001", "trash"),
        ("lock00000001", "turned off downloading"),
        ("missing00001", "not found"),
        ("../../etc", "not a Google Drive file ID"),
    ]:
        with pytest.raises(ConnectorError, match=message):
            resolve(file_id)
    with pytest.raises(ConnectorError, match="import limit"):
        resolve("pdf00000001", limit=10)


def test_google_docs_export_as_pdf_then_fall_back_to_text_when_too_large():
    d, _ = drive(FakeGoogle(FILES))
    budget = Budget(2**20)
    doc = first(d, "doc00000001")
    assert run(d.fetch(TOKEN, doc, budget)) == ("Board memo.pdf", PDF)
    big = first(d, "big00000001")
    assert run(d.fetch(TOKEN, big, budget)) == ("Huge doc.md", b"# Big")
    pdf = first(d, "pdf00000001")
    assert run(d.fetch(TOKEN, pdf, budget)) == ("Q3 report.pdf", PDF)
    assert budget.left == 2**20 - 2 * len(PDF) - len(b"# Big")


def test_rate_limits_and_dropped_downloads_are_retried():
    google = FakeGoogle(FILES)
    google.fail["pdf00000001?meta"] = [429, (403, "userRateLimitExceeded", {"retry-after": "7"})]
    google.fail["pdf00000001?media"] = [503, "drop"]
    d, sleeps = drive(google)
    budget = Budget(2**20)
    item = first(d, "pdf00000001")
    assert run(d.fetch(TOKEN, item, budget)) == ("Q3 report.pdf", PDF)
    assert len(sleeps) == 4 and sleeps[1] >= 7
    assert budget.left == 2**20 - len(PDF)


def test_persistent_failures_say_what_to_do():
    google = FakeGoogle(FILES)
    google.fail["pdf00000001?meta"] = [503] * http.ATTEMPTS
    google.fail["doc00000001?meta"] = [(403, "insufficientFilePermissions", {})]
    d, _ = drive(google)
    with pytest.raises(Unavailable, match="busy"):
        first(d, "pdf00000001")
    with pytest.raises(ConnectorError, match="insufficientFilePermissions"):
        first(d, "doc00000001")
    google.fail["pdf00000001?meta"] = [401]
    with pytest.raises(AuthError):
        first(d, "pdf00000001")


def test_an_import_stops_at_its_byte_budget_and_failed_files_give_it_back():
    d, _ = drive(FakeGoogle(FILES))
    item = Item("pdf00000001", "Q3 report.pdf", 15)
    budget = Budget(len(PDF) - 1)
    with pytest.raises(ConnectorError, match="limit"):
        run(d.fetch(TOKEN, item, budget))
    assert budget.left == len(PDF) - 1


def test_prepare_checks_the_token_then_resolves_each_pick_once():
    d, _ = drive(FakeGoogle(FILES))
    ids = ["pdf00000001", "img00000001", "pdf00000001", "doc00000001"]
    prepared = run(prepare(d, TOKEN, ids, Limits()))
    remotes = prepared.remotes
    assert prepared.skipped == []
    assert [r.name for r in remotes] == [
        "Q3 report.pdf",
        "Google Drive file img00000001",
        "Board memo",
    ]
    assert remotes[1].error.startswith("image/png") and remotes[1].fetch is None
    with pytest.raises(ConnectorError, match="no files"):
        run(prepare(d, TOKEN, [], Limits()))
    with pytest.raises(ConnectorError, match="at most 1"):
        run(prepare(d, TOKEN, ids, Limits(max_files=1)))
    with pytest.raises(AuthError):
        run(prepare(d, "ya29.wrong", ids, Limits()))


def test_remote_files_download_then_index_and_failures_stay_on_their_file(tmp_path):
    corpus = FakeCorpus()

    async def ok():
        return "memo.md", b"# Memo\nRevenue rose."

    async def broken():
        raise ConnectorError("not found, or not shared with this app")

    async def go():
        c = Collection(corpus, "library", tmp_path)
        job = c.add_remote(
            [
                Remote("Board memo", fetch=ok),
                Remote("gone", fetch=broken),
                Remote("photo.png", error="image/png can't be read"),
            ]
        )
        assert [f.status for f in job.files] == ["ingesting", "ingesting", "failed"]
        await job.task
        return job

    job = run(go())
    assert job.info()["status"] == "completed"
    memo, gone, photo = job.files
    assert (memo.file_name, memo.status, memo.chunks) == ("memo.md", "success", 7)
    assert gone.status == "failed" and "not shared" in gone.error
    assert photo.error == "image/png can't be read"
    assert corpus.indexed == [("library", f"{memo.doc}.txt")]


class FakeConnector:
    id, name = "fake-drive", "Fake Drive"

    def __init__(self):
        self.credentials = []

    def public(self):
        return {"client_id": "public"}

    async def authorize(self, credential):
        self.credentials.append(credential)
        if credential == "expired":
            raise AuthError("Fake sign-in expired; sign in again")
        if credential == "down":
            raise Unavailable("Fake Drive is busy; try again in a minute")
        return "ana@example.com"

    async def resolve(self, credential, item_id, limits):
        if item_id == "bad":
            raise ConnectorError("can't be read")
        if item_id == "empty":
            return Resolved([], ["Empty: the folder is empty"])
        if item_id == "dir":
            files = [Item(f"dir-{i}", f"dir-{i}.txt") for i in (1, 2)]
            return Resolved(files, ["dir: 1 item left out (photo)"])
        url = f"https://fake.example/{item_id}"
        return Resolved([Item(item_id, f"{item_id}.txt", web_url=url, version="v1", store="Fake")])

    async def fetch(self, credential, item, budget):
        if item.id == "flaky" and credential == "second":
            raise ConnectorError("gone")
        return item.name, f"Text of {item.id}".encode()

    async def aclose(self):
        pass


def test_the_ui_imports_picked_items_into_a_collection_and_polls_the_job(tmp_path):
    corpus = FakeCorpus()

    class Store:
        async def corpora(self):
            return set()

    corpus.store = Store()
    fake = FakeConnector()
    app = ui_server.build(FakeHarness, Library(corpus, tmp_path), connectors={fake.id: fake})
    chat = "s_" + "0" * 32
    with TestClient(app) as client:
        listed = client.get("/v1/connectors")
        assert listed.json() == {
            "connectors": [
                {
                    "id": "fake-drive",
                    "name": "Fake Drive",
                    "configured": True,
                    "config": {"client_id": "public"},
                },
                {"id": "google-drive", "name": "Google Drive", "configured": False, "config": {}},
                {"id": "onedrive", "name": "OneDrive", "configured": False, "config": {}},
                {"id": "lawmatics", "name": "Lawmatics", "configured": False, "config": {}},
            ],
            "preview": False,
        }
        assert listed.headers["cache-control"] == "no-store"

        url = f"/v1/collections/{chat}/import/fake-drive"
        r = client.post(url, json={"credential": "ok", "item_ids": ["a1", "bad", "b2"]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert [f["file_name"] for f in body["files"]] == [
            "a1.txt",
            "Fake Drive file bad",
            "b2.txt",
        ]
        assert body["files"][1] == {
            "file_id": body["file_ids"][1],
            "file_name": "Fake Drive file bad",
            "status": "failed",
            "error_message": "can't be read",
        }
        for _ in range(200):
            status = client.get(f"/v1/documents/{body['job_id']}/status").json()
            if status["status"] in ("completed", "failed"):
                break
            asyncio.run(asyncio.sleep(0.01))
        states = {d["file_name"]: d["status"] for d in status["file_details"]}
        assert states == {"a1.txt": "success", "Fake Drive file bad": "failed", "b2.txt": "success"}
        names = [
            f["file_name"] for f in client.get(f"/v1/collections/{chat}/documents").json()["files"]
        ]
        assert names == ["a1.txt", "Fake Drive file bad", "b2.txt"]

        assert (
            client.post(url, json={"credential": "expired", "item_ids": ["a1"]}).status_code == 401
        )
        assert client.post(url, json={"credential": "down", "item_ids": ["a1"]}).status_code == 502
        assert client.post(url, json={"credential": "ok", "item_ids": []}).status_code == 400
        assert client.post(url, json={"credential": 7, "item_ids": ["a1"]}).status_code == 400
        assert client.post(url, content=b"not json").status_code == 400
        assert client.post(url, content=b"x" * (ui_server.MAX_IMPORT_BODY + 1)).status_code == 413
        folder = client.post(url, json={"credential": "ok", "item_ids": ["dir", "a1", "empty"]})
        assert [f["file_name"] for f in folder.json()["files"]] == [
            "dir-1.txt",
            "dir-2.txt",
            "a1.txt",
        ]
        assert folder.json()["skipped"] == [
            "dir: 1 item left out (photo)",
            "Empty: the folder is empty",
        ]
        nothing = client.post(url, json={"credential": "ok", "item_ids": ["empty"]})
        assert nothing.status_code == 400
        assert nothing.json()["detail"] == "nothing to import: Empty: the folder is empty"

        for other in ("dropbox", "google-drive", "onedrive"):
            off = client.post(
                f"/v1/collections/{chat}/import/{other}",
                json={"credential": "ok", "item_ids": ["a1"]},
            )
            assert off.status_code == 404
        nowhere = "/v1/collections/nope/import/fake-drive"
        assert (
            client.post(nowhere, json={"credential": "ok", "item_ids": ["a1"]}).status_code == 404
        )


def test_no_library_no_connectors():
    with TestClient(ui_server.build(FakeHarness, connectors={"x": FakeConnector()})) as client:
        assert client.get("/v1/connectors").status_code == 404


class FakeHarness:
    def __init__(self, sources, corpora=()):
        pass

    async def run(self, question, events):
        pass


def test_preview_lists_unconfigured_stores_for_the_ui_to_show_disabled(tmp_path, monkeypatch):
    from ttdr.connectors import preview

    assert not preview({}) and not preview({"TTDR_CONNECTORS_PREVIEW": "0"})
    assert preview({"TTDR_CONNECTORS_PREVIEW": "1"})
    corpus = FakeCorpus()
    app = ui_server.build(FakeHarness, Library(corpus, tmp_path), preview=True)
    with TestClient(app) as client:
        listed = client.get("/v1/connectors").json()
    assert listed["preview"] is True
    assert [(c["id"], c["configured"]) for c in listed["connectors"]] == [
        ("google-drive", False),
        ("onedrive", False),
        ("lawmatics", False),
    ]


def test_a_google_redirect_to_another_host_does_not_carry_the_token():
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.host == "www.googleapis.com":
            return httpx.Response(
                302, headers={"location": "https://doc-0s.googleusercontent.com/x"}
            )
        return httpx.Response(200, content=PDF)

    http_client = http.client(follow_redirects=True, transport=httpx.MockTransport(handle))
    d = gdrive.GoogleDrive(CONFIG, http_client)
    item = Item("pdf00000001", "Q3 report.pdf", 15)
    assert run(d.fetch(TOKEN, item, Budget(2**20))) == ("Q3 report.pdf", PDF)
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert "authorization" not in seen[1].headers


def test_imports_record_where_each_file_came_from_and_who_imported_it():
    fake = FakeConnector()
    prepared = run(prepare(fake, "ok", ["a1"], Limits()))
    assert prepared.remotes[0].source == {
        "connector": "fake-drive",
        "store": "Fake",
        "item_id": "a1",
        "web_url": "https://fake.example/a1",
        "version": "v1",
        "imported_by": "ana@example.com",
    }


def test_reimporting_a_file_replaces_its_copy_only_when_the_new_one_indexes(tmp_path):
    corpus, fake = FakeCorpus(), FakeConnector()

    async def go():
        c = Collection(corpus, "board", tmp_path)
        for credential in ("first", "first", "second"):
            prepared = await prepare(fake, credential, ["a1", "flaky"], Limits())
            await c.add_remote(prepared.remotes).task
        return c

    c = run(go())
    files = list(c.files.values())
    assert [(f.file_name, f.status) for f in files] == [
        ("flaky.txt", "success"),
        ("a1.txt", "success"),
        ("flaky.txt", "failed"),
    ]
    assert files[1].source["item_id"] == "a1" and len(corpus.deleted) == 3
    reloaded = Collection(corpus, "board", tmp_path)
    assert [f.source["web_url"] for f in reloaded.files.values()][:2] == [
        "https://fake.example/flaky",
        "https://fake.example/a1",
    ]
    assert files[0].info("board")["metadata"]["source"]["imported_by"] == "ana@example.com"


def test_citations_name_the_file_and_link_to_it_in_its_store(tmp_path):
    corpus, fake = FakeCorpus(), FakeConnector()

    async def go():
        c = Collection(corpus, "board", tmp_path)
        await c.add_remote((await prepare(fake, "ok", ["a1"], Limits())).remotes).task
        upload = c.add([("Q3 memo.md", b"# Memo")])
        await upload.task
        return c

    c = run(go())
    imported, uploaded = c.files.values()
    day = f"{datetime.fromisoformat(imported.uploaded_at).day} " + datetime.fromisoformat(
        imported.uploaded_at
    ).strftime("%b %Y")
    assert cited_title(imported.doc, tmp_path) == (
        f"a1.txt (Fake, imported {day}: https://fake.example/a1)"
    )
    imported.uploaded_at = "2026-11-03T09:15:00+00:00"
    c._save()
    assert cited_title(imported.doc, tmp_path).startswith("a1.txt (Fake, imported 3 Nov 2026: ")
    assert cited_title(uploaded.doc, tmp_path) == "Q3 memo.md"
    assert cited_title("3M_2018_10K", tmp_path) == "3M_2018_10K"
    assert set(origins(tmp_path)) == {imported.doc, uploaded.doc}


def test_the_ui_imports_into_a_named_collection_it_creates(tmp_path):
    corpus = FakeCorpus()

    class Store:
        async def corpora(self):
            return set()

    corpus.store = Store()
    fake = FakeConnector()
    library = Library(corpus, tmp_path)
    app = ui_server.build(FakeHarness, library, connectors={fake.id: fake})
    with TestClient(app) as client:
        about = "Finance site, Board packs library (SharePoint)"
        made = client.post("/v1/collections", json={"name": "board-packs", "description": about})
        assert made.status_code == 201, made.text
        r = client.post(
            "/v1/collections/board-packs/import/fake-drive",
            json={"credential": "ok", "item_ids": ["a1"]},
        )
        assert r.status_code == 200, r.text
        asyncio.run(asyncio.sleep(0))
        for _ in range(200):
            if (
                client.get(f"/v1/documents/{r.json()['job_id']}/status").json()["status"]
                == "completed"
            ):
                break
            asyncio.run(asyncio.sleep(0.01))
        files = client.get("/v1/collections/board-packs/documents").json()["files"]
        assert [(f["file_name"], f["metadata"]["source"]["item_id"]) for f in files] == [
            ("a1.txt", "a1")
        ]
        sources = {s["id"]: s for s in client.get("/v1/data_sources").json()}
        assert sources["docs:board-packs"]["description"] == about
        assert client.post("/v1/collections", json={"name": "Bad Name!"}).status_code == 400
