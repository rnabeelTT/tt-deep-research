import asyncio
import base64
import json
import time

import httpx
import pytest

from ttdr import recipe
from ttdr.connectors import (
    AuthError,
    Budget,
    ConnectorError,
    Item,
    Limits,
    Unavailable,
    available,
    http,
    onedrive,
    prepare,
)
from ttdr.recipe import Connectors

CLIENT = "0f9c2b4e-1a2b-4c3d-8e9f-0123456789ab"
Q3_URL = "https://onedrive.live.com/?id=D%211&cid=d4648f06c91d9d3d"
TOKEN = "EwB4A8l6BAAU-opaque-consumer-token"
DRIVE = "d4648f06c91d9d3d"
PDF = b"%PDF-1.7 report"
GRAPH = onedrive.GRAPH
TENANT = "72f988bf-86f1-41af-91ab-2d7cd011db47"
SP_DRIVE = "b!Uj3kZVMhmUeR9hsQ1j4KqvR2_mPAd-bxMn4WnJJ-xvuXGrOtJHXPRbV6ZK3KXdyD"


def jwt(**claims):
    """A work account's Graph token: JWT claims, signature unchecked (Graph's job)."""
    claims = {
        "aud": "https://graph.microsoft.com",
        "appid": CLIENT,
        "tid": TENANT,
        "exp": time.time() + 3600,
        **claims,
    }
    part = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"eyJ0eXAiOiJKV1QifQ.{part}.c2ln"


WORK = jwt()


def file(item_id, name, size=100, **extra):
    return {"id": item_id, "name": name, "size": size, "file": {}, **extra}


def folder(item_id, name, children):
    return {"id": item_id, "name": name, "folder": {"childCount": len(children)}, "kids": children}


ITEMS = {
    item["id"]: item
    for item in [
        file("D!1", "Q3 report.pdf", len(PDF), webUrl=Q3_URL, eTag='"{E1},3"'),
        file("D!2", "Plan.pptx", 5000),
        file("D!3", "data.csv", 20),
        file("D!4", "photo.jpg", 20),
        file("D!5", "Notebook", 0, package={"type": "oneNote"}),
        file("D!6", "huge.pdf", 3 * 2**20),
        file("D!7", "old.pdf", 10, deleted={"state": "deleted"}),
        folder("F!1", "Reports", ["D!1", "D!2", "D!4", "D!5", "F!2", "S!1"]),
        folder("F!2", "2025", ["D!3", "F!3"]),
        folder("F!3", "Deeper", ["D!6"]),
        folder("F!9", "Empty", []),
        {"id": "S!1", "name": "From Sam", "remoteItem": {"id": "R!1"}},
        {
            "id": "L!1",
            "name": "Shortcut",
            "remoteItem": {"id": "D!1", "parentReference": {"driveId": DRIVE}},
        },
    ]
}


