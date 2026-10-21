"""Public-only, IP-pinned image/video downloads (no shared client state).

Requires httpx/httpcore support for the ``sni_hostname`` request extension.
The release dependency range must preserve this contract; TLS verification is
never disabled. Raster MIME validation is not image-content validation.
"""

from __future__ import annotations

import asyncio
import ipaddress
import math
import socket
import zlib
from typing import Literal
from urllib.parse import urlsplit

import anyio
import httpx

from muse_console.errors import InvalidRequest

_MAX_REDIRECTS = 5
_MIMES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp", "image/avif"})
_VIDEO_MIMES = frozenset({"video/mp4", "video/webm", "video/quicktime", "video/x-m4v"})
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_TRANSITION_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in ("::ffff:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
)


def _reject(message: str = "Media URL is not an allowed public HTTP(S) destination.") -> None:
    raise InvalidRequest(message)


def _public_ip(value: str) -> str:
    # Zone identifiers and IPv4-mapped/transition addresses are deliberately refused.
    if "%" in value:
        _reject()
    address = ipaddress.ip_address(value)
    if (
        not address.is_global
        or address.is_private
        or address.is_reserved
        or address.is_multicast
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or (address.version == 6 and address.is_site_local)
        or any(address in net for net in _TRANSITION_NETWORKS if net.version == address.version)
    ):
        _reject()
    return str(address)


def _parse_url(value: str) -> httpx.URL:
    # Reject ambiguous inputs before parsers normalize controls, credentials or fragments.
    if not value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        _reject()
    if "\\" in value or "#" in value:
        _reject()
    parts = urlsplit(value)
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not parts.netloc
        or "@" in parts.netloc
        or parts.netloc.endswith(":")
    ):
        _reject()
    default_port = 443 if parts.scheme.lower() == "https" else 80
    if parts.port not in (None, default_port):
        _reject()
    result = httpx.URL(value)
    if not result.host or "%" in result.host:
        _reject()
    return result


async def _pin(url: httpx.URL) -> str:
    try:
        ipaddress.ip_address(url.host)
    except ValueError:
        answers = await asyncio.get_running_loop().getaddrinfo(
            url.host,
            url.port or (443 if url.scheme == "https" else 80),
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
        if not answers:
            _reject()
        addresses = []
        for family, _, _, _, sockaddr in answers:
            if family not in (socket.AF_INET, socket.AF_INET6):
                _reject()
            addresses.append(_public_ip(sockaddr[0]))
        return addresses[0]
    return _public_ip(url.host)


async def _read_media(
    response: httpx.Response, max_bytes: int, allowed_mimes: frozenset[str]
) -> tuple[bytes, str]:
    mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if mime not in allowed_mimes:
        _reject("Media response has an unsupported media type.")
    length = response.headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdecimal():
            _reject("Media response has an invalid length.")
        if int(length) > max_bytes:
            _reject("Media download exceeds the size limit.")
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in {"identity", "gzip", "deflate"}:
        _reject("Media response has an unsupported encoding.")
    decoder = (
        None
        if encoding == "identity"
        else zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS)
    )
    body = bytearray()
    wire_bytes = 0
    async for chunk in response.aiter_raw(chunk_size=64 * 1024):
        wire_bytes += len(chunk)
        if wire_bytes > max_bytes:
            _reject("Media download exceeds the size limit.")
        # Bound the decoder itself, not just the bytes yielded by httpx's decoder.
        decoded = chunk if decoder is None else decoder.decompress(chunk, max_bytes - len(body) + 1)
        body.extend(decoded)
        if len(body) > max_bytes or (decoder is not None and decoder.unconsumed_tail):
            _reject("Media download exceeds the size limit.")
        if decoder is not None and decoder.unused_data:
            _reject("Media response has an invalid encoding.")
    if decoder is not None and not decoder.eof:
        _reject("Media response has an invalid encoding.")
    if not body:
        _reject("Media response is empty.")
    return bytes(body), mime


async def download_public_media(
    url: str,
    *,
    timeout: float = 20,  # noqa: ASYNC109 -- public API; enforced by anyio.fail_after
    max_bytes: int = 33554432,
    kind: Literal["image", "video"] = "image",
) -> tuple[bytes, str]:
    """Download public media within a total DNS/redirect/body time budget.

    All DNS answers must be public. Each hop uses a fresh, proxy-free client and
    a literal IP URL, with the original Host and TLS server name. No cookies,
    authorization or pooled TLS connection can cross a redirect boundary.
    Compressed wire bytes AND decompressed bytes are bounded by ``max_bytes``.
    """
    try:
        if kind not in ("image", "video"):
            _reject("Unsupported media download kind.")
        allowed_mimes = _MIMES if kind == "image" else _VIDEO_MIMES
        if not math.isfinite(timeout) or timeout <= 0 or max_bytes <= 0:
            _reject("Invalid media download limits.")
        with anyio.fail_after(timeout):
            current = _parse_url(url)
            for hop in range(_MAX_REDIRECTS + 1):
                address = await _pin(current)
                target = current.copy_with(host=address)
                host = current.raw_host.decode("ascii")
                host_header = f"[{host}]" if ":" in host else host
                transport = httpx.AsyncHTTPTransport(trust_env=False, verify=True, retries=0)
                async with httpx.AsyncClient(
                    transport=transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=timeout,
                ) as client:
                    async with client.stream(
                        "GET",
                        target,
                        headers={
                            "Host": host_header,
                            "Accept": ", ".join(sorted(allowed_mimes)),
                            "Accept-Encoding": "gzip, deflate",
                        },
                        extensions={"sni_hostname": host},
                    ) as response:
                        if response.status_code in _REDIRECTS:
                            location = response.headers.get("location")
                            if not location or hop == _MAX_REDIRECTS:
                                _reject("Media download has too many or invalid redirects.")
                            # Validate raw location as well: URL.join normalizes fragments.
                            if any(ord(c) <= 32 or ord(c) == 127 for c in location) or any(
                                c in location for c in ("#", "\\")
                            ):
                                _reject()
                            location_parts = urlsplit(location)
                            if location_parts.scheme or location_parts.netloc:
                                _parse_url(
                                    f"{current.scheme}:{location}"
                                    if location.startswith("//")
                                    else location
                                )
                            current = _parse_url(str(current.join(location)))
                            continue
                        if not 200 <= response.status_code < 300:
                            _reject("Media download returned an unsuccessful status.")
                        return await _read_media(response, max_bytes, allowed_mimes)
    except InvalidRequest:
        raise
    except (httpx.HTTPError, httpx.InvalidURL, OSError, ValueError, TimeoutError, zlib.error):
        raise InvalidRequest("Media download failed or timed out.") from None
    raise InvalidRequest("Media download has too many redirects.")


async def download_image(
    url: str,
    *,
    timeout: float = 20,  # noqa: ASYNC109 -- compatibility API
    max_bytes: int = 8 * 1024 * 1024,
) -> tuple[bytes, str]:
    """Download a public raster image, preserving the original 8 MiB default."""
    return await download_public_media(url, timeout=timeout, max_bytes=max_bytes, kind="image")
