from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import Message
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


@dataclass(frozen=True)
class ProviderHttpRequest:
    method: str
    url: str
    query: Mapping[str, str]
    headers: Mapping[str, str] = field(repr=False)


@dataclass(frozen=True)
class ProviderHttpResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


class ProviderHttpTransport(Protocol):
    def send(self, request: ProviderHttpRequest) -> ProviderHttpResponse: ...


class _UrlResponse(Protocol):
    status: int
    headers: Message

    def read(self, amount: int | None = None) -> bytes: ...

    def __enter__(self) -> _UrlResponse: ...

    def __exit__(self, *args: object) -> None: ...


class _UrlOpener(Protocol):
    def __call__(self, request: Request, *, timeout: float) -> _UrlResponse: ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())


def _open_without_redirects(request: Request, *, timeout: float) -> _UrlResponse:
    return cast(_UrlResponse, _NO_REDIRECT_OPENER.open(request, timeout=timeout))


class UrllibProviderHttpTransport:
    _MAX_RESPONSE_BYTES = 8 * 1024 * 1024

    def __init__(
        self,
        *,
        allowed_hosts: frozenset[str],
        opener: _UrlOpener | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("source_provider_timeout_invalid")
        if not allowed_hosts or any(
            not host or urlsplit(f"https://{host}").hostname != host or ":" in host
            for host in allowed_hosts
        ):
            raise ValueError("source_provider_allowed_hosts_invalid")
        self._allowed_hosts = allowed_hosts
        self._opener = opener or _open_without_redirects
        self._timeout_seconds = timeout_seconds

    def send(self, request: ProviderHttpRequest) -> ProviderHttpResponse:
        parsed = urlsplit(request.url)
        if (
            request.method != "GET"
            or parsed.scheme != "https"
            or parsed.hostname not in self._allowed_hosts
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
        ):
            raise ValueError("source_provider_url_forbidden")
        query = urlencode(sorted(request.query.items()))
        url = f"{request.url}?{query}" if query else request.url
        urllib_request = Request(url, headers=dict(request.headers), method=request.method)
        try:
            with self._opener(urllib_request, timeout=self._timeout_seconds) as response:
                return self._bounded_response(
                    status_code=response.status,
                    body=response.read(self._MAX_RESPONSE_BYTES + 1),
                    headers=dict(response.headers.items()),
                )
        except HTTPError as error:
            return self._bounded_response(
                status_code=error.code,
                body=error.read(self._MAX_RESPONSE_BYTES + 1),
                headers=dict(error.headers.items()) if error.headers is not None else {},
            )
        except URLError:
            return ProviderHttpResponse(
                status_code=503,
                body=b'{"message":"provider transport unavailable"}',
            )

    def _bounded_response(
        self,
        *,
        status_code: int,
        body: bytes,
        headers: Mapping[str, str],
    ) -> ProviderHttpResponse:
        if len(body) > self._MAX_RESPONSE_BYTES:
            return ProviderHttpResponse(
                status_code=502,
                body=b'{"message":"provider response too large"}',
            )
        return ProviderHttpResponse(status_code=status_code, body=body, headers=headers)
