"""Orchestrates one hunt cycle:
   scrape -> pre-filter (free) -> store/dedupe -> match (LLM) -> notify.

The pre-filter applies the hard rules cheaply so the LLM is only spent on
genuine candidates that are new or have dropped in price.
"""
from __future__ import annotations

from . import matcher, notifier, scraper
from .models import Listing
from .store import Store


def prefilter(listing: Listing, criteria: dict) -> bool:
    """Cheap, deterministic gate. Only known-bad listings are rejected;
    unknowns pass through to the (smarter) matcher. A 10% budget slack lets
    the matcher weigh borderline-priced listings on their merits."""
    max_price = criteria.get("max_price")
    if max_price and listing.price is not None and listing.price > max_price * 1.10:
        return False
    min_beds = criteria.get("min_beds")
    if min_beds is not None and listing.beds is not None and listing.beds < min_beds:
        return False
    city = (criteria.get("city") or "").lower()
    if city and listing.location and city not in listing.location.lower():
        return False
    return True


def run(cfg: dict, verbose: bool = False) -> dict:
    criteria = cfg["criteria"]
    matcher_cfg = cfg.get("matcher", {})
    notify_cfg = cfg.get("notify", {"console": True})
    currency = cfg.get("currency", "$")
    store = Store(cfg.get("db_path", "apartment_hunter.db"))

    seen = matched = notified = filtered = 0

    for source in cfg["sources"]:
        try:
            listings = scraper.load_listings(source)
        except SystemExit:
            raise
        except Exception as exc:
            print(f"[pipeline] source {source.get('name')!r} failed: {exc}")
            continue

        for listing in listings:
            seen += 1
            status = store.upsert(listing)

            if not prefilter(listing, criteria):
                filtered += 1
                if verbose:
                    print(f"  · filtered: {listing.title} "
                          f"({currency}{listing.price}, {listing.location})")
                continue

            # Nothing to do if we've already told the user and nothing changed.
            if status == "unchanged" and store.already_notified(listing.id):
                continue

            result = matcher.evaluate(listing, criteria, matcher_cfg, currency)
            store.save_match(listing.id, result)
            if verbose:
                verdict = "MATCH" if result.match else "skip "
                print(f"  · {verdict} {result.score:>3}: {listing.title}")

            if result.match and not store.already_notified(listing.id):
                matched += 1
                notifier.notify(listing, result, notify_cfg, currency)
                store.mark_notified(listing.id)
                notified += 1

    print(f"\nDone.  seen={seen}  matched={matched}  "
          f"notified={notified}  filtered={filtered}")
    return {"seen": seen, "matched": matched,
            "notified": notified, "filtered": filtered}
