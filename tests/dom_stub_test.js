/*
 * Smoke test for web/ai_review.js against a minimal DOM stub.
 * Run directly (`node tests/dom_stub_test.js`) or via pytest (test_panel_js.py).
 * Exits non-zero on the first failed assertion.
 */
"use strict";

class Element {
    constructor(tag) {
        this.tagName = tag; this.children = []; this.dataset = {};
        this.textContent = ""; this.hidden = false; this.disabled = false;
        this._classes = new Set(); this.id = ""; this.parent = null;
        this.value = ""; this.placeholder = ""; this.type = "";
        this.listeners = {};
    }
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

// --- chat expand + send + streaming bubble ---
A.expandChat();
const chatInput = root.find(e => e._classes.has("air-chat-input"));
chatInput.value = "why está?";
root.find(e => e._classes.has("air-chat-send")).click();
assert(sent[1] === "aiReview::chat::why está?", "chat cmd: " + sent[1]);
assert(root.find(e => e._classes.has("air-bubble-user")).textContent === "why está?", "user bubble");
A.chatStart(); A.chatStream("Bec"); A.chatEnd("Because states use estar.");
assert(root.find(e => e._classes.has("air-bubble-ai")).textContent === "Because states use estar.",
    "ai bubble finalized");
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
