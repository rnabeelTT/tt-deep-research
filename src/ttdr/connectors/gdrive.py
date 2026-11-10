"""Google Drive: files a user picks in Google's Picker, fetched with the access token their
browser got from Google.

The browser signs in with Google Identity Services for the `drive.file` scope, so the token
reaches only the files the user picked. The server holds no Google credentials: it checks that
each token was issued to this app's OAuth client, uses it for one import, and never stores or
logs it. Google Docs, Sheets and Slides are exported to PDF (pages for citations), falling back
to text when Google refuses the export as too large; other files download as they are.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

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
from ttdr.recipe import GoogleDrive as Settings

API = "https://www.googleapis.com/drive/v3/files"
TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
SCOPE = "https://www.googleapis.com/auth/drive.file"
READ_SCOPES = frozenset(
    {
        SCOPE,
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive",
    }
)
FILE_ID = re.compile(r"[A-Za-z0-9_-]{10,200}")
APP_ID = re.compile(r"\d{6,20}")
FIELDS = (
    "id,name,mimeType,size,trashed,capabilities/canDownload,shortcutDetails,webViewLink,version"
)
SHORTCUT = "application/vnd.google-apps.shortcut"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
STORED = {
    "application/pdf": ".pdf",
    DOCX: ".docx",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "text/csv": ".txt",
}
"""Drive file types the library reads, and the suffix each is saved under."""
SUFFIXES = {".pdf", ".docx", ".txt", ".md"}
"""Files Drive labels generically (application/octet-stream) are read by their name's suffix."""
EXPORTS = {
    "application/vnd.google-apps.document": (
        ("application/pdf", ".pdf"),
        ("text/markdown", ".md"),
        ("text/plain", ".txt"),
    ),
    "application/vnd.google-apps.presentation": (
        ("application/pdf", ".pdf"),
        ("text/plain", ".txt"),
    ),
    "application/vnd.google-apps.spreadsheet": (
        ("application/pdf", ".pdf"),
        ("text/csv", ".txt"),
    ),
}
"""Google-native files: the export formats to try in order (Drive caps each export at 10 MB)."""
RATE_LIMITS = {"rateLimitExceeded", "userRateLimitExceeded"}
MAX_TOKEN = 4096
ORIGINS = tuple(Connectors().origins)


@dataclass(frozen=True)
class Config:
    """The Google Cloud project's browser-side values; all public by design."""

    client_id: str
    api_key: str
    app_id: str
    origins: tuple[str, ...] = ORIGINS

    @classmethod
    def load(
        cls,
        settings: Settings | None = None,
        env: Mapping[str, str] | None = None,
        origins: tuple[str, ...] = ORIGINS,
    ):
        """The recipe's [connectors.google_drive], each value overridden by TTDR_GOOGLE_CLIENT_ID,
        TTDR_GOOGLE_API_KEY or TTDR_GOOGLE_APP_ID when set (development). None, and the import
        off, unless all three are set; ValueError if one isn't what it should be."""
        settings = settings or Settings()
        env = os.environ if env is None else env
        values = {}
        for key in ("client_id", "api_key", "app_id"):
            override = env.get(f"TTDR_GOOGLE_{key.upper()}", "").strip()
            values[key] = override or getattr(settings, key).strip()
        if not all(values.values()):
            return None
        if not values["client_id"].endswith(".apps.googleusercontent.com"):
            raise ValueError("client_id is not an OAuth client ID (….apps.googleusercontent.com)")
        if not APP_ID.fullmatch(values["app_id"]):
            raise ValueError("app_id is the Cloud project number (digits only)")
        return cls(**values, origins=tuple(origins))


def _reason(r: httpx.Response) -> tuple[str, str]:
    """Google's error reason and message, if the body has them."""
    try:
        error = r.json().get("error", {})
    except ValueError:
        return "", ""
    if not isinstance(error, dict):
        return "", str(error)
    reasons = error.get("errors") or [{}]
    return reasons[0].get("reason", "") or error.get("status", ""), error.get("message", "")


def _named(name: str, suffix: str) -> str:
    stem = name.strip() or "document"
    return stem if stem.lower().endswith(suffix) else f"{stem}{suffix}"


class _TooLargeToExport(Exception):
    pass


def _status_error(r: httpx.Response) -> Exception:
    reason, message = _reason(r)
    if reason == "exportSizeLimitExceeded":
        return _TooLargeToExport()
    if r.status_code == 401:
        return AuthError("Google sign-in expired before the import finished; import it again")
    if r.status_code == 404:
        return ConnectorError("not found, or not shared with this app")
    if r.status_code in RETRY_STATUS or reason in RATE_LIMITS:
        return Unavailable("Google Drive is busy; try again in a minute")
    return ConnectorError(message or reason or f"Google Drive returned {r.status_code}")


