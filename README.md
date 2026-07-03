# AI Language Tutor

An Anki add-on that turns passive flashcard reviews into active practice. In decks where
you enable it, instead of just flipping a card you're asked to **use the word in a
sentence** (or translate it), and a language model checks your answer and gives feedback
before you grade the card.

## What it does

- **Intercepts reviews** for decks you enable. The card renders exactly as Anki normally
  shows it (your template, styling, images), with the AI panel underneath.
- **Two review styles**, switchable per card:
  - **Full review** — a star score, grammar corrections, fluency notes, and an example
    sentence.
  - **Quick review** — a fast pass/fail with one line of feedback.
- **Follow-up chat** — after the feedback you can ask questions about the card for as many
  turns as you like.
- **Per-deck configuration** — each deck has its own language pair, user level, and
  default review style. Decks you don't enable behave like normal Anki cards.
- **Four AI providers** — a local **Ollama** server, **Google Gemini**, the
  **NVIDIA API catalog**, or **Cerebras**. Optionally, if your chosen provider fails
  (server down, quota exceeded), the add-on can fall back to the others and tell you it
  did so.
- Grading stays native: after the feedback the card flips and you use Anki's normal
  Again/Hard/Good/Easy buttons.

## What you need

- **Anki 2.1.55 or newer** (tested against Anki 25.9.x).
- **One AI provider:**
  - **Ollama** — a local LLM server. No API key, no account, nothing leaves your machine.
    Install from [ollama.com](https://ollama.com) and pull a model
    (e.g. `ollama pull gemma4`).
  - **Google Gemini** — needs an API key from
    [Google AI Studio](https://aistudio.google.com/apikey).
  - **NVIDIA API catalog** — needs an API key from
    [build.nvidia.com](https://build.nvidia.com); serves open models such as DeepSeek,
    Gemma, and GPT-OSS.
  - **Cerebras** — needs an API key from [cloud.cerebras.ai](https://cloud.cerebras.ai);
    serves GPT-OSS and Gemma at very high inference speed.

  All cloud providers have a free tier, which is generally enough for personal review
  sessions. No extra Python packages are needed for any provider — everything runs over
  plain HTTP with libraries Anki already bundles.

## Installation

Copy this folder into your Anki add-ons directory (`Tools → Add-ons → View Files`, drop
the folder alongside the others) and restart Anki.

## Configuration

Open **Tools → AI Language Tutor Settings** (or the **Config** button in Anki's
Add-ons manager). The dialog has two tabs.

### AI Providers tab

1. **Use** — pick **Ollama**, **Gemini**, **NVIDIA**, or **Cerebras**.
2. **If it fails, try** — **Nothing** (default) or **All other providers**. With fallback
   on, a failing provider is silently replaced by the next one that is configured
   (providers without an API key are skipped), and a small notice tells you which one
   actually answered.
3. **Ollama Settings** — endpoint (default `http://localhost:11434`) and model
   (default `gemma4`).
4. **Gemini Settings** — model (default `gemini-3.5-flash`) and your API key.
5. **NVIDIA Settings** — model (default `deepseek-ai/deepseek-v4-flash`) and your API key.
6. **Cerebras Settings** — model (default `gpt-oss-120b`) and your API key.

Each **Model** field is a dropdown seeded with known-good models for that provider, but
it's editable — click in and type any model name the provider supports, not just the
listed ones.

API keys are stored in the add-on's local `.env` file — never in Anki's synced
configuration. Each can be removed with its **Delete API Key** button, and the file is
deleted along with the add-on if you uninstall it.

### Decks & General tab

1. **Appearance** — the panel theme. **Native** (default) is built on Anki's own theme
   variables, so it matches your Anki theme, light or dark, and follows night-mode
   switches live. **Polished** is a custom design with its own light and dark variants.
2. **Logging** — off by default. When enabled, saves each review conversation to a local
   file (see [Conversation history](#conversation-history)).
3. **Deck Configuration** — select a deck and set:
   - **Source / Target language** — the language pair for that deck.
   - **First field holds** — whether the note's *first* field is the **word being
     learned** (default) or its **meaning/translation**. This tells the exercise and the
     model which word is which. Reversed cards ("Basic (and reversed)") are detected
     automatically, and the instruction never reveals the hidden side.
   - **User level** (Beginner / Intermediate / Advanced)
   - **Default Review** (Full or Quick)
   - **AI Review** (Enabled / Disabled) — only enabled decks are intercepted.
   - **Apply to subdecks** — extend this config to the deck's subdecks.

   Click **Save Deck Config** per deck, then **Save All**.

## Usage

Review an enabled deck as usual:

1. Optionally flip the **Quick review / Full review** toggle for this card.
2. Type your answer and press **Submit** (Enter sends; Shift+Enter adds a newline).
3. Read the feedback. The card flips to its back automatically, and you can ask follow-up
   questions — the model keeps the card's context for the whole exchange.
4. Grade the card with Anki's normal buttons whenever you're done.

Revealing the answer *before* submitting (in any way — the panel's "Show answer instead"
button, Anki's Show Answer, or the keyboard) skips the AI review for that card: you only
get feedback if you try first.

---

## Technical details

The rest of this document is for people who want to run the add-on from source, hack on
it, or understand how it works.

### Development setup

The repo includes `runanki.py`, which launches Anki from a local virtual environment so
the add-on and its dependencies live in that venv. Requires **Python 3.11**.

```bash
# from the repo root
python3.11 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python runanki.py
```

Dependencies (`requirements.txt`): `anki`, `aqt`, `requests`, `Jinja2`.

### Providers

All four providers are plain-HTTP clients in `providers.py`, built on `requests` (which
Anki bundles) — deliberately no vendor SDKs, since Anki's launcher can prune its Python
environment on update, which makes vendored compiled packages fragile:

- **Ollama** — `/api/chat`, streaming via NDJSON.
- **Gemini** — REST `v1beta` `generateContent` / `streamGenerateContent` (SSE).
- **NVIDIA** — the OpenAI-compatible `/v1/chat/completions` endpoint at
  `integrate.api.nvidia.com` (SSE streaming). Requests ask DeepSeek-style models to skip
  chain-of-thought (`chat_template_kwargs: {"thinking": false}`); any `<think>` output
  that arrives anyway is stripped before display.
- **Cerebras** — the OpenAI-compatible `/v1/chat/completions` endpoint at
  `api.cerebras.ai` (SSE streaming).

Provider errors are normalized to `RuntimeError` with user-readable messages.
`provider_models.py` is the single source of truth for the provider list, display labels,
and each provider's dropdown of known models — both `providers.py` and
`config_dialog.py` import it, so adding a provider or a model only means editing that one
file plus the corresponding backend functions.

**Fallback** is implemented by `stream_llm_with_fallback` / `chat_llm_with_fallback`,
which try `config["provider"]` and then each entry of `config["fallback_providers"]` in
order, skipping cloud providers without an API key, and return the reply together with
the name of the provider that produced it. One deliberate rule: a provider that fails
*mid-stream*, after text already reached the panel, is not fallen back on — a second
provider would replay text you already saw. The settings dialog writes the fallback list
as "all other providers", but it's an ordinary ordered list in the config, so you can
reorder or trim it in Anki's add-on config editor.

`config.json` holds the shipped defaults; user settings are stored by Anki in
`meta.json` and overlaid per top-level key. API keys live in the add-on's `.env`
(`GEMINI_API_KEY`, `NVIDIA_API_KEY`, `CEREBRAS_API_KEY`), which is git-ignored and
removed on uninstall.

### Panel architecture

The review panel (`web/ai_review.js`) is mounted as a sibling of Anki's `#qa` container,
so the card template renders untouched above it and the panel survives the
question→answer transition. All dynamic text crosses the Python↔JS boundary through
`json.dumps` and lands via `textContent` — never `innerHTML`. LLM calls run in a
background thread via Anki's task manager; streamed chunks are marshalled back to the
main thread for live display. Prompts are Jinja2 templates under `prompts/`.

### Running the tests

```bash
./venv/bin/python -m pytest
```

The suite under `tests/` covers feedback parsing, card direction / field-layout detection
(including reversed cards), deck-config matching, conversation logging, and the provider
plumbing (`.env` handling, request building, stream parsing, fallback chain — all offline
with fakes). If Node.js is installed, it also smoke-tests the review panel's JavaScript
against a DOM stub; without Node that test is skipped.

### Conversation history

When logging is enabled, every card review is saved as one conversation. All
conversations from a single review session are appended to one JSONL file (one line per
card) under `user_files/conversations/` (override with `conversations_dir` in the
config), including which provider and model answered. The data isn't used by the add-on
— it's there for your own history, analysis, or export.
