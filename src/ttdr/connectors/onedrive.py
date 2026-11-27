"""SharePoint and OneDrive: files and folders a user picks in the UI's Microsoft browser,
fetched through Microsoft Graph with the access token their browser got from Microsoft.

The browser signs in with MSAL (auth code with PKCE, no client secret). The recipe picks the
accounts: work and school accounts (Entra ID) get the delegated, read-only `Files.Read.All` and
`Sites.Read.All` scopes, for their OneDrive and the SharePoint document libraries they can open;
personal Microsoft accounts get `Files.Read`, their own OneDrive; "any" lets each person choose
which kind they sign in with. The server holds no Microsoft
credentials. It checks each token (a work token's claims, then Graph itself), uses it for one
import, and never stores or logs it. A picked folder brings its files, to a depth and within the
import's limits; Office and OpenDocument files Graph can convert arrive as PDF, the rest download
as they are. Download redirects go to Microsoft's content hosts without the token.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from ttdr.connectors import (
    AuthError,
    Budget,
    ConnectorError,
    Item,
    Limits,
    Resolved,
    Unavailable,
    web_link,
)
from ttdr.connectors.http import RETRY_STATUS, Fetcher, bearer, client
from ttdr.recipe import Connectors
from ttdr.recipe import OneDrive as Settings

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
SCOPES = {"work": ("Files.Read.All", "Sites.Read.All"), "personal": ("Files.Read",)}
"""Delegated, read-only. Work accounts reach SharePoint libraries and files shared with them
(the .All scopes, limited to what the person can open); personal accounts their own files."""
GRAPH_AUDIENCES = frozenset(
    {
        "https://graph.microsoft.com",
        "https://graph.microsoft.com/",
        "00000003-0000-0000-c000-000000000000",
    }
)
MSA_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"
"""The tenant personal Microsoft accounts' tokens name, when they are JWTs."""
LABELS = {
    "work": ("Work or school account", "SharePoint and OneDrive"),
    "personal": ("Personal Microsoft account", "OneDrive"),
}
REDIRECT_PATH = "/auth/msal-redirect"
"""The UI page MSAL's sign-in popup returns to; it hands the response to the app and closes."""
CLIENT_ID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
PART = r"[A-Za-z0-9!_-]{1,128}"
PICK = re.compile(rf"({PART}):({PART})")
"""A picked item: its drive ID and item ID, as the UI's browser sends them."""
SELECT = "id,name,size,file,folder,package,remoteItem,parentReference,deleted,webUrl,eTag"
PAGE = 200
DEPTH = 8
"""Folder levels below a picked folder that are imported."""
CONTENT_HOSTS = (
    "1drv.com",
    "1drv.ms",
    "livefilestore.com",
    "microsoftpersonalcontent.com",
    "onedrive.live.com",
    "sharepoint.com",
    "svc.ms",
)
"""Where Graph's download redirects may point (pre-authenticated URLs; the Graph token is never sent
there, only a link's own tempauth value when it refuses the request without it)."""
STORED = {".pdf": ".pdf", ".docx": ".docx", ".txt": ".txt", ".md": ".md", ".markdown": ".md"}
STORED[".csv"] = ".txt"
"""Types the library reads, by suffix, and the suffix each is saved under."""
CONVERTED = frozenset(
    {".doc", ".rtf", ".odt", ".epub", ".htm", ".html", ".eml", ".msg", ".ppt", ".pptx", ".pps"}
    | {".ppsx", ".odp", ".xls", ".xlsx", ".xlsm", ".ods", ".tif", ".tiff"}
)
"""Types Graph converts to PDF (`/content?format=pdf`)."""
TO_PDF = (("pdf", ".pdf"),)
MAX_TOKEN = 16384
ORIGINS = tuple(Connectors().origins)


