"""Price Tracker — the suite's second tool.

Same shape as the apartment hunt: web-search for real product offers, then score
each one (is it a genuine deal vs typical price?) in parallel, streaming events
the server turns into Server-Sent Events. Reuses the provider layer + settings.

Events: log · sources · offer · done
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterator

from . import providers, settings
from .search import accumulate, _to_int_price, fetch_og_image

MAX_OFFERS = 24

SCORE_SYSTEM = (
    "You are a shopping deal analyst. Given a product, the shopper's target price, "
    "and ONE offer, judge whether it's a genuinely good deal — weigh the offer "
    "price against the typical market price, the condition, and availability. "
    "Be a critical, calibrated judge. Reserve 80-100 for genuinely strong deals, "
    "60-79 for decent, 40-59 for weak, below 40 for poor. Do not inflate scores. "
    'Respond ONLY with JSON: {"deal": boolean, "score": integer 0-100 (deal '
    'quality), "verdict": short phrase (e.g. "Great deal", "Fair price", '
    '"Overpriced"), "reason": short string, "typical_price": integer estimate of '
    "the normal price in the same currency}."
)


def _to_offers(raw: list) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        url = str(d.get("url", "")).strip()
        title = str(d.get("title", "")).strip()
        key = url or (title + str(d.get("source", "")))
        if not key or key in seen or len(out) >= MAX_OFFERS:
            continue
        seen.add(key)
        out.append({
            "title": title,
            "price": _to_int_price(d.get("price")),
            "source": str(d.get("source", "")).strip() or "web",
            "url": url,
            "image": str(d.get("image", "")).strip(),
            "condition": str(d.get("condition", "")).strip(),
            "availability": str(d.get("availability", "")).strip(),
        })
    return out


def _find_offers(product, country, target, currency, condition, provider, model, key):
    cond = f" ({condition})" if condition else ""

    def make_prompt(exclude):
        prompt = (
            f"Search shopping sites for CURRENT prices of: {product}{cond}, in {country}. "
            f"The shopper's target price is about {currency}{target}. "
            f"Prefer well-known, popular retailers used in the {country} region. "
            "Return up to 15 offers as a JSON array. Each item: "
            '{"title": string, "price": integer (number only), "source": string '
            '(retailer name), "url": string (a direct link to the product page), '
            '"image": string (product image URL if available, else ""), '
            '"condition": string (new/used/refurbished), '
            '"availability": string (e.g. in stock)}. '
            "Only real, working product URLs from genuine retailers. Output ONLY the JSON array."
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
        return url or (title + str(d.get("source", "")))

    raw = accumulate(provider, model, key, make_prompt, key_of, target=25, max_rounds=4)
    return _to_offers(raw)


def _score_offer(offer, product, target, currency, threshold, provider, model, key) -> dict:
    import json

    try:
        payload = json.dumps({
            "product": product, "currency": currency, "target_price": target,
            "offer": offer,
        }, ensure_ascii=False)
        data = providers.chat_json(provider, model, key, SCORE_SYSTEM, payload)
        score = int(data.get("score", 0))
        deal = score >= threshold
        return {
            "offer": {**offer, "typical_price": _to_int_price(data.get("typical_price"))},
            "score": score,
            "deal": deal,
            "verdict": str(data.get("verdict", "")),
            "reason": str(data.get("reason", "")),
            "status": "deal" if deal else "ok",
        }
    except Exception as exc:  # heuristic fallback
        price, tgt = offer.get("price"), target
        good = price is not None and tgt and price <= tgt
        score = 80 if good else 40
        return {
            "offer": {**offer, "typical_price": None},
            "score": score, "deal": good,
            "verdict": "At/under target" if good else "Above target",
            "reason": f"[heuristic: {exc}]",
            "status": "deal" if good else "ok",
        }


def run_track(params: dict, cfg: dict) -> Iterator[tuple[str, dict]]:
    country = (params.get("country") or "").strip()
    product = (params.get("product") or "").strip()
    condition = (params.get("condition") or "").strip()
    currency = params.get("currency") or cfg.get("currency", "$")
    target = params.get("target_price")
    threshold = params.get("threshold", 60)
    matcher_cfg = cfg.get("matcher", {})

    yield "log", {"level": "info", "msg": f"Product: {product or '—'} · target {currency}{target} · {country or '—'}"}

    provider, model, key = settings.get_effective()
    if not key:
        yield "log", {"level": "error", "msg": "No API key set — open Settings and add a provider key."}
        yield "done", {"processed": 0, "deals": 0, "sources": 0, "currency": currency}
        return
    yield "log", {"level": "info", "msg": f"Provider: {provider} · model: {model}"}
    yield "log", {"level": "info", "msg": "Searching retailers for current prices"}

    try:
        offers = _find_offers(product, country, target, currency, condition, provider, model, key)
    except Exception as exc:
        yield "log", {"level": "error", "msg": f"Search failed: {exc}"}
        yield "done", {"processed": 0, "deals": 0, "sources": 0, "currency": currency}
        return

    if not offers:
        yield "log", {"level": "warn", "msg": "No offers found — try a broader product name."}
        yield "done", {"processed": 0, "deals": 0, "sources": 0, "currency": currency}
        return

    counts: dict[str, int] = {}
    for o in offers:
        counts[o["source"]] = counts.get(o["source"], 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])
    yield "sources", {"items": [{"name": n, "count": c} for n, c in ordered]}
    yield "log", {"level": "ok", "msg": f"Found {len(offers)} offer(s) from {len(counts)} retailer(s)"}

    yield "log", {"level": "info", "msg": "Fetching product images"}
    todo = [o for o in offers if not o.get("image") and o.get("url")]
    if todo:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for o, img in zip(todo, pool.map(lambda x: fetch_og_image(x["url"]), todo)):
                o["image"] = img

    total = len(offers)
    processed = deals = 0
    yield "log", {"level": "info", "msg": f"Scoring {total} offer(s) for deal quality"}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_score_offer, o, product, target, currency, threshold, provider, model, key): o
            for o in offers
        }
        for future in as_completed(futures):
            item = future.result()
            processed += 1
            if item["deal"]:
                deals += 1
            short = (item["offer"].get("title") or "offer")[:46]
            yield "log", {"level": "info", "msg": f"Scored [{processed}/{total}] {short}"}
            yield "offer", item

    yield "log", {"level": "ok", "msg": f"Complete — {deals} deal(s) of {processed}"}
    yield "done", {"processed": processed, "deals": deals,
                   "sources": len(counts), "currency": currency}
