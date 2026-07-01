"""
Shared test setup.

The repo root IS the add-on package, but its __init__.py touches
mw.addonManager at import time, which needs a running Anki. So the tests
import the submodules under a synthetic package instead: an empty parent
module whose __path__ points at the repo root, which lets reviewer.py's
relative imports (`from . import conversations, providers`, `from .prompts
import ...`) resolve normally without ever executing __init__.py.
"""
import importlib
import sys
import types
from pathlib import Path

import pytest

ADDON_DIR = Path(__file__).resolve().parent.parent
PKG = "anki_ai_review_addon"

# strip_html (used by reviewer.get_card_data) needs anki's i18n backend,
# which Anki initializes at startup; do the same here.
import anki.lang  # noqa: E402

anki.lang.set_lang("en_US")

if PKG not in sys.modules:
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(ADDON_DIR)]
    sys.modules[PKG] = pkg


def load(name):
    """Import an add-on submodule (e.g. "reviewer") under the synthetic package."""
    return importlib.import_module(f"{PKG}.{name}")


@pytest.fixture(scope="session")
def reviewer_mod():
    return load("reviewer")


@pytest.fixture(scope="session")
def conversations_mod():
    return load("conversations")


@pytest.fixture(scope="session")
def providers_mod():
    return load("providers")


class FakeNote:
    def __init__(self, fields, names):
        self.fields = fields
        self._names = names

    def note_type(self):
        return {"flds": [{"name": n, "ord": i} for i, n in enumerate(self._names)]}


class FakeCard:
    def __init__(self, note, qfmt):
        self._note = note
        self._qfmt = qfmt

    def note(self):
        return self._note

    def template(self):
        return {"qfmt": self._qfmt}


@pytest.fixture
def make_reviewer(reviewer_mod):
    """Build an AIReviewer with a fake current card, skipping __init__
    (which registers gui_hooks and needs a running Anki)."""

    def factory(fields, names, qfmt, deck_config):
        r = reviewer_mod.AIReviewer.__new__(reviewer_mod.AIReviewer)
        r.current_card = FakeCard(FakeNote(fields, names), qfmt)
        r.deck_config = deck_config
        return r

    return factory