@dataclass(frozen=True)
class Config:
    """The Microsoft app's browser-side values; public by design."""

    client_id: str
    accounts: str = "work"
    tenant: str = ""
    origins: tuple[str, ...] = ORIGINS

    @property
    def kinds(self) -> tuple[str, ...]:
        """The kinds of account that may sign in, work first."""
        return ("work", "personal") if self.accounts == "any" else (self.accounts,)

    def authority_for(self, kind: str) -> str:
        if kind == "personal":
            return f"{LOGIN}/consumers"
        return f"{LOGIN}/{self.tenant or 'organizations'}"

    @property
    def authority(self) -> str:
        return self.authority_for(self.kinds[0])

    @classmethod
    def load(
        cls,
        settings: Settings | None = None,
        env: Mapping[str, str] | None = None,
        origins: tuple[str, ...] = ORIGINS,
    ):
        """The recipe's [connectors.onedrive], its client ID overridden by
        TTDR_MICROSOFT_CLIENT_ID when set (development). None, and the import off, while unset;
        ValueError if it isn't an application ID."""
        settings = settings or Settings()
        env = os.environ if env is None else env
        client_id = env.get("TTDR_MICROSOFT_CLIENT_ID", "").strip() or settings.client_id.strip()
        if not client_id:
            return None
        if not CLIENT_ID.fullmatch(client_id):
            raise ValueError("client_id is the app registration's Application (client) ID, a GUID")
        tenant = settings.tenant.strip()
        if tenant and (settings.accounts == "personal" or not CLIENT_ID.fullmatch(tenant)):
            raise ValueError(
                'tenant is an Entra tenant ID (a GUID), with accounts = "work" or "any"'
            )
        return cls(client_id.lower(), settings.accounts, tenant.lower(), tuple(origins))


def _error(r: httpx.Response) -> tuple[str, str]:
    """Graph's error code and message, if the body has them."""
    try:
        error = r.json().get("error", {})
    except (ValueError, AttributeError):
        return "", ""
    if not isinstance(error, dict):
        return "", str(error)
    return str(error.get("code", "")), str(error.get("message", ""))


def _status_errors(store: str) -> Callable[[httpx.Response], Exception]:
    def status_error(r: httpx.Response) -> Exception:
        code, message = _error(r)
        if r.status_code in (401, 403):
            # which host refused and why, never the token: tells an expired sign-in from a refused
            # download link
            log.warning(
                "%s %s from %s: %s %s (WWW-Authenticate: %s)",
                store,
                r.status_code,
                r.request.url.host,
                code or "-",
                message[:200] or "-",
                r.headers.get("www-authenticate", "-")[:200],
            )
        if r.status_code == 401:
            return AuthError(
                "Microsoft sign-in expired before the import finished; import it again"
            )
        if r.status_code == 403:
            return ConnectorError(f"{store} refused access to this file")
        if r.status_code == 404:
            return ConnectorError(f"not found in {store}; it may have been moved or deleted")
        if r.status_code in (406, 415) or code == "notSupported":
            return ConnectorError(
                f"{store} can't convert this file to PDF; save it as PDF and pick that"
            )
        if r.status_code in RETRY_STATUS:
            return Unavailable(f"{store} is busy; try again in a minute")
        return ConnectorError(message or code or f"{store} returned {r.status_code}")

    return status_error


_status_error = _status_errors("OneDrive")


def _claims(token: str) -> dict | None:
    """A JWT's payload, unverified (Graph verifies the signature when the token is used), or None
    when the token isn't a JWT: personal-account tokens are opaque."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        claims = json.loads(payload)
    except ValueError:
        return None
    return claims if isinstance(claims, dict) else None


def _content_host(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(
        host == h or host.endswith(f".{h}") for h in CONTENT_HOSTS
    )


def _pick(item_id: str) -> tuple[str, str]:
    if not (m := PICK.fullmatch(item_id)):
        raise ConnectorError("not a SharePoint or OneDrive item")
    return m.group(1), m.group(2)


def _item_url(drive: str, item: str) -> str:
    return f"{GRAPH}/drives/{drive}/items/{item}"


class _Unreadable(Exception):
    """A file in a folder that's left out, and why (counted per reason)."""


