"""
Main reviewer logic - intercepts card reviews and shows AI interface
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
        self.peeked = False  # back revealed before answering => AI review skipped
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
        gui_hooks.webview_will_set_content.append(self.on_webview_will_set_content)
        # Persist the final conversation when the review ends or the profile closes.
        gui_hooks.reviewer_will_end.append(self.flush_conversation)
        gui_hooks.profile_will_close.append(self.flush_conversation)

    def on_webview_will_set_content(self, web_content: WebContent, context):
        """Add our custom command handlers"""
        if not isinstance(context, Reviewer):
            return

        # Add bridge command handler
        def handle_ai_command(handled, message, context):
            if message == "aiReview::peek":
                # Revealing the back before answering forfeits the AI review for
                # this card; just show the answer so it can be graded normally.
                self.peeked = True
                mw.reviewer._showAnswer()
                return (True, None)
            if message.startswith("aiReview::submit::"):
                # Ignore a (racing) submit once the back has been revealed.
                if self.peeked:
                    return (True, None)
                # Payload is "<mode>::<answer>"
                payload = message.replace("aiReview::submit::", "")
                mode, _, answer = payload.partition("::")
                self.user_answer = answer
                # Change button immediately
                mw.reviewer.web.eval("""
                    const btn = document.getElementById('submitBtn');
                    if (btn) {
                        btn.disabled = true;
                        btn.textContent = 'Evaluating...';
                    }
                """)
                self.evaluate_answer(answer, mode)
                return (True, None)
            if message.startswith("aiReview::chat::"):
                text = message.replace("aiReview::chat::", "")
                self.send_chat(text)
                return (True, None)
            return handled

        gui_hooks.webview_did_receive_js_message.append(handle_ai_command)

    def on_show_question(self, card):
        """Intercept card review"""
        # Persist the previous card's conversation before starting a new one.
        self.flush_conversation()

        # Get deck name
        deck_id = card.did
        deck_name = mw.col.decks.name(deck_id)

        # Check if AI review is enabled for this deck (or an ancestor opted in).
        config = mw.addonManager.getConfig(__name__)
        deck_configs = config.get("deck_configs", {})

        deck_config = self._match_deck_config(deck_name, deck_configs)
        if deck_config is None:
            return  # Use normal review

        self.deck_config = deck_config

        # Store current card and reset user answer
        self.current_card = card
        self.user_answer = None
        self.peeked = False

        # Show AI interface
        self.show_ai_interface()

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

    def show_ai_interface(self):
        """Show AI review interface"""
        card_data = self.get_card_data()

        word = card_data['front']
        instruction = f"Write a sentence using '{word}' in {card_data['target_language']}"

        # The fully rendered back (images, examples, notes) for the toggle view.
        back_html = self.current_card.answer()

        # Default review mode for this deck; the user can flip it before submitting.
        default_mode = self.deck_config.get("review_mode", "full")
        quick_active = " active" if default_mode == "quick" else ""
        full_active = " active" if default_mode != "quick" else ""

        html = f"""
        <style>
            .air-page {{
                min-height: 100vh; display: flex; align-items: center; justify-content: center;
                background: #eaf3fb; padding: 20px; box-sizing: border-box;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                color: #2b3a47;
            }}
            .air-card {{
                background: #ffffff; border: 1px solid #d4e4f2; border-radius: 12px;
                padding: 36px; max-width: 680px; width: 100%;
                box-shadow: 0 4px 16px rgba(59,130,196,0.10);
            }}
            .air-word {{ font-size: 40px; font-weight: 600; color: #2c5f8a; text-align: center; }}
            .air-instruction {{ font-size: 16px; color: #5a6b78; text-align: center; margin-top: 8px; margin-bottom: 24px; }}
            .air-toggle {{ display: flex; gap: 8px; justify-content: center; margin-bottom: 20px; }}
            .air-pill {{
                padding: 7px 18px; border: 1px solid #cfe0ef; border-radius: 999px;
                background: #f4f9fd; color: #5a6b78; font-size: 14px; cursor: pointer; user-select: none;
            }}
            .air-pill.active {{ background: #3b82c4; color: #ffffff; border-color: #3b82c4; }}
            .air-textarea {{
                width: 100%; min-height: 120px; padding: 14px; border: 1px solid #cfe0ef;
                border-radius: 10px; font-size: 16px; font-family: inherit; resize: vertical;
                box-sizing: border-box;
                background: #eaf3fb !important; color: #2b3a47 !important;
            }}
            .air-textarea:focus {{ outline: none; border-color: #3b82c4; }}
            .air-btn {{
                width: 100%; padding: 14px; border: none; border-radius: 10px; margin-top: 16px;
                font-size: 16px; font-weight: 600; cursor: pointer; background: #3b82c4; color: #ffffff;
            }}
            .air-btn:disabled {{ opacity: 0.6; cursor: default; }}
            .air-answer {{ background: #f4f9fd; border: 1px solid #e1edf7; border-radius: 10px; padding: 14px; margin-bottom: 18px; }}
            .air-answer-label {{ font-size: 13px; font-weight: 600; color: #3b82c4; margin-bottom: 4px; }}
            .air-feedback {{ background: #f4f9fd; border: 1px solid #e1edf7; border-radius: 10px; padding: 18px; }}
            .air-feedback-title {{ font-size: 18px; font-weight: 600; margin-bottom: 10px; color: #2c5f8a; }}
            .air-feedback-text {{ font-size: 15px; line-height: 1.6; color: #3f5160; }}
            .air-verdict {{ font-size: 18px; font-weight: 600; margin-bottom: 8px; }}
            .air-verdict.correct {{ color: #2f9e6f; }}
            .air-verdict.incorrect {{ color: #d06b5c; }}
            .air-chat {{ margin-top: 18px; }}
            .air-bubble {{
                padding: 10px 14px; border-radius: 12px; margin-bottom: 10px; max-width: 85%;
                font-size: 15px; line-height: 1.5; white-space: pre-wrap; word-wrap: break-word;
            }}
            .air-bubble-user {{ background: #3b82c4 !important; color: #ffffff !important; margin-left: auto; }}
            .air-bubble-ai {{ background: #f4f9fd !important; color: #3f5160 !important; border: 1px solid #e1edf7; margin-right: auto; }}
            .air-bubble-error {{ background: #fdecea !important; color: #b3382c !important; border: 1px solid #f5c6c0; margin-right: auto; }}
            .air-error {{
                background: #fdecea; color: #b3382c; border: 1px solid #f5c6c0;
                border-radius: 12px; padding: 12px 14px; margin-bottom: 14px;
                font-size: 14px; line-height: 1.5; white-space: pre-wrap; word-wrap: break-word;
            }}
            .air-chat-row {{ display: flex; gap: 8px; margin-top: 12px; }}
            .air-chat-input {{
                flex: 1; min-height: 44px; padding: 10px; border: 1px solid #cfe0ef; border-radius: 10px;
                font-size: 15px; font-family: inherit; resize: vertical; box-sizing: border-box;
                background: #eaf3fb !important; color: #2b3a47 !important;
            }}
            .air-chat-input:focus {{ outline: none; border-color: #3b82c4; }}
            .air-chat-send {{
                padding: 10px 18px; border: none; border-radius: 10px; font-size: 15px; font-weight: 600;
                cursor: pointer; background: #3b82c4; color: #ffffff;
            }}
            .air-chat-send:disabled {{ opacity: 0.6; cursor: default; }}
            .air-view-toggle {{ display: flex; justify-content: flex-end; margin-bottom: 14px; }}
            .air-view-btn {{
                padding: 6px 14px; border: 1px solid #cfe0ef; border-radius: 999px;
                background: #f4f9fd; color: #3b82c4; font-size: 13px; font-weight: 600;
                cursor: pointer; user-select: none;
            }}
            .air-backview {{ font-size: 16px; line-height: 1.6; color: #2b3a47; }}
            .air-backview img {{ max-width: 100%; height: auto; }}
        </style>
        <div class="air-page">
            <div class="air-card">
                <div class="air-view-toggle">
                    <div id="viewToggleBtn" class="air-view-btn" onclick="window.toggleBackView()">Show card back</div>
                </div>

                <div id="aiBody">
                <div class="air-word">{word}</div>
                <div class="air-instruction">{instruction}</div>

                <div id="aiError" class="air-error" style="display: none;"></div>

                <div id="modeToggle" class="air-toggle">
                    <div class="air-pill{quick_active}" data-mode="quick" onclick="window.setAIMode('quick')">Quick review</div>
                    <div class="air-pill{full_active}" data-mode="full" onclick="window.setAIMode('full')">Full review</div>
                </div>

                <div id="inputContainer">
                    <textarea id="aiAnswer" class="air-textarea" placeholder="Type your answer here..."></textarea>
                </div>

                <div id="submitContainer">
                    <button onclick="window.submitAIAnswer()" id="submitBtn" class="air-btn">Submit</button>
                </div>

                <div id="peekNotice" class="air-instruction" style="display: none; color: #d06b5c; margin-top: 16px;">
                    You looked at the back, so there's no AI review this time — grade the card as usual and you'll get another shot when it comes back.
                </div>

                <div id="yourAnswerDisplay" class="air-answer" style="display: none;">
                    <div class="air-answer-label">Your answer</div>
                    <div id="yourAnswerText" style="font-size: 15px; color: #2b3a47;"></div>
                </div>

                <div id="feedback" class="air-feedback" style="display: none;">
                    <div class="air-feedback-title">AI Feedback</div>
                    <div id="feedbackText" class="air-feedback-text"></div>
                </div>

                <div id="chatBlock" class="air-chat" style="display: none;">
                    <div id="chatThread"></div>
                    <div class="air-chat-row">
                        <textarea id="chatInput" class="air-chat-input" placeholder="Ask a follow-up..."></textarea>
                        <button onclick="window.sendChat()" id="chatSendBtn" class="air-chat-send">Send</button>
                    </div>
                </div>
                </div>

                <div id="backView" class="air-backview" style="display: none;"></div>
            </div>
        </div>
        """

        # Show in reviewer
        mw.reviewer.web.eval(f"document.body.innerHTML = `{html}`;")

        # Inject the rendered back, then a toggle between the AI panel and the back.
        mw.reviewer.web.eval(
            f"document.getElementById('backView').innerHTML = {json.dumps(back_html)};"
        )
        mw.reviewer.web.eval("""
        window.showingBack = false;
        window.aiSubmitted = false;
        window.aiPeeked = false;
        window.toggleBackView = function() {
            window.showingBack = !window.showingBack;
            // Revealing the back BEFORE answering forfeits the AI review for
            // this card — you only get to use the LLM if you try first.
            if (window.showingBack && !window.aiSubmitted && !window.aiPeeked) {
                window.aiPeeked = true;
                document.getElementById('inputContainer').style.display = 'none';
                document.getElementById('submitContainer').style.display = 'none';
                document.getElementById('modeToggle').style.display = 'none';
                document.getElementById('peekNotice').style.display = 'block';
                pycmd('aiReview::peek');
            }
            document.getElementById('aiBody').style.display = window.showingBack ? 'none' : 'block';
            document.getElementById('backView').style.display = window.showingBack ? 'block' : 'none';
            document.getElementById('viewToggleBtn').textContent =
                window.showingBack ? 'Show AI review' : 'Show card back';
        };
        """)

        # Inject the JavaScript: mode selection + submit
        mw.reviewer.web.eval(f"""
        window.aiSelectedMode = '{default_mode}';
        window.setAIMode = function(mode) {{
            window.aiSelectedMode = mode;
            document.querySelectorAll('#modeToggle .air-pill').forEach(function(p) {{
                p.classList.toggle('active', p.dataset.mode === mode);
            }});
        }};
        window.submitAIAnswer = function() {{
            const answer = document.getElementById('aiAnswer').value.trim();
            if (answer.length < 2) {{
                alert('Please enter a longer answer (at least 2 characters)');
                return;
            }}
            // Clear any prior failure banner before the new attempt.
            const err = document.getElementById('aiError');
            if (err) {{ err.style.display = 'none'; err.textContent = ''; }}
            window.aiSubmitted = true;
            // Send mode + answer to Python (button updated from Python side)
            pycmd('aiReview::submit::' + window.aiSelectedMode + '::' + answer);
        }};

        // LLM evaluation failed: drop back to the edit/send screen with the prior
        // answer still in the textarea and the error shown on top.
        window.aiEvalError = function(msg) {{
            document.getElementById('feedback').style.display = 'none';
            document.getElementById('yourAnswerDisplay').style.display = 'none';
            document.getElementById('inputContainer').style.display = 'block';
            document.getElementById('submitContainer').style.display = 'block';
            document.getElementById('modeToggle').style.display = 'flex';
            const btn = document.getElementById('submitBtn');
            if (btn) {{ btn.disabled = false; btn.textContent = 'Submit'; }}
            const err = document.getElementById('aiError');
            if (err) {{
                err.textContent = 'The AI review failed: ' + msg
                    + '\\n\\nEdit your answer and submit again, or click "Show card back" to grade the card normally.';
                err.style.display = 'block';
                err.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
            }}
        }};

        // --- Follow-up chat ---
        window.appendBubble = function(role, text) {{
            const cls = role === 'user' ? 'air-bubble-user'
                      : role === 'error' ? 'air-bubble-error' : 'air-bubble-ai';
            const div = document.createElement('div');
            div.className = 'air-bubble ' + cls;
            div.textContent = text;              // textContent => safe, preserves newlines
            document.getElementById('chatThread').appendChild(div);
            div.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
        }};
        window.endChatWait = function() {{
            const b = document.getElementById('chatSendBtn');
            b.disabled = false; b.textContent = 'Send';
            document.getElementById('chatInput').disabled = false;
        }};
        window.chatReply = function(text) {{ window.appendBubble('ai', text); window.endChatWait(); }};
        window.chatError = function(text) {{
            if (window._aiStreamBubble) {{ window._aiStreamBubble.remove(); window._aiStreamBubble = null; }}
            window.appendBubble('error', text); window.endChatWait();
        }};
        // Streaming AI bubble: created empty, filled delta-by-delta.
        window.startAIStream = function() {{
            const div = document.createElement('div');
            div.className = 'air-bubble air-bubble-ai';
            document.getElementById('chatThread').appendChild(div);
            window._aiStreamBubble = div;
            div.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
        }};
        window.streamAIBubble = function(text) {{
            if (window._aiStreamBubble) {{
                window._aiStreamBubble.textContent = text;
                window._aiStreamBubble.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
            }}
        }};
        window.endAIStream = function(text) {{
            window.streamAIBubble(text); window._aiStreamBubble = null; window.endChatWait();
        }};
        window.sendChat = function() {{
            const input = document.getElementById('chatInput');
            const text = input.value.trim();
            if (!text) return;
            window.appendBubble('user', text);
            input.value = '';
            const b = document.getElementById('chatSendBtn');
            b.disabled = true; b.textContent = '...';
            input.disabled = true;
            pycmd('aiReview::chat::' + text);
        }};

        // Enter sends; Shift+Enter inserts a newline.
        window.aiSendOnEnter = function(el, fn) {{
            if (!el) return;
            el.addEventListener('keydown', function(e) {{
                if (e.key === 'Enter' && !e.shiftKey) {{
                    e.preventDefault();
                    fn();
                }}
            }});
        }};
        window.aiSendOnEnter(document.getElementById('aiAnswer'), window.submitAIAnswer);
        window.aiSendOnEnter(document.getElementById('chatInput'), window.sendChat);
        """)

    def get_card_data(self):
        """Extract card data for the review"""
        note = self.current_card.note()

        # Get front and back
        front = note.fields[0] if len(note.fields) > 0 else ""
        back = note.fields[1] if len(note.fields) > 1 else ""

        # Extract just the word from back (remove examples, etc.)
        back_word = back.split('\n')[0].split('(')[0].strip()

        # Plain-text of the whole back (meaning, examples, notes) for the LLM.
        # strip_html (NORMAL mode) removes HTML tags, <img>, and [sound:…] refs
        # entirely, so no images/audio are sent — only text.
        back_text = re.sub(r'\s+\n', '\n', strip_html(back)).strip()

        return {
            "front": front.strip(),
            "back": back_word,
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
            source_word=card_data['front'],
            target_word=card_data['back'],
            card_back=card_data['back_text'],
            sentence=answer
        )
        system = system_prompt.render()
        messages = [{"role": "user", "content": prompt}]

        web = mw.reviewer.web

        # Reveal the answer + feedback shell immediately so streamed text has a
        # home and the card-back toggle stays usable while the model works.
        web.eval(f"""
        document.getElementById('inputContainer').style.display = 'none';
        document.getElementById('submitContainer').style.display = 'none';
        document.getElementById('modeToggle').style.display = 'none';
        document.getElementById('yourAnswerDisplay').style.display = 'block';
        document.getElementById('yourAnswerText').textContent = '{self._js_escape(answer)}';
        document.getElementById('feedback').style.display = 'block';
        document.getElementById('feedbackText').textContent = '…';
        """)

        def task():
            buf = []

            def on_chunk(delta):
                buf.append(delta)
                # Live progress view: drop <think>/tags so raw markup never flashes.
                cleaned = self._plainify(self._strip_think("".join(buf)))
                mw.taskman.run_on_main(
                    lambda c=cleaned: web.eval(
                        f"document.getElementById('feedbackText').textContent = '{self._js_escape(c)}';"
                    )
                )

            return providers.stream_llm(config, system, messages, on_chunk)

        def on_done(future):
            try:
                ai_feedback = future.result()
            except Exception as e:
                conversations.append_error(config, f"Evaluation failed: {e}")
                # Return to the edit/send screen with the error shown on top so
                # the user can tweak the answer and resubmit (or turn the card).
                web.eval(f"window.aiEvalError('{self._js_escape(str(e))}');")
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
                source_word=card_data['front'],
                target_word=card_data['back'],
                card_back=card_data['back_text'],
                sentence=answer,
                feedback=self._plainify(ai_feedback),
            )
            self.chat_messages = []

            # Swap the live streamed text for the parsed, formatted feedback.
            if mode == "quick":
                feedback_html = self.parse_quick_to_html(ai_feedback)
            else:
                feedback_html = self.parse_feedback_to_html(ai_feedback)

            web.eval(f"""
            document.getElementById('feedbackText').innerHTML = '{self._js_escape(feedback_html)}';
            document.getElementById('chatBlock').style.display = 'block';
            """)

            # Now show the answer to enable Anki's native buttons.
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
        web.eval("window.startAIStream();")

        def task():
            buf = []

            def on_chunk(delta):
                buf.append(delta)
                cleaned = self._strip_think("".join(buf))
                mw.taskman.run_on_main(
                    lambda c=cleaned: web.eval(f"window.streamAIBubble('{self._js_escape(c)}');")
                )

            return providers.stream_llm(config, self.chat_system, self.chat_messages, on_chunk)

        def on_done(future):
            try:
                reply = future.result()
            except Exception as e:
                conversations.append_error(config, f"Chat failed: {e}")
                # Drop the failed turn so a retry doesn't duplicate it.
                self.chat_messages.pop()
                web.eval(f"window.chatError('{self._js_escape(str(e))}');")
                return

            reply = self._strip_think(reply)
            self.chat_messages.append({"role": "assistant", "content": reply})
            web.eval(f"window.endAIStream('{self._js_escape(reply)}');")

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
            "word": card_data["front"],
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
    def _js_escape(text):
        return (text.replace('\\', '\\\\').replace('`', '\\`')
                .replace('$', '\\$').replace('\n', '\\n').replace("'", "\\'"))

    def parse_quick_to_html(self, feedback):
        """Parse the quick-review <verdict>/<feedback> tags into compact HTML"""
        verdict_match = re.search(r'<verdict>(.*?)</verdict>', feedback, re.DOTALL | re.IGNORECASE)
        feedback_match = re.search(r'<feedback>(.*?)</feedback>', feedback, re.DOTALL | re.IGNORECASE)

        verdict = (verdict_match.group(1).strip().lower() if verdict_match else "")
        message = (feedback_match.group(1).strip() if feedback_match else feedback.strip())

        if "incorrect" in verdict:
            label = '<div class="air-verdict incorrect">✗ Not quite</div>'
        elif "correct" in verdict:
            label = '<div class="air-verdict correct">✓ Correct</div>'
        else:
            label = ''

        return f'{label}{message}'

    def parse_feedback_to_html(self, feedback):
        """Parse XML-tagged feedback into formatted HTML"""
        html_parts = []

        # Extract each section using regex
        def extract_tag_content(tag_name, content):
            """Extract content from XML tags, return None if tag is missing or contains only 'None'"""
            pattern = f'<{tag_name}>(.*?)</{tag_name}>'
            match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
            if match:
                extracted = match.group(1).strip()
                # Check if content contains the "ignore_field"
                if 'ignore_field' in extracted.lower():
                    return None
                return extracted
            return None

        # Extract score section
        score = extract_tag_content('score', feedback)
        if score:
            html_parts.append(f'<div class="feedback-score"><strong>Score:</strong> {score}</div>')

        # Positive feedback when the sentence has no issues, or the meaning
        # verdict for a translation answer (✗ => wrong, render it red not green).
        praise = extract_tag_content('praise', feedback)
        if praise:
            color = '#d06b5c' if praise.lstrip().startswith('✗') else '#2f9e6f'
            html_parts.append(f'<div style="color: {color}; font-weight: 600;">{praise}</div>')

        # Extract grammar section
        grammar = extract_tag_content('grammar', feedback)
        if grammar:
            html_parts.append(f'<div class="feedback-grammar"><strong>Grammar:</strong><br>{grammar}</div>')

        # Extract fluency section
        fluency = extract_tag_content('fluency', feedback)
        if fluency:
            html_parts.append(f'<div class="feedback-fluency"><strong>Fluency:</strong><br>{fluency}</div>')

        # Extract paraphrase section (always present according to specs)
        paraphrase = extract_tag_content('paraphrase', feedback)
        if paraphrase:
            html_parts.append(f'<div class="feedback-paraphrase"><strong>Example:</strong><br>{paraphrase}</div>')

        # Join all parts with spacing
        if html_parts:
            return '<br><br>'.join(html_parts)
        else:
            # Fallback if no tags were found
            return feedback.replace('\n', '<br>')
