"""Credential-free public HTML reader used by the harness adapter and Ember's tool broker."""

import re
import socket
from html.parser import HTMLParser
from ipaddress import ip_address
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import JsonValue


class ReadableHTML(HTMLParser):
    """Extract visible text while excluding scripts, forms and navigation."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "nav", "footer", "form", "svg"}:
            self.hidden.append(tag)
        if tag in {"p", "div", "article", "h1", "h2", "h3", "li", "br"} and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()
        if tag in {"p", "div", "h1", "h2", "h3", "li"} and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def public_target(url: str) -> tuple[httpx.URL, str]:
    """Resolve once, reject every private address, and return the exact connection target."""
    value = urlsplit(url)
    host = value.hostname
    if (
        len(url) > 2048
        or value.scheme not in {"http", "https"}
        or not host
        or value.username is not None
        or value.password is not None
        or value.port not in {None, 80, 443}
        or any(ord(c) < 33 for c in url)
    ):
        raise ValueError("page_url_denied")
    addresses = socket.getaddrinfo(
        host, value.port or (443 if value.scheme == "https" else 80), type=socket.SOCK_STREAM
    )
    ips = sorted({row[4][0] for row in addresses})
    if not ips or any(
        not ip_address(ip).is_global or ip_address(ip).is_multicast or ip_address(ip).is_reserved
        for ip in ips
    ):
        raise ValueError("page_private_address_denied")
    return httpx.URL(url).copy_with(host=ips[0], fragment=None), host


def read_page(url: str) -> dict[str, JsonValue]:
    """Bound redirects and content; no ambient cookies, proxy or credentials."""
    requested = url
    raw = bytearray()
    with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
        for _ in range(4):
            target, host = public_target(url)
            client.cookies.clear()
            with client.stream(
                "GET",
                target,
                headers={
                    "Host": host,
                    "Accept-Encoding": "identity",
                    "Accept": "text/html,text/plain",
                },
                extensions={"sni_hostname": host},
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("page_redirect_invalid")
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                media = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if media not in {"text/html", "application/xhtml+xml", "text/plain"}:
                    raise ValueError("page_content_type_unsupported")
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ValueError("page_encoding_unsupported")
                for chunk in response.iter_raw():
                    raw.extend(chunk)
                    if len(raw) > 1048576:
                        raise ValueError("page_wire_limit")
                text = raw.decode(response.encoding or "utf-8", errors="replace")
                if media != "text/plain":
                    parser = ReadableHTML()
                    parser.feed(text)
                    text = "".join(parser.parts)
                text = "\n".join(
                    re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()
                )
                encoded = text.encode()
                excerpt = encoded[:1536].decode("utf-8", errors="ignore")
                if not excerpt.strip():
                    raise ValueError("page_no_readable_text")
                return {
                    "url": url,
                    "requested_url": requested,
                    "text": excerpt,
                    "partial": len(encoded) > 1536,
                    "format": "plain_text",
                }
    raise ValueError("page_redirect_limit")
