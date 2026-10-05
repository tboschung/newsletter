from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


ENV_FILE = Path("/etc/newsletter.env")
APP_DIR = Path(__file__).resolve().parent
CONFIG_DIR = APP_DIR.parent / "config"
DEFAULT_ENGINES_DIR = CONFIG_DIR / "engines"
DEFAULT_SUBSCRIBERS_DIR = CONFIG_DIR / "subscribers"
DEFAULT_PRESETS_DIR = CONFIG_DIR / "presets"
IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SUPPORTED_ADAPTERS = {
    "rss", "hacker_news", "arxiv", "arbeitnow", "jobicy", "remotive",
    "remote_ok", "swiss_ai_job", "jobs_ch", "swiss_dev_jobs",
}


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
class SourceConfig:
    name: str
    adapter: str
    category: str
    url: str = ""
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class EngineConfig:
    name: str
    sources: tuple[SourceConfig, ...]


@dataclass(frozen=True, slots=True)
class Subscriber:
    subscriber_id: str
    email: str
    content: frozenset[str]
    job_profile: str = "cv"


@dataclass(frozen=True, slots=True)
class Preset:
    preset_id: str
    name: str
    description: str
    content: frozenset[str]
    job_profile: str = "cv"


def load_presets(directory: Path) -> tuple[Preset, ...]:
    presets: list[Preset] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.toml")):
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
        if not doc.get("enabled", True):
            continue
        preset_id = str(doc.get("id", path.stem)).strip()
        if not IDENTIFIER_RE.fullmatch(preset_id) or preset_id in seen:
            raise ValueError(f"invalid or duplicate preset id in {path}: {preset_id!r}")
        name = str(doc.get("name", "")).strip()
        description = str(doc.get("description", "")).strip()
        if not name or not description:
            raise ValueError(f"preset {preset_id!r} requires a name and description")
        content = frozenset(str(value) for value in doc.get("content", []))
        unknown = content - {"news", "technology", "jobs"}
        if not content or unknown:
            raise ValueError(f"preset {preset_id!r} has invalid content: {sorted(unknown)}")
        presets.append(Preset(
            preset_id=preset_id,
            name=name,
            description=description,
            content=content,
            job_profile=str(doc.get("job_profile", "cv")),
        ))
        seen.add(preset_id)
    if not presets:
        raise ValueError(f"no enabled preset configurations found in {directory}")
    return tuple(presets)


def resolve_subscriber(
    subscriber_id: str,
    email: str,
    preset: Preset,
    overrides: dict[str, object] | None = None,
) -> Subscriber:
    """Resolve a stored subscription into today's digest configuration.

    Overrides are deliberately narrow. Keeping this merge at the domain boundary lets a
    future authenticated API expose custom settings without changing storage or delivery.
    """
    values = overrides or {}
    raw_content = values.get("content", preset.content)
    if not isinstance(raw_content, (list, tuple, set, frozenset)):
        raise ValueError(f"subscriber {subscriber_id!r} has invalid content override")
    content = frozenset(str(value) for value in raw_content)
    unknown = content - {"news", "technology", "jobs"}
    if not content or unknown:
        raise ValueError(f"subscriber {subscriber_id!r} has invalid content: {sorted(unknown)}")
    job_profile = str(values.get("job_profile", preset.job_profile)).strip()
    if not IDENTIFIER_RE.fullmatch(job_profile):
        raise ValueError(f"subscriber {subscriber_id!r} has invalid job profile")
    return Subscriber(subscriber_id, email, content, job_profile)


def load_engine(path: Path) -> EngineConfig:
    doc = tomllib.loads(path.read_text(encoding="utf-8"))
    name = str(doc.get("engine", {}).get("name", path.stem)).strip()
    if not IDENTIFIER_RE.fullmatch(name):
        raise ValueError(f"invalid engine name in {path}: {name!r}")
    sources = tuple(SourceConfig(**source) for source in doc.get("sources", []))
    if not sources:
        raise ValueError(f"engine has no sources: {path}")
    for source in sources:
        if source.adapter not in SUPPORTED_ADAPTERS:
            raise ValueError(f"invalid adapter {source.adapter!r} in {path}")
        if source.category not in {"news", "technology", "jobs"}:
            raise ValueError(f"invalid category {source.category!r} in {path}")
        if source.adapter == "rss" and not source.url:
            raise ValueError(f"RSS source {source.name!r} has no URL in {path}")
    return EngineConfig(name, sources)


