"""
Check the public site from outside, as GitHub's scheduled workflow does
(.github/workflows/site-check.yml), or by hand:

    python deploy/public/check_site.py https://reader.example

It checks /version (a database the code can use, fully migrated), the
Library, robots.txt and the sitemap, a book page from the sitemap, and that
plain http:// is sent on to https://. During the private preview the site
is behind Cloudflare Access: with CF_ACCESS_CLIENT_ID and
CF_ACCESS_CLIENT_SECRET set it sends that service token. Each check is
tried three times, 20 seconds apart; the script exits 1 if one still fails.
Python's standard library only.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

ATTEMPTS = 3
PAUSE = 20
TIMEOUT = 30
GREEK = re.compile("[Ͱ-Ͽἀ-῿]")
SITEMAP = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


@dataclass
class Response:
    status: int
    headers: dict[str, str]  # names in lower case
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


class CheckFailed(Exception):
    pass


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect comes back as it is: the http:// check wants to see it."""

    def redirect_request(self, *args, **kwargs):
        return None


def http_fetch(headers: dict[str, str]):
    """fetch(url) -> Response over the network, sending headers."""
    opener = urllib.request.build_opener(_NoRedirects)

    def fetch(url: str) -> Response:
        request = urllib.request.Request(url, headers=headers)
        try:
            with opener.open(request, timeout=TIMEOUT) as r:
                return Response(r.status, _lower(r.headers), r.read())
        except urllib.error.HTTPError as e:
            return Response(e.code, _lower(e.headers), e.read())

    return fetch


def _lower(headers) -> dict[str, str]:
    return {name.lower(): value for name, value in headers.items()}


def get(fetch, url: str) -> Response:
    """A page that must answer 200."""
    r = fetch(url)
    if r.status in (
        301,
        302,
        303,
        307,
        308,
    ) and ".cloudflareaccess.com/" in r.headers.get("location", ""):
        raise CheckFailed(
            f"{url}: Cloudflare Access asks for a login "
            "(is the service token set, and allowed by the application's policy?)"
        )
    if r.status != 200:
        raise CheckFailed(f"{url} answered {r.status}")
    return r


def check_version(fetch, site: str) -> str:
    v = json.loads(get(fetch, site + "/version").body)
    database = v["database"]
    if not v["serving"]:
        raise CheckFailed("/version: the site can't use its database")
    if not database["version"] or database["pending"]:
        raise CheckFailed(f"/version: the database is not migrated: {database}")
    return f"code {v['code']['commit']}, database {database['version']}"


def check_library(fetch, site: str) -> str:
    r = get(fetch, site + "/library")
    if "content-security-policy" not in r.headers:
        raise CheckFailed("/library has no Content-Security-Policy")
    texts = set(re.findall(r'href="/texts/(\d+)/toc"', r.text))
    if not texts:
        raise CheckFailed("/library lists no texts")
    return f"{len(texts)} texts"


def check_pages(fetch, site: str) -> str:
    robots = get(fetch, site + "/robots.txt").text
    if f"Sitemap: {site}/sitemap.xml" not in robots:
        raise CheckFailed(f"/robots.txt does not name {site}/sitemap.xml")
    sitemap = ET.fromstring(get(fetch, site + "/sitemap.xml").body)
    urls = [e.text or "" for e in sitemap.findall("s:url/s:loc", SITEMAP)]
    if not urls or any(not url.startswith(site + "/") for url in urls):
        raise CheckFailed(f"the sitemap's {len(urls)} URLs are not all on {site}")
    books = [url for url in urls if "/read?at=" in url]
    if not books:
        raise CheckFailed("the sitemap lists no book pages")
    page = books[0][len(site) :]
    if not GREEK.search(get(fetch, books[0]).text):
        raise CheckFailed(f"{page} has no Greek")
    return f"the sitemap's {len(urls)} URLs; {page} has its Greek"


def check_https(fetch, site: str) -> str:
    """Cloudflare's Always Use HTTPS: http:// is sent on to https://."""
    r = fetch("http://" + site.removeprefix("https://") + "/library")
    location = r.headers.get("location", "")
    if r.status not in (301, 308) or location != site + "/library":
        raise CheckFailed(
            f"http:// answered {r.status} {location!r}, not a redirect to https://"
        )
    return f"http:// is sent on to https:// ({r.status})"


def run(site: str, fetch, sleep=time.sleep, out=print) -> bool:
    """Every check, each tried ATTEMPTS times; whether all passed."""
    checks = [
        ("version", check_version),
        ("library", check_library),
        ("pages", check_pages),
    ]
    if site.startswith("https://"):
        checks.append(("https", check_https))
    passed = True
    for name, check in checks:
        for attempt in range(1, ATTEMPTS + 1):
            started = time.monotonic()
            try:
                detail = check(fetch, site)
            except (CheckFailed, OSError, ValueError, KeyError, ET.ParseError) as e:
                if attempt < ATTEMPTS:
                    sleep(PAUSE)
                    continue
                out(f"FAIL {name}: {e}")
                passed = False
            else:
                out(f"OK   {name}: {detail} ({time.monotonic() - started:.1f} s)")
            break
    return passed


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not args[0].startswith(("http://", "https://")):
        print(__doc__.strip())
        return 2
    site = args[0].rstrip("/")
    headers = {"User-Agent": "ancient-greek-reader site check"}
    client_id = os.environ.get("CF_ACCESS_CLIENT_ID")
    secret = os.environ.get("CF_ACCESS_CLIENT_SECRET")
    if client_id and secret:
        headers |= {"CF-Access-Client-Id": client_id, "CF-Access-Client-Secret": secret}
    return (
        0
        if run(site, http_fetch(headers), out=lambda line: print(line, flush=True))
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
