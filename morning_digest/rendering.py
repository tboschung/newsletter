from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, select_autoescape

from .models import Digest


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def render(digest: Digest) -> tuple[str, str, str]:
    env = Environment(autoescape=select_autoescape(("html", "xml")), trim_blocks=True, lstrip_blocks=True)
    html_template = env.from_string((TEMPLATE_DIR / "digest.html.j2").read_text(encoding="utf-8"))
    text_template = Environment(trim_blocks=True, lstrip_blocks=True).from_string(
        (TEMPLATE_DIR / "digest.txt.j2").read_text(encoding="utf-8")
    )
    context = {"digest": digest, "date": digest.cutoff_end.strftime("%A, %d %B %Y")}
    subject = f"AI Newsletter — {digest.cutoff_end:%d %b %Y}"
    return subject, html_template.render(**context), text_template.render(**context)