class FakeGraph:
    """Microsoft Graph for one personal drive, and its download host. FAIL queues a status or
    "drop" per key: "me", "<id>", "<id>/children", "<id>/content", "<id>/download"."""

    def __init__(self, page=100, token=TOKEN, drive=DRIVE, download="public.dm.files.1drv.com"):
        self.page, self.fail, self.seen = page, {}, []
        self.token, self.drive, self.download = token, drive, download

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        url = request.url
        if url.host == self.download:
            item_id = url.path.rsplit("/", 1)[-1]
            if (failed := self._failed(f"{item_id}/download")) is not None:
                return failed
            body = PDF if item_id != "D!2" or url.params.get("format") else b"pptx"
            return httpx.Response(200, content=body)
        assert url.host == "graph.microsoft.com"
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
        path = url.path.removeprefix("/v1.0")
        if path == "/me/drive":
            owner = {"user": {"displayName": "Ana Lima"}}
            return self._failed("me") or httpx.Response(200, json={"id": DRIVE, "owner": owner})
        if path == "/sites/root":
            return self._failed("me") or httpx.Response(200, json={"id": "contoso.sharepoint.com"})
        _, _, drive, _, item_id, *rest = path.split("/")
        assert drive == self.drive
        key = "/".join([item_id, *rest])
        if (failed := self._failed(key)) is not None:
            return failed
        if item_id not in ITEMS:
            return httpx.Response(404, json={"error": {"code": "itemNotFound"}})
        item = ITEMS[item_id]
        if rest == ["children"]:
            start = int(url.params.get("skip", 0))
            kids = item["kids"][start : start + self.page]
            body = {}
            body["value"] = [{k: v for k, v in ITEMS[c].items() if k != "kids"} for c in kids]
            if start + self.page < len(item["kids"]):
                nxt = f"{GRAPH}/drives/{drive}/items/{item_id}/children?skip={start + self.page}"
                body["@odata.nextLink"] = nxt
            return httpx.Response(200, json=body)
        if rest == ["content"]:
            fmt = url.params.get("format")
            if fmt and item["name"].endswith(".csv"):
                return httpx.Response(406, json={"error": {"code": "notSupported"}})
            q = f"?format={fmt}" if fmt else ""
            where = f"https://{self.download}/y4m/{item_id}{q}"
            return httpx.Response(302, headers={"location": where})
        return httpx.Response(200, json={k: v for k, v in item.items() if k != "kids"})

    def _failed(self, key):
        queue = self.fail.get(key)
        if not queue:
            return None
        what = queue.pop(0)
        if what == "drop":
            raise httpx.ReadError("connection reset")
        if isinstance(what, httpx.Response):
            return what
        return httpx.Response(what, headers={"retry-after": "3"} if what == 429 else {})


def connector(graph=None, accounts="personal", tenant=""):
    graph = graph or FakeGraph()
    sleeps = []

    async def sleep(s):
        sleeps.append(s)

    transport = httpx.MockTransport(graph.handle)
    d = onedrive.OneDrive(
        onedrive.Config(CLIENT, accounts, tenant),
        httpx.AsyncClient(transport=transport),
        sleep=sleep,
    )
    return d, graph, sleeps


def run(coro):
    return asyncio.run(coro)


def pick(item_id):
    return f"{DRIVE}:{item_id}"


def test_onedrive_is_off_until_the_recipe_or_environment_sets_its_client_id(monkeypatch):
    monkeypatch.delenv("TTDR_MICROSOFT_CLIENT_ID", raising=False)
    assert onedrive.Config.load(recipe.OneDrive(), {}) is None
    assert available(recipe.load("recipe.toml").connectors) == {}
    shipped = Connectors(onedrive=recipe.OneDrive(client_id=CLIENT.upper(), accounts="personal"))
    found = available(shipped, {})
    assert list(found) == ["onedrive"]
    assert found["onedrive"].name == "OneDrive"
    assert found["onedrive"].public() == {
        "client_id": CLIENT,
        "accounts": "personal",
        "authority": "https://login.microsoftonline.com/consumers",
        "scopes": ["Files.Read"],
        "sign_ins": [
            {
                "accounts": "personal",
                "label": "Personal Microsoft account",
                "reaches": "OneDrive",
                "authority": "https://login.microsoftonline.com/consumers",
                "scopes": ["Files.Read"],
            }
        ],
        "redirect_path": "/auth/msal-redirect",
        "origins": ["http://localhost:3000", "http://127.0.0.1:3000"],
        "importable": sorted({*onedrive.STORED, *onedrive.CONVERTED}),
    }
    assert {".pdf", ".docx", ".pptx", ".xlsx", ".md"} <= set(
        found["onedrive"].public()["importable"]
    )
    dev = onedrive.Config.load(
        recipe.OneDrive(client_id=CLIENT), {"TTDR_MICROSOFT_CLIENT_ID": "1" * 8 + CLIENT[8:]}
    )
    assert dev.client_id.startswith("11111111")


def test_a_client_id_that_isnt_an_application_id_turns_onedrive_off(caplog):
    with pytest.raises(ValueError, match="Application \\(client\\) ID"):
        onedrive.Config.load(recipe.OneDrive(client_id="my-app"), {})
    assert available(Connectors(onedrive=recipe.OneDrive(client_id="my-app")), {}) == {}
    assert "OneDrive import is off" in caplog.text


