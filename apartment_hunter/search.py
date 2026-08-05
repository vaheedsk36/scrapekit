"""Find real, current rental listings via the OpenAI web-search tool.

Returns listings with genuine source URLs — no synthetic data. This is the
single data path for the app.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
from urllib.request import Request, urlopen

from .models import Listing
from .scraper import _to_beds, _to_int_price

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

_OG_PATTERNS = [
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
]


def fetch_og_image(url: str, timeout: float = 4.0) -> str:
    """Best-effort: pull the Open Graph preview image from a listing page."""
    if not url or url == "#":
        return ""
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (apartment-hunter)"})
        html = urlopen(req, timeout=timeout).read(200_000).decode("utf-8", "ignore")
    except Exception:
        return ""
    for pat in _OG_PATTERNS:
        m = re.search(pat, html, re.I)
        if m and m.group(1).startswith("http"):
            return m.group(1)
    return ""


def enrich_images(listings: list[Listing], max_workers: int = 6) -> None:
    """Fill in .image for listings that don't already have one (in parallel)."""
    todo = [l for l in listings if not l.image and l.url and l.url != "#"]
    if not todo:
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        for listing, img in zip(todo, pool.map(lambda l: fetch_og_image(l.url), todo)):
            listing.image = img


def _parse(text: str) -> list:
    """Extract the listing objects from the model's answer, tolerating code
    fences, leading/trailing prose, citations, a {"listings": [...]} wrapper,
    or a bare object. Falls back to scraping individual JSON objects."""
    if not text:
        return []
    candidates = []
    fence = _FENCE.search(text)
    if fence:
        candidates.append(fence.group(1).strip())
    candidates.append(text.strip())

    decoder = json.JSONDecoder()
    for raw in candidates:
        for opener in ("[", "{"):
            idx = raw.find(opener)
            if idx == -1:
                continue
            try:
                data, _ = decoder.raw_decode(raw, idx)  # ignores trailing text
            except json.JSONDecodeError:
                continue
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                if isinstance(data.get("listings"), list):
                    return data["listings"]
                return [data]

    # last resort: pull out individual flat objects
    out = []
    for chunk in re.findall(r"\{[^{}]*\}", text, re.S):
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and ("title" in obj or "url" in obj):
            out.append(obj)
    return out


def accumulate(provider, model, key, make_prompt, key_of, target=25, max_rounds=4):
    """Run web_search repeatedly, each round excluding items already seen, until
    `target` unique items are collected or `max_rounds` is hit. make_prompt(exclude)
    returns the search prompt given a list of already-seen short labels to avoid;
    key_of(item_dict) returns a dedupe key. Returns the merged list of raw dicts.

    The web-search tool tends to return only a handful of items per call, so we
    run several rounds — each asking for MORE while excluding what we've already
    seen — then merge and dedupe. Round 1 asks with no exclusions; later rounds
    only fire while we're still under target and stop early on a dry round (one
    that adds nothing new). max_rounds is a hard cap (each round is a paid call).
    """
    from . import providers

    results: list = []
    seen_keys: set = set()
    seen_labels: list[str] = []

    # Hard cap at 2 rounds: the web-search tool tends to return the same handful
    # each call, so extra rounds mostly yield duplicates while multiplying latency
    # and cost. One exclusion-aware top-up round is the sweet spot.
    rounds = max(1, min(max_rounds, 2))
    for round_i in range(rounds):
        # Only keep spending rounds while we still need more.
        if round_i and len(results) >= target:
            break
        # Round 1 gets no exclusions; later rounds get the most recent ~25 labels
        # to keep the prompt small.
        exclude = seen_labels[-25:] if round_i else []
        prompt = make_prompt(exclude)
        try:
            batch = _parse(providers.web_search(provider, model, key, prompt))
        except Exception:
            if round_i == 0:
                raise  # first-round failure is a real error — let callers handle it
            break  # keep whatever we've already gathered
        added = 0
        for item in batch:
            if not isinstance(item, dict):
                continue
            k = key_of(item)
            if not k or k in seen_keys:
                continue
            seen_keys.add(k)
            results.append(item)
            added += 1
            title = str(item.get("title") or "").strip()
            src = str(item.get("source") or "").strip()
            label = f"{title} ({src})" if title and src else (title or src)
            if label:
                seen_labels.append(label[:60])
        if round_i and added == 0:
            break  # dry round — searching more is unlikely to help
    return results


def _to_listings(raw: list) -> list[Listing]:
    seen: set[str] = set()
    listings: list[Listing] = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        url = str(d.get("url", "")).strip()
        title = str(d.get("title", "")).strip()
        key = url or (title + str(d.get("location", "")))
        if not key or key in seen:
            continue
        seen.add(key)
        listings.append(Listing(
            source=str(d.get("source", "")).strip() or "web",
            title=title,
            url=url,
            price=_to_int_price(d.get("price")),
            beds=_to_beds(d.get("beds")),
            location=str(d.get("location", "")).strip(),
            description=str(d.get("features") or d.get("description") or "").strip(),
            image=str(d.get("image", "")).strip(),
        ))
    return listings


def find_listings(city: str, country: str, criteria: dict,
                  currency: str, cfg: dict, area: str = "") -> list[Listing]:
    from . import providers, settings

    provider, model, key = settings.get_effective()
    if not key:
        raise RuntimeError("No provider API key configured — set one in Settings")

    budget = criteria.get("max_price")
    beds = criteria.get("min_beds")
    keywords = ", ".join(criteria.get("keywords", []))
    location = f"{area + ', ' if area else ''}{city}, {country}"

    def make_prompt(exclude):
        prompt = (
            f"Search the web for CURRENT rental apartment listings in {location}. "
            f"Target: monthly rent up to {currency}{budget}, at least {beds} bedrooms. "
            + (f"Preferred features: {keywords}. " if keywords else "")
            + f"Prefer well-known, popular property websites used in the {country} region. "
            + "Return up to 15 items as a JSON array. Each item must be: "
            '{"title": string, "price": integer monthly rent (number only), '
            '"beds": number, "location": string, "url": string (prefer a direct link to '
            'the specific property\'s detail page, which usually has photos; a filtered '
            'search URL is acceptable only if no direct link is available), '
            '"source": string (website name), "features": string, '
            '"image": string (a direct image URL for the property if available, else "")}. '
            "Include only listings with real, working URLs from genuine property sites. "
            "Output ONLY the raw JSON array — no markdown code fences, no commentary."
        )
        if exclude:
            prompt += (
                " Do NOT repeat any of these already-listed results: "
                + ", ".join(exclude) + ". Return only NEW ones."
            )
        return prompt

    def key_of(d):
        url = str(d.get("url", "")).strip()
        title = str(d.get("title", "")).strip()
        return url or (title + str(d.get("location", "")))

    raw = accumulate(provider, model, key, make_prompt, key_of, target=25, max_rounds=4)
    return _to_listings(raw)
