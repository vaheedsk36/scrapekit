"""Fetch + parse listings from sources.

Two source types:
  * "json" — bundled/demo data, parsed with the standard library only.
  * "html" — a real website. Fetched with Scrapling's stealth fetcher when
             available (Cloudflare bypass, like Hermes uses), else a plain
             urllib request; parsed with BeautifulSoup + per-site CSS selectors.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from .config import PROJECT_ROOT
from .models import Listing


def _to_int_price(text) -> "int | None":
    if text is None:
        return None
    digits = re.sub(r"[^\d]", "", str(text))
    return int(digits) if digits else None


def _to_beds(text) -> "float | None":
    if text is None:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", str(text))
    return float(match.group(1)) if match else None


def load_listings(source: dict) -> list[Listing]:
    stype = source.get("type", "html")
    if stype == "json":
        return _from_json(source)
    if stype == "html":
        return _from_html(source)
    raise SystemExit(f"Unknown source type: {stype!r}")


def _from_json(source: dict) -> list[Listing]:
    path = Path(source["path"])
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    data = json.loads(path.read_text())
    return [
        Listing(
            source=source["name"],
            title=item.get("title", ""),
            url=item.get("url", ""),
            price=_to_int_price(item.get("price")),
            beds=_to_beds(item.get("beds")),
            location=item.get("location", ""),
            description=item.get("description", ""),
        )
        for item in data
    ]


def _fetch_html(url: str, stealth: bool = False) -> str:
    if url.startswith("file://"):
        return Path(url[len("file://"):]).read_text()
    if stealth:
        try:
            from scrapling.fetchers import StealthyFetcher

            page = StealthyFetcher.fetch(url, headless=True)
            return page.html_content
        except Exception as exc:  # fall back to a plain request
            print(f"[scraper] stealth fetch unavailable ({exc}); using plain request")
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (apartment-hunter)"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "ignore")


def _from_html(source: dict) -> list[Listing]:
    html = _fetch_html(source["url"], stealth=source.get("stealth", False))
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "Live HTML scraping needs BeautifulSoup — `pip install beautifulsoup4`."
        ) from exc

    selectors = source["selectors"]
    soup = BeautifulSoup(html, "html.parser")
    listings: list[Listing] = []
    for item in soup.select(selectors["item"]):
        def pick(key: str) -> str:
            sel = selectors.get(key)
            if not sel:
                return ""
            el = item.select_one(sel)
            return el.get_text(strip=True) if el else ""

        anchor = item.select_one("a[href]")
        href = anchor["href"] if anchor else ""
        listings.append(
            Listing(
                source=source["name"],
                title=pick("title"),
                url=urllib.parse.urljoin(source["url"], href),
                price=_to_int_price(pick("price")),
                beds=_to_beds(pick("beds")),
                location=pick("location"),
                description=pick("description"),
            )
        )
    return listings
