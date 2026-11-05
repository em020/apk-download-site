#!/usr/bin/env python3
"""Read-only HTTP smoke test for the origin or a gateway URL (including a prefix)."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from publishing import APK_NAME


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ("href", "src") and value:
                self.urls.add(value)


def verify(base, apk_name="App.apk"):
    if not base.endswith("/"):
        raise ValueError("The base URL must end with /")
    if not APK_NAME.fullmatch(apk_name):
        raise ValueError("Invalid APK filename")
    # Test the actual local route, not a shell-configured forward proxy.
    opener = build_opener(ProxyHandler({}))

    def fetch(path, method="GET", headers=None):
        request = Request(urljoin(base, path), method=method, headers=headers or {})
        try:
            with opener.open(request, timeout=10) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()

    status, headers, page = fetch("")
    assert status == 200 and b"<!doctype html>" in page.lower(), "Download page failed"
    assert "text/html" in headers["Content-Type"], "Wrong HTML MIME type"
    status, _, payload = fetch("latest/release.json")
    assert status == 200, "Missing latest metadata"
    metadata = json.loads(payload)
    status, headers, apk = fetch("latest/" + apk_name)
    assert status == 200, "APK download failed"
    assert headers["Content-Type"] == "application/vnd.android.package-archive", "Wrong APK MIME"
    assert hashlib.sha256(apk).hexdigest() == metadata["sha256"], "APK checksum mismatch"
    assert len(apk) == metadata["bytes"], "APK length mismatch"
    assert headers["Cache-Control"] == "no-store", "Latest must not become stale"
    assert headers["X-Content-Type-Options"] == "nosniff"
    status, headers, body = fetch("latest/" + apk_name, method="HEAD")
    assert status == 200 and not body and int(headers["Content-Length"]) == len(apk)
    status, headers, body = fetch("latest/" + apk_name, headers={"Range": "bytes=0-15"})
    assert status == 206 and body == apk[:16], "Byte-range download failed"
    assert headers["Content-Range"] == f"bytes 0-15/{len(apk)}"
    status, _, body = fetch(f"releases/{metadata['id']}/{apk_name}")
    assert status == 200 and body == apk, "Immutable and latest APK differ"
    status, _, body = fetch("latest/SHA256SUMS.txt")
    assert status == 200 and body.decode().startswith(metadata["sha256"])
    for forbidden in (".git/config", "backups/debug.keystore", "logs/", "README.md",
                      "releases/", "service/nginx.conf", "%2e%2e/backups/debug.keystore"):
        assert fetch(forbidden)[0] in (400, 403, 404), f"Unexpected exposure: {forbidden}"
    # Empty POST verifies method rejection; no application mutation endpoint exists.
    assert fetch("", method="POST")[0] == 405, "Writes must be rejected"
    links = Links()
    links.feed(page.decode())
    for link in links.urls:
        assert not link.startswith("/") and not urlsplit(link).scheme, "Nonportable page link"
        assert fetch(link)[0] == 200, f"Broken page link: {link}"
    print(f"PASS {base}: page, all {len(links.urls)} links, APK SHA-256/MIME, HEAD, Range,")
    print("immutable/latest identity, no-cache, private-path denial and write rejection.")
    print("Release:", metadata["id"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Example: http://127.0.0.1:8098/ or https://example.com/android/")
    parser.add_argument("--apk-name", default="App.apk", help="APK filename for a legacy root alias")
    args = parser.parse_args()
    verify(args.url, args.apk_name)
