"""Provider abstraction over OpenAI, Anthropic (Claude), and xAI (Grok).

Three operations the app needs:
  - list_models(provider, key)         -> [model_id, ...]   (live, no hardcoding)
  - web_search(provider, model, key, prompt) -> raw text (contains the JSON)
  - chat_json(provider, model, key, system, user) -> parsed dict

Only OpenAI is verified in this environment; Anthropic and xAI are implemented
to their documented 2026 API shapes. xAI is OpenAI-compatible via base_url.
"""
from __future__ import annotations

import json
import re
import urllib.request

_XAI_BASE = "https://api.x.ai/v1"
_OPENAI_BASE = "https://api.openai.com/v1"
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _http_get(url: str, headers: dict, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def loads_json(text: str):
    """Tolerant JSON extraction: code fences, leading/trailing prose."""
    if not text:
        return {}
    m = _FENCE.search(text)
    raw = (m.group(1) if m else text).strip()
    dec = json.JSONDecoder()
    for opener in ("{", "["):
        idx = raw.find(opener)
        if idx != -1:
            try:
                return dec.raw_decode(raw, idx)[0]
            except json.JSONDecodeError:
                continue
    return {}


# ---------------------------------------------------------------- models
def list_models(provider: str, api_key: str) -> list[str]:
    if provider == "anthropic":
        data = _http_get(
            "https://api.anthropic.com/v1/models",
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
        return [m["id"] for m in data.get("data", []) if m.get("id")]

    base = _XAI_BASE if provider == "xai" else _OPENAI_BASE
    data = _http_get(base + "/models", {"Authorization": f"Bearer {api_key}"})
    ids = [m["id"] for m in data.get("data", []) if m.get("id")]
    if provider == "xai":
        ids = [i for i in ids if i.startswith("grok")]
    else:  # openai — drop non-chat models (embeddings, tts, image, moderation…)
        keep = ("gpt-", "o1", "o3", "o4", "chatgpt")
        ids = [i for i in ids if i.startswith(keep)]
    return sorted(set(ids))


# ---------------------------------------------------------------- clients
def _openai_client(api_key: str, base_url: "str | None" = None):
    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)


# ---------------------------------------------------------------- web search
def web_search(provider: str, model: str, api_key: str, prompt: str) -> str:
    if provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model, max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}],
        )
        return "".join(getattr(b, "text", "") for b in resp.content
                       if getattr(b, "type", "") == "text")

    if provider == "xai":
        client = _openai_client(api_key, _XAI_BASE)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            tools=[{"type": "web_search"}],
        )
        return resp.choices[0].message.content or ""

    # openai — Responses API with the web-search tool
    client = _openai_client(api_key)
    resp = client.responses.create(
        model=model, tools=[{"type": "web_search_preview"}], input=prompt,
    )
    return resp.output_text or ""


# ---------------------------------------------------------------- scoring
def chat_json(provider: str, model: str, api_key: str, system: str, user: str) -> dict:
    if provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model, max_tokens=1024, system=system,
            messages=[{"role": "user", "content": user + "\n\nRespond with ONLY a JSON object."}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content
                       if getattr(b, "type", "") == "text")
        return loads_json(text)

    base = _XAI_BASE if provider == "xai" else None
    client = _openai_client(api_key, base)
    resp = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        temperature=0.2,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return loads_json(resp.choices[0].message.content or "")
