/*
 * AI Review panel.
 *
 * Mounted as a sibling of Anki's #qa container, so it survives the
 * question -> answer re-render (_showAnswer only replaces #qa). All dynamic
 * text lands via textContent — never innerHTML — so card/LLM/user content
 * can't inject markup. Python drives the panel through the AIReview.* calls
 * below and receives events through pycmd("aiReview::...").
 */
"use strict";

var AIReview = (function () {
    let ui = null; // element refs for the mounted panel, or null
    let selectedMode = "full";

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    // Enter sends; Shift+Enter inserts a newline.
    function sendOnEnter(textarea, fn) {
        textarea.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                fn();
            }
        });
    }

    function setMode(mode) {
        selectedMode = mode;
        ui.pills.forEach(function (p) {
            p.classList.toggle("air-active", p.dataset.mode === mode);
        });
    }

    function submit() {
        const answer = ui.input.value.trim();
        if (answer.length < 2) {
            ui.input.focus();
            return;
        }
        hideError();
        ui.submitBtn.disabled = true;
        ui.submitBtn.textContent = "Evaluating…";
        pycmd("aiReview::submit::" + selectedMode + "::" + answer);
    }

    function sendChat() {
        const text = ui.chatInput.value.trim();
        if (!text) return;
        appendBubble("user", text);
        ui.chatInput.value = "";
        ui.chatInput.disabled = true;
        ui.chatSend.disabled = true;
        ui.chatSend.textContent = "…";
        pycmd("aiReview::chat::" + text);
    }

    function appendBubble(role, text) {
        const div = el("div", "air-bubble air-bubble-" + role, text);
        ui.thread.appendChild(div);
        div.scrollIntoView({ behavior: "smooth", block: "nearest" });
        return div;
    }

    function endChatWait() {
        ui.chatInput.disabled = false;
        ui.chatSend.disabled = false;
        ui.chatSend.textContent = "Send";
    }

    function showError(msg) {
        ui.error.textContent = msg;
        ui.error.hidden = false;
        ui.error.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    function hideError() {
        ui.error.hidden = true;
        ui.error.textContent = "";
    }

    return {
        mount: function (opts) {
            this.unmount();

            const root = el("div");
            root.id = "ai-review-root";
            root.dataset.airTheme = opts.theme || "native";

            const panel = el("div", "air-panel");
            root.appendChild(panel);

            const instruction = el("div", "air-instruction", opts.instruction || "");
            panel.appendChild(instruction);

            const error = el("div", "air-error");
            error.hidden = true;
            panel.appendChild(error);

            // Quick/full mode pills.
            const modeRow = el("div", "air-mode");
            const pills = ["quick", "full"].map(function (mode) {
                const pill = el(
                    "button",
                    "air-pill",
                    mode === "quick" ? "Quick review" : "Full review"
                );
                pill.type = "button";
                pill.dataset.mode = mode;
                pill.addEventListener("click", function () { setMode(mode); });
                modeRow.appendChild(pill);
                return pill;
            });
            panel.appendChild(modeRow);

            // Input phase.
            const inputBlock = el("div", "air-input-block");
            const input = el("textarea", "air-input");
            input.placeholder = "Type your answer…";
            inputBlock.appendChild(input);
            const actions = el("div", "air-actions");
            const submitBtn = el("button", "air-submit", "Submit");
            submitBtn.type = "button";
            submitBtn.addEventListener("click", submit);
            actions.appendChild(submitBtn);
            const peekBtn = el("button", "air-ghost", "Show answer instead");
            peekBtn.type = "button";
            peekBtn.addEventListener("click", function () { pycmd("aiReview::peek"); });
            actions.appendChild(peekBtn);
            inputBlock.appendChild(actions);
            panel.appendChild(inputBlock);

            const notice = el(
                "div",
                "air-notice",
                "You looked at the answer, so there's no AI review this time — " +
                "grade the card as usual and you'll get another shot when it comes back."
            );
            notice.hidden = true;
            panel.appendChild(notice);

            // Post-submit: answer echo + feedback.
            const answerBlock = el("div", "air-answer");
            answerBlock.hidden = true;
            answerBlock.appendChild(el("div", "air-label", "Your answer"));
            const answerText = el("div", "air-answer-text");
            answerBlock.appendChild(answerText);
            panel.appendChild(answerBlock);

            const feedback = el("div", "air-feedback");
            feedback.hidden = true;
            const verdictRow = el("div", "air-verdict");
            verdictRow.hidden = true;
            const verdictLabel = el("span", "air-verdict-label");
            verdictRow.appendChild(verdictLabel);
            const score = el("span", "air-score");
            verdictRow.appendChild(score);
            feedback.appendChild(verdictRow);
            const feedbackBody = el("div", "air-feedback-body");
            feedback.appendChild(feedbackBody);
            panel.appendChild(feedback);

            // Follow-up chat, collapsed behind a ghost button.
            const chat = el("div", "air-chat");
            chat.hidden = true;
            const chatToggle = el("button", "air-ghost", "Ask a follow-up ▾");
            chatToggle.type = "button";
            chatToggle.addEventListener("click", function () { AIReview.expandChat(); });
            chat.appendChild(chatToggle);
            const chatArea = el("div", "air-chat-area");
            chatArea.hidden = true;
            const thread = el("div", "air-thread");
            chatArea.appendChild(thread);
            const chatRow = el("div", "air-chat-row");
            const chatInput = el("textarea", "air-chat-input");
            chatInput.placeholder = "Ask a follow-up…";
            chatRow.appendChild(chatInput);
            const chatSend = el("button", "air-chat-send", "Send");
            chatSend.type = "button";
            chatSend.addEventListener("click", sendChat);
            chatRow.appendChild(chatSend);
            chatArea.appendChild(chatRow);
            chat.appendChild(chatArea);
            panel.appendChild(chat);

            document.body.appendChild(root);

            ui = {
                root: root, error: error, pills: pills, modeRow: modeRow,
                inputBlock: inputBlock, input: input, submitBtn: submitBtn,
                notice: notice, answerBlock: answerBlock, answerText: answerText,
                feedback: feedback, verdictRow: verdictRow,
                verdictLabel: verdictLabel, score: score,
                feedbackBody: feedbackBody, chat: chat, chatToggle: chatToggle,
                chatArea: chatArea, thread: thread, chatInput: chatInput,
                chatSend: chatSend, streamBubble: null,
            };

            sendOnEnter(input, submit);
            sendOnEnter(chatInput, sendChat);
            setMode(opts.mode === "quick" ? "quick" : "full");
            input.focus();
        },

        unmount: function () {
            const stale = document.getElementById("ai-review-root");
            if (stale) stale.remove();
            ui = null;
        },

        // Submit accepted: switch to the waiting state and echo the answer.
        setEvaluating: function (answer) {
            if (!ui) return;
            hideError();
            ui.inputBlock.hidden = true;
            ui.modeRow.hidden = true;
            ui.answerText.textContent = answer;
            ui.answerBlock.hidden = false;
            ui.verdictRow.hidden = true;
            ui.feedbackBody.textContent = "…";
            ui.feedback.hidden = false;
        },

        // Live plain-text preview while the model streams.
        streamFeedback: function (text) {
            if (!ui) return;
            ui.feedbackBody.textContent = text || "…";
        },

        // Final structured feedback:
        // {verdict: "correct"|"partial"|"incorrect"|null, score, summary,
        //  sections: [{label, text}]}
        showFeedback: function (data) {
            if (!ui) return;
            if (data.verdict) {
                const labels = {
                    correct: "✓ Correct",
                    partial: "≈ Partially correct",
                    incorrect: "✗ Not quite",
                };
                ui.verdictLabel.textContent = labels[data.verdict] || data.verdict;
                ui.verdictRow.className = "air-verdict air-" + data.verdict;
                ui.score.textContent = data.score || "";
                ui.verdictRow.hidden = false;
            }
            ui.feedbackBody.textContent = "";
            if (data.summary) {
                ui.feedbackBody.appendChild(el("div", "air-summary", data.summary));
            }
            (data.sections || []).forEach(function (section) {
                const div = el("div", "air-section");
                div.appendChild(el("div", "air-label", section.label));
                div.appendChild(el("div", "air-section-text", section.text));
                ui.feedbackBody.appendChild(div);
            });
            ui.feedback.hidden = false;
            ui.chat.hidden = false;
        },

        // Evaluation failed: back to the edit state with the error on top.
        evalError: function (msg) {
            if (!ui) return;
            ui.answerBlock.hidden = true;
            ui.feedback.hidden = true;
            ui.inputBlock.hidden = false;
            ui.modeRow.hidden = false;
            ui.submitBtn.disabled = false;
            ui.submitBtn.textContent = "Submit";
            showError(
                "The AI review failed: " + msg +
                "\n\nEdit your answer and submit again, or show the answer to grade the card normally."
            );
        },

        // Back was revealed before answering: AI review is forfeited.
        forfeit: function () {
            if (!ui || ui.inputBlock.hidden) return;
            ui.inputBlock.hidden = true;
            ui.modeRow.hidden = true;
            hideError();
            ui.notice.hidden = false;
        },

        expandChat: function () {
            if (!ui) return;
            ui.chatToggle.hidden = true;
            ui.chatArea.hidden = false;
            ui.chatInput.focus();
        },

        // Streaming chat bubble: created empty, filled delta-by-delta.
        chatStart: function () {
            if (!ui) return;
            ui.streamBubble = appendBubble("ai", "");
        },
        chatStream: function (text) {
            if (!ui || !ui.streamBubble) return;
            ui.streamBubble.textContent = text;
            ui.streamBubble.scrollIntoView({ behavior: "smooth", block: "nearest" });
        },
        chatEnd: function (text) {
            if (!ui) return;
            this.chatStream(text);
            ui.streamBubble = null;
            endChatWait();
        },
        chatError: function (text) {
            if (!ui) return;
            if (ui.streamBubble) {
                ui.streamBubble.remove();
                ui.streamBubble = null;
            }
            appendBubble("error", text);
            endChatWait();
        },
    };
})();
