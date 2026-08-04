"""Drives one web-initiated hunt as a stream of (event, data) tuples the server
turns into Server-Sent Events: web-search for listings, fetch preview images,
then score every listing against the brief in parallel.

Events: log · sources · listing · done
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterator

from . import matcher, search

MAX_LISTINGS = 24  # cap on how many we score per hunt


def _passes_hard_rules(listing, criteria: dict) -> bool:
    """Cheap gate before the LLM. Does NOT filter on city name (web results say
    'Bangalore' vs 'Bengaluru' etc.) — the LLM judges location."""
    max_price = criteria.get("max_price")
    if max_price and listing.price is not None and listing.price > max_price * 1.15:
        return False
    min_beds = criteria.get("min_beds")
    if min_beds is not None and listing.beds is not None and listing.beds < min_beds:
        return False
    return True


def _score_one(listing, criteria: dict, matcher_cfg: dict, currency: str) -> dict:
    if not _passes_hard_rules(listing, criteria):
        return {"listing": listing.to_dict(), "score": 0, "match": False,
                "status": "filtered", "blurb": "", "reason": "outside hard limits"}
    result = matcher.evaluate(listing, criteria, matcher_cfg, currency)
    return {"listing": listing.to_dict(), "score": result.score, "match": result.match,
            "status": "match" if result.match else "review",
            "blurb": result.blurb, "reason": result.reason}


def run_hunt(params: dict, cfg: dict) -> Iterator[tuple[str, dict]]:
    country = (params.get("country") or "").strip()
    city = (params.get("city") or "").strip()
    area = (params.get("area") or "").strip()
    currency = params.get("currency") or cfg.get("currency", "$")
    matcher_cfg = cfg.get("matcher", {})
    criteria = {
        "city": city, "area": area,
        "max_price": params.get("max_price"),
        "min_beds": params.get("min_beds"),
        "keywords": params.get("keywords", []),
        "match_threshold": params.get("match_threshold", 60),
    }
    where = ", ".join(x for x in (area, city, country) if x) or "—"

    yield "log", {"level": "info", "msg": f"Query: {where}"}
    yield "log", {"level": "info", "msg": f"Budget {currency}{criteria['max_price']} · {criteria['min_beds']}+ BHK"}

    if not os.environ.get("OPENAI_API_KEY"):
        yield "log", {"level": "error", "msg": "OPENAI_API_KEY not set"}
        yield "done", {"processed": 0, "matched": 0, "sources": 0, "currency": currency}
        return

    yield "log", {"level": "info", "msg": "Connecting to web search and scanning property portals"}
    try:
        listings = search.find_listings(city, country, criteria, currency, cfg, area=area)
    except Exception as exc:
        yield "log", {"level": "error", "msg": f"Search failed: {exc}"}
        yield "done", {"processed": 0, "matched": 0, "sources": 0, "currency": currency}
        return
    listings = listings[:MAX_LISTINGS]

    if not listings:
        yield "log", {"level": "warn", "msg": "No listings found — try widening the budget or beds."}
        yield "done", {"processed": 0, "matched": 0, "sources": 0, "currency": currency}
        return

    # platforms actually represented in the merged set
    counts: dict[str, int] = {}
    for l in listings:
        counts[l.source] = counts.get(l.source, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])
    yield "sources", {"items": [{"name": n, "count": c} for n, c in ordered]}
    yield "log", {"level": "ok", "msg": f"Merged {len(listings)} unique listing(s) across {len(counts)} platform(s)"}

    yield "log", {"level": "info", "msg": "Fetching preview images"}
    search.enrich_images(listings)
    with_img = sum(1 for l in listings if l.image)
    yield "log", {"level": "ok", "msg": f"Preview images resolved for {with_img}/{len(listings)}"}

    total = len(listings)
    processed = matched = 0
    yield "log", {"level": "info", "msg": f"Scoring {total} listing(s) in parallel"}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_score_one, l, criteria, matcher_cfg, currency): l
                   for l in listings}
        for future in as_completed(futures):
            item = future.result()
            processed += 1
            if item["match"]:
                matched += 1
            short = (item["listing"].get("title") or "untitled")[:46]
            yield "log", {"level": "info", "msg": f"Scored [{processed}/{total}] {short}"}
            yield "listing", item

    yield "log", {"level": "ok", "msg": f"Complete — {matched} match(es) of {processed}"}
    yield "done", {"processed": processed, "matched": matched,
                   "sources": len(counts), "currency": currency}
