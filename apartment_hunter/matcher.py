"""Decide whether a listing fits the user's criteria.

Uses OpenAI GPT-4.1-mini when an API key is present; otherwise falls back to a
transparent scoring heuristic so the project always runs. If an LLM call fails
mid-run, it degrades to the heuristic rather than crashing the pipeline.
"""
from __future__ import annotations

import json
import os

from .models import Listing, MatchResult

SYSTEM_PROMPT = (
    "You are an apartment-hunting assistant. Given a renter's criteria and a "
    "single listing, judge how well the listing fits. Weigh budget, bedrooms, "
    "location, and desired features. Be strict about hard limits (budget, city) "
    'and reward listed perks. Respond ONLY with JSON: '
    '{"match": boolean, "score": integer 0-100, "reason": short string, '
    '"blurb": one-line human summary}.'
)


def evaluate(listing: Listing, criteria: dict, matcher_cfg: dict,
             currency: str = "$") -> MatchResult:
    use_llm = matcher_cfg.get("use_llm", True) and bool(os.environ.get("OPENAI_API_KEY"))
    if use_llm:
        try:
            return _llm_eval(listing, criteria, matcher_cfg, currency)
        except Exception as exc:
            result = _heuristic(listing, criteria, currency)
            result.reason = f"[LLM fallback: {exc}] " + result.reason
            return result
    return _heuristic(listing, criteria, currency)


def _heuristic(listing: Listing, criteria: dict, currency: str = "$") -> MatchResult:
    score = 50
    reasons: list[str] = []
    cur = currency

    max_price = criteria.get("max_price")
    if listing.price is not None and max_price:
        if listing.price <= max_price:
            score += 25
            reasons.append(f"within budget ({cur}{listing.price} <= {cur}{max_price})")
        else:
            score -= 40
            reasons.append(f"over budget ({cur}{listing.price} > {cur}{max_price})")

    min_beds = criteria.get("min_beds")
    if listing.beds is not None and min_beds is not None:
        if listing.beds >= min_beds:
            score += 10
            reasons.append(f"{listing.beds:g} beds")
        else:
            score -= 20
            reasons.append(f"only {listing.beds:g} beds")

    city = (criteria.get("city") or "").lower()
    if city:
        if city in listing.location.lower():
            score += 10
            reasons.append(f"in {criteria['city']}")
        else:
            score -= 15
            reasons.append("outside target city")

    haystack = f"{listing.title} {listing.description}".lower()
    hits = [kw for kw in criteria.get("keywords", []) if kw.lower() in haystack]
    if hits:
        score += 5 * len(hits)
        reasons.append("has " + ", ".join(hits))

    score = max(0, min(100, score))
    threshold = criteria.get("match_threshold", 60)
    reason_text = "; ".join(reasons) if reasons else "no signal"
    blurb = (
        f"{listing.beds:g}bd" if listing.beds is not None else "?bd"
    ) + f" in {listing.location or 'unknown'} for " + (
        f"{cur}{listing.price}" if listing.price is not None else f"{cur}?"
    ) + f" — {reason_text}"
    return MatchResult(
        match=score >= threshold, score=score, reason=reason_text, blurb=blurb
    )


def _llm_eval(listing: Listing, criteria: dict, matcher_cfg: dict,
              currency: str = "$") -> MatchResult:
    from openai import OpenAI

    client = OpenAI()
    payload = json.dumps(
        {
            "currency": currency,
            "criteria": criteria,
            "listing": {
                "title": listing.title,
                "price": listing.price,
                "beds": listing.beds,
                "location": listing.location,
                "description": listing.description,
            },
        },
        ensure_ascii=False,
    )
    resp = client.chat.completions.create(
        model=matcher_cfg.get("model", "gpt-4.1-mini"),
        response_format={"type": "json_object"},
        temperature=0.2,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": payload},
        ],
    )
    data = json.loads(resp.choices[0].message.content)
    return MatchResult(
        match=bool(data.get("match")),
        score=int(data.get("score", 0)),
        reason=str(data.get("reason", "")),
        blurb=str(data.get("blurb", "")),
    )
