"""SQLite persistence: remembers what we've seen so we never alert twice,
and keeps a price history so we can spot genuine drops."""
from __future__ import annotations

import sqlite3
import time

from .models import Listing, MatchResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
  id          TEXT PRIMARY KEY,
  source      TEXT,
  title       TEXT,
  url         TEXT,
  price       INTEGER,
  beds        REAL,
  location    TEXT,
  description TEXT,
  score       INTEGER,
  matched     INTEGER DEFAULT 0,
  reason      TEXT,
  blurb       TEXT,
  notified    INTEGER DEFAULT 0,
  first_seen  REAL,
  last_seen   REAL
);
CREATE TABLE IF NOT EXISTS price_history (
  listing_id TEXT,
  price      INTEGER,
  seen_at    REAL
);
"""


class Store:
    def __init__(self, path: str = "apartment_hunter.db") -> None:
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def upsert(self, listing: Listing) -> str:
        """Insert or update a listing. Returns 'new', 'price_drop', or 'unchanged'."""
        now = time.time()
        row = self.conn.execute(
            "SELECT price FROM listings WHERE id=?", (listing.id,)
        ).fetchone()

        if row is None:
            self.conn.execute(
                """INSERT INTO listings
                   (id, source, title, url, price, beds, location, description,
                    first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (listing.id, listing.source, listing.title, listing.url, listing.price,
                 listing.beds, listing.location, listing.description, now, now),
            )
            self._record_price(listing, now)
            self.conn.commit()
            return "new"

        old_price = row["price"]
        status = "unchanged"
        if listing.price is not None and old_price is not None and listing.price < old_price:
            status = "price_drop"
            self._record_price(listing, now)
        self.conn.execute(
            "UPDATE listings SET price=?, last_seen=? WHERE id=?",
            (listing.price, now, listing.id),
        )
        self.conn.commit()
        return status

    def _record_price(self, listing: Listing, now: float) -> None:
        if listing.price is not None:
            self.conn.execute(
                "INSERT INTO price_history (listing_id, price, seen_at) VALUES (?,?,?)",
                (listing.id, listing.price, now),
            )

    def already_notified(self, listing_id: str) -> bool:
        row = self.conn.execute(
            "SELECT notified FROM listings WHERE id=?", (listing_id,)
        ).fetchone()
        return bool(row and row["notified"])

    def save_match(self, listing_id: str, result: MatchResult) -> None:
        self.conn.execute(
            "UPDATE listings SET score=?, matched=?, reason=?, blurb=? WHERE id=?",
            (result.score, int(result.match), result.reason, result.blurb, listing_id),
        )
        self.conn.commit()

    def mark_notified(self, listing_id: str) -> None:
        self.conn.execute(
            "UPDATE listings SET notified=1 WHERE id=?", (listing_id,)
        )
        self.conn.commit()
