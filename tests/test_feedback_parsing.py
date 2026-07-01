"""The XML-ish LLM feedback -> structured verdict data used by the panel."""
import pytest


@pytest.fixture
def r(reviewer_mod):
    return reviewer_mod.AIReviewer.__new__(reviewer_mod.AIReviewer)


class TestParseFull:
    def test_flawless_sentence_is_correct(self, r):
        feedback = """<SCORE>⭐⭐⭐⭐⭐</SCORE>
        <praise>✓ Good: the sentence is correct and fluent!</praise>
        <grammar>ignore_field</grammar><fluency>ignore_field</fluency>
        <paraphrase>El sol brilla mucho hoy.</paraphrase>"""
        d = r._parse_full(feedback)
        assert d["verdict"] == "correct"
        assert d["score"] == "⭐⭐⭐⭐⭐"
        # The verdict row carries the check mark; the summary must not repeat it.
        assert d["summary"] == "Good: the sentence is correct and fluent!"
        assert [s["label"] for s in d["sections"]] == ["Example"]

    def test_wrong_meaning_is_incorrect(self, r):
        feedback = ("<SCORE>⭐</SCORE><praise>✗ That means something else.</praise>"
                    "<grammar>Use 'ser' here.</grammar>")
        d = r._parse_full(feedback)
        assert d["verdict"] == "incorrect"
        assert d["summary"] == "That means something else."

    def test_issues_without_praise_is_partial(self, r):
        feedback = ("<SCORE>⭐⭐⭐</SCORE><praise>ignore_field</praise>"
                    "<grammar>1. \"stabilidad\" should be \"estabilidad.\"</grammar>"
                    "<fluency>Slightly unnatural.</fluency>"
                    "<paraphrase>Estamos comprometidos con el proyecto.</paraphrase>")
        d = r._parse_full(feedback)
        assert d["verdict"] == "partial"
        assert d["summary"] is None
        assert [s["label"] for s in d["sections"]] == ["Grammar", "Fluency", "Example"]

    def test_untagged_reply_falls_back_to_raw_text(self, r):
        raw = "The model just rambled with no tags."
        d = r._parse_full(raw)
        assert d == {"verdict": None, "score": None, "summary": raw, "sections": []}

    def test_sections_preserve_multiline_text(self, r):
        feedback = "<grammar>line one\nline two</grammar>"
        d = r._parse_full(feedback)
        assert d["sections"][0]["text"] == "line one\nline two"


class TestParseQuick:
    def test_incorrect_verdict(self, r):
        d = r._parse_quick("<verdict>Incorrect</verdict><feedback>Wrong word.</feedback>")
        assert d["verdict"] == "incorrect"
        assert d["summary"] == "Wrong word."

    def test_correct_verdict(self, r):
        d = r._parse_quick("<verdict>correct</verdict><feedback>Nice.</feedback>")
        assert d["verdict"] == "correct"

    def test_missing_tags_fall_back_to_raw_text(self, r):
        d = r._parse_quick("Great job!")
        assert d["verdict"] is None
        assert d["summary"] == "Great job!"


class TestHelpers:
    def test_extract_tag_ignore_field_is_dropped(self, reviewer_mod):
        assert reviewer_mod.AIReviewer._extract_tag(
            "grammar", "<grammar>ignore_field</grammar>") is None

    def test_extract_tag_case_insensitive(self, reviewer_mod):
        assert reviewer_mod.AIReviewer._extract_tag(
            "score", "<SCORE>⭐⭐</SCORE>") == "⭐⭐"

    def test_strip_think_removes_reasoning(self, reviewer_mod):
        text = "<think>step 1... step 2...</think>Actual feedback"
        assert reviewer_mod.AIReviewer._strip_think(text) == "Actual feedback"
        text = "<thinking>hmm</thinking>ok"
        assert reviewer_mod.AIReviewer._strip_think(text) == "ok"

    def test_plainify_drops_tags_keeps_text(self, reviewer_mod):
        text = "<praise>Good</praise>\n\n\n\n<grammar>none</grammar>"
        out = reviewer_mod.AIReviewer._plainify(text)
        assert "<" not in out
        assert "Good" in out and "none" in out
