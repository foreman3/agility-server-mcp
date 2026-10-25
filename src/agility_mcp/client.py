"""Bounded async HTTP client. Only explicitly read-only requests may retry."""

from __future__ import annotations

import asyncio
import contextvars
import json
import random
import re
from builtins import list as List
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from . import __version__
from .config import Config
from .credentials import CredentialStore, authorization
from .errors import AgilityError
from .items import (
    ITEM_TYPES,
    canonical_oid,
    item_selection,
    normalize_query_items,
    prepare_queries,
    with_parent,
)
from .xml import assert_name, asset_xml, oid_path, parse_assets, parse_xml

before_attempt: contextvars.ContextVar[Callable[[], None] | None] = contextvars.ContextVar(
    "agility_before_attempt", default=None
)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class Limiter:
    def __init__(self, maximum: int):
        self.semaphore = asyncio.Semaphore(maximum)
        self.waiting = 0

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        if self.semaphore.locked() and self.waiting >= 64:
            raise AgilityError(
                "Too many pending Agility requests. Try again after current requests finish."
            )
        self.waiting += 1
        try:
            await self.semaphore.acquire()
        finally:
            self.waiting -= 1
        try:
            yield
        finally:
            self.semaphore.release()


def retry_delay(header: str | None, attempt: int) -> float:
    if header is not None:
        try:
            supplied = float(header)
        except ValueError:
            try:
                supplied = (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                supplied = float("nan")
        if supplied == supplied:
            return max(0, min(supplied, 30))
    return min(0.5 * 2**attempt + random.random() * 0.2, 10)


def check_page(size: int, start: int) -> None:
    if (
        type(size) is not int
        or not 1 <= size <= 1000
        or type(start) is not int
        or not 0 <= start <= 9007199254740991
    ):
        raise AgilityError("pageSize must be 1–1000 and start a nonnegative safe integer.")


class AgilityClient:
    def __init__(self, config: Config, store: CredentialStore, *, http: httpx.AsyncClient):
        self.config = config
        self.store = store
        self.http = http
        self.limiter = Limiter(config.maxConcurrency)

    async def request(
        self,
        path: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        body: str | None = None,
        json: bool = False,
        readOnly: bool | None = None,
        authenticated: bool = True,
    ) -> Any:
        if not re.fullmatch(
            r"(?:meta\.v1(?:/[A-Za-z0-9_]+){0,2}|query\.v1|rest-1\.(?:oauth\.)?v1/(?:Data|Hist|New)/[A-Za-z0-9_]+(?:/[0-9]+){0,2})",
            path,
        ):
            raise AgilityError("Invalid API path.")
        read_only = method == "GET" if readOnly is None else readOnly
        async with self.limiter.slot():
            headers = {
                "Accept": "application/json" if json else "application/xml",
                "User-Agent": f"agility-wincred-mcp/{__version__}",
            }
            if authenticated:
                try:
                    async with asyncio.timeout(self.config.timeoutMs / 1000):
                        headers["Authorization"] = await authorization(self.store, self.config)
                except TimeoutError:
                    raise AgilityError(
                        "Windows Credential Manager timed out while loading the Agility credential."
                    ) from None
            if body is not None:
                if len(body.encode("utf-8")) > 1024 * 1024:
                    raise AgilityError("Request body exceeds 1 MiB.")
                headers["Content-Type"] = (
                    "application/json" if json else "application/xml; charset=utf-8"
                )
            retries = self.config.retries if read_only else 0
            for attempt in range(retries + 1):
                if callback := before_attempt.get():
                    callback()
                received = False
                delay = None
                try:
                    async with asyncio.timeout(self.config.timeoutMs / 1000):
                        async with self.http.stream(
                            method,
                            self.config.baseUrl + "/" + path,
                            params={k: v for k, v in (params or {}).items() if v is not None},
                            content=body.encode() if body is not None else None,
                            headers=headers,
                            follow_redirects=False,
                        ) as response:
                            received = True
                            status = response.status_code
                            if read_only and status in (429, 502, 503, 504) and attempt < retries:
                                delay = retry_delay(response.headers.get("retry-after"), attempt)
                            elif not response.is_success:
                                hint = {
                                    401: "Check the stored credential, auth mode, and REST endpoint.",
                                    403: "The Agility account lacks permission.",
                                    404: "The asset or endpoint was not found, or is not visible to this account.",
                                }.get(
                                    status,
                                    "Redirects are blocked. Check the instance URL and SSO/API configuration."
                                    if 300 <= status < 400
                                    else "Check asset metadata, required fields, and query syntax.",
                                )
                                raise AgilityError(
                                    f"Agility HTTP {status}. {hint}"
                                    + (
                                        " Verify the asset before retrying the mutation."
                                        if not read_only
                                        else ""
                                    ),
                                    status,
                                )
                            else:
                                try:
                                    length = int(response.headers.get("content-length", "0"))
                                except ValueError:
                                    length = 0
                                maximum = self.config.maxResponseBytes
                                if length > maximum:
                                    raise AgilityError(
                                        "Agility response exceeds maxResponseBytes. Reduce the page size or selected fields."
                                    )
                                data = bytearray()
                                async for chunk in response.aiter_bytes():
                                    if len(data) + len(chunk) > maximum:
                                        raise AgilityError(
                                            "Agility response exceeds maxResponseBytes. Reduce the page size or selected fields."
                                        )
                                    data.extend(chunk)
                                text = data.decode("utf-8", errors="replace")
                                if not text.strip():
                                    return None
                                if json:
                                    try:
                                        return __import__("json").loads(text)
                                    except ValueError:
                                        raise AgilityError(
                                            "Agility returned invalid JSON. Check the instance URL and authentication configuration."
                                        ) from None
                                return text
                except AgilityError:
                    raise
                except (TimeoutError, httpx.TimeoutException):
                    raise AgilityError(
                        "Agility request timed out."
                        + (
                            " The mutation may have completed; verify the asset before retrying."
                            if not read_only
                            else ""
                        )
                    ) from None
                except (httpx.HTTPError, OSError):
                    if read_only and not received and attempt < retries:
                        delay = retry_delay(None, attempt)
                    else:
                        raise AgilityError(
                            "Agility network request failed. Check connectivity and TLS trust."
                            + (
                                " The mutation may have completed; verify the asset before retrying."
                                if not read_only
                                else ""
                            )
                        ) from None
                if delay is not None:
                    await asyncio.sleep(delay)

    async def metadata(
        self, *, assetType: str | None = None, member: str | None = None
    ) -> dict[str, Any]:
        if member and not assetType:
            raise AgilityError("assetType is required when member is specified.")
        path = "/".join(
            ["meta.v1"] + [assert_name(value) for value in (assetType, member) if value]
        )
        return parse_xml(await self.request(path, authenticated=False))

    async def list(
        self,
        *,
        assetType: str,
        select: List[str] | None = None,
        where: str | None = None,
        sort: List[str] | None = None,
        find: str | None = None,
        findIn: List[str] | None = None,
        pageSize: int = 100,
        start: int = 0,
        asOf: str | None = None,
    ) -> dict[str, Any]:
        assert_name(assetType)
        check_page(pageSize, start)
        result = parse_assets(
            await self.request(
                f"{self.config.restEndpoint}/{'Hist' if asOf else 'Data'}/{assetType}",
                params={
                    "sel": ",".join(
                        item_selection(assetType, select if select is not None else ["Name"])
                    ),
                    "where": where,
                    "sort": ",".join(sort) if sort is not None else None,
                    "find": find,
                    "findin": ",".join(findIn) if findIn is not None else None,
                    "page": f"{pageSize},{start}",
                    "asof": asOf,
                },
            )
        )
        end = start + len(result["assets"])
        more = end < result["total"] if "total" in result else bool(result["assets"])
        result |= {
            "assets": [with_parent(item) for item in result["assets"]],
            "start": start,
            "pageSize": pageSize,
            "nextStart": end if more and result["assets"] else None,
        }
        if more and not result["assets"]:
            result |= {
                "paginationStatus": "stalled",
                "paginationWarning": "Agility reported more assets but returned an empty page.",
            }
        return result

    async def get(self, *, oid: str, select: List[str] | None = None) -> dict[str, Any]:
        result = parse_assets(
            await self.request(
                f"{self.config.restEndpoint}/Data/{oid_path(oid)}",
                params={
                    "sel": ",".join(
                        item_selection(
                            oid.split(":")[0], select if select is not None else ["Name"]
                        )
                    )
                },
            )
        )
        return result | {"assets": [with_parent(item) for item in result["assets"]]}

    async def history(
        self, *, oid: str, select: List[str] | None = None, pageSize: int = 100, start: int = 0
    ) -> dict[str, Any]:
        check_page(pageSize, start)
        result = parse_assets(
            await self.request(
                f"{self.config.restEndpoint}/Hist/{oid_path(oid, False)}",
                params={
                    "sel": ",".join(
                        item_selection(
                            oid.split(":")[0],
                            select
                            if select is not None
                            else ["Name", "ChangeDateUTC", "ChangedBy.Name"],
                        )
                    ),
                    "page": f"{pageSize},{start}",
                },
            )
        )
        return result | {"assets": [with_parent(item) for item in result["assets"]]}

    async def template(self, *, assetType: str, context: str | None = None) -> dict[str, Any]:
        if context:
            oid_path(context, False)
        result = parse_assets(
            await self.request(
                f"{self.config.restEndpoint}/New/{assert_name(assetType)}", params={"ctx": context}
            )
        )
        return result | {"assets": [with_parent(item, assetType) for item in result["assets"]]}

    async def query(self, *, queries: List[dict[str, Any]]) -> Any:
        return normalize_query_items(
            await self.request(
                "query.v1",
                method="POST",
                body=dumps(prepare_queries(queries)),
                json=True,
                readOnly=True,
            )
        )

    async def detail(self, **args: Any) -> dict[str, Any]:
        from .detail import get_item_detail

        return await get_item_detail(self, **args)

    async def related(self, **args: Any) -> dict[str, Any]:
        from .detail import related_items

        return await related_items(self, **args)

    def ensure_writes(self) -> None:
        if not self.config.enableWrites:
            raise AgilityError("Writes are disabled. Start the server with --enable-writes.")

    async def mutate(
        self,
        *,
        assetType: str | None = None,
        oid: str | None = None,
        attributes: dict[str, Any] | None = None,
        relations: List[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.ensure_writes()
        path = oid_path(oid, False) if oid else assert_name(assetType or "")
        text = await self.request(
            f"{self.config.restEndpoint}/Data/{path}",
            method="POST",
            body=asset_xml(attributes, relations),
            readOnly=False,
        )
        result = self.mutation_result(text)
        if len(result.get("assets", [])) == 1:
            item = result["assets"][0]
            current = canonical_oid(item.get("oid"))
            if (
                current
                and current.split(":")[0] in ITEM_TYPES
                and item.get("parentStatus") == "unavailable"
            ):
                try:
                    read = (await self.get(oid=current, select=["Name", "Number"]))["assets"]
                    if read and canonical_oid(read[0].get("oid")) == current:
                        result["assets"][0] = read[0] | {
                            "attributes": item["attributes"] | read[0]["attributes"],
                            "relations": item["relations"] | read[0]["relations"],
                        }
                    if result["assets"][0].get("parentStatus") == "unavailable":
                        result["hierarchyWarnings"] = [
                            "Mutation succeeded, but the API did not return parent information."
                        ]
                except Exception:
                    result["hierarchyWarnings"] = [
                        "Mutation succeeded, but parent enrichment failed. Read the item to resolve its hierarchy; do not repeat the mutation."
                    ]
        return result

    async def operation(self, *, oid: str, operation: str) -> dict[str, Any]:
        self.ensure_writes()
        assert_name(operation)
        if operation not in self.config.allowOperations:
            raise AgilityError(
                "This operation is not in the operator-configured allowOperations list."
            )
        return self.mutation_result(
            await self.request(
                f"{self.config.restEndpoint}/Data/{oid_path(oid, False)}",
                method="POST",
                params={"op": operation},
                readOnly=False,
            )
        )

    def mutation_result(self, text: str | None) -> dict[str, Any]:
        if not text:
            return {"completed": True}
        try:
            result = parse_assets(text)
            return result | {"assets": [with_parent(item) for item in result["assets"]]}
        except AgilityError:
            raise AgilityError(
                "Agility accepted the mutation but returned an unexpected result. Verify the asset before retrying."
            ) from None