def _store(web_url: str) -> str:
    """SharePoint for a site's library; OneDrive for a person's (work: *-my.sharepoint.com)."""
    host = (urlsplit(web_url).hostname or "").lower()
    sharepoint = host.endswith(".sharepoint.com") and "-my." not in host
    return "SharePoint" if sharepoint else "OneDrive"


def _file(meta: dict, drive: str, max_bytes: int) -> Item:
    """The import plan for one file's metadata; _Unreadable with the reason if there is none."""
    name = (meta.get("name") or "document").strip()
    suffix = Path(name).suffix.lower()
    item_id = f"{drive}:{meta['id']}"
    web_url = web_link(meta.get("webUrl"))
    seen = {"web_url": web_url, "version": str(meta.get("eTag") or ""), "store": _store(web_url)}
    if "package" in meta:
        raise _Unreadable("OneNote notebooks can't be imported; export their pages to PDF")
    if suffix in CONVERTED:
        return Item(item_id, name, 0, TO_PDF, **seen)
    if suffix not in STORED:
        raise _Unreadable(
            f"{suffix or 'files without a type'} can't be read; use PDF, Word, PowerPoint,"
            " Excel, text or Markdown"
        )
    size = int(meta.get("size") or 0)
    if size > max_bytes:
        raise _Unreadable(f"larger than the {max_bytes // 2**20} MB import limit")
    saved = STORED[suffix]
    return Item(item_id, name if suffix == saved else f"{Path(name).stem}{saved}", size, **seen)


