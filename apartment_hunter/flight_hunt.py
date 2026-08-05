"""Flight Deals — the suite's flight-fare tool.

Same shape as the Price Tracker: web-search for real flight offers, then score
each one (is it a genuinely good fare vs typical prices for that route/season?)
in parallel, streaming events the server turns into Server-Sent Events. Reuses
the provider layer + settings.

Events: log · sources · flight · done
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterator

from . import providers, settings
from .search import _parse, _to_int_price

MAX_OFFERS = 24

SCORE_SYSTEM = (
    "You are an airfare deal analyst. Given a route, dates, cabin, and ONE flight "
    "offer, judge whether the price is a genuinely good deal vs typical fares for "
    'that route/season. Respond ONLY with JSON: {"deal": boolean, "score": integer '
    '0-100, "verdict": short phrase (e.g. "Great fare", "Average", "Overpriced"), '
    '"reason": short string, "typical_price": integer estimate of a normal fare}.'
)


def _to_offers(raw: list) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        url = str(d.get("url", "")).strip()
        airline = str(d.get("airline", "")).strip()
        price = _to_int_price(d.get("price"))
        depart = str(d.get("depart", "")).strip()
        key = url or (airline + str(price) + depart)
        if not key or key in seen or len(out) >= MAX_OFFERS:
            continue
        seen.add(key)
        stops = d.get("stops")
        if not isinstance(stops, (int, str)):
            stops = str(stops) if stops is not None else ""
        out.append({
            "airline": airline,
            "route": str(d.get("route", "")).strip(),
            "price": price,
            "depart": depart,
            "stops": stops,
            "duration": str(d.get("duration", "")).strip(),
            "cabin": str(d.get("cabin", "")).strip(),
            "url": url,
            "source": str(d.get("source", "")).strip() or "web",
        })
    return out


def _find_offers(country, origin, destination, when, cabin, currency, provider, model, key):
    primary = (
        f"Search travel and airline sites for CURRENT flight offers from {origin} to "
        f"{destination} for {when}, in {cabin} cabin class, in the {country} market "
        f"(prices in {currency}). "
        "Return up to 12 offers as a JSON array. Each item: "
        '{"airline": string, "route": string (e.g. "BLR → DXB"), "price": integer '
        '(number only), "depart": string (date/time or day), "stops": integer or '
        '"non-stop", "duration": string, "cabin": string, "url": string (a direct '
        'booking or search URL), "source": string (site name)}. '
        "Only real, working booking URLs from genuine travel sites. Output ONLY the JSON array."
    )
    raw = _parse(providers.web_search(provider, model, key, primary))
    if len(raw) < 5:
        supplement = (
            f"Find MORE current flight offers from {origin} to {destination} for {when}, "
            f"{cabin} cabin, in the {country} market (prices in {currency}), from a "
            "VARIETY of airlines and booking sites (avoid duplicates). "
            'Return ONLY a JSON array of {"airline","route","price","depart","stops",'
            '"duration","cabin","url","source"}. Real booking URLs. Never return an empty array.'
        )
        raw = raw + _parse(providers.web_search(provider, model, key, supplement))
    return _to_offers(raw)


def _score_offer(offer, country, origin, destination, when, cabin, currency, threshold,
                 provider, model, key) -> dict:
    import json

    try:
        payload = json.dumps({
            "country": country, "origin": origin, "destination": destination,
            "when": when, "cabin": cabin, "currency": currency, "offer": offer,
        }, ensure_ascii=False)
        data = providers.chat_json(provider, model, key, SCORE_SYSTEM, payload)
        score = int(data.get("score", 0))
        return {
            "flight": {**offer, "typical_price": _to_int_price(data.get("typical_price"))},
            "score": score,
            "deal": bool(data.get("deal")) or score >= threshold,
            "verdict": str(data.get("verdict", "")),
            "reason": str(data.get("reason", "")),
            "status": "deal" if (bool(data.get("deal")) or score >= threshold) else "ok",
        }
    except Exception as exc:  # heuristic fallback
        score = 50
        good = score >= threshold
        return {
            "flight": {**offer, "typical_price": None},
            "score": score, "deal": good,
            "verdict": "Fair fare" if good else "Average",
            "reason": f"[heuristic: {exc}]",
            "status": "deal" if good else "ok",
        }


def run_flights(params: dict, cfg: dict) -> Iterator[tuple[str, dict]]:
    country = (params.get("country") or "").strip()
    origin = (params.get("origin") or "").strip()
    destination = (params.get("destination") or "").strip()
    when = (params.get("when") or "").strip() or "flexible"
    cabin = (params.get("cabin") or "").strip() or "Economy"
    currency = params.get("currency") or cfg.get("currency", "$")
    threshold = params.get("threshold", 60)

    yield "log", {"level": "info", "msg": f"Route: {origin or '—'} → {destination or '—'} · {when} · {cabin} · {country or '—'}"}

    provider, model, key = settings.get_effective()
    if not key:
        yield "log", {"level": "error", "msg": "No API key set — open Settings and add a provider key."}
        yield "done", {"processed": 0, "deals": 0, "sources": 0, "currency": currency}
        return
    yield "log", {"level": "info", "msg": f"Provider: {provider} · model: {model}"}
    yield "log", {"level": "info", "msg": "Searching travel sites for current fares"}

    try:
        offers = _find_offers(country, origin, destination, when, cabin, currency, provider, model, key)
    except Exception as exc:
        yield "log", {"level": "error", "msg": f"Search failed: {exc}"}
        yield "done", {"processed": 0, "deals": 0, "sources": 0, "currency": currency}
        return

    if not offers:
        yield "log", {"level": "warn", "msg": "No offers found — try flexible dates or a different route."}
        yield "done", {"processed": 0, "deals": 0, "sources": 0, "currency": currency}
        return

    counts: dict[str, int] = {}
    for o in offers:
        counts[o["source"]] = counts.get(o["source"], 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])
    yield "sources", {"items": [{"name": n, "count": c} for n, c in ordered]}
    yield "log", {"level": "ok", "msg": f"Found {len(offers)} offer(s) from {len(counts)} site(s)"}

    total = len(offers)
    processed = deals = 0
    yield "log", {"level": "info", "msg": f"Scoring {total} offer(s) for deal quality"}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_score_offer, o, country, origin, destination, when, cabin,
                        currency, threshold, provider, model, key): o
            for o in offers
        }
        for future in as_completed(futures):
            item = future.result()
            processed += 1
            if item["deal"]:
                deals += 1
            short = (item["flight"].get("route") or item["flight"].get("airline") or "offer")[:46]
            yield "log", {"level": "info", "msg": f"Scored [{processed}/{total}] {short}"}
            yield "flight", item

    yield "log", {"level": "ok", "msg": f"Complete — {deals} deal(s) of {processed}"}
    yield "done", {"processed": processed, "deals": deals,
                   "sources": len(counts), "currency": currency}