def test_an_opaque_personal_token_is_accepted_when_graph_honours_it():
    d, graph, _ = connector()
    run(d.authorize(TOKEN))
    assert str(graph.seen[0].url).startswith(f"{GRAPH}/me/drive")
    assert TOKEN not in str(graph.seen[0].url)


@pytest.mark.parametrize(
    "token, fail, error, message",
    [
        ("", None, AuthError, "missing"),
        ("has space", None, AuthError, "missing"),
        ("x" * (onedrive.MAX_TOKEN + 1), None, AuthError, "missing"),
        ("EwB-someone-elses", None, AuthError, "expired"),
        (TOKEN, 403, AuthError, "can't read OneDrive"),
        (TOKEN, [503] * http.ATTEMPTS, Unavailable, "busy"),
    ],
)
def test_tokens_graph_refuses_are_refused(token, fail, error, message):
    graph = FakeGraph()
    if fail:
        graph.fail["me"] = fail if isinstance(fail, list) else [fail]
    d, _, _ = connector(graph)
    with pytest.raises(error, match=message):
        run(d.authorize(token))


def test_picked_files_resolve_to_what_the_library_reads():
    d, _, _ = connector()

    def one(item_id, mb=2):
        found = run(d.resolve(TOKEN, pick(item_id), Limits(max_bytes=mb * 2**20)))
        assert len(found.items) == 1
        return found.items[0]

    q3 = Item(
        pick("D!1"), "Q3 report.pdf", len(PDF), web_url=Q3_URL, version='"{E1},3"', store="OneDrive"
    )
    assert one("D!1") == q3
    assert one("D!2") == Item(pick("D!2"), "Plan.pptx", 0, (("pdf", ".pdf"),), store="OneDrive")
    assert one("D!3").name == "data.txt"
    assert one("L!1") == q3
    for item_id, message in [
        ("D!4", ".jpg can't be read"),
        ("D!5", "OneNote"),
        ("D!6", "2 MB import limit"),
        ("D!7", "recycle bin"),
        ("X!404", "not found"),
        ("S!1", "not a SharePoint or OneDrive item"),
    ]:
        with pytest.raises(ConnectorError, match=message):
            one(item_id)


@pytest.mark.parametrize(
    "bad",
    ["../../me", f"{DRIVE}/x:1", f"{DRIVE}:a b", f"{DRIVE}:1?x=1", "x" * 129 + ":1", "nodrive"],
)
def test_item_ids_are_checked_before_any_url_is_built(bad):
    d, graph, _ = connector()
    with pytest.raises(ConnectorError, match="not a SharePoint or OneDrive item"):
        run(d.resolve(TOKEN, bad, Limits()))
    assert graph.seen == []


def test_a_picked_folder_brings_its_files_and_says_what_it_left_out(monkeypatch):
    monkeypatch.setattr(onedrive, "DEPTH", 1)
    d, _, _ = connector(FakeGraph(page=2))
    found = run(d.resolve(TOKEN, pick("F!1"), Limits(max_bytes=2 * 2**20)))
    assert [i.name for i in found.items] == ["Q3 report.pdf", "Plan.pptx", "data.txt"]
    assert sorted(found.skipped) == [
        "Reports: 1 item left out (.jpg can't be read; use PDF, Word, PowerPoint, Excel, text or Markdown)",
        "Reports: 1 item left out (OneNote notebooks can't be imported; export their pages to PDF)",
        "Reports: 1 item left out (folders more than 1 levels down)",
        "Reports: 1 item left out (shared with you by others; not supported yet)",
    ]
    empty = run(d.resolve(TOKEN, pick("F!9"), Limits()))
    assert empty.items == [] and empty.skipped == ["Empty: the folder is empty"]


def test_a_folder_stops_at_the_import_file_limit():
    d, _, _ = connector()
    found = run(d.resolve(TOKEN, pick("F!1"), Limits(max_files=1)))
    assert [i.name for i in found.items] == ["Q3 report.pdf"]
    assert "Reports: stopped at 1 files, the most an import takes" in found.skipped


