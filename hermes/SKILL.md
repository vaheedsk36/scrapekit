---
name: apartment-hunter
description: >
  Scrape rental listing sites, use an LLM to score each listing against the
  renter's criteria, de-duplicate against a local database, and alert on new
  matches. Runs as a scheduled Hermes cron job.
---

# Apartment Hunter skill

A self-contained scrape → match → alert pipeline. Hermes drives it on a
schedule; the script does the work (and calls the model itself), so the cron
layer needs no LLM.

## When to use
Invoke on a schedule (every 30–60 min) to watch rental sites for listings that
fit the user's saved criteria and notify them the moment a good one appears.

## How to run
```bash
cd ~/Documents/apartment-hunter
python -m apartment_hunter run --config config.yaml
```

- Criteria and sources live in `config.yaml` (`config.example.yaml` is a template).
- Secrets (`OPENAI_API_KEY`, `TELEGRAM_*`) live in `.env`.
- Already-notified listings are remembered in `apartment_hunter.db`, so repeat
  runs only surface genuinely new matches — safe to run frequently.

## Adding a new site
Find the repeating card element and its child selectors, then add a `type: html`
source with `selectors`. Set `stealth: true` for Cloudflare-protected sites so
Hermes/Scrapling's stealth fetcher handles the bypass.

## Notes for the agent
- Exit code 0 always; a failed source logs and is skipped, never aborts the run.
- If `OPENAI_API_KEY` is missing the script still runs on a heuristic matcher.
