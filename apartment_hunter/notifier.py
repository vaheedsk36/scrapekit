"""Deliver matches. Telegram when configured (via the stdlib — no extra deps),
always with a console fallback so you see output during development."""
from __future__ import annotations

import json
import os
import urllib.request

from .models import Listing, MatchResult


def notify(listing: Listing, result: MatchResult, notify_cfg: dict,
           currency: str = "$") -> None:
    message = _format(listing, result, currency)
    if notify_cfg.get("console", True):
        print("\n" + message + "\n")
    if notify_cfg.get("telegram"):
        _telegram(message)


def _format(listing: Listing, result: MatchResult, currency: str = "$") -> str:
    stars = "⭐" * max(1, result.score // 20)
    price = f"{currency}{listing.price}" if listing.price is not None else f"{currency}?"
    beds = f"{listing.beds:g}" if listing.beds is not None else "?"
    return (
        f"🏠 *New match* {stars} ({result.score}/100)\n"
        f"*{listing.title}*\n"
        f"💵 {price}   🛏 {beds}   📍 {listing.location}\n"
        f"_{result.blurb}_\n"
        f"{listing.url}"
    )


def _telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        print("[notifier] Telegram enabled but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID unset")
        return
    body = json.dumps(
        {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as exc:
        print(f"[notifier] Telegram send failed: {exc}")
