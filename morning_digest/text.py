from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}


def clean_html(value: str) -> str:
    value = re.sub(r"<(script|style).*?</\1>", " ", value or "", flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_KEYS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", host, path, urlencode(query), ""))


def normalized_title(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", title.lower()))


def fingerprint(url: str, title: str) -> str:
    payload = f"{canonicalize_url(url)}\n{normalized_title(title)}"
    return hashlib.sha256(payload.encode()).hexdigest()


def title_tokens(title: str) -> set[str]:
    return {x for x in normalized_title(title).split() if len(x) > 2}


def similar_titles(left: str, right: str, threshold: float = 0.72) -> bool:
    a, b = title_tokens(left), title_tokens(right)
    return bool(a and b) and len(a & b) / len(a | b) >= threshold

