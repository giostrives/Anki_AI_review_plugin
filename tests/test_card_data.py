"""Card direction detection and field-layout mapping (get_card_data).

The "real reversed card" cases mirror the note type that triggered the
answer-leak bug: a 10-field note whose "Card 2" template shows {{Back}}
(the meaning) while field 0 holds the word being learned.
"""
import pytest

ASW_NAMES = ["Front", "Back", "Reverse", "Image", "Imgsrc",
             "Information", "FPM", "Root", "Hidden", "Examples"]
ASW_FIELDS = ["Comprometido (adj) ", "Committed, engaged, involved",
              "", "<img src='x.jpg'>", "", "", "", "", "", ""]
QFMT_FORWARD = "{{Front}}\n<br><br>\n<div><small>{{Subdeck}}</small></div>"
QFMT_REVERSED = ("{{Back}}\n<br><br>\n<div><small>{{Subdeck}}</small></div>\n"
                 "{{#Reverse}}<span>{{Reverse}}</span>{{/Reverse}}\n{{Image}}")

CFG_TARGET_FIRST = {"front_field": "target",
                    "source_language": "English", "target_language": "Spanish"}
CFG_SOURCE_FIRST = {"front_field": "source",
                    "source_language": "English", "target_language": "Spanish"}


class TestWordFirstDeck:
    """Deck convention: first field = the word being learned (the default)."""

    def test_forward_card_shows_the_word(self, make_reviewer):
        d = make_reviewer(ASW_FIELDS, ASW_NAMES, QFMT_FORWARD, CFG_TARGET_FIRST).get_card_data()
        assert d["shows_word"] is True
        assert d["word"] == "Comprometido"          # "(adj)" annotation stripped
        assert d["gloss"] == "Committed, engaged, involved"
        assert d["back_text"] == "Committed, engaged, involved"

    def test_reversed_card_shows_the_gloss(self, make_reviewer):
        d = make_reviewer(ASW_FIELDS, ASW_NAMES, QFMT_REVERSED, CFG_TARGET_FIRST).get_card_data()
        assert d["shows_word"] is False
        # word/gloss mapping is unchanged by direction
        assert d["word"] == "Comprometido"
        assert d["gloss"] == "Committed, engaged, involved"

    def test_reversed_card_instruction_must_not_leak_answer(self, make_reviewer):
        """The original bug: the instruction printed the hidden word."""
        d = make_reviewer(ASW_FIELDS, ASW_NAMES, QFMT_REVERSED, CFG_TARGET_FIRST).get_card_data()
        assert not d["shows_word"]
        # what on_show_question builds for shows_word == False:
        instruction = (f"Write a sentence using the {d['target_language']} "
                       f"word for '{d['gloss']}'")
        assert "Comprometido" not in instruction


class TestMeaningFirstDeck:
    """Deck convention: first field = the source-language meaning."""

    def test_forward_card_shows_the_gloss(self, make_reviewer):
        d = make_reviewer(["dog", "el perro"], ["Front", "Back"],
                          "{{Front}}", CFG_SOURCE_FIRST).get_card_data()
        assert d["word"] == "el perro"
        assert d["gloss"] == "dog"
        assert d["shows_word"] is False

    def test_reversed_card_shows_the_word(self, make_reviewer):
        d = make_reviewer(["dog", "el perro"], ["Front", "Back"],
                          "{{Back}}", CFG_SOURCE_FIRST).get_card_data()
        assert d["shows_word"] is True

    def test_missing_front_field_setting_defaults_to_target(self, make_reviewer):
        cfg = {"source_language": "English", "target_language": "Spanish"}
        d = make_reviewer(["hola", "hello"], ["Front", "Back"], "{{Front}}", cfg).get_card_data()
        assert d["word"] == "hola"


class TestEdgeCases:
    def test_unrecognized_template_falls_back_to_forward(self, make_reviewer):
        d = make_reviewer(["hola", "hello"], ["Front", "Back"],
                          "{{cloze:Text}}", CFG_TARGET_FIRST).get_card_data()
        assert d["shows_word"] is True

    def test_template_showing_both_fields_counts_as_forward(self, make_reviewer):
        d = make_reviewer(["hola", "hello"], ["Front", "Back"],
                          "{{Front}} — {{Back}}", CFG_TARGET_FIRST).get_card_data()
        assert d["shows_word"] is True

    def test_single_field_note_does_not_crash(self, make_reviewer):
        d = make_reviewer(["hola"], ["Front"], "{{Front}}", CFG_TARGET_FIRST).get_card_data()
        assert d["word"] == "hola"
        assert d["gloss"] == ""
        assert d["shows_word"] is True

    def test_word_cleaning_strips_html_and_keeps_first_line(self, make_reviewer):
        d = make_reviewer(["<b>Grueso</b> (adj)\nextra note", "thick"],
                          ["Front", "Back"], "{{Front}}", CFG_TARGET_FIRST).get_card_data()
        assert d["word"] == "Grueso"

    def test_back_text_is_the_hidden_side(self, make_reviewer):
        # Reversed card: the hidden side is field 0.
        d = make_reviewer(["Comprometido (adj)", "Committed"], ["Front", "Back"],
                          "{{Back}}", CFG_TARGET_FIRST).get_card_data()
        assert d["back_text"] == "Comprometido (adj)"
