from morning_digest.text import canonicalize_url, similar_titles


def test_canonical_url_removes_tracking_and_fragment():
    assert canonicalize_url("https://www.Example.com/a/?utm_source=x&keep=1#part") == "https://example.com/a?keep=1"


def test_similar_titles_detects_reworded_duplicate():
    assert similar_titles("OpenAI launches a new reasoning model today", "OpenAI launches new reasoning model")
    assert not similar_titles("New reasoning model", "Zurich graduate internship")

