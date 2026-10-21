from __future__ import annotations

import asyncio
import gzip
import socket
import ssl
import zlib

import httpx
import pytest

from muse_console.core import safe_download as sd
from muse_console.errors import InvalidRequest


class Chunks(httpx.AsyncByteStream):
    def __init__(self, *chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.fixture(autouse=True)
async def offline(monkeypatch):
    async def no_dns(*args, **kwargs):
        raise AssertionError("Unexpected DNS access")

    async def no_transport(*args, **kwargs):
        raise AssertionError("Unexpected network access")

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", no_dns)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_transport)
    yield
    # Drain nested httpx stream finalizers before the per-test loop is closed.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.fixture
def dns(monkeypatch):
    calls = []
    answers = ["8.8.8.8"]

    async def resolve(self, host, port, **kwargs):
        calls.append((host, port))
        return [
            (
                socket.AF_INET6 if ":" in ip else socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (ip, port),
            )
            for ip in answers
        ]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", resolve)
    return calls, answers


@pytest.fixture
def network(monkeypatch):
    requests = []
    responses = []
    options = []
    original_init = httpx.AsyncHTTPTransport.__init__

    def init(self, *args, **kwargs):
        options.append(kwargs)
        original_init(self, *args, **kwargs)

    async def handle(self, request):
        requests.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "__init__", init)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    return requests, responses, options


def image(body=b"image", **headers):
    return httpx.Response(
        200, headers={"content-type": "image/png", **headers}, stream=Chunks(body)
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.org/x",
        "http://user:secret@example.org/x",
        "http://@example.org/x",
        "http://example.org/x#",
        "http://example.org:8080/x",
        "https://example.org:80/x",
        "http://example.org:/x",
        "http://example.org/\nx",
        "http://example.org\\@8.8.8.8/x",
        "http:///x",
        "http://[fe80::1%25eth0]/x",
        "http://127.0.0.1/x",
        "http://10.1.2.3/x",
        "http://169.254.169.254/x",
        "http://100.64.0.1/x",
        "http://0.0.0.0/x",
        "http://224.0.0.1/x",
        "http://240.0.0.1/x",
        "http://192.0.2.1/x",
        "http://[::1]/x",
        "http://[::ffff:8.8.8.8]/x",
        "http://[fc00::1]/x",
        "http://[ff02::1]/x",
        "http://[64:ff9b::808:808]/x",
        "http://[2002:808:808::1]/x",
    ],
)
async def test_reject_unsafe_urls(url):
    with pytest.raises(InvalidRequest):
        await sd.download_image(url)


@pytest.mark.parametrize(
    "answers", [[], ["127.0.0.1"], ["8.8.8.8", "10.0.0.1"], ["8.8.8.8", "::ffff:8.8.8.8"]]
)
async def test_all_dns_results_must_be_public(dns, answers):
    dns[1][:] = answers
    with pytest.raises(InvalidRequest):
        await sd.download_image("https://example.org/image")


async def test_pinned_https_host_sni_no_environment(dns, network, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("SSL_CERT_FILE", "nonexistent-cert-file")
    requests, responses, options = network
    responses.append(image())
    assert await sd.download_image("https://example.org:443/x?token=secret") == (
        b"image",
        "image/png",
    )
    assert dns[0] == [("example.org", 443)]
    assert requests[0].url.host == "8.8.8.8"
    assert requests[0].url.raw_path == b"/x?token=secret"
    assert requests[0].headers["host"] == "example.org"
    assert requests[0].extensions["sni_hostname"] == "example.org"
    assert options == [{"trust_env": False, "verify": True, "retries": 0}]


async def test_redirect_private_target(dns, network):
    network[1].append(httpx.Response(302, headers={"location": "http://169.254.169.254/latest"}))
    with pytest.raises(InvalidRequest):
        await sd.download_image("https://example.org/x")
    assert len(network[0]) == 1


async def test_redirect_rechecks_dns_and_drops_cookies(dns, network):
    network[1].extend(
        [
            httpx.Response(
                302,
                headers={
                    "location": "https://other.example/x",
                    "set-cookie": "secret=value; Domain=.example",
                },
            ),
            image(),
        ]
    )
    await sd.download_image("https://first.example/x")
    assert dns[0] == [("first.example", 443), ("other.example", 443)]
    assert len(network[2]) == 2  # No pooled connection with the previous TLS identity.
    assert network[0][1].headers["host"] == "other.example"
    assert "cookie" not in network[0][1].headers
    assert "authorization" not in network[0][1].headers


async def test_rebinding_same_host_redirect(dns, network, monkeypatch):
    async def resolve(self, *args, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("8.8.8.8" if not network[0] else "127.0.0.1", 443),
            )
        ]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", resolve)
    network[1].append(httpx.Response(302, headers={"location": "/next"}))
    with pytest.raises(InvalidRequest):
        await sd.download_image("https://example.org/start")
    assert len(network[0]) == 1
    assert network[0][0].url.host == "8.8.8.8"