def test_a_listing_page_from_another_host_is_refused():
    graph = FakeGraph(page=1)
    real = graph.handle

    def elsewhere(request):
        r = real(request)
        if request.url.path.endswith("/children") and r.status_code == 200:
            body = r.json() | {"@odata.nextLink": "https://evil.example/next"}
            return httpx.Response(200, json=body)
        return r

    graph.handle = elsewhere
    d, _, _ = connector(graph)
    with pytest.raises(ConnectorError, match="somewhere unexpected"):
        run(d.resolve(TOKEN, pick("F!2"), Limits()))


def test_prepare_flattens_folders_dedupes_and_holds_the_limits_across_picks():
    d, _, _ = connector()
    picks = [pick("D!1"), pick("F!2"), pick("F!1"), pick("D!4")]
    prepared = run(prepare(d, TOKEN, picks, Limits(max_files=4, max_bytes=3 * 2**20)))
    assert [r.name for r in prepared.remotes] == [
        "Q3 report.pdf",
        "data.txt",
        "Plan.pptx",
        "OneDrive file d4648f06c91d",
    ]
    assert prepared.remotes[-1].error.startswith(".jpg can't be read")
    assert "1 more files: an import takes at most 3 MB" in prepared.skipped
    assert any(s.startswith("Reports: 1 item left out (OneNote") for s in prepared.skipped)

    counted = run(prepare(d, TOKEN, [pick("F!1")], Limits(max_files=2)))
    assert [r.name for r in counted.remotes] == ["Q3 report.pdf", "Plan.pptx"]
    assert "Reports: stopped at 2 files, the most an import takes" in counted.skipped

    small = run(prepare(d, TOKEN, [pick("F!2")], Limits(max_bytes=2 * 2**20)))
    assert [r.name for r in small.remotes] == ["data.txt"]
    assert small.skipped == ["2025: 1 item left out (larger than the 2 MB import limit)"]


def test_downloads_follow_the_redirect_without_the_token():
    d, graph, _ = connector()
    budget = Budget(2**20)
    item = Item(pick("D!1"), "Q3 report.pdf", len(PDF))
    assert run(d.fetch(TOKEN, item, budget)) == ("Q3 report.pdf", PDF)
    api, download = graph.seen
    assert (
        api.url.host == "graph.microsoft.com" and api.headers["authorization"] == f"Bearer {TOKEN}"
    )
    assert download.url.host == "public.dm.files.1drv.com"
    assert "authorization" not in download.headers
    assert budget.left == 2**20 - len(PDF)


def test_a_personalcontent_link_gets_its_own_tempauth_never_the_graph_token():
    graph = FakeGraph(download="my.microsoftpersonalcontent.com")
    graph.fail["D!1/content"] = [
        httpx.Response(
            302,
            headers={"location": "https://my.microsoftpersonalcontent.com/y4m/D!1?tempauth=v1.abc"},
        )
    ]
    graph.fail["D!1/download"] = [401]
    d, graph, _ = connector(graph)
    item = Item(pick("D!1"), "Q3 report.pdf", len(PDF))
    assert run(d.fetch(TOKEN, item, Budget(2**20))) == ("Q3 report.pdf", PDF)
    refused, retried = graph.seen[1], graph.seen[-1]
    assert "authorization" not in refused.headers
    assert retried.headers["authorization"] == "Bearer v1.abc"
    content = [r for r in graph.seen if r.url.host == "my.microsoftpersonalcontent.com"]
    assert content and all(TOKEN not in r.headers.get("authorization", "") for r in content)


