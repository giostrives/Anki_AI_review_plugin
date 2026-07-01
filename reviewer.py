"""
Main reviewer logic - adds the AI review panel beneath the natively
rendered card.

The panel (web/ai_review.js) is mounted as a sibling of Anki's #qa
container, so the card template renders untouched above it and the panel
survives the question->answer transition (_showAnswer only re-renders #qa).
Every string crossing the Python->JS boundary goes through json.dumps and
lands via textContent on the JS side.
"""
import json
import re
import uuid
from datetime import datetime

from anki.utils import strip_html
from aqt import mw, gui_hooks
from aqt.reviewer import Reviewer
from aqt.webview import WebContent

from . import conversations, providers
from .prompts import (conversation_prompt, language_card_prompt,
                      quick_card_prompt, system_prompt)


class AIReviewer:
    def __init__(self):
        self.current_card = None
        self.deck_config = None
        self.user_answer = None
        self.peeked = False            # back revealed before answering => AI review skipped
        self.answer_submitted = False  # an evaluation completed for this card
        self.evaluating = False        # an evaluation is in flight
        # Per-card conversation state.
        self.conv_meta = None       # metadata + the human answer, for persistence
        self.first_feedback = None  # the evaluation reply (think-stripped)
        self.chat_system = None     # tutor system prompt for the follow-up chat
        self.chat_messages = None   # follow-up turns only (no evaluation context)
        config = mw.addonManager.getConfig(__name__)
        # Only commit to a session file when logging is on; off by default so a
        # fresh install writes nothing and creates no folder.
        self.session_path = (
            conversations.new_session_path(config)
            if conversations.is_enabled(config) else None
        )
        gui_hooks.reviewer_did_show_question.append(self.on_show_question)
        gui_hooks.reviewer_did_show_answer.append(self.on_show_answer)
        gui_hooks.webview_will_set_content.append(self.on_webview_will_set_content)
        gui_hooks.webview_did_receive_js_message.append(self.on_js_message)
        # Persist the final conversation when the review ends or the profile closes.
        gui_hooks.reviewer_will_end.append(self.flush_conversation)
        gui_hooks.profile_will_close.append(self.flush_conversation)

    def on_webview_will_set_content(self, web_content: WebContent, context):
        """Attach the panel's CSS/JS to the reviewer page.

        The files are served from web/ via mw.addonManager.setWebExports()
        (see __init__.py). Both stylesheets always load; they are fully
        scoped under #ai-review-root and inert for disabled decks.
        """
        if not isinstance(context, Reviewer):
            return
        pkg = mw.addonManager.addonFromModule(__name__)
        web_content.css.append(f"/_addons/{pkg}/web/ai_review.css")
        web_content.css.append(f"/_addons/{pkg}/web/ai_review_polished.css")
        web_content.js.append(f"/_addons/{pkg}/web/ai_review.js")

    def on_js_message(self, handled, message, context):
        """Bridge for the panel's pycmd() commands."""
        if not message.startswith("aiReview::"):
            return handled
        if message == "aiReview::peek":
            # Just turn the card; on_show_answer records the forfeit.
            mw.reviewer._showAnswer()
            return (True, None)
        if message.startswith("aiReview::submit::"):
            # Ignore racing submits once the back is revealed or a
            # previous evaluation is running/done.
            if self.peeked or self.answer_submitted or self.evaluating:
                return (True, None)
            # Payload is "<mode>::<answer>"
            payload = message[len("aiReview::submit::"):]
            mode, _, answer = payload.partition("::")
            self.user_answer = answer
            self.evaluate_answer(answer, mode)
            return (True, None)
        if message.startswith("aiReview::chat::"):
            self.send_chat(message[len("aiReview::chat::"):])
            return (True, None)
        return handled

    def on_show_question(self, card):
        """Mount the AI panel below the card (or clean it up if disabled)."""
        # Persist the previous card's conversation before starting a new one.
        self.flush_conversation()

        deck_name = mw.col.decks.name(card.did)
        config = mw.addonManager.getConfig(__name__)
        deck_configs = config.get("deck_configs", {})

        self.deck_config = self._match_deck_config(deck_name, deck_configs)
        self.current_card = card
        self.user_answer = None
        self.peeked = False
        self.answer_submitted = False
        self.evaluating = False

        if self.deck_config is None:
            # Remove a panel left over from a previous, AI-enabled card.
            mw.reviewer.web.eval("if (window.AIReview) AIReview.unmount();")
            return

        card_data = self.get_card_data()
        if card_data["shows_word"]:
            instruction = (f"Write a sentence using '{card_data['word']}' "
                           f"in {card_data['target_language']}")
        else:
            # The question shows the meaning; never name the hidden word.
            instruction = (f"Write a sentence using the "
                           f"{card_data['target_language']} word for "
                           f"'{card_data['gloss']}'")
        opts = {
            "instruction": instruction,
            "mode": self.deck_config.get("review_mode", "full"),
            "theme": config.get("theme", "native"),
        }
        mw.reviewer.web.eval(f"AIReview.mount({json.dumps(opts)});")

    def on_show_answer(self, card):
        """Back revealed before answering => forfeit the AI review.

        Fires for every path to the answer (panel button, bottom-bar Show
        Answer, keyboard), so the forfeit logic lives only here. Turning the
        card while an evaluation is streaming is fine — the answer is already
        in, so it's not a peek.
        """
        if (self.deck_config is None or self.answer_submitted
                or self.evaluating or self.peeked):
            return
        self.peeked = True
        mw.reviewer.web.eval("if (window.AIReview) AIReview.forfeit();")

    @staticmethod
    def _match_deck_config(deck_name, deck_configs):
        """Return the enabled deck config governing `deck_name`, or None.

        An exact match wins first (current behavior). Otherwise the nearest
        ancestor deck (by "::" hierarchy) whose config is enabled AND has
        `include_subdecks` set governs this subdeck.
        """
        cfg = deck_configs.get(deck_name)
        if cfg is not None:
            return cfg if cfg.get("enabled", False) else None

        parts = deck_name.split("::")
        for i in range(len(parts) - 1, 0, -1):
            ancestor = "::".join(parts[:i])
            cfg = deck_configs.get(ancestor)
            if cfg and cfg.get("enabled", False) and cfg.get("include_subdecks", False):
                return cfg
        return None

    def _question_field_index(self):
        """Index (0 or 1) of the note field this card shows as its question.

        Reversed card templates display the second field. Read which of the
        first two fields the question format actually references; on any
        doubt (cloze, both fields shown, odd templates) assume the first.
        """
        try:
            qfmt = self.current_card.template().get("qfmt", "")
            flds = self.current_card.note().note_type()["flds"]
        except Exception:
            return 0

        def referenced(name):
            return re.search(r"\{\{[^}]*\b" + re.escape(name) + r"\}\}", qfmt)

        if (len(flds) > 1 and referenced(flds[1]["name"])
                and not referenced(flds[0]["name"])):
            return 1
        return 0

    def get_card_data(self):
        """Extract card data for the review.

        Two independent orientations matter here:
        - Which note field holds the word being learned vs its meaning is
          the deck's convention — the per-deck `front_field` setting
          ("target": the first field is the target-language word, the
          default; "source": the first field is the mother-tongue meaning).
        - Which of the two this particular card SHOWS as its question is
          the card template's choice (reversed cards show the second
          field) — see _question_field_index().
        """
        note = self.current_card.note()
        first = note.fields[0] if len(note.fields) > 0 else ""
        second = note.fields[1] if len(note.fields) > 1 else ""

        if self.deck_config.get("front_field", "target") == "target":
            word_raw, gloss_raw = first, second
            word_index = 0
        else:
            word_raw, gloss_raw = second, first
            word_index = 1

        shown_index = self._question_field_index()

        # First line, minus parenthetical annotations:
        # "Comprometido (adj)" -> "Comprometido"
        def head(text):
            return strip_html(text).split('\n')[0].split('(')[0].strip()

        # Plain-text of the card's hidden side (meaning, examples, notes) for
        # the LLM. strip_html (NORMAL mode) removes HTML tags, <img>, and
        # [sound:…] refs entirely, so no images/audio are sent — only text.
        hidden = second if shown_index == 0 else first
        back_text = re.sub(r'\s+\n', '\n', strip_html(hidden)).strip()

        return {
            "word": head(word_raw),    # target-language word being learned
            "gloss": head(gloss_raw),  # its source-language meaning
            "shows_word": shown_index == word_index,
            "back_text": back_text,
            "source_language": self.deck_config.get("source_language", "English"),
            "target_language": self.deck_config.get("target_language", "Spanish")
        }

    def evaluate_answer(self, answer, mode="full"):
        """Evaluate user's answer with the configured AI provider"""
        card_data = self.get_card_data()
        config = mw.addonManager.getConfig(__name__)

        template = quick_card_prompt if mode == "quick" else language_card_prompt
        prompt = template.render(
            user_proficiency=self.deck_config.get("user_level", "Beginner").lower(),
            source_language=card_data['source_language'],
            target_language=card_data['target_language'],
            source_word=card_data['gloss'],
            target_word=card_data['word'],
            card_back=card_data['back_text'],
            sentence=answer
        )
        system = system_prompt.render()
        messages = [{"role": "user", "content": prompt}]

        web = mw.reviewer.web
        self.evaluating = True

        # Switch the panel to the waiting state so streamed text has a home.
        web.eval(f"AIReview.setEvaluating({json.dumps(answer)});")

        def task():
            buf = []

            def on_chunk(delta):
                buf.append(delta)
                # Live progress view: drop <think>/tags so raw markup never flashes.
                cleaned = self._plainify(self._strip_think("".join(buf)))
                mw.taskman.run_on_main(
                    lambda c=cleaned: web.eval(f"AIReview.streamFeedback({json.dumps(c)});")
                )

            return providers.stream_llm(config, system, messages, on_chunk)

        def on_done(future):
            self.evaluating = False
            try:
                ai_feedback = future.result()
            except Exception as e:
                conversations.append_error(config, f"Evaluation failed: {e}")
                # Return to the edit/send state with the error shown on top so
                # the user can tweak the answer and resubmit (or turn the card).
                self.user_answer = None
                web.eval(f"AIReview.evalError({json.dumps(str(e))});")
                return

            # Never surface the model's chain-of-thought.
            ai_feedback = self._strip_think(ai_feedback)

            # Set up the follow-up chat with its OWN tutor framing, decoupled from
            # the evaluation prompt — so follow-ups are a normal conversation (no
            # scores, XML, or <think>), not another review. Only the follow-up
            # turns are sent; the card context lives in chat_system.
            self.conv_meta = self._build_conv_meta(config, card_data, answer, mode)
            self.first_feedback = ai_feedback
            self.chat_system = conversation_prompt.render(
                user_proficiency=self.deck_config.get("user_level", "Beginner").lower(),
                source_language=card_data['source_language'],
                target_language=card_data['target_language'],
                source_word=card_data['gloss'],
                target_word=card_data['word'],
                card_back=card_data['back_text'],
                sentence=answer,
                feedback=self._plainify(ai_feedback),
            )
            self.chat_messages = []

            # Swap the live streamed text for the structured verdict view.
            if mode == "quick":
                data = self._parse_quick(ai_feedback)
            else:
                data = self._parse_full(ai_feedback)
            web.eval(f"AIReview.showFeedback({json.dumps(data)});")

            # Turn the card so Anki's native grading buttons appear; the
            # panel (a #qa sibling) keeps the feedback + chat visible.
            self.answer_submitted = True
            mw.reviewer._showAnswer()

        mw.taskman.run_in_background(task, on_done)

    def send_chat(self, text):
        """Handle a follow-up chat turn under the tutor (conversation) framing.

        Runs off the main thread and streams the reply into the chat bubble.
        """
        if self.conv_meta is None or self.chat_system is None:
            return
        config = mw.addonManager.getConfig(__name__)
        web = mw.reviewer.web

        self.chat_messages.append({"role": "user", "content": text})
        web.eval("AIReview.chatStart();")

        def task():
            buf = []

            def on_chunk(delta):
                buf.append(delta)
                cleaned = self._strip_think("".join(buf))
                mw.taskman.run_on_main(
                    lambda c=cleaned: web.eval(f"AIReview.chatStream({json.dumps(c)});")
                )

            return providers.stream_llm(config, self.chat_system, self.chat_messages, on_chunk)

        def on_done(future):
            try:
                reply = future.result()
            except Exception as e:
                conversations.append_error(config, f"Chat failed: {e}")
                # Drop the failed turn so a retry doesn't duplicate it.
                self.chat_messages.pop()
                web.eval(f"AIReview.chatError({json.dumps(str(e))});")
                return

            reply = self._strip_think(reply)
            self.chat_messages.append({"role": "assistant", "content": reply})
            web.eval(f"AIReview.chatEnd({json.dumps(reply)});")

        mw.taskman.run_in_background(task, on_done)

    def _build_conv_meta(self, config, card_data, answer, mode):
        provider = config.get("provider", "ollama")
        if provider == "gemini":
            model = config.get("gemini", {}).get("model", "")
        else:
            model = config.get("ollama", {}).get("model") or config.get("model", "gemma3")
        return {
            "id": uuid.uuid4().hex,
            "created": datetime.now().isoformat(timespec="seconds"),
            "deck": mw.col.decks.name(self.current_card.did),
            "provider": provider,
            "model": model,
            "review_mode": mode,
            "source_language": card_data["source_language"],
            "target_language": card_data["target_language"],
            "word": card_data["word"],
            "answer": answer,
        }

    def flush_conversation(self, *args):
        """Write the current card's conversation as one JSONL line, then reset."""
        if self.conv_meta is None:
            self._reset_conversation()
            return

        # Stored history: the human answer, the evaluation, then the follow-ups.
        messages = [
            {"role": "user", "content": self.conv_meta.get("answer", "")},
            {"role": "assistant", "content": self.first_feedback or ""},
        ]
        messages.extend(self.chat_messages or [])

        record = {k: v for k, v in self.conv_meta.items() if k != "answer"}
        record["messages"] = messages

        config = mw.addonManager.getConfig(__name__)
        if conversations.is_enabled(config):
            if not self.session_path:
                self.session_path = conversations.new_session_path(config)
            conversations.append_conversation(self.session_path, record)
        self._reset_conversation()

    def _reset_conversation(self):
        self.conv_meta = None
        self.first_feedback = None
        self.chat_system = None
        self.chat_messages = None

    @staticmethod
    def _strip_think(text):
        """Remove <think>…</think> (and <thinking>…) chain-of-thought blocks."""
        return re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', text,
                      flags=re.DOTALL | re.IGNORECASE).strip()

    @staticmethod
    def _plainify(text):
        """Drop XML-ish tags (keeping inner text) for use as plain-text context."""
        text = re.sub(r'</?[a-zA-Z_][\w-]*>', ' ', text)
        return re.sub(r'\n{3,}', '\n\n', text).strip()

    @staticmethod
    def _extract_tag(tag_name, content):
        """Extract content from XML tags; None if missing or 'ignore_field'."""
        pattern = f'<{tag_name}>(.*?)</{tag_name}>'
        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
        if match:
            extracted = match.group(1).strip()
            if 'ignore_field' in extracted.lower():
                return None
            return extracted
        return None

    def _parse_quick(self, feedback):
        """Parse the quick-review <verdict>/<feedback> tags into feedback data."""
        verdict_raw = (self._extract_tag('verdict', feedback) or "").lower()
        message = self._extract_tag('feedback', feedback) or feedback.strip()

        if "incorrect" in verdict_raw:
            verdict = "incorrect"
        elif "correct" in verdict_raw:
            verdict = "correct"
        else:
            verdict = None

        return {"verdict": verdict, "score": None, "summary": message, "sections": []}

    def _parse_full(self, feedback):
        """Parse the full-review XML tags into feedback data."""
        score = self._extract_tag('score', feedback)
        praise = self._extract_tag('praise', feedback)
        grammar = self._extract_tag('grammar', feedback)
        fluency = self._extract_tag('fluency', feedback)
        paraphrase = self._extract_tag('paraphrase', feedback)

        if not any([score, praise, grammar, fluency, paraphrase]):
            # Model ignored the format: show the raw reply as-is.
            return {"verdict": None, "score": None,
                    "summary": feedback.strip(), "sections": []}

        # <praise> is positive feedback, or the meaning verdict for a
        # translation answer (a leading ✗ marks it wrong).
        if praise and praise.lstrip().startswith('✗'):
            verdict = "incorrect"
        elif praise and not grammar:
            verdict = "correct"
        else:
            verdict = "partial"

        # The verdict row already carries the ✓/✗, so drop a leading mark.
        summary = praise.lstrip('✓✗ ').strip() if praise else None

        sections = []
        if grammar:
            sections.append({"label": "Grammar", "text": grammar})
        if fluency:
            sections.append({"label": "Fluency", "text": fluency})
        if paraphrase:
            sections.append({"label": "Example", "text": paraphrase})

        return {"verdict": verdict, "score": score,
                "summary": summary, "sections": sections}
