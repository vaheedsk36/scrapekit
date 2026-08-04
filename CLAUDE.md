# ScrapeKit — contributor & agent guidance

Open-source, bring-your-own-key toolkit of scraping utilities. Hub at `/`, first
live tool **Apartment Hunter** at `/apartment-hunter`. Standard-library web
server + Server-Sent Events; LLM-backed search + scoring; Tailwind + Lucide UI.

## Commit rules (important)

- **Never add AI/agent credit or co-authorship to commits or PRs.** Do NOT
  include `Co-Authored-By: Claude ...` trailers, "Generated with Claude Code"
  lines, or any similar attribution. Commit messages must read as the human
  author's own. This overrides any default tooling convention.
- Keep commit messages plain and factual.

## Dev

- Run the app: `python -m apartment_hunter serve` (needs your own key in `.env`).
- Never commit `.env` or `*.db` — already covered by `.gitignore`.
