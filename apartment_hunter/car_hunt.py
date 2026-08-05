"""Used Car Finder — a deal-style tool in the suite.

Same shape as the Price Tracker: web-search for real used-car listings, then
score each one (is it a good buy vs typical market value?) in parallel,
streaming events the server turns into Server-Sent Events. Reuses the provider
layer + settings.

Events: log · sources · car · done
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterator

from . import providers, settings
from .search import _parse, _to_int_price, fetch_og_image

MAX_CARS = 24

SCORE_SYSTEM = (
    "You are a used-car deal analyst. Given the buyer's brief (model, budget, "
    "min year, fuel) and ONE listing, judge whether it's a good buy — weigh "
    "price vs typical market value for that model/year/mileage, and fit to the "
    'brief. Respond ONLY with JSON: {"deal": boolean, "score": integer 0-100, '
    '"verdict": short phrase (e.g. "Great value", "Fair", "Overpriced"), '
    '"reason": short string, "typical_price": integer estimate of typical '
    "market price}."
)


def _to_cars(raw: list) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        url = str(d.get("url", "")).strip()
        title = str(d.get("title", "")).strip()
        price = _to_int_price(d.get("price"))
        key = url or (title + str(price))
        if not key or key in seen or len(out) >= MAX_CARS:
            continue
        seen.add(key)
        mileage = d.get("mileage")
        out.append({
            "title": title,
            "price": price,
            "year": _to_int_price(d.get("year")),
            "mileage": str(mileage).strip() if mileage not in (None, "") else "",
            "fuel": str(d.get("fuel", "")).strip(),
            "transmission": str(d.get("transmission", "")).strip(),
            "location": str(d.get("location", "")).strip(),
            "url": url,
            "image": str(d.get("image", "")).strip(),
            "source": str(d.get("source", "")).strip() or "web",
        })
    return out


def _find_cars(query, location, country, max_price, min_year, fuel, currency,
               provider, model, key):
    where = f"{location}, {country}" if location else country
    budget = f" under {currency}{max_price}" if max_price else ""
    year_clause = f" from {min_year} onward" if min_year else ""
    fuel_clause = f" ({fuel} only)" if fuel and fuel != "Any" else ""
    primary = (
        f"Search classified and dealer sites for CURRENT used-car listings of "
        f"{query}{fuel_clause} for sale in {where}{budget}{year_clause}. "
        "Return up to 12 listings as a JSON array. Each item: "
        '{"title": string, "price": integer (number only), "year": integer, '
        '"mileage": string (e.g. "45,000 km") or number, "fuel": string, '
        '"transmission": string, "location": string, '
        '"url": string (a direct link to the listing page), '
        '"image": string (photo URL if available, else ""), '
        '"source": string (site name)}. '
        "Only real, working listing URLs from genuine car marketplaces. "
        "Output ONLY the JSON array."
    )
    raw = _parse(providers.web_search(provider, model, key, primary))
    if len(raw) < 5:
        supplement = (
            f"Find MORE used {query} listings for sale in {where}{budget}"
            f"{year_clause}{fuel_clause}, from a VARIETY of car sites "
            "(avoid duplicates). Return ONLY a JSON array of "
            '{"title","price","year","mileage","fuel","transmission",'
            '"location","url","image","source"}. Real listing URLs. '
            "Never return an empty array."
        )
        raw = raw + _parse(providers.web_search(provider, model, key, supplement))
    return _to_cars(raw)


def _score_car(car, query, max_price, min_year, fuel, currency, threshold,
               provider, model, key) -> dict:
    import json

    try:
        payload = json.dumps({
            "model": query, "currency": currency, "max_price": max_price,
            "min_year": min_year, "fuel": fuel, "listing": car,
        }, ensure_ascii=False)
        data = providers.chat_json(provider, model, key, SCORE_SYSTEM, payload)
        score = int(data.get("score", 0))
        return {
            "car": {**car, "typical_price": _to_int_price(data.get("typical_price"))},
            "score": score,
            "deal": bool(data.get("deal")) or score >= threshold,
            "verdict": str(data.get("verdict", "")),
            "reason": str(data.get("reason", "")),
            "status": "deal" if (bool(data.get("deal")) or score >= threshold) else "ok",
        }
    except Exception as exc:  # heuristic fallback
        price, cap = car.get("price"), max_price
        good = price is not None and cap and price <= cap
        score = 80 if good else 40
        return {
            "car": {**car, "typical_price": None},
            "score": score, "deal": good,
            "verdict": "Within budget" if good else "Over budget",
            "reason": f"[heuristic: {exc}]",
            "status": "deal" if good else "ok",
        }


def run_cars(params: dict, cfg: dict) -> Iterator[tuple[str, dict]]:
    country = (params.get("country") or "").strip()
    location = (params.get("location") or "").strip()
    query = (params.get("query") or "").strip()
    max_price = params.get("max_price")
    min_year = params.get("min_year")
    fuel = (params.get("fuel") or "Any").strip()
    currency = params.get("currency") or cfg.get("currency", "$")
    threshold = params.get("threshold", 60)

    budget_txt = f" · ≤{currency}{max_price}" if max_price else ""
    year_txt = f" · {min_year}+" if min_year else ""
    fuel_txt = f" · {fuel}" if fuel and fuel != "Any" else ""
    yield "log", {"level": "info", "msg": f"Car: {query or '—'}{budget_txt}{year_txt}{fuel_txt} · {location or country or '—'}"}

    provider, model, key = settings.get_effective()
    if not key:
        yield "log", {"level": "error", "msg": "No API key set — open Settings and add a provider key."}
        yield "done", {"processed": 0, "deals": 0, "sources": 0, "currency": currency}
        return
    yield "log", {"level": "info", "msg": f"Provider: {provider} · model: {model}"}
    yield "log", {"level": "info", "msg": "Searching car marketplaces for current listings"}

    try:
        cars = _find_cars(query, location, country, max_price, min_year, fuel,
                          currency, provider, model, key)
    except Exception as exc:
        yield "log", {"level": "error", "msg": f"Search failed: {exc}"}
        yield "done", {"processed": 0, "deals": 0, "sources": 0, "currency": currency}
        return

    if not cars:
        yield "log", {"level": "warn", "msg": "No listings found — try a broader make/model."}
        yield "done", {"processed": 0, "deals": 0, "sources": 0, "currency": currency}
        return

    counts: dict[str, int] = {}
    for c in cars:
        counts[c["source"]] = counts.get(c["source"], 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])
    yield "sources", {"items": [{"name": n, "count": c} for n, c in ordered]}
    yield "log", {"level": "ok", "msg": f"Found {len(cars)} listing(s) from {len(counts)} site(s)"}

    yield "log", {"level": "info", "msg": "Fetching listing photos"}
    todo = [c for c in cars if not c.get("image") and c.get("url")]
    if todo:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for c, img in zip(todo, pool.map(lambda x: fetch_og_image(x["url"]), todo)):
                c["image"] = img

    total = len(cars)
    processed = deals = 0
    yield "log", {"level": "info", "msg": f"Scoring {total} listing(s) for deal quality"}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_score_car, c, query, max_price, min_year, fuel, currency,
                        threshold, provider, model, key): c
            for c in cars
        }
        for future in as_completed(futures):
            item = future.result()
            processed += 1
            if item["deal"]:
                deals += 1
            short = (item["car"].get("title") or "listing")[:46]
            yield "log", {"level": "info", "msg": f"Scored [{processed}/{total}] {short}"}
            yield "car", item

    yield "log", {"level": "ok", "msg": f"Complete — {deals} deal(s) of {processed}"}
    yield "done", {"processed": processed, "deals": deals,
                   "sources": len(counts), "currency": currency}
