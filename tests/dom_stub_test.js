/*
 * Smoke test for web/ai_review.js against a minimal DOM stub.
 * Run directly (`node tests/dom_stub_test.js`) or via pytest (test_panel_js.py).
 * Exits non-zero on the first failed assertion.
 */
"use strict";

class Element {
    constructor(tag) {
        this.tagName = tag; this.children = []; this.dataset = {};
        this._text = ""; this.hidden = false; this.disabled = false;
        this._classes = new Set(); this.id = ""; this.parent = null;
        this.value = ""; this.placeholder = ""; this.type = "";
        this.listeners = {};
    }
    // Like the real DOM: assigning textContent drops all children; reading
    // it concatenates the subtree's text.
    set textContent(v) { this._text = String(v); this.children = []; }
    get textContent() {
        return this._text + this.children.map(c => c.textContent).join("");
    }
    get lastChild() { return this.children[this.children.length - 1] || null; }
    set className(v) { this._classes = new Set(v.split(/\s+/).filter(Boolean)); }
    get className() { return [...this._classes].join(" "); }
    get classList() {
        const self = this;
        return {
            toggle(c, on) { on ? self._classes.add(c) : self._classes.delete(c); },
            add(c) { self._classes.add(c); },
            contains(c) { return self._classes.has(c); },
        };
    }
    appendChild(c) { c.parent = this; this.children.push(c); return c; }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(x => x !== this); }
    addEventListener(ev, fn) { (this.listeners[ev] ||= []).push(fn); }
    click() { (this.listeners.click || []).forEach(fn => fn()); }
    scrollIntoView() {} focus() {}
    find(pred) {
        if (pred(this)) return this;
        for (const c of this.children) { const r = c.find(pred); if (r) return r; }
        return null;
    }
}

const body = new Element("body");
global.document = {
    body,
    createElement: t => new Element(t),
    createTextNode: t => { const n = new Element("#text"); n._text = String(t); return n; },
    getElementById: id => body.find(e => e.id === id),
};
const sent = [];
global.pycmd = msg => sent.push(msg);

function assert(cond, msg) {
    if (!cond) { console.error("FAIL: " + msg); process.exit(1); }
}

// Load the panel script exactly as a browser <script> would: a classic
// script whose top-level `var` lands on the global object (a strict-mode
// `eval`/`require` would scope it away instead).
const path = require("path");
const src = require("fs").readFileSync(
    path.join(__dirname, "..", "web", "ai_review.js"), "utf8");
require("vm").runInThisContext(src);
const A = AIReview;

// --- mount ---
A.mount({ instruction: "Write a sentence using 'sol'", mode: "quick", theme: "polished" });
let root = document.getElementById("ai-review-root");
assert(root, "root mounted");
assert(root.dataset.airTheme === "polished", "theme set");
const activePill = root.find(e => e._classes.has("air-pill") && e._classes.has("air-active"));
assert(activePill.dataset.mode === "quick", "quick pill active, got " + activePill.dataset.mode);

// --- remount replaces the old panel ---
A.mount({ instruction: "x", mode: "full", theme: "native" });
assert(body.children.filter(c => c.id === "ai-review-root").length === 1, "single root after remount");
root = document.getElementById("ai-review-root");

// --- submit: short answers ignored; real submit sends pycmd + disables ---
const input = root.find(e => e._classes.has("air-input"));
const submitBtn = root.find(e => e._classes.has("air-submit"));
input.value = "a";
submitBtn.click();
assert(sent.length === 0, "short answer not sent");
input.value = "El sol es brillante";
submitBtn.click();
assert(sent[0] === "aiReview::submit::full::El sol es brillante", "submit cmd: " + sent[0]);
assert(submitBtn.disabled, "submit disabled while evaluating");

// --- evaluating + streaming ---
A.setEvaluating("El sol es brillante");
assert(root.find(e => e._classes.has("air-answer-text")).textContent === "El sol es brillante",
    "answer echoed");
A.streamFeedback("Looks go");
assert(root.find(e => e._classes.has("air-feedback-body")).textContent === "Looks go",
    "streamed text lands");

// --- structured feedback ---
A.showFeedback({ verdict: "partial", score: "3/5", summary: "Almost.",
    sections: [{ label: "Grammar", text: "Use 'está'." }] });
const verdict = root.find(e => e._classes.has("air-verdict"));
assert(!verdict.hidden && verdict._classes.has("air-partial"), "verdict shown as partial");
assert(root.find(e => e._classes.has("air-section-text")).textContent === "Use 'está'.",
    "section text rendered");
