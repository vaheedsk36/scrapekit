"""Grants & Tenders — public funding discovery tool.

Same shape as the price tracker: web-search for CURRENT open funding
opportunities (grants and public tenders), then score each one (how relevant
and worth-pursuing for the seeker's sector) in parallel, streaming events the
server turns into Server-Sent Events. Reuses the provider layer + settings.

Events: log · sources · grant · done
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterator

from . import providers, settings
from .search import accumulate, _to_int_price

MAX_ITEMS = 24

SCORE_SYSTEM = (
    "You are a grants and tenders analyst. Given a seeker's sector and keywords "
    "and ONE funding opportunity (grant or tender), judge how relevant and "
    "worth-pursuing it is. Weigh sector/keyword relevance, whether it's open, "
    "and fit. Be a critical, calibrated judge. Reserve 80-100 for genuinely "
    "strong matches, 60-79 for decent, 40-59 for weak, below 40 for poor. Do not "
    'inflate scores. Respond ONLY with JSON: {"match": boolean, "score": integer '
    '0-100, "verdict": short phrase (e.g. "Strong match", "Maybe", "Off-topic"), '
    '"reason": short string}.'
)


def _to_opportunities(raw: list) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        url = str(d.get("url", "")).strip()
        title = str(d.get("title", "")).strip()
        funder = str(d.get("funder", "")).strip()
        key = url or (title + funder)
        if not key or key in seen or len(out) >= MAX_ITEMS:
            continue
        seen.add(key)
        amount = d.get("amount", "")
        out.append({
            "title": title,
            "funder": funder,
            "amount": amount if isinstance(amount, (int, float)) else str(amount).strip(),
            "deadline": str(d.get("deadline", "")).strip(),
            "url": url,
            "location": str(d.get("location", "")).strip(),
            "type": str(d.get("type", "")).strip(),
            "description": str(d.get("description", "")).strip(),
            "source": str(d.get("source", "")).strip() or "web",
        })
    return out


def _find_opportunities(sector, otype, country, keywords, provider, model, key):
    kw = ", ".join(keywords)
    if otype == "grants":
        what = "grants and funding programmes"
    elif otype == "tenders":
        what = "government / public procurement tenders and RFPs"
    else:
        what = "both grants (funding programmes) and government/public procurement tenders and RFPs"

    def make_prompt(exclude):
        prompt = (
            f"Search public funding portals for CURRENT open {what} in {country}, "
            f"relevant to the sector: {sector}. "
            + (f"Match these keywords: {kw}. " if kw else "")
            + f"Prefer well-known, popular funding portals used in the {country} region. "
            + "Return up to 15 items as a JSON array. Each item: "
            '{"title": string, "funder": string (funding agency / issuing body), '
            '"amount": string (like "$50,000") or number, else "", '
            '"deadline": string (date) or "", '
            '"url": string (direct link to the opportunity/notice page), '
            '"location": string, "type": string (grant/tender), '
            '"description": string, '
            '"source": string (portal name, e.g. Grants.gov, TED, SAM.gov)}. '
            "Only real, working opportunity URLs from genuine funding portals. "
            "Output ONLY the JSON array."
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
        funder = str(d.get("funder", "")).strip()
        return url or (title + funder)

    raw = accumulate(provider, model, key, make_prompt, key_of, target=25, max_rounds=4)
    return _to_opportunities(raw)


def _score_opportunity(opp, sector, otype, keywords, country, threshold, provider, model, key) -> dict:
    import json

    try:
        payload = json.dumps({
            "sector": sector, "type": otype, "keywords": keywords,
            "country": country, "opportunity": opp,
        }, ensure_ascii=False)
        data = providers.chat_json(provider, model, key, SCORE_SYSTEM, payload)
        score = int(data.get("score", 0))
        match = score >= threshold
        return {
            "grant": opp,
            "score": score,
            "match": match,
            "verdict": str(data.get("verdict", "")),
            "reason": str(data.get("reason", "")),
            "status": "match" if match else "ok",
        }
    except Exception as exc:  # heuristic fallback
        hay = (str(opp.get("title", "")) + " " + str(opp.get("description", ""))).lower()
        terms = [t.lower() for t in ([sector] + list(keywords)) if t]
        overlap = sum(1 for t in terms if t and t in hay)
        match = overlap > 0
        score = min(90, 40 + overlap * 20)
        return {
            "grant": opp,
            "score": score,
            "match": match,
            "verdict": "Keyword match" if match else "No keyword overlap",
            "reason": f"[heuristic: {exc}]",
            "status": "match" if match else "ok",
        }


def run_grants(params: dict, cfg: dict) -> Iterator[tuple[str, dict]]:
    country = (params.get("country") or "").strip()
    sector = (params.get("sector") or "").strip()
    otype = (params.get("type") or "both").strip().lower()
    if otype not in ("grants", "tenders", "both"):
        otype = "both"
    keywords = params.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    currency = params.get("currency") or cfg.get("currency", "$")
    threshold = params.get("threshold", 60)

    yield "log", {"level": "info", "msg": f"Sector: {sector or '—'} · {otype} · {country or '—'}"}

    provider, model, key = settings.get_effective()
    if not key:
        yield "log", {"level": "error", "msg": "No API key set — open Settings and add a provider key."}
        yield "done", {"processed": 0, "matched": 0, "sources": 0, "currency": currency}
        return
    yield "log", {"level": "info", "msg": f"Provider: {provider} · model: {model}"}
    yield "log", {"level": "info", "msg": "Searching public funding portals"}

    try:
        opps = _find_opportunities(sector, otype, country, keywords, provider, model, key)
    except Exception as exc:
        yield "log", {"level": "error", "msg": f"Search failed: {exc}"}
        yield "done", {"processed": 0, "matched": 0, "sources": 0, "currency": currency}
        return

    if not opps:
        yield "log", {"level": "warn", "msg": "No opportunities found — try a broader sector or fewer keywords."}
        yield "done", {"processed": 0, "matched": 0, "sources": 0, "currency": currency}
        return

    counts: dict[str, int] = {}
    for o in opps:
        counts[o["source"]] = counts.get(o["source"], 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])
    yield "sources", {"items": [{"name": n, "count": c} for n, c in ordered]}
    yield "log", {"level": "ok", "msg": f"Found {len(opps)} opportunity(ies) from {len(counts)} portal(s)"}

    total = len(opps)
    processed = matched = 0
    yield "log", {"level": "info", "msg": f"Scoring {total} opportunity(ies) for relevance"}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_score_opportunity, o, sector, otype, keywords, country, threshold, provider, model, key): o
            for o in opps
        }
        for future in as_completed(futures):
            item = future.result()
            processed += 1
            if item["match"]:
                matched += 1
            short = (item["grant"].get("title") or "opportunity")[:46]
            yield "log", {"level": "info", "msg": f"Scoring [{processed}/{total}] {short}"}
            yield "grant", item

    yield "log", {"level": "ok", "msg": f"Complete — {matched} match(es) of {processed}"}
    yield "done", {"processed": processed, "matched": matched,
                   "sources": len(counts), "currency": currency}
