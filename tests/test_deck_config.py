"""Which deck config (if any) governs a card's deck (_match_deck_config)."""
import pytest


@pytest.fixture
def match(reviewer_mod):
    return reviewer_mod.AIReviewer._match_deck_config


def test_exact_match_enabled(match):
    cfgs = {"Spanish": {"enabled": True}}
    assert match("Spanish", cfgs) == cfgs["Spanish"]


def test_exact_match_disabled_returns_none(match):
    assert match("Spanish", {"Spanish": {"enabled": False}}) is None


def test_unknown_deck_returns_none(match):
    assert match("French", {"Spanish": {"enabled": True}}) is None


def test_subdeck_inherits_when_include_subdecks(match):
    cfgs = {"Spanish": {"enabled": True, "include_subdecks": True}}
    assert match("Spanish::Verbs", cfgs) == cfgs["Spanish"]
    assert match("Spanish::Verbs::Irregular", cfgs) == cfgs["Spanish"]


def test_subdeck_not_inherited_without_include_subdecks(match):
    cfgs = {"Spanish": {"enabled": True, "include_subdecks": False}}
    assert match("Spanish::Verbs", cfgs) is None


def test_exact_disabled_overrides_enabled_ancestor(match):
    """An explicit (disabled) config on the subdeck wins over the parent."""
    cfgs = {
        "Spanish": {"enabled": True, "include_subdecks": True},
        "Spanish::Verbs": {"enabled": False},
    }
    assert match("Spanish::Verbs", cfgs) is None


def test_nearest_enabled_ancestor_wins(match):
    cfgs = {
        "Spanish": {"enabled": True, "include_subdecks": True, "user_level": "Beginner"},
        "Spanish::Verbs": {"enabled": True, "include_subdecks": True, "user_level": "Advanced"},
    }
    assert match("Spanish::Verbs::Irregular", cfgs)["user_level"] == "Advanced"


def test_disabled_ancestor_does_not_govern(match):
    cfgs = {"Spanish": {"enabled": False, "include_subdecks": True}}
    assert match("Spanish::Verbs", cfgs) is None