def load_subscribers(directory: Path, env: dict[str, str]) -> tuple[Subscriber, ...]:
    subscribers: list[Subscriber] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.toml")):
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
        if not doc.get("enabled", True):
            continue
        subscriber_id = str(doc.get("id", path.stem)).strip()
        if not IDENTIFIER_RE.fullmatch(subscriber_id) or subscriber_id in seen:
            raise ValueError(f"invalid or duplicate subscriber id in {path}: {subscriber_id!r}")
        email = str(doc.get("email", "")).strip()
        email_env = str(doc.get("email_env", "")).strip()
        if email_env:
            email = env.get(email_env, "").strip()
        if not email or "@" not in email:
            raise ValueError(f"subscriber {subscriber_id!r} has no valid email in {path}")
        content = frozenset(str(value) for value in doc.get("content", []))
        unknown = content - {"news", "technology", "jobs"}
        if not content or unknown:
            raise ValueError(f"subscriber {subscriber_id!r} has invalid content: {sorted(unknown)}")
        subscribers.append(Subscriber(
            subscriber_id, email, content, str(doc.get("job_profile", "cv"))
        ))
        seen.add(subscriber_id)
    if not subscribers:
        raise ValueError(f"no enabled subscriber configurations found in {directory}")
    return tuple(subscribers)


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    output_dir: Path
    timezone: ZoneInfo
    smtp_username: str
    smtp_app_password: str
    gemini_api_key: str
    gemini_model: str
    job_profiles_dir: Path
    engines: tuple[EngineConfig, ...]
    subscribers: tuple[Subscriber, ...]
    presets: tuple[Preset, ...]
    public_url: str = ""

    @classmethod
    def load(cls, engine_file: str | None = None) -> "Settings":
        config = read_env()
        engines_dir = Path(config.get("DIGEST_ENGINES_DIR", DEFAULT_ENGINES_DIR))
        engine_paths = [Path(engine_file)] if engine_file else sorted(engines_dir.glob("*.toml"))
        if not engine_paths:
            raise ValueError(f"no engine configurations found in {engines_dir}")
        subscribers_dir = Path(config.get("DIGEST_SUBSCRIBERS_DIR", DEFAULT_SUBSCRIBERS_DIR))
        presets_dir = Path(config.get("DIGEST_PRESETS_DIR", DEFAULT_PRESETS_DIR))
        engines = tuple(load_engine(path) for path in engine_paths)
        engine_names = [engine.name for engine in engines]
        if len(engine_names) != len(set(engine_names)):
            raise ValueError(f"duplicate engine names configured: {engine_names}")
        public_url = config.get("DIGEST_PUBLIC_URL", "").rstrip("/")
        if not public_url:
            raise ValueError("DIGEST_PUBLIC_URL is required")
        if not public_url.startswith(("http://", "https://")):
            raise ValueError("DIGEST_PUBLIC_URL must be an absolute HTTP(S) URL")
        return cls(
            database_path=Path(config.get("DIGEST_DATABASE", "data/digest.sqlite3")),
            output_dir=Path(config.get("DIGEST_OUTPUT_DIR", "data/previews")),
            timezone=ZoneInfo(config.get("TZ", "Europe/Zurich")),
            smtp_username=config.get("SMTP_USERNAME", ""),
            smtp_app_password=config.get("SMTP_APP_PASSWORD", ""),
            gemini_api_key=config.get("GEMINI_API_KEY", ""),
            gemini_model=config.get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
            job_profiles_dir=Path(config.get("JOB_PROFILES_DIR", "data/job_profiles")),
            engines=engines,
            subscribers=load_subscribers(subscribers_dir, config),
            presets=load_presets(presets_dir),
            public_url=public_url,
        )
