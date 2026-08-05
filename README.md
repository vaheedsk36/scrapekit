# ScrapeKit

A self-hosted, bring-your-own-key toolkit of small scraping utilities. The home
page (`/`) is a hub of tools. Two are live — **Apartment Hunter** and
**Price Tracker** — with Job Radar and Grants & Tenders on the way. Both share
the same engine: web-search for real results, score each with an LLM, stream the
process live, and export.

## Apartment Hunter

Enter a country, city, localities and your requirements; it searches live
property sites for real listings, scores each one against your brief with an
LLM, pulls preview images, and streams the whole process to an interactive
dashboard you can export from.

Built to mirror the [Nous Research **Hermes Agent**](https://hermes-agent.nousresearch.com)
pattern — **search → structure → score → present** — as a small, readable
Python project (the web server is standard-library only).

> **Bring your own key.** Everything runs locally and uses *your* API key.
> Choose your provider in-app (**OpenAI**, **Anthropic/Claude**, or **xAI/Grok**);
> the model list is fetched live from that provider — nothing hard-coded. The key
> is cached **on the server** (`~/.scrapekit/settings.json`), never in the browser.
> Nothing is sent anywhere except your chosen provider (search + scoring), the
> geocoder (city autocomplete), and the listing sites (preview images).

> Only the OpenAI path is verified in this repo's testing; the Claude and Grok
> integrations follow each provider's documented API — verify with your own key.

## Quickstart

### One command with [uv](https://docs.astral.sh/uv/) (recommended)

`uv` manages Python **and** dependencies for you — no venv, no pip, and it uses
its own Python so it sidesteps system-Python issues. Run straight from GitHub
(no clone needed):

```bash
uvx --from git+https://github.com/vaheedsk36/scrapekit scrapekit serve
```

It opens <http://127.0.0.1:8000> in your browser. Click **Apartment Hunter**,
open **Settings**, add your provider key — or pass it inline:

```bash
OPENAI_API_KEY=sk-... uvx --from git+https://github.com/vaheedsk36/scrapekit scrapekit serve
```

From a clone (for development):

```bash
git clone https://github.com/vaheedsk36/scrapekit && cd scrapekit
uv run scrapekit serve
```

### Or plain pip

```bash
git clone https://github.com/vaheedsk36/scrapekit && cd scrapekit
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
scrapekit serve
```

Then open <http://127.0.0.1:8000>, click **Apartment Hunter**, set your brief,
and hit **Search listings**.

## The web UI

- **Country / City / Localities** — city + locality fields autocomplete as you
  type (free, via the OpenStreetMap/Photon geocoder). Add multiple localities.
- **BHK, Max rent, Furnishing, Amenities** — pick from chips; currency is
  derived from the country.
- **Live terminal** — streams every step (search → sources → images → scoring).
- **Results table** — real listings linking back to their source, with preview
  images, an LLM **Match** score (0–100) and a one-line assessment. Sort by
  rent / BHK / match, filter to matches, and **export CSV or JSON**.

## How it works

```
web search (your key)  ->  parse + dedupe  ->  og:image fetch  ->  LLM scoring  ->  live UI
   apartment_hunter/       search.py           search.py          matcher.py       server.py + web/
```

| Module | Role |
|--------|------|
| `search.py`   | OpenAI web-search for real listings; robust JSON parsing; og:image fetch |
| `webhunt.py`  | Orchestrates one hunt; scores listings in parallel; yields SSE events |
| `matcher.py`  | Scores a listing 0–100 vs the brief (GPT-4.1-mini; heuristic fallback) |
| `server.py`   | Zero-dependency stdlib server + Server-Sent Events; serves `web/index.html` |
| `store.py` / `notifier.py` / `pipeline.py` | The optional CLI cron/alert path |

## Known limitation: result volume

Listings come from OpenAI's **web-search tool**, which is built to answer a
question with a few citations — not to return large result sets. Expect roughly
**6–12 listings per search, varying run to run** (a complementary search tops up
thin results). This is a deliberate tradeoff for a free, key-only tool. To get
30–50+ listings you'd need a paid data source (a rentals API, or a scraping API
like ScraperAPI/ZenRows to read portals' own search pages) — intentionally out
of scope here.

Cost per search is a few cents on `gpt-4.1-mini` (one web search + a handful of
scoring calls). Preview images and the geocoder are free.

## Optional: CLI + scheduled alerts

`python -m apartment_hunter run` runs a headless cycle against configured
sources (see `config.example.yaml`) with SQLite de-duplication and optional
Telegram alerts — installable as a Hermes cron job via `hermes/SKILL.md`.

## License

MIT — see [LICENSE](LICENSE).