def test_a_refused_redirect_falls_back_to_the_items_own_download_link():
    graph = FakeGraph(download="my.microsoftpersonalcontent.com")
    graph.fail["D!1/content"] = [
        httpx.Response(302, headers={"location": "https://my.microsoftpersonalcontent.com/y4m/X!1"})
    ]
    graph.fail["X!1/download"] = [401]
    graph.fail["D!1"] = [
        httpx.Response(
            200,
            json={
                "@microsoft.graph.downloadUrl": "https://my.microsoftpersonalcontent.com/y4m/D!1"
            },
        )
    ]
    d, graph, _ = connector(graph)
    item = Item(pick("D!1"), "Q3 report.pdf", len(PDF))
    assert run(d.fetch(TOKEN, item, Budget(2**20))) == ("Q3 report.pdf", PDF)
    assert (
        graph.seen[-1].url.path.endswith("/D!1") and "authorization" not in graph.seen[-1].headers
    )


def test_a_refused_link_without_tempauth_says_the_link_was_refused():
    graph = FakeGraph(download="my.microsoftpersonalcontent.com")
    graph.fail["D!1/download"] = [401]
    d, _, _ = connector(graph)
    graph.fail["D!1"] = [httpx.Response(200, json={})]
    with pytest.raises(AuthError, match="refused the download link"):
        run(d.fetch(TOKEN, Item(pick("D!1"), "Q3 report.pdf"), Budget(2**20)))


def test_an_expired_sign_in_still_says_so():
    graph = FakeGraph(token="another")
    d, _, _ = connector(graph)
    with pytest.raises(AuthError, match="expired"):
        run(d.fetch(TOKEN, Item(pick("D!1"), "Q3 report.pdf"), Budget(2**20)))


def test_office_files_arrive_as_pdf_and_what_graph_cant_convert_says_so():
    d, graph, _ = connector()
    budget = Budget(2**20)
    pptx = Item(pick("D!2"), "Plan.pptx", 0, onedrive.TO_PDF)
    assert run(d.fetch(TOKEN, pptx, budget)) == ("Plan.pdf", PDF)
    assert graph.seen[0].url.params["format"] == "pdf"
    csv = Item(pick("D!3"), "data.csv", 0, onedrive.TO_PDF)
    with pytest.raises(ConnectorError, match="can't convert"):
        run(d.fetch(TOKEN, csv, budget))


@pytest.mark.parametrize(
    "location",
    [
        "http://public.dm.files.1drv.com/y4m/D!1",
        "https://evil.example/D!1",
        "https://1drv.com.evil.example/D!1",
        "",
    ],
)
def test_a_redirect_anywhere_but_microsofts_content_hosts_is_refused(location):
    graph = FakeGraph()
    graph.fail["D!1/content"] = [httpx.Response(302, headers={"location": location})]
    d, graph, _ = connector(graph)
    with pytest.raises(ConnectorError, match="somewhere unexpected"):
        run(d.fetch(TOKEN, Item(pick("D!1"), "Q3 report.pdf"), Budget(2**20)))
    assert all(r.url.host == "graph.microsoft.com" for r in graph.seen)


def test_rate_limits_server_errors_and_dropped_downloads_are_retried():
    graph = FakeGraph()
    graph.fail["D!1"] = [429, 503]
    graph.fail["D!1/content"] = ["drop"]
    graph.fail["D!1/download"] = [500, "drop"]
    d, _, sleeps = connector(graph)
    budget = Budget(2**20)
    item = run(d.resolve(TOKEN, pick("D!1"), Limits())).items[0]
    assert run(d.fetch(TOKEN, item, budget)) == ("Q3 report.pdf", PDF)
    assert len(sleeps) == 5 and sleeps[0] >= 3
    assert budget.left == 2**20 - len(PDF)


def test_persistent_failures_say_what_to_do():
    graph = FakeGraph()
    graph.fail["D!1"] = [503] * http.ATTEMPTS
    graph.fail["D!2"] = [401]
    graph.fail["D!3"] = [403]
    d, _, _ = connector(graph)
    with pytest.raises(Unavailable, match="busy"):
        run(d.resolve(TOKEN, pick("D!1"), Limits()))
    with pytest.raises(AuthError, match="expired"):
        run(d.resolve(TOKEN, pick("D!2"), Limits()))
    with pytest.raises(ConnectorError, match="refused access"):
        run(d.resolve(TOKEN, pick("D!3"), Limits()))


