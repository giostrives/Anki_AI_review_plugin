"""
Main reviewer logic - intercepts card reviews and shows AI interface
"""
import re
import uuid
from datetime import datetime

from aqt import mw, gui_hooks
from aqt.reviewer import Reviewer
from aqt.utils import showInfo
from aqt.webview import WebContent

from . import conversations, providers
from .prompts import (conversation_prompt, language_card_prompt,
                      quick_card_prompt, system_prompt)


class AIReviewer:
    def __init__(self):
        self.current_card = None
        self.deck_config = None
        self.user_answer = None
        # Per-card conversation state.
        self.conv_meta = None       # metadata + the human answer, for persistence
        self.first_feedback = None  # the evaluation reply (think-stripped)
        self.chat_system = None     # tutor system prompt for the follow-up chat
        self.chat_messages = None   # follow-up turns only (no evaluation context)
        self.session_path = conversations.new_session_path(
            mw.addonManager.getConfig(__name__)
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
            if message.startswith("aiReview::submit::"):
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

        # Check if AI review is enabled for this deck
        config = mw.addonManager.getConfig(__name__)
        deck_configs = config.get("deck_configs", {})

        if deck_name not in deck_configs:
            return  # Use normal review

        self.deck_config = deck_configs[deck_name]
        if not self.deck_config.get("enabled", False):
            return  # Use normal review

        # Store current card and reset user answer
        self.current_card = card
        self.user_answer = None

        # Show AI interface
        self.show_ai_interface()

    def show_ai_interface(self):
        """Show AI review interface"""
        card_data = self.get_card_data()

        word = card_data['front']
        instruction = f"Write a sentence using '{word}' in {card_data['target_language']}"

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
        </style>
        <div class="air-page">
            <div class="air-card">
                <div class="air-word">{word}</div>
                <div class="air-instruction">{instruction}</div>

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
        </div>
        """

        # Show in reviewer
        mw.reviewer.web.eval(f"document.body.innerHTML = `{html}`;")

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
            if (!answer) {{
                alert('Please enter an answer');
                return;
            }}
            // Send mode + answer to Python (button updated from Python side)
            pycmd('aiReview::submit::' + window.aiSelectedMode + '::' + answer);
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
        window.chatError = function(text) {{ window.appendBubble('error', text); window.endChatWait(); }};
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
        """)

    def get_card_data(self):
        """Extract card data for the review"""
        note = self.current_card.note()

        # Get front and back
        front = note.fields[0] if len(note.fields) > 0 else ""
        back = note.fields[1] if len(note.fields) > 1 else ""

        # Extract just the word from back (remove examples, etc.)
        back_word = back.split('\n')[0].split('(')[0].strip()

        return {
            "front": front.strip(),
            "back": back_word,
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
            sentence=answer
        )
        system = system_prompt.render()

        try:
            ai_feedback = providers.call_llm(config, system, prompt)
        except Exception as e:
            showInfo(str(e))
            mw.reviewer.web.eval("""
                const btn = document.getElementById('submitBtn');
                if (btn) { btn.disabled = false; btn.textContent = 'Submit'; }
            """)
            return

        # Never surface the model's chain-of-thought.
        ai_feedback = self._strip_think(ai_feedback)

        # Set up the follow-up chat with its OWN tutor framing, decoupled from the
        # evaluation prompt — so follow-ups are a normal conversation (no scores,
        # XML, or <think>), not another review. Only the follow-up turns are sent;
        # the card context lives in chat_system.
        self.conv_meta = self._build_conv_meta(config, card_data, answer, mode)
        self.first_feedback = ai_feedback
        self.chat_system = conversation_prompt.render(
            user_proficiency=self.deck_config.get("user_level", "Beginner").lower(),
            source_language=card_data['source_language'],
            target_language=card_data['target_language'],
            source_word=card_data['front'],
            target_word=card_data['back'],
            sentence=answer,
            feedback=self._plainify(ai_feedback),
        )
        self.chat_messages = []

        # Parse the response into HTML depending on the review mode.
        if mode == "quick":
            feedback_html = self.parse_quick_to_html(ai_feedback)
        else:
            feedback_html = self.parse_feedback_to_html(ai_feedback)

        # Transform UI: hide inputs, show compact answer and feedback
        answer_escaped = answer.replace('`', '\\`').replace('$', '\\$').replace("'", "\\'")
        feedback_escaped = feedback_html.replace('`', '\\`').replace('$', '\\$').replace('\n', '\\n').replace("'", "\\'")

        js = f"""
        document.getElementById('inputContainer').style.display = 'none';
        document.getElementById('submitContainer').style.display = 'none';
        document.getElementById('modeToggle').style.display = 'none';

        document.getElementById('yourAnswerDisplay').style.display = 'block';
        document.getElementById('yourAnswerText').textContent = '{answer_escaped}';

        document.getElementById('feedback').style.display = 'block';
        document.getElementById('feedbackText').innerHTML = '{feedback_escaped}';

        document.getElementById('chatBlock').style.display = 'block';
        """
        mw.reviewer.web.eval(js)

        # Now show the answer to enable Anki's native buttons
        mw.reviewer._showAnswer()

    def send_chat(self, text):
        """Handle a follow-up chat turn under the tutor (conversation) framing."""
        if self.conv_meta is None or self.chat_system is None:
            return
        config = mw.addonManager.getConfig(__name__)

        self.chat_messages.append({"role": "user", "content": text})
        try:
            reply = providers.chat_llm(config, self.chat_system, self.chat_messages)
        except Exception as e:
            # Drop the failed turn so a retry doesn't duplicate it.
            self.chat_messages.pop()
            mw.reviewer.web.eval(f"window.chatError('{self._js_escape(str(e))}');")
            return

        reply = self._strip_think(reply)
        self.chat_messages.append({"role": "assistant", "content": reply})
        mw.reviewer.web.eval(f"window.chatReply('{self._js_escape(reply)}');")

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

        # Positive feedback when the sentence has no issues.
        praise = extract_tag_content('praise', feedback)
        if praise:
            html_parts.append(f'<div style="color: #2f9e6f; font-weight: 600;">{praise}</div>')

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
