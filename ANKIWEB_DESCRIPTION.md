<!--
Paste the text BELOW this comment into the "Description" field when you upload
the add-on at https://ankiweb.net/shared/addons/ . AnkiWeb renders it as
Markdown. This file is NOT part of the packaged add-on (build_ankiaddon.sh
excludes it) — it lives in the repo only.
-->

# Anki AI Review

Turn passive flashcard reviews into active practice. For decks you opt in,
instead of just flipping a card you're asked to **use the word in a sentence**
(or translate it), and a language model checks your answer and gives feedback
**before** you grade the card. Decks you don't enable behave like normal Anki.

## Features

- **Two review styles**, switchable per card:
  - **Full review** — a star score, grammar corrections, fluency notes, and an
    example sentence.
  - **Quick review** — a fast pass/fail with one line of feedback.
- **Follow-up chat** — after the feedback you can keep asking questions about
  the card; the model remembers its context for the whole exchange.
- **Per-deck setup** — each deck has its own language pair, level, and default
  review style.
- **Native card + native grading** — your card renders exactly as usual (your
  template, styling, images) with the AI panel underneath, and you still grade
  with Anki's normal Again/Hard/Good/Easy buttons.
- **Four AI providers** with optional automatic fallback — a local **Ollama**
  server, **Google Gemini**, the **NVIDIA API catalog**, or **Cerebras**.

## Requirements

- **Anki 2.1.55 or newer** (tested on 25.9.x).
- **One AI provider** (each cloud option has a free tier that is generally
  enough for personal review sessions):
  - **Ollama** — a local LLM server. No API key, nothing leaves your machine.
    Install from ollama.com and pull a model (e.g. `ollama pull gemma4`).
  - **Google Gemini** — API key from Google AI Studio.
  - **NVIDIA API catalog** — API key from build.nvidia.com (DeepSeek, Gemma,
    GPT-OSS, …).
  - **Cerebras** — API key from cloud.cerebras.ai (very fast GPT-OSS / Gemma).

No extra Python packages are needed — everything runs over plain HTTP with
libraries Anki already bundles.

## Configuration

Open **Tools → AI Reviewer Settings**. Two tabs:

**AI Providers** — pick the provider to **Use**, optionally turn on
**If it fails, try → All other providers**, and enter the model + API key for
each provider you want. Every model field is a dropdown of known-good models but
is editable — type any model the provider supports. API keys are stored in the
add-on's local `.env` file, never in Anki's synced config, and are removed if
you uninstall.

**Decks & General** — choose the panel **Appearance** (Native follows your Anki
theme; Polished is a custom design), toggle optional **Logging**, then per deck
set:

- **Source / Target language** for that deck.
- **First field holds** — whether the note's first field is the word being
  learned (default) or its meaning. Reversed cards are detected automatically.
- **User level** (Beginner / Intermediate / Advanced).
- **Default Review** (Full or Quick).
- **AI Review** (Enabled / Disabled) — only enabled decks are intercepted.
- **Apply to subdecks** — optional.

Click **Save Deck Config** per deck, then **Save All**.

## Usage

Review an enabled deck as usual:

1. Optionally flip the **Quick / Full** toggle for this card.
2. Type your answer and press **Submit** (Enter sends; Shift+Enter for a
   newline).
3. Read the feedback — the card flips to its back automatically, and you can ask
   follow-up questions.
4. Grade the card with Anki's normal buttons when you're done.

Revealing the answer *before* submitting (the panel's "Show answer instead"
button, Anki's Show Answer, or the keyboard) skips the AI review for that card —
you only get feedback if you try first.

---

Source, issues, and full documentation: <add your GitHub/homepage URL here>