def test_a_download_over_the_budget_stops_and_gives_its_bytes_back():
    d, _, _ = connector()
    budget = Budget(len(PDF) - 1)
    with pytest.raises(ConnectorError, match="limit"):
        run(d.fetch(TOKEN, Item(pick("D!1"), "Q3 report.pdf"), budget))
    assert budget.left == len(PDF) - 1


def test_work_accounts_sign_in_through_entra_for_sharepoint_and_onedrive():
    work = available(Connectors(onedrive=recipe.OneDrive(client_id=CLIENT)), {})["onedrive"]
    assert work.name == "SharePoint and OneDrive"
    public = work.public()
    assert public["accounts"] == "work"
    assert public["authority"] == "https://login.microsoftonline.com/organizations"
    assert public["scopes"] == ["Files.Read.All", "Sites.Read.All"]
    one = recipe.OneDrive(client_id=CLIENT, tenant=TENANT.upper())
    assert onedrive.Config.load(one, {}).authority == f"https://login.microsoftonline.com/{TENANT}"
    for bad in [
        recipe.OneDrive(client_id=CLIENT, tenant="contoso.com"),
        recipe.OneDrive(client_id=CLIENT, accounts="personal", tenant=TENANT),
    ]:
        with pytest.raises(ValueError, match="tenant"):
            onedrive.Config.load(bad, {})


def test_a_work_token_issued_to_this_app_is_accepted_when_graph_honours_it():
    d, graph, _ = connector(FakeGraph(token=WORK), accounts="work", tenant=TENANT)
    run(d.authorize(WORK))
    assert [str(r.url).split("?")[0] for r in graph.seen] == [f"{GRAPH}/sites/root"]
    v2 = jwt(appid=None, azp=CLIENT.upper(), aud="00000003-0000-0000-c000-000000000000")
    d, _, _ = connector(FakeGraph(token=v2), accounts="work")
    run(d.authorize(v2))


@pytest.mark.parametrize(
    "token, message",
    [
        (TOKEN, "work or school account"),
        (jwt(appid="11111111-2222-3333-4444-555555555555"), "wasn't made for this app"),
        (jwt(aud="https://management.azure.com"), "wasn't made for this app"),
        (jwt(tid="00000000-0000-0000-0000-000000000001"), "one organization only"),
        (jwt(exp=time.time() - 60), "expired"),
        ("a.%%%.c", "work or school account"),
    ],
)
def test_work_tokens_for_another_app_tenant_or_time_are_refused_before_graph(token, message):
    graph = FakeGraph(token=token)
    d, _, _ = connector(graph, accounts="work", tenant=TENANT)
    with pytest.raises(AuthError, match=message):
        run(d.authorize(token))
    assert graph.seen == []


def test_any_account_offers_work_then_personal_sign_in():
    both = recipe.OneDrive(client_id=CLIENT, accounts="any", tenant=TENANT)
    d = available(Connectors(onedrive=both), {})["onedrive"]
    assert d.name == "SharePoint and OneDrive"
    public = d.public()
    assert public["accounts"] == "any"
    assert [(k["accounts"], k["authority"], k["scopes"]) for k in public["sign_ins"]] == [
        (
            "work",
            f"https://login.microsoftonline.com/{TENANT}",
            ["Files.Read.All", "Sites.Read.All"],
        ),
        ("personal", "https://login.microsoftonline.com/consumers", ["Files.Read"]),
    ]
    assert public["sign_ins"][1]["reaches"] == "OneDrive"
    single = connector(accounts="work")[0].public()["sign_ins"]
    assert [k["accounts"] for k in single] == ["work"]


def test_any_account_tells_work_tokens_from_personal_ones():
    d, graph, _ = connector(FakeGraph(token=WORK), accounts="any", tenant=TENANT)
    run(d.authorize(WORK))
    assert [str(r.url).split("?")[0] for r in graph.seen] == [f"{GRAPH}/sites/root"]

    d, graph, _ = connector(FakeGraph(token=TOKEN), accounts="any", tenant=TENANT)
    assert run(d.authorize(TOKEN)) == "Ana Lima"
    assert [str(r.url).split("?")[0] for r in graph.seen] == [f"{GRAPH}/me/drive"]

    mine = jwt(tid=onedrive.MSA_TENANT)
    d, graph, _ = connector(FakeGraph(token=mine), accounts="any", tenant=TENANT)
    assert run(d.authorize(mine)) == "Ana Lima"
    assert [str(r.url).split("?")[0] for r in graph.seen] == [f"{GRAPH}/me/drive"]


