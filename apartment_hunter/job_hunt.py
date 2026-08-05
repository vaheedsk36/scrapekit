"""Job Radar — find and rank current job openings that fit a candidate.

Same shape as the price tracker: web-search for real job openings, then score
each one (how well does it fit the candidate?) in parallel, streaming events the
server turns into Server-Sent Events. Reuses the provider layer + settings.

Events: log · sources · job · done
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterator

from . import providers, settings
from .search import _parse, _to_int_price

MAX_JOBS = 24

SCORE_SYSTEM = (
    "You are a job-fit analyst. Given a candidate's brief and ONE job, judge how "
    "well it fits. Weigh role match, seniority, required skills, remote preference, "
    'and location. Respond ONLY with JSON: {"match": boolean, "score": integer '
    '0-100, "verdict": short phrase (e.g. "Strong fit", "Maybe", "Poor fit"), '
    '"reason": short string}.'
)


def _to_jobs(raw: list) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        url = str(d.get("url", "")).strip()
        title = str(d.get("title", "")).strip()
        company = str(d.get("company", "")).strip()
        key = url or (title + company)
        if not key or key in seen or len(out) >= MAX_JOBS:
            continue
        seen.add(key)
        out.append({
            "title": title,
            "company": company,
            "location": str(d.get("location", "")).strip(),
            "url": url,
            "salary": str(d.get("salary", "")).strip(),
            "posted": str(d.get("posted", "")).strip(),
            "remote": bool(d.get("remote")),
            "source": str(d.get("source", "")).strip() or "web",
            "tags": str(d.get("tags", "")).strip(),
            "description": str(d.get("description", "")).strip(),
        })
    return out


def _find_jobs(role, location, keywords, seniority, remote, provider, model, key):
    skills = ", ".join(keywords)
    rem = " remote" if remote else ""
    primary = (
        f"Search job boards for CURRENT job openings for a {seniority}{rem} "
        f"{role} in {location}. "
        + (f"Matching skills/keywords: {skills}. " if skills else "")
        + "Return up to 12 openings as a JSON array. Each item: "
        '{"title": string, "company": string, "location": string, '
        '"url": string (a direct link to the job posting page), '
        '"salary": string (like "₹20–30 LPA" or a number, else ""), '
        '"posted": string (e.g. "2 days ago"), "remote": boolean, '
        '"source": string (job board name), '
        '"tags": string (comma-separated skills), "description": string}. '
        "Only real, working job posting URLs from genuine job boards. "
        "Output ONLY the JSON array."
    )
    raw = _parse(providers.web_search(provider, model, key, primary))
    if len(raw) < 5:
        supplement = (
            f"Find MORE current {seniority}{rem} {role} openings in {location}"
            + (f" matching {skills}" if skills else "")
            + ", from a VARIETY of job boards (avoid duplicates). "
            'Return ONLY a JSON array of {"title","company","location","url",'
            '"salary","posted","remote","source","tags","description"}. '
            "Real job posting URLs. Never return an empty array."
        )
        raw = raw + _parse(providers.web_search(provider, model, key, supplement))
    return _to_jobs(raw)


def _score_job(job, role, seniority, keywords, remote, location, threshold, provider, model, key) -> dict:
    import json

    try:
        payload = json.dumps({
            "role": role, "seniority": seniority, "must_have_skills": keywords,
            "remote_pref": bool(remote), "location": location, "job": job,
        }, ensure_ascii=False)
        data = providers.chat_json(provider, model, key, SCORE_SYSTEM, payload)
        score = int(data.get("score", 0))
        match = bool(data.get("match")) or score >= threshold
        return {
            "job": job,
            "score": score,
            "match": match,
            "verdict": str(data.get("verdict", "")),
            "reason": str(data.get("reason", "")),
            "status": "match" if match else "ok",
        }
    except Exception as exc:  # heuristic fallback
        hay = (job.get("tags", "") + " " + job.get("description", "") + " "
               + job.get("title", "")).lower()
        wanted = [w.lower() for w in (keywords + role.split()) if w.strip()]
        hits = sum(1 for w in wanted if w in hay)
        score = min(100, int(hits / len(wanted) * 100)) if wanted else 40
        match = score >= threshold
        return {
            "job": job,
            "score": score, "match": match,
            "verdict": "Keyword fit" if match else "Weak fit",
            "reason": f"[heuristic: {exc}]",
            "status": "match" if match else "ok",
        }


def run_jobs(params: dict, cfg: dict) -> Iterator[tuple[str, dict]]:
    country = (params.get("country") or "").strip()
    location = (params.get("location") or "").strip()
    role = (params.get("role") or "").strip()
    keywords = params.get("keywords") or []
    seniority = (params.get("seniority") or "").strip()
    remote = (params.get("remote") or "").strip()
    currency = params.get("currency") or cfg.get("currency", "$")
    threshold = params.get("threshold", 60)

    rem_txt = " · remote" if remote else ""
    yield "log", {"level": "info", "msg": f"Role: {role or '—'} · {seniority or '—'} · {location or '—'}{rem_txt}"}

    provider, model, key = settings.get_effective()
    if not key:
        yield "log", {"level": "error", "msg": "No API key set — open Settings and add a provider key."}
        yield "done", {"processed": 0, "matched": 0, "sources": 0, "currency": currency}
        return
    yield "log", {"level": "info", "msg": f"Provider: {provider} · model: {model}"}
    yield "log", {"level": "info", "msg": "Searching job boards for current openings"}

    try:
        jobs = _find_jobs(role, location, keywords, seniority, remote, provider, model, key)
    except Exception as exc:
        yield "log", {"level": "error", "msg": f"Search failed: {exc}"}
        yield "done", {"processed": 0, "matched": 0, "sources": 0, "currency": currency}
        return

    if not jobs:
        yield "log", {"level": "warn", "msg": "No openings found — try a broader role or location."}
        yield "done", {"processed": 0, "matched": 0, "sources": 0, "currency": currency}
        return

    counts: dict[str, int] = {}
    for j in jobs:
        counts[j["source"]] = counts.get(j["source"], 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])
    yield "sources", {"items": [{"name": n, "count": c} for n, c in ordered]}
    yield "log", {"level": "ok", "msg": f"Found {len(jobs)} opening(s) from {len(counts)} board(s)"}

    total = len(jobs)
    processed = matched = 0
    yield "log", {"level": "info", "msg": f"Scoring {total} opening(s) for fit"}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_score_job, j, role, seniority, keywords, remote, location, threshold, provider, model, key): j
            for j in jobs
        }
        for future in as_completed(futures):
            item = future.result()
            processed += 1
            if item["match"]:
                matched += 1
            short = (item["job"].get("title") or "job")[:46]
            yield "log", {"level": "info", "msg": f"Scoring [{processed}/{total}] {short}"}
            yield "job", item

    yield "log", {"level": "ok", "msg": f"Complete — {matched} match(es) of {processed}"}
    yield "done", {"processed": processed, "matched": matched,
                   "sources": len(counts), "currency": currency}
