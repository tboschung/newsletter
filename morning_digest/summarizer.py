from __future__ import annotations

import json
import logging
from collections.abc import Iterable

import httpx

from .models import DigestEntry, Item

LOG = logging.getLogger(__name__)
FREE_MODEL_FALLBACKS = (
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
)


def fallback_summary(item: Item) -> DigestEntry:
    excerpt = item.excerpt.strip()
    if len(excerpt) > 280:
        summary = excerpt[:280].rsplit(" ", 1)[0] + "…"
    else:
        summary = excerpt or item.title
    if item.category == "jobs":
        why = "A recent entry-level AI/ML opportunity matching the digest's location criteria."
    elif item.category == "technology":
        why = "Potentially relevant to current AI research or development workflows."
    else:
        why = "A highly ranked recent AI development from the monitored sources."
    return DigestEntry(item=item, summary=summary, why_it_matters=why)


def summarize(items: Iterable[Item], api_key: str, model: str) -> tuple[dict[int, DigestEntry], list[str], bool]:
    values = list(items)
    fallback = {i.item_id: fallback_summary(i) for i in values if i.item_id is not None}
    overview = [i.title for i in values if i.category != "jobs"][:3]
    if not api_key or not values:
        return fallback, overview, True
    payload_items = [{
        "id": i.item_id, "section": i.category, "title": i.title,
        "source": i.source, "excerpt": i.excerpt[:1600], "company": i.company,
        "location": i.location,
    } for i in values]
    prompt = (
        "Create an English AI newsletter using only the supplied records. Return strict JSON: "
        '{"overview":[three short bullets],"items":[{"id":integer,"summary":"1-2 factual sentences",'
        '"why_it_matters":"one short sentence"}]}. Do not add facts, URLs, or IDs. '
        "For jobs, summarize responsibilities and eligibility without inventing requirements. Records:\n"
        + json.dumps(payload_items, ensure_ascii=False)
    )
    try:
        with httpx.Client(timeout=45) as client:
            request_body = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
            }
            response = _generate(client, api_key, model, request_body)
            if response.status_code == 404:
                replacement = _available_fallback_model(client, api_key, model)
                if not replacement:
                    raise RuntimeError(
                        f"Gemini model {model!r} is unavailable and no approved fallback model "
                        "supports generateContent for this API key"
                    )
                LOG.warning("Gemini model %s unavailable; retrying with %s", model, replacement)
                response = _generate(client, api_key, replacement, request_body)
            response.raise_for_status()
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
        known = {i.item_id: i for i in values}
        generated: dict[int, DigestEntry] = {}
        for entry in result.get("items", []):
            item_id = entry.get("id")
            if item_id not in known:
                continue
            summary = str(entry.get("summary", "")).strip()
            why = str(entry.get("why_it_matters", "")).strip()
            if summary:
                generated[item_id] = DigestEntry(known[item_id], summary[:700], why[:400])
        if not generated:
            raise ValueError("Gemini returned no recognized items")
        fallback.update(generated)
        clean_overview = [str(x).strip()[:300] for x in result.get("overview", []) if str(x).strip()][:3]
        return fallback, clean_overview or overview, False
    except Exception as exc:
        LOG.warning("Gemini summarization failed; using excerpts: %s", exc)
        return fallback, overview, True


def _generate(client: httpx.Client, api_key: str, model: str, body: dict) -> httpx.Response:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    return client.post(url, headers={"x-goog-api-key": api_key}, json=body)


def _available_fallback_model(client: httpx.Client, api_key: str, requested: str) -> str | None:
    """Select only an explicitly approved model exposed to this API project.

    The list call is intentionally made only after a 404. This avoids an extra daily
    request while still surviving model retirement or project-specific availability.
    """
    response = client.get(
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
        headers={"x-goog-api-key": api_key},
    )
    response.raise_for_status()
    supported = {
        str(entry.get("name", "")).removeprefix("models/")
        for entry in response.json().get("models", [])
        if "generateContent" in entry.get("supportedGenerationMethods", [])
    }
    for candidate in (requested, *FREE_MODEL_FALLBACKS):
        if candidate in supported:
            return candidate
    return None
