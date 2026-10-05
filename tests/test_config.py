from pathlib import Path

import pytest

from morning_digest.config import (
    Preset,
    load_engine,
    load_presets,
    load_subscribers,
    read_env,
    resolve_subscriber,
)


def test_read_env(tmp_path: Path):
    env_file = tmp_path / "newsletter.env"
    env_file.write_text(
        "# comment\nTZ=Europe/Zurich\nSMTP_APP_PASSWORD='secret value'\n",
        encoding="utf-8",
    )

    assert read_env(env_file) == {
        "TZ": "Europe/Zurich",
        "SMTP_APP_PASSWORD": "secret value",
    }


def test_read_env_rejects_invalid_lines(tmp_path: Path):
    env_file = tmp_path / "newsletter.env"
    env_file.write_text("not-an-assignment\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected KEY=value"):
        read_env(env_file)


def test_loads_independent_engine_config(tmp_path: Path):
    path = tmp_path / "news.toml"
    path.write_text(
        '[engine]\nname="news"\n[[sources]]\nname="Example"\nadapter="rss"\n'
        'category="news"\nurl="https://example.com/feed"\n',
        encoding="utf-8",
    )
    engine = load_engine(path)
    assert engine.name == "news"
    assert engine.sources[0].adapter == "rss"


def test_loads_subscriber_content_from_its_own_config(tmp_path: Path):
    (tmp_path / "alice.toml").write_text(
        'id="alice"\nemail_env="ALICE_EMAIL"\ncontent=["jobs"]\njob_profile="cv"\n',
        encoding="utf-8",
    )
    subscribers = load_subscribers(tmp_path, {"ALICE_EMAIL": "alice@example.com"})
    assert subscribers[0].content == frozenset({"jobs"})
    assert subscribers[0].email == "alice@example.com"


def test_loads_and_resolves_preset_with_custom_override_boundary(tmp_path: Path):
    (tmp_path / "brief.toml").write_text(
        'id="brief"\nname="Brief"\ndescription="The essentials."\n'
        'content=["news","technology"]\n',
        encoding="utf-8",
    )
    preset = load_presets(tmp_path)[0]
    assert preset.name == "Brief"
    subscriber = resolve_subscriber(
        "web-1", "reader@example.com", preset, {"content": ["jobs"]}
    )
    assert subscriber.content == frozenset({"jobs"})


def test_resolve_subscriber_rejects_unknown_custom_content():
    preset = Preset("brief", "Brief", "The essentials.", frozenset({"news"}))
    with pytest.raises(ValueError, match="invalid content"):
        resolve_subscriber("web-1", "reader@example.com", preset, {"content": ["sports"]})
