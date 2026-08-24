from datetime import datetime, timezone

from morning_digest.models import Item
from morning_digest.summarizer import _available_fallback_model, summarize


def test_no_api_key_uses_fallback():
    item = Item("source", "rss", "AI launch", "https://example.com", datetime.now(timezone.utc),
                excerpt="A factual source excerpt.", item_id=4)
    entries, overview, fallback = summarize([item], "", "unused")
    assert fallback
    assert entries[4].summary == "A factual source excerpt."
    assert overview == ["AI launch"]


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"models": [
            {"name": "models/gemini-3.1-flash-lite", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["countTokens"]},
        ]}


class FakeClient:
    def get(self, *args, **kwargs):
        return FakeResponse()


def test_model_resolution_uses_approved_available_fallback():
    assert _available_fallback_model(FakeClient(), "key", "missing-model") == "gemini-3.1-flash-lite"


def test_model_resolution_does_not_select_unapproved_paid_model():
    response = FakeResponse()
    response.json = lambda: {"models": [
        {"name": "models/gemini-3.5-flash-lite", "supportedGenerationMethods": ["generateContent"]}
    ]}
    client = FakeClient()
    client.get = lambda *args, **kwargs: response
    assert _available_fallback_model(client, "key", "missing-model") is None
