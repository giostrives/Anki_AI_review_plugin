**Prefer the settings dialog** (Tools → AI Language Tutor Settings), it edits
everything below safely. This raw editor is only for hand-tweaking.

**API keys are NOT stored here.** They live in the add-on's private `.env`
file (deleted when you uninstall the add-on) and are set from the settings
dialog. Adding a key to this JSON does nothing.

- `provider`: which backend answers reviews — `ollama`, `gemini`, `nvidia`,
  `cerebras`, `openai`, `xai`, `anthropic`, or `custom`.
- `fallback_providers`: list of providers to try, in order, when the primary
  one fails. Providers without an API key are skipped.
- `ollama`: Local LLM provider (Ollama by default). `endpoint` is the local
  server's base URL (default `http://localhost:11434`); any server speaking
  Ollama's `/api/chat` protocol works. Also specify `model`.
- `gemini` / `nvidia` / `cerebras` / `openai` / `xai` / `anthropic`: `model`
  to use.
- `custom`: any OpenAI-compatible chat/completions API — `endpoint` is the
  base URL (e.g. `https://api.example.com/v1`) and `model` the model name.
  Compatibility with third-party APIs is not guaranteed.
- `theme`: review panel look — `native` (match Anki) or `polished`.
- `logging_enabled` / `conversations_dir`: save per-session conversations and
  an `errors.log` to a folder (blank = the add-on's `user_files`).
- `deck_configs`: per-deck settings (source/target language — the language
  you speak / the language you're learning; level, review mode, whether the
  first field holds the word being learned or its meaning, `include_subdecks`).
  Easiest to edit from the dialog's deck list.