async def test_redirect_loop_is_bounded(dns, network):
    network[1].extend(httpx.Response(302, headers={"location": "/loop"}) for _ in range(6))
    with pytest.raises(InvalidRequest, match="redirects"):
        await sd.download_image("http://example.org/loop")
    assert len(network[0]) == 6


@pytest.mark.parametrize(
    "location",
    [
        "http://user:secret@example.org/",
        "https://example.org/#x",
        "http://example.org:8080/",
        "file:///etc/passwd",
        "",
    ],
)
async def test_bad_redirect(dns, network, location):
    network[1].append(httpx.Response(302, headers={"location": location}))
    with pytest.raises(InvalidRequest):
        await sd.download_image("http://example.org/x")


@pytest.mark.parametrize(
    "response",
    [
        image(b"12345"),
        image(b"x", **{"content-length": "100"}),
        image(b"x", **{"content-length": "-1"}),
        image(b"x", **{"content-length": "1, 1"}),
        image(b""),
        httpx.Response(404),
        httpx.Response(200, headers={"content-type": "image/svg+xml"}, stream=Chunks(b"svg")),
        image(b"x", **{"content-encoding": "br"}),
    ],
)
async def test_invalid_response_and_sizes(dns, network, response):
    network[1].append(response)
    with pytest.raises(InvalidRequest):
        await sd.download_image("http://example.org/x", max_bytes=4)


@pytest.mark.parametrize("encoding,compress", [("gzip", gzip.compress), ("deflate", zlib.compress)])
async def test_decoded_size_bounded(dns, network, encoding, compress):
    payload = compress(b"x" * 100_000)
    network[1].append(image(payload, **{"content-encoding": encoding}))
    with pytest.raises(InvalidRequest, match="size limit"):
        await sd.download_image("https://example.org/x", max_bytes=1024)
    network[1].append(image(compress(b"abcd"), **{"content-encoding": encoding}))
    assert await sd.download_image("https://example.org/x", max_bytes=1024) == (
        b"abcd",
        "image/png",
    )


async def test_content_length_rejected_before_body(dns, network):
    response = image(b"not read", **{"content-length": "1000"})
    stream = response.stream
    network[1].append(response)
    with pytest.raises(InvalidRequest):
        await sd.download_image("https://example.org/x", max_bytes=4)
    assert stream.closed
    assert not response.is_stream_consumed


@pytest.mark.parametrize(
    "error",
    [httpx.ReadTimeout("secret URL"), httpx.ConnectError("secret URL"), OSError("secret URL")],
)
async def test_safe_exception_message(dns, network, error):
    network[1].append(error)
    with pytest.raises(InvalidRequest) as exc:
        await sd.download_image("https://example.org/?token=secret")
    assert "secret" not in str(exc.value)
    assert exc.value.__suppress_context__


async def test_total_timeout_includes_dns(monkeypatch):
    async def slow(self, *args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", slow)
    with pytest.raises(InvalidRequest, match="timed out"):
        await sd.download_image("https://example.org/x", timeout=0.01)


async def test_real_httpcore_connection_uses_ip_and_verified_hostname(monkeypatch, dns):
    # Exercise the installed httpx -> httpcore -> TLS extension contract offline.
    original_handle = httpx.AsyncHTTPTransport.handle_async_request
    # The autouse guard replaced this method, so bind the class's original public
    # implementation from its defining module via the saved test module constant.
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _REAL_HANDLE)
    seen = {}

    class Stream:
        async def start_tls(self, ssl_context, server_hostname, timeout):
            seen["sni"] = server_hostname
            assert ssl_context.verify_mode == ssl.CERT_REQUIRED
            assert ssl_context.check_hostname
            return self

        async def write(self, buffer, timeout=None):
            seen.setdefault("sent", bytearray()).extend(buffer)

        async def read(self, max_bytes, timeout=None):
            return b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: 3\r\nConnection: close\r\n\r\nabc"

        async def aclose(self):
            pass

        def get_extra_info(self, info):
            return None

    async def connect(self, host, port, **kwargs):
        seen["address"] = (host, port)
        return Stream()

    from httpcore._backends.auto import AutoBackend

    monkeypatch.setattr(AutoBackend, "connect_tcp", connect)
    assert await sd.download_image("https://example.org/image") == (b"abc", "image/png")
    assert seen["address"] == ("8.8.8.8", 443)
    assert seen["sni"] == "example.org"
    assert b"Host: example.org\r\n" in seen["sent"]
    assert len(dns[0]) == 1
    assert original_handle is not _REAL_HANDLE


