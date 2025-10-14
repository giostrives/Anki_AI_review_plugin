"""
Main reviewer logic - intercepts card reviews and shows AI interface
"""

from aqt import mw, gui_hooks
from aqt.reviewer import Reviewer
from aqt.utils import tooltip, showInfo
from aqt.webview import WebContent
import json
import os
import requests

class AIReviewer:
    def __init__(self):
        self.current_card = None
        self.deck_config = None
        self.mode = None
        gui_hooks.reviewer_did_show_question.append(self.on_show_question)
        gui_hooks.webview_will_set_content.append(self.on_webview_will_set_content)

    def on_webview_will_set_content(self, web_content: WebContent, context):
        """Add our custom command handlers"""
        if not isinstance(context, Reviewer):
            return

        # Add bridge command handler
        def handle_ai_command(handled, message, context):
            if message.startswith("aiReview::submit::"):
                answer = message.replace("aiReview::submit::", "")
                self.evaluate_answer(answer)
                return (True, None)
            elif message.startswith("aiReview::rate::"):
                ease = int(message.replace("aiReview::rate::", ""))
                self.rate_card(ease)
                return (True, None)
            return handled

        gui_hooks.webview_did_receive_js_message.append(handle_ai_command)

    def on_show_question(self, card):
        """Intercept card review"""
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

        # Store current card
        self.current_card = card

        # Replace card display with AI interface
        self.show_ai_interface()

    def show_ai_interface(self):
        """Show AI review interface"""
        card_data = self.get_card_data()

        # Determine mode randomly for now
        import random
        self.mode = 'sentence' if random.random() > 0.5 else 'translate'

        if self.mode == 'sentence':
            word = card_data['front']
            instruction = f"Write a sentence using '{word}' in {card_data['target_language']}"
        else:
            word = card_data['back']
            instruction = f"Translate '{word}' to {card_data['source_language']}"

        html = f"""
        <div style="min-height: 100vh; display: flex; align-items: center; justify-content: center; 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px;">
            <div style="background: white; border-radius: 20px; padding: 40px; max-width: 700px; 
                        width: 100%; box-shadow: 0 20px 60px rgba(0,0,0,0.3);">
                <div style="text-align: center; margin-bottom: 30px;">
                    <div style="font-size: 48px; font-weight: bold; color: #667eea; margin-bottom: 10px;">
                        {word}
                    </div>
                    <div style="font-size: 18px; color: #666; margin-bottom: 20px;">
                        {instruction}
                    </div>
                </div>
                
                <div style="margin-bottom: 20px;">
                    <textarea id="aiAnswer" style="width: 100%; min-height: 120px; padding: 15px; 
                             border: 2px solid #e0e0e0; border-radius: 10px; font-size: 16px; 
                             font-family: inherit; resize: vertical;" 
                             placeholder="Type your answer here..."></textarea>
                </div>
                
                <button onclick="window.submitAIAnswer()" id="submitBtn"
                        style="width: 100%; padding: 15px; border: none; border-radius: 10px; 
                               font-size: 16px; font-weight: 600; cursor: pointer; 
                               background: #667eea; color: white; transition: all 0.3s;">
                    Submit
                </button>
                
                <div id="feedback" style="display: none; background: #f8f9fa; border-radius: 10px; 
                                          padding: 20px; margin-top: 20px;">
                    <div style="font-size: 20px; font-weight: bold; margin-bottom: 10px; color: #333;">
                        AI Feedback
                    </div>
                    <div id="feedbackText" style="font-size: 16px; line-height: 1.6; color: #555; 
                                                   white-space: pre-wrap;"></div>
                </div>
                
                <div id="ratingButtons" style="display: none; margin-top: 20px; 
                                               display: flex; gap: 10px;">
                    <button onclick="window.aiReviewRate(1)" 
                            style="flex: 1; padding: 15px; border: none; border-radius: 10px; 
                                   font-size: 16px; font-weight: 600; cursor: pointer; 
                                   background: #ff6b6b; color: white;">Again</button>
                    <button onclick="window.aiReviewRate(2)" 
                            style="flex: 1; padding: 15px; border: none; border-radius: 10px; 
                                   font-size: 16px; font-weight: 600; cursor: pointer; 
                                   background: #ffa500; color: white;">Hard</button>
                    <button onclick="window.aiReviewRate(3)" 
                            style="flex: 1; padding: 15px; border: none; border-radius: 10px; 
                                   font-size: 16px; font-weight: 600; cursor: pointer; 
                                   background: #51cf66; color: white;">Good</button>
                    <button onclick="window.aiReviewRate(4)" 
                            style="flex: 1; padding: 15px; border: none; border-radius: 10px; 
                                   font-size: 16px; font-weight: 600; cursor: pointer; 
                                   background: #339af0; color: white;">Easy</button>
                </div>
            </div>
        </div>
        """

        # Show in reviewer
        mw.reviewer.web.eval(f"document.body.innerHTML = `{html}`;")

        # Inject the functions separately
        mw.reviewer.web.eval("""
        window.submitAIAnswer = function() {
            const answer = document.getElementById('aiAnswer').value.trim();
            if (!answer) {
                alert('Please enter an answer');
                return;
            }
            
            const btn = document.getElementById('submitBtn');
            btn.disabled = true;
            btn.textContent = 'Evaluating...';
            
            pycmd('aiReview::submit::' + answer);
        };
        
        window.aiReviewRate = function(ease) {
            pycmd('aiReview::rate::' + ease);
        };
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

    def evaluate_answer(self, answer):
        """Evaluate user's answer with AI"""
        card_data = self.get_card_data()
        config = mw.addonManager.getConfig(__name__)

        prompt = self.mode == 'sentence' and f"""Evaluate if the student knows the word "{card_data['front']}" ({card_data['source_language']}) based on their sentence in {card_data['target_language']}: "{answer}"

    The word should translate to: "{card_data['back']}"

    Check:
    1. Did they use the correct word/translation?
    2. Is the grammar acceptable (minor mistakes OK)?
    3. Is the usage contextually appropriate?

    Respond with:
    - PASS or FAIL on the first line
    - Then provide brief, encouraging feedback (2-3 sentences)

    Be lenient - if they clearly know the word despite minor grammar mistakes, mark as PASS.""" or f"""Evaluate if the student correctly translated "{card_data['back']}" ({card_data['target_language']}) to {card_data['source_language']}.

    Their answer: "{answer}"
    Expected: "{card_data['front']}"

    Check if the translation is correct (accept synonyms and variations).

    Respond with:
    - PASS or FAIL on the first line  
    - Then provide brief feedback (1-2 sentences)"""

        try:
            response = requests.post(
                f"{config.get('ollama_endpoint', 'http://localhost:11434')}/api/generate",
                json={
                    "model": config.get("model", "gemma3"),
                    "prompt": prompt,
                    "stream": False
                },
                timeout=30
            )

            data = response.json()
            ai_response = data['response']

            # Show feedback
            feedback_escaped = ai_response.replace('`', '\\`').replace('$', '\\$').replace('\n', '\\n')
            js = f"""
            document.getElementById('feedback').style.display = 'block';
            document.getElementById('feedbackText').textContent = `{feedback_escaped}`;
            document.getElementById('ratingButtons').style.display = 'flex';
            """
            mw.reviewer.web.eval(js)

        except Exception as e:
            showInfo(f"Error connecting to Ollama: {e}\\n\\nMake sure Ollama is running with: ollama serve")

    def rate_card(self, ease):
        """Rate the card and move to next"""
        if self.current_card:
            mw.reviewer._answerCard(ease)