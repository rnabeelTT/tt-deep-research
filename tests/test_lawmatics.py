import asyncio
from urllib.parse import parse_qs

import httpx
import pytest
from starlette.testclient import TestClient
from test_library import FakeCorpus

from ttdr import recipe, ui_server
from ttdr.connectors import AuthError, Budget, ConnectorError, Limits, available, lawmatics, prepare
from ttdr.corpus.library import Library
from ttdr.recipe import Connectors

API = "https://api.lawmatics.com/v1"
TOKEN = "lm-access-token"
SECRET = "lm-client-secret"
PDF = b"%PDF-1.7 engagement letter"
STORAGE = "lawmatics-prod.s3.amazonaws.com"


def resource(kind, ident, **attrs):
    return {"id": str(ident), "type": kind, "attributes": attrs}


MATTERS = [
    resource("prospect", 11, name="Smith v. Acme", status="hired"),
    resource("prospect", 12, first_name="Dana", last_name="Lee", status="pnc"),
    resource("prospect", 13, case_title="Estate of Ortiz"),
]
FILES = {
    "11": [
        resource("file", 101, name="Engagement letter.pdf", size=len(PDF), updated_at="2026-11-01"),
        resource("file", 102, name="Intake notes.docx", size=900, updated_at="2026-11-02"),
        resource("file", 103, name="Photo.jpg", size=50),
        resource("file", 104, name="Exhibits.csv", size=40),
    ],
    "12": [],
}


class FakeLawmatics:
    """Lawmatics' API for one firm: matters, their files (two per page), downloads that redirect
    to signed storage URLs, and the OAuth token endpoint."""

    def __init__(self, redirect_to=STORAGE, matter_pages=1):
        self.seen, self.forms, self.redirect_to = [], [], redirect_to
        self.matter_pages = matter_pages

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        url = request.url
        if url.path == "/oauth/token":
            form = parse_qs(request.content.decode())
            self.forms.append(form)
            if form.get("code") != ["good-code"] or form.get("client_secret") != [SECRET]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            body = {"access_token": TOKEN, "refresh_token": "lasting", "expires_in": 7200}
            return httpx.Response(200, json=body)
        if url.host == STORAGE or url.host == "evil.example":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=PDF)
        assert url.host == "api.lawmatics.com"
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"errors": [{"detail": "Unauthorized"}]})
        path, page = url.path.removeprefix("/v1/"), int(url.params.get("page", "1"))
        if path == "users/me":
            return httpx.Response(200, json={"data": resource("user", 1, email="ana@firm.law")})
        if path == "prospects":
            meta = {"total_pages": self.matter_pages}
            data = MATTERS if page == 1 else [resource("prospect", 20 + page, name=f"Ortiz {page}")]
            return httpx.Response(200, json={"data": data, "meta": meta})
        if path == "files":
            files = FILES.get(url.params.get("matter_id", ""), [])
            chunk = files[(page - 1) * 2 : page * 2]
            links = {"next": f"{API}/files?page={page + 1}"} if page * 2 < len(files) else {}
            return httpx.Response(200, json={"data": chunk, "links": links})
        if path.startswith("files/download/"):
            ident = path.rsplit("/", 1)[1]
            where = f"https://{self.redirect_to}/{ident}?X-Amz-Signature=abc"
            return httpx.Response(302, headers={"location": where})
        if path.startswith("files/"):
            ident = path.rsplit("/", 1)[1]
            found = [f for fs in FILES.values() for f in fs if f["id"] == ident]
            if not found:
                return httpx.Response(404, json={"errors": [{"detail": "Not found"}]})
            return httpx.Response(200, json={"data": found[0]})
        return httpx.Response(404)