def _rate_limited(r: httpx.Response) -> bool:
    return _reason(r)[0] in RATE_LIMITS


class GoogleDrive:
    id = "google-drive"
    name = "Google Drive"

    def __init__(
        self,
        config: Config,
        http: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.config = config
        self.http = http or client(follow_redirects=True)
        self.api = Fetcher("Google Drive", self.http, _status_error, _rate_limited, sleep)

    def public(self) -> dict:
        return {
            "client_id": self.config.client_id,
            "api_key": self.config.api_key,
            "app_id": self.config.app_id,
            "scope": SCOPE,
            "origins": list(self.config.origins),
        }

    async def authorize(self, credential: str) -> str | None:
        """Accept only a live token issued to this app's OAuth client with a Drive scope, so the
        import can't be driven with a token minted for another app."""
        token = credential
        if not token or len(token) > MAX_TOKEN or not token.isascii() or not token.isprintable():
            raise AuthError("Google sign-in is missing; sign in again")
        if any(c.isspace() for c in token):
            raise AuthError("Google sign-in is missing; sign in again")
        try:
            r = await self.http.post(TOKENINFO, data={"access_token": token})
        except httpx.TransportError:
            raise Unavailable("couldn't reach Google to check the sign-in") from None
        if r.status_code != 200:
            raise AuthError("Google sign-in expired; sign in again")
        info = r.json()
        if self.config.client_id not in (info.get("aud"), info.get("azp")):
            raise AuthError("this Google sign-in belongs to another app")
        if not READ_SCOPES & set(str(info.get("scope", "")).split()):
            raise AuthError("this Google sign-in doesn't allow reading the picked files")
        if int(info.get("expires_in") or 0) <= 0:
            raise AuthError("Google sign-in expired; sign in again")

    async def resolve(self, credential: str, item_id: str, limits: Limits) -> Resolved:
        """The picked file's name, size and fetch plan (drive.file can't list a folder, so a
        pick is always one file). Raises ConnectorError for one the library can't take: unknown
        type, too large, trashed, or not downloadable."""
        if not FILE_ID.fullmatch(item_id):
            raise ConnectorError("not a Google Drive file ID")
        params = {"fields": FIELDS, "supportsAllDrives": "true"}
        meta = await self.api.json(f"{API}/{item_id}", params, bearer(credential))
        if meta.get("mimeType") == SHORTCUT:
            target = (meta.get("shortcutDetails") or {}).get("targetId", "")
            if not FILE_ID.fullmatch(target):
                raise ConnectorError("this shortcut points nowhere")
            meta = await self.api.json(f"{API}/{target}", params, bearer(credential))
        name, mime = meta.get("name") or "document", meta.get("mimeType", "")
        if meta.get("trashed"):
            raise ConnectorError("this file is in the trash")
        if (meta.get("capabilities") or {}).get("canDownload") is False:
            raise ConnectorError("the owner turned off downloading this file")
        seen = {
            "web_url": web_link(meta.get("webViewLink")),
            "version": str(meta.get("version", "")),
        }
        if mime in EXPORTS:
            return Resolved([Item(meta["id"], name, 0, EXPORTS[mime], **seen)])
        suffix = STORED.get(mime)
        if not suffix and mime == "application/octet-stream":
            suffix = s if (s := Path(name).suffix.lower()) in SUFFIXES else None
        if not suffix:
            kind = mime or "this file type"
            raise ConnectorError(f"{kind} can't be read; use PDF, Word, text or Google Docs")
        size = int(meta.get("size") or 0)
        if size > limits.max_bytes:
            raise ConnectorError(f"larger than the {limits.max_bytes // 2**20} MB import limit")
        return Resolved([Item(meta["id"], _named(name, suffix), size, **seen)])

    async def fetch(self, credential: str, item: Item, budget: Budget) -> tuple[str, bytes]:
        """The file's bytes and the name to save them under (its suffix follows the format)."""
        if not item.exports:
            params = {"alt": "media", "supportsAllDrives": "true"}
            url = f"{API}/{item.id}"
            return item.name, await self.api.body(url, budget, params, bearer(credential))
        for mime, suffix in item.exports:
            url = f"{API}/{item.id}/export"
            try:
                data = await self.api.body(url, budget, {"mimeType": mime}, bearer(credential))
            except _TooLargeToExport:
                continue
            return _named(item.name, suffix), data
        raise ConnectorError("Google can't export a file this large; download it as PDF instead")

    async def aclose(self) -> None:
        await self.http.aclose()
