from datetime import datetime, timezone

import pytest

from morning_digest.models import Item
from morning_digest.recommendations import load_recommendation_profile


def write_profile(root, mode="cv"):
    directory = root / mode
    directory.mkdir()
    (directory / "target_roles.txt").write_text("machine learning engineer\n")
    (directory / "preferred_skills.txt").write_text("python\nneo4j\n")
    (directory / "excluded_terms.txt").write_text("senior\n")
    (directory / "locations.txt").write_text("zurich\n")


def job(title, excerpt="", location="Zurich"):
    return Item("test", "fixture", title, "https://example.com/job",
                datetime.now(timezone.utc), excerpt=excerpt, location=location, category="jobs")


def test_profile_rewards_role_skills_and_location(tmp_path):
    write_profile(tmp_path)
    profile = load_recommendation_profile(tmp_path, "cv")
    assert profile.score_adjustment(job("Machine Learning Engineer", "Python and Neo4j")) > 2


def test_exclusions_apply_to_title_not_incidental_description(tmp_path):
    write_profile(tmp_path)
    profile = load_recommendation_profile(tmp_path, "cv")
    assert profile.score_adjustment(job("Senior Machine Learning Engineer")) == -100
    assert profile.score_adjustment(job("Machine Learning Engineer", "Reports to senior manager")) > 0


def test_specification_mode_requires_its_documents(tmp_path):
    (tmp_path / "specification").mkdir()
    with pytest.raises(ValueError, match="target_roles.txt"):
        load_recommendation_profile(tmp_path, "specification")


def test_rejects_unknown_mode(tmp_path):
    with pytest.raises(ValueError, match="cv.*specification"):
        load_recommendation_profile(tmp_path, "guess")
