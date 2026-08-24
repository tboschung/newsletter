from pathlib import Path

import pytest

from morning_digest.config import read_env


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
