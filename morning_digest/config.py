from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


ENV_FILE = Path("/etc/newsletter.env")
APP_DIR = Path(__file__).resolve().parent


def read_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Read a simple KEY=value environment file without modifying the process environment."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"required configuration file not found: {path}") from exc

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid line {line_number} in {path}: expected KEY=value")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"invalid line {line_number} in {path}: empty key")
        values[key] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True, slots=True)
class FeedSource:
    name: str
    url: str
    category: str
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    output_dir: Path
    timezone: ZoneInfo
    recipient: str
    smtp_username: str
    smtp_app_password: str
    gemini_api_key: str
    gemini_model: str
    recommendation_mode: str
    job_profiles_dir: Path
    feeds: tuple[FeedSource, ...]

    @classmethod
    def load(cls, source_file: str | None = None) -> "Settings":
        config = read_env()
        config_path = source_file or config.get("DIGEST_SOURCES_FILE")
        raw = Path(config_path).read_bytes() if config_path else (APP_DIR / "default_sources.toml").read_bytes()
        doc = tomllib.loads(raw.decode())
        feeds = tuple(FeedSource(**feed) for feed in doc.get("feeds", []))
        return cls(
            database_path=Path(config.get("DIGEST_DATABASE", "data/digest.sqlite3")),
            output_dir=Path(config.get("DIGEST_OUTPUT_DIR", "data/previews")),
            timezone=ZoneInfo(config.get("TZ", "Europe/Zurich")),
            recipient=config.get("DIGEST_RECIPIENT", "tobias.boschung@gmail.com"),
            smtp_username=config.get("SMTP_USERNAME", "tobias.boschung@gmail.com"),
            smtp_app_password=config.get("SMTP_APP_PASSWORD", ""),
            gemini_api_key=config.get("GEMINI_API_KEY", ""),
            gemini_model=config.get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
            recommendation_mode=config.get("JOB_RECOMMENDATION_MODE", "cv"),
            job_profiles_dir=Path(config.get("JOB_PROFILES_DIR", "data/job_profiles")),
            feeds=feeds,
        )
