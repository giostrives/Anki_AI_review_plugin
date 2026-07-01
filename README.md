# Anki AI Review Plugin

An Anki add-on that turns passive flashcard reviews into active practice. For decks you
opt in, instead of just flipping a card you're asked to **use the word in a sentence** (or
translate it), and a Large Language Model evaluates your answer and gives feedback before
you grade the card.

It's an experiment in using LLMs as a lightweight language tutor inside your normal review
flow.

## What it does

- **Intercepts reviews** for decks you enable. The card's first field is the prompt word;
  you write a sentence using it in the deck's target language (or translate it).
- **Two review styles**, switchable per card:
  - **Full review** — a detailed evaluation: a star score, grammar corrections, fluency
    notes, and an example sentence.
  - **Quick review** — a fast pass/fail that just checks whether you used the word / gave
    the correct translation, with one line of feedback.
- **Follow-up chat** — after the feedback you can keep the conversation going ("so *el sol
  es caliente* but *el pan está caliente*, right?") for as many turns as you like, with
  either backend.
- **Per-deck configuration** — each deck has its own source/target language pair, user
  level, and default review style.
- **Two AI backends** — run locally with **Ollama**, or use **Google Gemini**.
- **Conversation log** — every review (and its follow-ups) is saved to a per-session file,
  for your own history/analysis.
- **Native look** — the card renders exactly as Anki would show it (your card template,
  styling, and images), with the AI panel beneath it. Two themes:
  - **Native (default)** — built entirely on Anki's own theme variables, so it matches
    whatever Anki theme you use, light or dark, and follows night-mode switches live.
  - **Polished** — a custom slate-and-indigo design with its own light and dark variants.
- Grading stays 100% native: after the feedback the card flips and you use Anki's normal
  Again/Hard/Good/Easy buttons.

## Requirements

- **Anki 2.1.55 or newer** (the add-on's Native theme uses Anki's built-in CSS theme
  variables, introduced in 2.1.55). Developed and tested against **Anki / aqt 25.9.x**
  (the versions pinned in `requirements.txt`).
- **Python 3.11** for the local dev setup (see below).
- An AI backend — **one** of:
  - **Ollama** (recommended to start): a local LLM server. No API key, nothing leaves your
    machine. Install from [ollama.com](https://ollama.com), then pull a model, e.g.
    `ollama pull gemma3`.
  - **Google Gemini**: just a `GEMINI_API_KEY`
    (free key from [Google AI Studio](https://aistudio.google.com/apikey)). No extra Python
    package needed — the add-on calls Gemini's REST API over `requests`, which Anki bundles.

Python dependencies are listed in `requirements.txt`: `anki`, `aqt`, `requests`, and
`Jinja2`. Both AI backends (Ollama and Gemini) talk plain HTTP, so there's nothing else to
install.

## Installation

### Option A — Run from this repo (development)

This repo includes `runanki.py`, which launches Anki from a local virtual environment, so
the add-on and its dependencies all live in that venv.

```bash
# from the repo root
python3.11 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python runanki.py
```

Anki starts with the add-on loaded.

### Option B — Install into a normal Anki desktop app

Copy this folder into your Anki add-ons directory (`Tools → Add-ons → View Files`,
then drop the folder alongside the others) and restart Anki. Both backends work out of the
box — they only use `requests`, which Anki already bundles, so there are no extra packages
to install or vendor.

## Configuration

Open **Tools → AI Reviewer Settings**.

1. **Appearance** — pick the panel theme: **Native (match Anki)** or **Polished**.
   Takes effect on the next card.
2. **AI Provider** — choose **Ollama** or **Gemini**.
3. **Ollama Settings** — endpoint (default `http://localhost:11434`) and model
   (default `gemma3`).
4. **Gemini Settings** — model (default `gemini-3.5-flash`) and your API key. The key is
   stored in the add-on's `.env` file, can be deleted with the **Delete API Key** button,
   and is removed automatically when you uninstall the add-on. It is never written into
   Anki's synced config. (You can also set it manually in `.env` as `GEMINI_API_KEY=...`.)
5. **Deck Configuration** — pick a deck and set:
   - **Source / Target language** (the language pair for that deck)
   - **First field holds** — whether the note's *first* field is the **word being
     learned** (e.g. "comprometido" first, default) or its **meaning/translation**
     (e.g. "committed" first). The plugin uses this to word the exercise correctly and
     to tell the model which word is which. Reversed cards ("Basic (and reversed)")
     are detected automatically from the card template, and the instruction never
     reveals the hidden side.
   - **User level** (Beginner / Intermediate / Advanced)
   - **Default Review** (Full or Quick)
   - **AI Review** (Enabled / Disabled) — only enabled decks are intercepted.

   Click **Save Deck Config** per deck, then **Save All**.

## Usage

Review an enabled deck as usual. Each card shows normally, with the AI panel underneath:

1. Optionally flip the **Quick review / Full review** toggle for this card.
2. Type your answer and press **Submit** (Enter sends; Shift+Enter adds a newline).
3. Read the verdict and feedback. The card flips to its back automatically. You can now
   **ask follow-up questions** via the "Ask a follow-up" button — the model keeps the
   card's context for the whole exchange.
4. Grade the card with Anki's normal Again/Hard/Good/Easy buttons whenever you're done.

Revealing the answer *before* submitting (the "Show answer instead" button, Anki's own
Show Answer button, or the keyboard) skips the AI review for that card — you only get
the LLM's feedback if you try first.

Decks that aren't enabled in the settings behave like normal Anki cards.

## Running the tests

```bash
./venv/bin/python -m pytest
```

The suite under `tests/` covers the feedback parsing, card direction / field-layout
detection (including reversed cards), deck-config matching, conversation logging, and
the provider plumbing (`.env` handling, request building, stream parsing — all offline
with fakes). If Node.js is installed, it also smoke-tests the review panel's
JavaScript against a DOM stub; without Node that one test is skipped.

## Conversation history

Every card review is saved as one conversation. All conversations from a single review
session are appended to one JSONL file (one line per card) under
`user_files/conversations/` (override with `conversations_dir` in the config). The data
isn't used by the add-on yet — it's there for your own history, analysis, or export.

## Project layout

| File | Purpose |
|------|---------|
| `__init__.py` | Add-on entry point; menu + hooks |
| `reviewer.py` | Intercepts reviews, renders the UI, parses feedback, drives the chat |
| `providers.py` | Backend abstraction (Ollama / Gemini), multi-turn chat, `.env` key handling |
| `conversations.py` | Saves each conversation to the per-session JSONL log |
| `config_dialog.py` | The settings dialog |
| `prompts/` | Jinja2 prompt templates (`language_card.j2` = full, `quick_card.j2` = quick, `system_prompt.j2`) |
| `web/` | The review panel's JS (`ai_review.js`) and themes (`ai_review.css`, `ai_review_polished.css`) |
| `tests/` | Pytest suite (plus the Node-based JS panel test) |
| `config.json` | Default configuration |
| `.env` | Holds `GEMINI_API_KEY` (git-ignored) |