@pytest.mark.parametrize(
    "token, message",
    [
        (jwt(tid="00000000-0000-0000-0000-000000000001"), "one organization only"),
        (jwt(appid="11111111-2222-3333-4444-555555555555"), "wasn't made for this app"),
        (
            jwt(tid=onedrive.MSA_TENANT, appid="11111111-2222-3333-4444-555555555555"),
            "wasn't made for this app",
        ),
    ],
)
def test_any_account_still_checks_the_claims_a_token_carries(token, message):
    graph = FakeGraph(token=token)
    d, _, _ = connector(graph, accounts="any", tenant=TENANT)
    with pytest.raises(AuthError, match=message):
        run(d.authorize(token))
    assert graph.seen == []


def test_a_personal_account_that_graph_refuses_cant_read_onedrive():
    graph = FakeGraph(token=TOKEN)
    graph.fail["me"] = [403]
    d, _, _ = connector(graph, accounts="any")
    with pytest.raises(AuthError, match="can't read OneDrive"):
        run(d.authorize(TOKEN))


def test_a_work_account_graph_refuses_cant_read_sharepoint():
    graph = FakeGraph(token=WORK)
    graph.fail["me"] = [403]
    d, _, _ = connector(graph, accounts="work")
    with pytest.raises(AuthError, match="can't read SharePoint"):
        run(d.authorize(WORK))


def test_sharepoint_library_picks_resolve_and_convert_through_microsofts_hosts():
    graph = FakeGraph(token=WORK, drive=SP_DRIVE, download="westus1-mediap.svc.ms")
    d, _, _ = connector(graph, accounts="work")
    found = run(d.resolve(WORK, f"{SP_DRIVE}:F!1", Limits(max_bytes=2 * 2**20)))
    assert [i.name for i in found.items] == ["Q3 report.pdf", "Plan.pptx", "data.txt"]
    plan = found.items[1]
    assert plan.id == f"{SP_DRIVE}:D!2" and plan.exports == (("pdf", ".pdf"),)
    name, body = run(d.fetch(WORK, plan, Budget(2**20)))
    assert (name, body) == ("Plan.pdf", PDF)
    assert WORK not in str(graph.seen[-1].headers)
    graph.fail["D!404"] = [404]
    with pytest.raises(ConnectorError, match="not found in SharePoint"):
        run(d.resolve(WORK, f"{SP_DRIVE}:D!404", Limits()))


def test_the_signed_in_account_is_returned_for_the_files_provenance():
    d, _, _ = connector()
    assert run(d.authorize(TOKEN)) == "Ana Lima"
    d, _, _ = connector(FakeGraph(token=WORK), accounts="work")
    assert run(d.authorize(WORK)) is None
    named = jwt(preferred_username="ana@contoso.com", name="Ana Lima")
    d, _, _ = connector(FakeGraph(token=named), accounts="work")
    assert run(d.authorize(named)) == "ana@contoso.com"


@pytest.mark.parametrize(
    "url, store",
    [
        ("https://contoso.sharepoint.com/sites/finance/Shared%20Documents/a.pdf", "SharePoint"),
        ("https://contoso-my.sharepoint.com/personal/ana/Documents/a.pdf", "OneDrive"),
        ("https://onedrive.live.com/?id=1", "OneDrive"),
        ("", "OneDrive"),
    ],
)
def test_files_say_whether_they_live_in_sharepoint_or_onedrive(url, store):
    meta = {"id": "D!9", "name": "a.pdf", "size": 1, "file": {}, "webUrl": url}
    item = onedrive._file(meta, DRIVE, 2**20)
    assert (item.store, item.web_url) == (store, url)