assert(!root.find(e => e._classes.has("air-chat")).hidden, "chat offered after feedback");

// --- markdown rendering in LLM output ---
A.showFeedback({ verdict: "partial", score: null, summary: "Use **está**, not `es`.",
    sections: [{ label: "Grammar", text: "Two issues:\n- tense\n- article" }] });
const summary = root.find(e => e._classes.has("air-summary"));
const strong = summary.find(e => e.tagName === "strong");
assert(strong && strong.textContent === "está", "bold rendered as <strong>");
const codeEl = summary.find(e => e.tagName === "code");
assert(codeEl && codeEl.textContent === "es", "backticks rendered as <code>");
assert(summary.textContent === "Use está, not es.", "summary text intact: " + summary.textContent);
const sectionText = root.find(e => e._classes.has("air-section-text"));
const ul = sectionText.find(e => e.tagName === "ul");
assert(ul && ul.children.length === 2 && ul.children[1].textContent === "article",
    "bullet list rendered as <ul>/<li>");

// Numbered lists become <ol>.
A.streamFeedback("1. first\n2. second");
const fb = root.find(e => e._classes.has("air-feedback-body"));
const ol = fb.find(e => e.tagName === "ol");
assert(ol && ol.children.length === 2, "numbered list rendered as <ol>");

// HTML in model output stays literal text — never becomes an element.
A.streamFeedback("evil <script>alert(1)</script> <img src=x onerror=y>");
assert(!fb.find(e => e.tagName === "script" || e.tagName === "img"),
    "HTML tags not turned into elements");
assert(fb.textContent.includes("<script>alert(1)</script>"),
    "HTML shown as literal text: " + fb.textContent);

// Unclosed markers stay literal (mid-stream state).
A.streamFeedback("Use **está");
assert(!fb.find(e => e.tagName === "strong") && fb.textContent === "Use **está",
    "unclosed bold stays literal");

// --- chat expand + send + streaming bubble ---
A.expandChat();
const chatInput = root.find(e => e._classes.has("air-chat-input"));
chatInput.value = "why está?";
root.find(e => e._classes.has("air-chat-send")).click();
assert(sent[1] === "aiReview::chat::why está?", "chat cmd: " + sent[1]);
assert(root.find(e => e._classes.has("air-bubble-user")).textContent === "why está?", "user bubble");
A.chatStart(); A.chatStream("Bec"); A.chatStream("Because *states*");
A.chatEnd("Because *states* use estar.");
const aiBubble = root.find(e => e._classes.has("air-bubble-ai"));
assert(aiBubble.textContent === "Because states use estar.",
    "ai bubble finalized: " + aiBubble.textContent);
const ems = [];
aiBubble.find(e => { if (e.tagName === "em") ems.push(e); return false; });
assert(ems.length === 1 && ems[0].textContent === "states",
    "chat markdown rendered once, no stream duplication");
assert(!chatInput.disabled, "chat input re-enabled");

// --- chat error removes the streaming bubble, adds an error bubble ---
A.chatStart(); A.chatStream("half a rep");
A.chatError("connection lost");
assert(root.find(e => e._classes.has("air-bubble-error")).textContent === "connection lost",
    "error bubble shown");
assert(!root.find(e => e._classes.has("air-bubble-ai") && e.textContent === "half a rep"),
    "partial stream bubble removed");

// --- eval error restores the edit state ---
A.evalError("connection refused");
assert(!submitBtn.disabled && submitBtn.textContent === "Submit", "submit restored");
const err = root.find(e => e._classes.has("air-error"));
assert(!err.hidden && err.textContent.includes("connection refused"), "error banner shown");

// --- forfeit hides the input and is idempotent ---
A.mount({ instruction: "x", mode: "full", theme: "native" });
root = document.getElementById("ai-review-root");
A.forfeit(); A.forfeit();
assert(root.find(e => e._classes.has("air-notice")).hidden === false, "peek notice shown");
assert(root.find(e => e._classes.has("air-input-block")).hidden === true, "input hidden");

// --- unmount ---
A.unmount();
assert(!document.getElementById("ai-review-root"), "unmounted");

// --- calls on an unmounted panel are safe no-ops ---
A.streamFeedback("x"); A.showFeedback({}); A.evalError("x"); A.forfeit();
A.chatStart(); A.chatStream("x"); A.chatEnd("x"); A.chatError("x");

console.log("ALL JS SMOKE TESTS PASSED");
