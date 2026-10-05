from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

from dateutil import parser as date_parser

from morning_digest.config import Settings
from morning_digest.emailer import smtp_check
from morning_digest.service import run
from morning_digest.storage import Storage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and send the newsletter")
    parser.add_argument("--engine", help="Run one alternative engine TOML file")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check-config", help="Validate configuration")
    check.add_argument("--smtp", action="store_true", help="Also authenticate to Gmail")

    execute = subparsers.add_parser("run", help="Build and optionally send a digest")
    execute.add_argument("--dry-run", action="store_true", help="Write a preview without sending")
    execute.add_argument("--as-of", help="ISO-8601 time used to determine the cutoff")
    web = subparsers.add_parser("serve", help="Serve the signup page and subscription API")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8080)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = Settings.load(args.engine)
        if args.command == "check-config":
            Storage(settings.database_path).close()
            if args.smtp:
                if not settings.smtp_app_password:
                    raise ValueError("SMTP_APP_PASSWORD is not set")
                smtp_check(settings.smtp_username, settings.smtp_app_password)
            source_count = sum(len(engine.sources) for engine in settings.engines)
            print(
                f"Configuration valid: {len(settings.engines)} engines, {source_count} sources, "
                f"{len(settings.subscribers)} subscribers, database {settings.database_path}"
            )
            if not settings.gemini_api_key:
                print("Note: GEMINI_API_KEY is absent; excerpt fallback will be used.")
            return 0

        if args.command == "serve":
            from morning_digest.web import serve

            serve(settings, args.host, args.port, Path(__file__).with_name("index.html"))
            return 0

        as_of = date_parser.isoparse(args.as_of) if args.as_of else datetime.now().astimezone()
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=settings.timezone)
        previews, message = run(settings, as_of, dry_run=args.dry_run)
        print(message)
        for preview in previews:
            print(preview.resolve())
        return 0
    except Exception as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