def connector(fake=None):
    fake = fake or FakeLawmatics()

    async def sleep(_):
        pass

    config = lawmatics.Config.load(
        recipe.Lawmatics(client_id="lm-client"), {"TTDR_LAWMATICS_CLIENT_SECRET": SECRET}
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    return lawmatics.Lawmatics(config, http, sleep=sleep), fake


def run(coro):
    return asyncio.run(coro)


def test_lawmatics_is_off_until_its_client_id_and_secret_are_set():
    assert lawmatics.Config.load(recipe.Lawmatics(), {}) is None
    assert lawmatics.Config.load(recipe.Lawmatics(client_id="lm-client"), {}) is None
    on = Connectors(lawmatics=recipe.Lawmatics(client_id="lm-client"))
    found = available(on, {"TTDR_LAWMATICS_CLIENT_SECRET": SECRET})
    assert list(found) == ["lawmatics"]
    assert available(recipe.load("recipe.toml").connectors, {}).get("lawmatics") is None
    with pytest.raises(ValueError, match="https"):
        lawmatics.Config.load(
            recipe.Lawmatics(client_id="x", token_url="http://api.lawmatics.com/oauth/token"),
            {"TTDR_LAWMATICS_CLIENT_SECRET": SECRET},
        )


def test_the_browser_gets_the_sign_in_values_but_never_the_secret():
    d, _ = connector()
    public = d.public()
    assert public["client_id"] == "lm-client"
    assert public["authorize_url"] == "https://app.lawmatics.com/oauth/authorize"
    assert public["redirect_path"] == "/auth/lawmatics-redirect"
    assert SECRET not in repr(public)


def test_the_server_finishes_the_sign_in_and_keeps_no_lasting_credential():
    d, fake = connector()
    redirect = "http://localhost:3000/auth/lawmatics-redirect"
    got = run(d.exchange("good-code", "verifier-123", redirect))
    assert got == {"access_token": TOKEN, "expires_in": 7200}  # no refresh token
    form = fake.forms[-1]
    assert form["grant_type"] == ["authorization_code"] and form["code_verifier"] == [
        "verifier-123"
    ]
    assert form["client_id"] == ["lm-client"] and form["redirect_uri"] == [redirect]
    with pytest.raises(AuthError, match="refused"):
        run(d.exchange("bad-code", "", redirect))
    with pytest.raises(AuthError, match="own address"):
        run(d.exchange("good-code", "", "https://evil.example/auth/lawmatics-redirect"))


def test_a_token_is_checked_with_lawmatics_and_names_who_imported():
    d, _ = connector()
    assert run(d.authorize(TOKEN)) == "ana@firm.law"
    with pytest.raises(AuthError, match="expired"):
        run(d.authorize("stale"))
    with pytest.raises(AuthError, match="missing"):
        run(d.authorize("has space"))


def test_matters_are_listed_with_readable_names_and_searched():
    d, _ = connector()
    listed = run(d.browse(TOKEN, {"kind": "matters"}))
    assert [(m["id"], m["name"]) for m in listed["matters"]] == [
        ("11", "Smith v. Acme"),
        ("12", "Dana Lee"),
        ("13", "Estate of Ortiz"),
    ]
    d, _ = connector(FakeLawmatics(matter_pages=3))
    found = run(d.browse(TOKEN, {"kind": "matters", "query": "ortiz"}))
    assert [m["name"] for m in found["matters"]] == ["Estate of Ortiz", "Ortiz 2", "Ortiz 3"]
    with pytest.raises(ConnectorError):
        run(d.browse(TOKEN, {"kind": "contacts"}))


def test_a_matters_files_say_which_can_be_imported():
    d, _ = connector()
    page = run(d.browse(TOKEN, {"kind": "files", "matter": "11"}))
    assert [(f["name"], f["importable"]) for f in page["files"]] == [
        ("Engagement letter.pdf", True),
        ("Intake notes.docx", True),
    ]
    assert page["more"] is True
    with pytest.raises(ConnectorError, match="matter"):
        run(d.browse(TOKEN, {"kind": "files", "matter": "../users"}))


def test_a_picked_matter_brings_its_files_and_says_what_it_left_out():
    d, _ = connector()
    found = run(d.resolve(TOKEN, "matter:11", Limits()))
    assert [(i.id, i.name, i.store) for i in found.items] == [
        ("file:101", "Engagement letter.pdf", "Lawmatics"),
        ("file:102", "Intake notes.docx", "Lawmatics"),
        ("file:104", "Exhibits.txt", "Lawmatics"),
    ]
    assert found.skipped == [
        "Matter 11: 1 file left out (.jpg can't be read; use PDF, Word (.docx), text or Markdown)"
    ]
    assert run(d.resolve(TOKEN, "matter:12", Limits())).skipped == ["Matter 12: it has no files"]
    capped = run(d.resolve(TOKEN, "matter:11", Limits(max_files=1)))
    assert len(capped.items) == 1 and "stopped at 1 files" in capped.skipped[0]


def test_a_picked_file_resolves_and_downloads_from_signed_storage_without_the_token():
    d, fake = connector()
    item = run(d.resolve(TOKEN, "file:101", Limits())).items[0]
    assert (
        item.version == "2026-11-01" and item.web_url == ""
    )  # a signed URL never goes in a report
    name, data = run(d.fetch(TOKEN, item, Budget(2**20)))
    assert (name, data) == ("Engagement letter.pdf", PDF)
    assert fake.seen[-1].url.host == STORAGE
    with pytest.raises(ConnectorError, match="not a Lawmatics"):
        run(d.resolve(TOKEN, "file:../../users", Limits()))
    with pytest.raises(ConnectorError, match="can't be read"):
        run(d.resolve(TOKEN, "file:103", Limits()))


def test_a_download_redirect_anywhere_else_is_refused():
    d, _ = connector(FakeLawmatics(redirect_to="evil.example"))
    item = run(d.resolve(TOKEN, "file:101", Limits())).items[0]
    with pytest.raises(ConnectorError, match="somewhere unexpected"):
        run(d.fetch(TOKEN, item, Budget(2**20)))


def test_prepare_imports_a_matter_with_provenance():
    d, _ = connector()
    prepared = run(prepare(d, TOKEN, ["matter:11"], Limits()))
    assert [r.name for r in prepared.remotes] == [
        "Engagement letter.pdf",
        "Intake notes.docx",
        "Exhibits.txt",
    ]
    source = prepared.remotes[0].source
    assert source["store"] == "Lawmatics" and source["imported_by"] == "ana@firm.law"


class FakeHarness:
    def __init__(self, sources, corpora=()):
        pass

    async def run(self, question, events):
        pass


def test_the_ui_signs_in_browses_and_imports_through_the_server(tmp_path):
    d, _ = connector()
    app = ui_server.build(FakeHarness, Library(FakeCorpus(), tmp_path), connectors={d.id: d})
    redirect = "http://localhost:3000/auth/lawmatics-redirect"
    with TestClient(app) as client:
        listed = client.get("/v1/connectors").json()["connectors"]
        assert SECRET not in repr(listed)
        r = client.post(
            "/v1/connectors/lawmatics/token",
            json={"code": "good-code", "verifier": "v", "redirect_uri": redirect},
        )
        assert r.status_code == 200 and r.json()["access_token"] == TOKEN
        assert r.headers["cache-control"] == "no-store"
        bad = client.post(
            "/v1/connectors/lawmatics/token", json={"code": "bad", "redirect_uri": redirect}
        )
        assert bad.status_code == 401
        page = client.post(
            "/v1/connectors/lawmatics/browse",
            json={"credential": TOKEN, "request": {"kind": "matters"}},
        )
        assert page.status_code == 200 and len(page.json()["matters"]) == 3
        stale = client.post(
            "/v1/connectors/lawmatics/browse",
            json={"credential": "stale", "request": {"kind": "matters"}},
        )
        assert stale.status_code == 401
        assert client.post("/v1/connectors/lawmatics/browse", json=[]).status_code == 400
        assert client.post("/v1/connectors/lawmatics/browse", json={}).status_code == 400
        assert client.post("/v1/connectors/nope/token", json={}).status_code == 404

        r = client.post(
            "/v1/collections/library/import/lawmatics",
            json={"credential": TOKEN, "item_ids": ["file:101"]},
        )
        assert r.status_code == 200 and r.json()["files"][0]["file_name"] == "Engagement letter.pdf"


def test_stores_without_server_steps_refuse_them(tmp_path):
    class Plain:
        id, name = "plain", "Plain"

    app = ui_server.build(
        FakeHarness, Library(FakeCorpus(), tmp_path), connectors={"plain": Plain()}
    )
    with TestClient(app) as client:
        assert client.post("/v1/connectors/plain/token", json={}).status_code == 404
        assert client.post("/v1/connectors/plain/browse", json={}).status_code == 404