@pytest.mark.parametrize(
    "body", [b"invalid gzip", gzip.compress(b"abc")[:-2], gzip.compress(b"a") + gzip.compress(b"b")]
)
async def test_invalid_compressed_stream(dns, network, body):
    network[1].append(image(body, **{"content-encoding": "gzip"}))
    with pytest.raises(InvalidRequest):
        await sd.download_image("https://example.org/x")


async def test_stream_limit_without_content_length(dns, network):
    response = httpx.Response(
        200, headers={"content-type": "image/png"}, stream=Chunks(b"ab", b"cd", b"e")
    )
    network[1].append(response)
    with pytest.raises(InvalidRequest, match="size limit"):
        await sd.download_image("https://example.org/x", max_bytes=4)
    assert response.is_closed


async def test_public_ipv6_literal(network):
    network[1].append(image())
    assert await sd.download_image("https://[2606:4700:4700::1111]/x") == (b"image", "image/png")
    assert network[0][0].url.host == "2606:4700:4700::1111"
    assert network[0][0].headers["host"] == "[2606:4700:4700::1111]"


_REAL_HANDLE = httpx.AsyncHTTPTransport.handle_async_request


@pytest.mark.parametrize("mime", ["video/mp4", "video/webm", "video/quicktime", "video/x-m4v"])
async def test_public_video_mimes_and_pinning(dns, network, mime):
    network[1].append(image(b"video", **{"content-type": f"{mime}; codecs=test"}))
    assert await sd.download_public_media("https://example.org/movie", kind="video") == (
        b"video",
        mime,
    )
    request = network[0][0]
    assert request.url.host == "8.8.8.8"
    assert request.headers["host"] == "example.org"
    assert request.extensions["sni_hostname"] == "example.org"
    assert set(request.headers["accept"].split(", ")) == sd._VIDEO_MIMES


@pytest.mark.parametrize(
    "kind,mime",
    [
        ("image", "video/mp4"),
        ("video", "image/png"),
        ("video", "application/octet-stream"),
        ("video", "text/html"),
        ("video", "video/mpeg"),
        ("image", "image/svg+xml"),
    ],
)
async def test_public_media_kind_restricts_mime(dns, network, kind, mime):
    network[1].append(image(**{"content-type": mime}))
    with pytest.raises(InvalidRequest, match="media type"):
        await sd.download_public_media("https://example.org/media", kind=kind)


async def test_public_media_defaults_and_image_compatibility(monkeypatch):
    calls = []

    async def download(url, **kwargs):
        calls.append((url, kwargs))
        return b"image", "image/png"

    monkeypatch.setattr(sd, "download_public_media", download)
    assert await sd.download_image("https://example.org/image") == (b"image", "image/png")
    await sd.download_image("https://example.org/image", timeout=3, max_bytes=123)
    assert calls == [
        ("https://example.org/image", {"timeout": 20, "max_bytes": 8388608, "kind": "image"}),
        ("https://example.org/image", {"timeout": 3, "max_bytes": 123, "kind": "image"}),
    ]


async def test_public_media_default_limit_and_image_kind(dns, network):
    network[1].append(image(**{"content-length": "33554433"}))
    with pytest.raises(InvalidRequest, match="size limit"):
        await sd.download_public_media("https://example.org/media")
    network[1].append(image())
    assert await sd.download_public_media("https://example.org/media") == (b"image", "image/png")


async def test_public_video_private_redirect(dns, network):
    network[1].append(httpx.Response(302, headers={"location": "http://127.0.0.1/movie"}))
    with pytest.raises(InvalidRequest):
        await sd.download_public_media("https://example.org/movie", kind="video")
    assert len(network[0]) == 1


async def test_public_video_mixed_dns_rejected(dns, network):
    dns[1][:] = ["8.8.8.8", "10.0.0.1"]
    with pytest.raises(InvalidRequest):
        await sd.download_public_media("https://example.org/movie", kind="video")
    assert not network[0]


async def test_public_video_decoded_limit(dns, network):
    response = image(
        gzip.compress(b"x" * 100000), **{"content-type": "video/mp4", "content-encoding": "gzip"}
    )
    network[1].append(response)
    with pytest.raises(InvalidRequest, match="size limit"):
        await sd.download_public_media("https://example.org/movie", kind="video", max_bytes=1024)
    assert response.is_closed


async def test_public_video_redirect_loop(dns, network):
    network[1].extend(httpx.Response(302, headers={"location": "/loop"}) for _ in range(6))
    with pytest.raises(InvalidRequest, match="redirects"):
        await sd.download_public_media("https://example.org/movie", kind="video")
    assert len(network[0]) == 6


async def test_invalid_media_kind_rejected_before_network(network):
    with pytest.raises(InvalidRequest, match="kind"):
        await sd.download_public_media("https://example.org/movie", kind="audio")
    assert not network[0]
