from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import Item

PROFILE_DOCUMENTS = ("target_roles.txt", "preferred_skills.txt", "excluded_terms.txt", "locations.txt")


@dataclass(frozen=True, slots=True)
class RecommendationProfile:
    mode: str
    target_roles: tuple[str, ...]
    preferred_skills: tuple[str, ...]
    excluded_terms: tuple[str, ...]
    locations: tuple[str, ...]

    def score_adjustment(self, item: Item) -> float:
        title = item.title.casefold()
        text = f"{item.title} {item.excerpt} {item.company}".casefold()
        location = item.location.casefold()
        if any(term in title for term in self.excluded_terms):
            return -100.0
        role_matches = sum(term in title for term in self.target_roles)
        skill_matches = sum(term in text for term in self.preferred_skills)
        location_match = any(term in location for term in self.locations)
        return min(4.0, role_matches * 1.5 + skill_matches * 0.25 + (0.4 if location_match else 0))


def _lines(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        raise ValueError(f"missing recommendation profile document: {path}")
    values = tuple(
        line.strip().casefold() for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if not values:
        raise ValueError(f"recommendation profile document is empty: {path}")
    return values


def load_recommendation_profile(root: Path, mode: str) -> RecommendationProfile:
    if mode not in {"cv", "specification"}:
        raise ValueError("job profile must be 'cv' or 'specification'")
    directory = root / mode
    values = {name.removesuffix(".txt"): _lines(directory / name) for name in PROFILE_DOCUMENTS}
    return RecommendationProfile(mode=mode, **values)
