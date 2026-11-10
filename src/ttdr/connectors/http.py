"""HTTP for connectors: GETs to a store's API with one retry policy, and downloads within an
import's byte budget.

Rate limits (429, or a store-specific 403), server errors and failed connections are retried
with exponential backoff and jitter, honouring Retry-After; a download cut off part-way starts
over. A failed status is handed to the store's own reading of it, so people see its reason.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

import httpx

from ttdr.connectors import Budget, ConnectorError, Unavailable

RETRY_STATUS = {429, 500, 502, 503, 504}
ATTEMPTS = 5
MAX_BACKOFF_S = 32.0


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def client(follow_redirects: bool = False, **options) -> httpx.AsyncClient:
    """Redirects are not followed unless asked; when they are, httpx drops the Authorization
    header on a redirect to another host."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(60, connect=10), follow_redirects=follow_redirects, **options
    )


class Fetcher:
    """One store's GETs. STATUS_ERROR turns a failed response (body read) into the exception
    people see; RATE_LIMITED says whether a 403 is really a rate limit."""

    def __init__(
        self,
        store: str,
        http: httpx.AsyncClient,
        status_error: Callable[[httpx.Response], Exception],
        rate_limited: Callable[[httpx.Response], bool] = lambda r: False,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.store, self.http, self.sleep = store, http, sleep
        self.status_error, self.rate_limited = status_error, rate_limited

    async def send(
        self, url: str, params: dict | None = None, headers: dict | None = None
    ) -> httpx.Response:
        """A streamed GET, retried; the last failed response is returned unread-from for the
        caller, not raised."""
        for attempt in range(ATTEMPTS):
            last = attempt == ATTEMPTS - 1
            try:
                request = self.http.build_request("GET", url, params=params, headers=headers)
                r = await self.http.send(request, stream=True)
            except httpx.TransportError:
                if last:
                    raise Unavailable(f"couldn't reach {self.store}") from None
                await self.backoff(attempt, None)
                continue
            retry = r.status_code in RETRY_STATUS
            if r.status_code == 403:
                await r.aread()
                retry = self.rate_limited(r)
            if not retry or last:
                return r
            after = r.headers.get("retry-after")
            await r.aclose()
            await self.backoff(attempt, after)
        raise AssertionError("unreachable")

    async def backoff(self, attempt: int, retry_after: str | None) -> None:
        wait = 2**attempt + random.random()
        if retry_after and retry_after.isdigit():
            wait = max(wait, float(retry_after))
        await self.sleep(min(MAX_BACKOFF_S, wait))

    async def json(self, url: str, params: dict | None = None, headers: dict | None = None):
        r = await self.send(url, params, headers)
        try:
            await r.aread()
        finally:
            await r.aclose()
        if r.status_code >= 400:
            raise self.status_error(r)
        if r.is_redirect:
            raise ConnectorError(f"{self.store} answered with a redirect")
        try:
            return r.json()
        except ValueError:
            raise Unavailable(f"{self.store} sent an unreadable answer") from None

    async def body(
        self, url: str, budget: Budget, params: dict | None = None, headers: dict | None = None
    ) -> bytes:
        """The response body, within BUDGET; a download cut off part-way starts over."""
        for attempt in range(ATTEMPTS):
            r = await self.send(url, params, headers)
            chunks: list[bytes] = []
            try:
                if r.status_code >= 400 or r.is_redirect:
                    await r.aread()
                    if r.is_redirect:
                        raise ConnectorError(f"{self.store} answered with a redirect")
                    raise self.status_error(r)
                async for chunk in r.aiter_bytes():
                    budget.take(len(chunk))
                    chunks.append(chunk)
            except httpx.TransportError:
                budget.give(sum(map(len, chunks)))
                if attempt == ATTEMPTS - 1:
                    raise Unavailable(f"the download from {self.store} kept failing") from None
                await self.backoff(attempt, None)
                continue
            except BaseException:
                budget.give(sum(map(len, chunks)))
                raise
            finally:
                await r.aclose()
            if not chunks:
                raise ConnectorError("this file is empty")
            return b"".join(chunks)
        raise AssertionError("unreachable")