class OneDrive:
    id = "onedrive"

    def __init__(
        self,
        config: Config,
        http: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.config = config
        self.work = "work" in config.kinds
        self.name = "SharePoint and OneDrive" if self.work else "OneDrive"
        store = {"work": "SharePoint", "personal": "OneDrive"}.get(config.accounts, "Microsoft")
        self.http = http or client()
        self.api = Fetcher(store, self.http, _status_errors(store), sleep=sleep)

    def public(self) -> dict:
        return {
            "client_id": self.config.client_id,
            "accounts": self.config.accounts,
            "authority": self.config.authority,
            "scopes": list(SCOPES[self.config.kinds[0]]),
            "sign_ins": [
                {
                    "accounts": kind,
                    "label": LABELS[kind][0],
                    "reaches": LABELS[kind][1],
                    "authority": self.config.authority_for(kind),
                    "scopes": list(SCOPES[kind]),
                }
                for kind in self.config.kinds
            ],
            "redirect_path": REDIRECT_PATH,
            "origins": list(self.config.origins),
            "importable": sorted({*STORED, *CONVERTED}),
        }

    async def authorize(self, credential: str) -> str | None:
        """Accept a token Graph honours. A work token is a JWT: it must have been issued to this
        app for Graph (and, with a tenant set, in that tenant) before Graph is asked; Graph then
        checks its signature, for the SharePoint root (Sites.Read.All, and every work account has
        one, OneDrive or not). Personal-account tokens are opaque, so Graph is the only judge; the
        drive call needs exactly the Files.Read the app asks for (/me would also need User.Read).
        With both kinds allowed, a token is a work token when it is a JWT from an Entra tenant;
        a JWT from the personal-accounts tenant must still be this app's."""
        token = credential
        if not token or len(token) > MAX_TOKEN or not token.isascii() or not token.isprintable():
            raise AuthError("Microsoft sign-in is missing; sign in again")
        if any(c.isspace() for c in token):
            raise AuthError("Microsoft sign-in is missing; sign in again")
        if self._is_work(token):
            account = self._check_claims(token)
            probe, select = f"{GRAPH}/sites/root", "id"
        else:
            account, probe, select = None, f"{GRAPH}/me/drive", "id,owner"
        try:
            found = await self.api.json(probe, {"$select": select}, bearer(token))
        except AuthError:
            raise AuthError("Microsoft sign-in expired; sign in again") from None
        except Unavailable:
            raise
        except ConnectorError:
            what = "SharePoint" if probe.endswith("/sites/root") else "OneDrive"
            raise AuthError(f"this Microsoft sign-in can't read {what} files") from None
        if account is None:  # personal: the drive's owner
            owner = ((found.get("owner") or {}).get("user") or {}).get("displayName")
            account = owner if isinstance(owner, str) and owner.isprintable() else None
        return account

    def _is_work(self, token: str) -> bool:
        if self.config.accounts != "any":
            return self.config.accounts == "work"
        claims = _claims(token)
        if claims is None:
            return False
        if str(claims.get("tid", "")).lower() != MSA_TENANT:
            return True
        self._check_claims(token, personal=True)
        return False

    def _check_claims(self, token: str, personal: bool = False) -> str | None:
        """The signed-in account, once the claims show the token is this app's, for Graph."""
        claims = _claims(token)
        if claims is None:
            raise AuthError("sign in with a work or school account")
        app = claims.get("appid") or claims.get("azp")
        if str(app).lower() != self.config.client_id or claims.get("aud") not in GRAPH_AUDIENCES:
            raise AuthError("this Microsoft sign-in wasn't made for this app; sign in again")
        tenant = self.config.tenant
        if not personal and tenant and str(claims.get("tid", "")).lower() != tenant:
            raise AuthError("this installation accepts accounts from one organization only")
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)) or exp < time.time():
            raise AuthError("Microsoft sign-in expired; sign in again")
        for claim in ("preferred_username", "upn", "unique_name", "email", "name"):
            if isinstance(v := claims.get(claim), str) and v.isprintable() and 0 < len(v) <= 256:
                return v
        return None

    async def resolve(self, credential: str, item_id: str, limits: Limits) -> Resolved:
        """A picked file, or a picked folder's files to DEPTH levels and no more than LIMITS
        allow; what's left out is counted in `skipped`. Raises ConnectorError for a picked file
        the library can't take."""
        drive, item = _pick(item_id)
        meta = await self._meta(credential, drive, item)
        if remote := meta.get("remoteItem"):
            drive = (remote.get("parentReference") or {}).get("driveId", "")
            _pick(f"{drive}:{remote.get('id', '')}")
            meta = await self._meta(credential, drive, remote["id"])
        if meta.get("deleted"):
            raise ConnectorError("this item is in the recycle bin")
        if "folder" not in meta:
            try:
                return Resolved([_file(meta, drive, limits.max_bytes)])
            except _Unreadable as e:
                raise ConnectorError(str(e)) from None
        return await self._folder(credential, drive, meta, limits)

    async def fetch(self, credential: str, item: Item, budget: Budget) -> tuple[str, bytes]:
        """The file's bytes (as PDF when it converts) and the name to save them under. Graph
        answers with a redirect to a pre-authenticated download URL, followed without the token
        and only to Microsoft's content hosts."""
        drive, item_id = _pick(item.id)
        url = f"{_item_url(drive, item_id)}/content"
        params = {"format": item.exports[0][0]} if item.exports else None
        name = f"{Path(item.name).stem}{item.exports[0][1]}" if item.exports else item.name
        r = await self.api.send(url, params, bearer(credential))
        try:
            if r.status_code >= 400:
                await r.aread()
                raise _status_error(r)
            location = r.headers.get("location", "") if r.is_redirect else None
        finally:
            await r.aclose()
        if location is None:
            return name, await self.api.body(url, budget, params, bearer(credential))
        if not _content_host(location):
            raise ConnectorError(f"{self.api.store} sent the download somewhere unexpected")
        try:
            return name, await self.api.body(location, budget)
        except AuthError:
            pass
        # Personal accounts' links on my.microsoftpersonalcontent.com now refuse the redirect.
        # Try the link Graph hands out on the item itself (@microsoft.graph.downloadUrl), then the
        # redirect with its own tempauth as the bearer. The Graph token is never sent to these
        # hosts; only query parameter names are logged (the values are credentials).
        query = parse_qs(urlsplit(location).query)
        log.warning(
            "%s download link on %s refused; its query has %s",
            self.api.store,
            urlsplit(location).hostname,
            sorted(query) or "nothing",
        )
        if not item.exports:
            meta = await self.api.json(_item_url(drive, item_id), None, bearer(credential))
            link = meta.get("@microsoft.graph.downloadUrl") or ""
            if _content_host(link):
                try:
                    return name, await self.api.body(link, budget)
                except AuthError:
                    log.warning(
                        "%s item download link on %s refused too",
                        self.api.store,
                        urlsplit(link).hostname,
                    )
        if not (tempauth := (query.get("tempauth") or [""])[0]):
            raise AuthError(f"{self.api.store} refused the download link for this file")
        try:
            return name, await self.api.body(location, budget, headers=bearer(tempauth))
        except AuthError:
            raise AuthError(
                f"{self.api.store} refused the download link for this file (a change on "
                "Microsoft's side for personal accounts); upload the file from the Files tab instead"
            ) from None

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _meta(self, credential: str, drive: str, item: str) -> dict:
        return await self.api.json(_item_url(drive, item), {"$select": SELECT}, bearer(credential))

    async def _folder(self, credential: str, drive: str, folder: dict, limits: Limits):
        """Breadth first, so a folder that is too big keeps its top-level files."""
        top = folder.get("name") or "folder"
        found = Resolved([])
        left_out: dict[str, int] = {}

        def leave(reason: str) -> None:
            left_out[reason] = left_out.get(reason, 0) + 1

        queue = [(folder["id"], 0)]
        while queue:
            folder_id, depth = queue.pop(0)
            async for child in self._children(credential, drive, folder_id):
                if "remoteItem" in child:
                    leave("shared with you by others; not supported yet")
                    continue
                if "folder" in child:
                    if depth + 1 > DEPTH:
                        leave(f"folders more than {DEPTH} levels down")
                    else:
                        queue.append((child["id"], depth + 1))
                    continue
                if len(found.items) >= limits.max_files:
                    found.skipped.append(
                        f"{top}: stopped at {limits.max_files} files, the most an import takes"
                    )
                    return self._summarize(found, top, left_out)
                try:
                    found.items.append(_file(child, drive, limits.max_bytes))
                except _Unreadable as e:
                    leave(str(e))
        return self._summarize(found, top, left_out)

    @staticmethod
    def _summarize(found: Resolved, top: str, left_out: dict[str, int]) -> Resolved:
        for reason, n in left_out.items():
            what = "1 item" if n == 1 else f"{n} items"
            found.skipped.append(f"{top}: {what} left out ({reason})")
        if not found.items and not found.skipped:
            found.skipped.append(f"{top}: the folder is empty")
        return found

    async def _children(self, credential: str, drive: str, folder: str):
        url: str | None = f"{_item_url(drive, folder)}/children"
        params: dict | None = {"$select": SELECT, "$top": str(PAGE)}
        while url:
            page = await self.api.json(url, params, bearer(credential))
            for child in page.get("value") or []:
                ok = isinstance(child, dict) and isinstance(child.get("id"), str)
                if ok and PICK.fullmatch(f"{drive}:{child['id']}"):
                    yield child
            url, params = page.get("@odata.nextLink"), None
            if url and not url.startswith(f"{GRAPH}/"):
                raise ConnectorError(
                    f"{self.api.store} sent a folder listing from somewhere unexpected"
                )
