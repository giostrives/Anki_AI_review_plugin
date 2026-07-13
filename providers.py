"""
LLM provider abstraction.

Supports several backends, selected by config["provider"]:
  - "ollama": the Local LLM provider — any server speaking the Ollama
    /api/chat protocol, at a configurable endpoint (defaults to a local
    Ollama install).
  - "gemini": Google Gemini via its REST v1beta streamGenerateContent endpoint.
  - "nvidia": NVIDIA API Catalog via its OpenAI-compatible chat/completions
    endpoint.
  - "cerebras": Cerebras Inference via its OpenAI-compatible chat/completions
    endpoint.
  - "openai": OpenAI chat/completions.
  - "xai": xAI (Grok) chat/completions.
  - "anthropic": Anthropic (Claude) via its Messages REST API.
  - "custom": any user-configured OpenAI-compatible chat/completions endpoint.

NVIDIA, Cerebras, OpenAI, xAI and "custom" share one OpenAI-compatible client
(see `_openai_compat_*`).

All backends talk plain HTTP through `requests` (which Anki bundles), so the
add-on needs no extra Python packages. Cloud API keys are read from the
add-on's `.env` file (git-ignored).

Optional fallback: config["fallback_providers"] lists providers to try, in
order, when the primary one fails (see `stream_llm_with_fallback`).
"""
import json
import os

import requests

from .provider_models import PROVIDERS as _KNOWN_PROVIDERS

_addon_dir = os.path.dirname(os.path.abspath(__file__))


def load_env():
    """Parse the add-on's `.env` file into a dict (KEY=VALUE per line).

    Kept dependency-free on purpose: Anki ships its own Python and adding
    `python-dotenv` would mean another package to vendor.
    """
    env = {}
    path = os.path.join(_addon_dir, ".env")
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


def _get_api_key(name):
    """Return the named API key from `.env` (or env var), or empty string."""
    return load_env().get(name) or os.environ.get(name, "")


def _set_api_key(name, key):
    """Write the named key into the add-on's `.env`, preserving other lines.

    Pass an empty string to clear the key. The `.env` file lives inside the
    add-on folder, so it is removed automatically when the add-on is uninstalled.
    """
    path = os.path.join(_addon_dir, ".env")
    lines = []
    found = False
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                stripped = raw.strip()
                if (not stripped.startswith("#")
                        and stripped.split("=", 1)[0].strip() == name):
                    lines.append(f"{name}={key}\n")
                    found = True
                else:
                    lines.append(raw if raw.endswith("\n") else raw + "\n")
    except FileNotFoundError:
        pass
    if not found:
        lines.append(f"{name}={key}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def api_key(provider):
    """Return `provider`'s API key from `.env` (or env var), or empty string.

    Optional for the "custom" provider: a self-hosted OpenAI-compatible
    endpoint may require no auth at all.
    """
    return _get_api_key(f"{provider.upper()}_API_KEY")


def set_api_key(provider, key):
    return _set_api_key(f"{provider.upper()}_API_KEY", key)


def delete_api_key(provider):
    """Remove the stored API key for `provider` (clears it in `.env`)."""
    set_api_key(provider, "")


# Dispatch table for stream_llm. Values are thin lambdas (rather than the
# `_stream_*` functions themselves) so that monkeypatching a `_stream_*` name
# on the module — as the tests do — is honored: each lambda looks the name up
# fresh at call time instead of freezing the function object at import time.
_STREAMERS = {
    "gemini": lambda *a: _stream_gemini(*a),
    "nvidia": lambda *a: _stream_nvidia(*a),
    "cerebras": lambda *a: _stream_cerebras(*a),
    "openai": lambda *a: _stream_openai(*a),
    "xai": lambda *a: _stream_xai(*a),
    "anthropic": lambda *a: _stream_anthropic(*a),
    "custom": lambda *a: _stream_custom(*a),
}


def stream_llm(config, system, messages, on_chunk):
    """Stream a conversation from the configured provider.

    `on_chunk(delta)` is called with each text fragment as it arrives. Returns
    the full accumulated reply once the stream completes. The callback runs on
    the calling (worker) thread; the caller marshals UI updates to the main
    thread.
    """
    provider = config.get("provider", "ollama")
    streamer = _STREAMERS.get(provider, lambda *a: _stream_ollama(*a))
    return streamer(config, system, messages, on_chunk)


def _provider_chain(config):
    """Ordered provider names to try: the primary, then configured fallbacks.

    Unknown names and duplicates are dropped, so a hand-edited config can't
    break the chain.
    """
    known = _KNOWN_PROVIDERS
    chain = [config.get("provider", "ollama")]
    for name in config.get("fallback_providers", []):
        if name in known and name not in chain:
            chain.append(name)
    return chain


def _is_configured(name, config):
    """Whether a provider is worth trying at all (cloud ones need a key).

    Ollama always qualifies: it needs no key, and an unreachable server is a
    runtime failure the chain already handles by moving on. Custom needs a
    base URL but no key (auth is optional for self-hosted servers).
    """
    if name == "ollama":
        return True
    if name == "custom":
        return bool((config.get("custom", {}).get("endpoint") or "").strip())
    return bool(api_key(name))


def stream_llm_with_fallback(config, system, messages, on_chunk):
    """Like `stream_llm`, but with the fallback chain. Returns
    (reply, provider_used).

    A provider that fails AFTER streaming chunks is not fallen back on: the
    caller has already shown that text, and a second provider would replay it.
    In practice almost every failure (missing key, connection refused, 4xx)
    happens before the first chunk.
    """
    emitted = [False]

    def counting_chunk(delta):
        emitted[0] = True
        on_chunk(delta)

    def call(cfg):
        emitted[0] = False
        return stream_llm(cfg, system, messages, counting_chunk)

    return _run_with_fallback(config, call, midstream=emitted)


def _run_with_fallback(config, call, midstream):
    """Try `call` once per provider in the chain; return (result, name)."""
    chain = _provider_chain(config)
    if len(chain) == 1:
        # No fallbacks configured: behave exactly like the single-provider
        # path, including its error messages.
        return call(config), chain[0]
    failures = []
    for name in chain:
        if not _is_configured(name, config):
            failures.append(f"{name}: no API key configured")
            continue
        try:
            return call({**config, "provider": name}), name
        except Exception as e:
            if midstream[0]:
                # Partial output already reached the UI; don't replay it.
                raise
            failures.append(f"{name}: {e}")
    raise RuntimeError("All providers failed:\n" + "\n".join(failures))


def _stream_ollama(config, system, messages, on_chunk):
    ollama = config.get("ollama", {})
    endpoint = ollama.get("endpoint") or config.get("ollama_endpoint", "http://localhost:11434")
    model = ollama.get("model") or config.get("model", "gemma4")

    chat_messages = []
    if system:
        chat_messages.append({"role": "system", "content": system})
    chat_messages.extend(messages)

    parts = []
    try:
        with requests.post(
            f"{endpoint}/api/chat",
            json={"model": model, "messages": chat_messages, "stream": True},
            stream=True,
            timeout=120,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                delta = obj.get("message", {}).get("content", "")
                if delta:
                    parts.append(delta)
                    on_chunk(delta)
                if obj.get("done"):
                    break
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Could not connect to the local LLM server at {endpoint} — is it "
            "running? (for Ollama: ollama serve)"
        )
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Local LLM error: {e}")
    return "".join(parts)


def _sse_json(response):
    """Yield parsed JSON objects from a `data:` Server-Sent-Events stream.

    Shared by Gemini, the OpenAI-compatible providers, and Anthropic — they
    all send SSE with a JSON payload per event. Skips blank/keep-alive lines,
    non-`data:` lines (e.g. `: comment`), and the `[DONE]` sentinel some APIs
    use to close the stream (Anthropic has no such sentinel, but checking for
    it anyway is harmless there).
    """
    for raw in response.iter_lines():
        if not raw:
            continue
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


def _stream_gemini(config, system, messages, on_chunk):
    key = api_key("gemini")
    if not key:
        raise RuntimeError(
            "No Gemini API key found. Set it in the AI Reviewer settings "
            "(or add GEMINI_API_KEY to the add-on's .env file)."
        )

    model = config.get("gemini", {}).get("model", "gemini-3.5-flash")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:streamGenerateContent?alt=sse")

    contents = [
        {
            "role": "model" if m["role"] == "assistant" else "user",
            "parts": [{"text": m["content"]}],
        }
        for m in messages
    ]
    body = {"contents": contents}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    parts = []
    try:
        with requests.post(
            url,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=body,
            stream=True,
            timeout=120,
        ) as response:
            if response.status_code != 200:
                try:
                    detail = response.json().get("error", {}).get("message", "")
                except Exception:
                    detail = response.text[:200]
                raise RuntimeError(f"Gemini API error ({response.status_code}): {detail}")
            for obj in _sse_json(response):
                try:
                    delta = obj["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError):
                    continue
                if delta:
                    parts.append(delta)
                    on_chunk(delta)
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Could not reach the Gemini API. Check your internet connection.")
    if not parts:
        raise RuntimeError("Gemini returned no usable text.")
    return "".join(parts)


# --- Shared OpenAI-compatible chat/completions client ------------------------
#
# NVIDIA, Cerebras, OpenAI, xAI and the "custom" provider all speak the same
# OpenAI /chat/completions dialect (Bearer auth, a `messages` array, and either
# a JSON reply or an SSE `data:` stream of choice deltas). These helpers hold
# that one implementation; each provider only supplies its URL, resolved key,
# display name (used in error messages) and any extra body fields.


def _openai_compat_request_parts(api_key, model, system, messages, stream,
                                 extra_body=None):
    """Build the headers + OpenAI-style body for a chat/completions request.

    `api_key` is already resolved; when it is empty no Authorization header is
    sent (self-hosted custom endpoints may not require auth).
    """
    chat_messages = []
    if system:
        chat_messages.append({"role": "system", "content": system})
    chat_messages.extend(messages)

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = {"model": model, "messages": chat_messages, "stream": stream}
    if extra_body:
        body.update(extra_body)
    return headers, body


def _openai_compat_error(response, name):
    try:
        detail = response.json().get("error", {}).get("message", "")
    except Exception:
        detail = response.text[:200]
    return RuntimeError(f"{name} API error ({response.status_code}): {detail}")


def _openai_compat_stream(url, headers, body, name, on_chunk):
    """Streaming chat/completions call against an OpenAI-compatible API."""
    parts = []
    try:
        with requests.post(
            url,
            headers=headers,
            json=body,
            stream=True,
            timeout=120,
        ) as response:
            if response.status_code != 200:
                raise _openai_compat_error(response, name)
            for obj in _sse_json(response):
                try:
                    delta = obj["choices"][0].get("delta", {}).get("content", "")
                except (KeyError, IndexError, TypeError):
                    continue
                if delta:
                    parts.append(delta)
                    on_chunk(delta)
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Could not reach the {name} API. Check your internet connection.")
    if not parts:
        raise RuntimeError(f"{name} returned no usable text.")
    return "".join(parts)


# Table of OpenAI-compatible cloud providers: (url, default model, display
# name for error messages, extra body fields beyond model/messages/stream).
_OPENAI_COMPAT = {
    "nvidia": (
        "https://integrate.api.nvidia.com/v1/chat/completions",
        "deepseek-ai/deepseek-v4-flash",
        "NVIDIA",
        {
            "temperature": 1,
            "top_p": 0.95,
            "max_tokens": 16384,
            # Ask DeepSeek-style models to skip chain-of-thought output.
            "chat_template_kwargs": {"thinking": False},
        },
    ),
    "cerebras": ("https://api.cerebras.ai/v1/chat/completions",
                 "gpt-oss-120b", "Cerebras", None),
    "openai": ("https://api.openai.com/v1/chat/completions",
               "gpt-5.1", "OpenAI", None),
    "xai": ("https://api.x.ai/v1/chat/completions", "grok-4", "xAI", None),
}


def _stream_openai_compat(name, config, system, messages, on_chunk):
    """Auth check, request build, and stream for a provider in `_OPENAI_COMPAT`."""
    url, default_model, display, extra_body = _OPENAI_COMPAT[name]
    key = api_key(name)
    if not key:
        raise RuntimeError(
            f"No {display} API key found. Set it in the AI Reviewer settings "
            f"(or add {name.upper()}_API_KEY to the add-on's .env file)."
        )
    model = config.get(name, {}).get("model", default_model)
    headers, body = _openai_compat_request_parts(
        key, model, system, messages, stream=True, extra_body=extra_body)
    return _openai_compat_stream(url, headers, body, display, on_chunk)


def _stream_nvidia(config, system, messages, on_chunk):
    return _stream_openai_compat("nvidia", config, system, messages, on_chunk)


def _stream_cerebras(config, system, messages, on_chunk):
    return _stream_openai_compat("cerebras", config, system, messages, on_chunk)


def _stream_openai(config, system, messages, on_chunk):
    return _stream_openai_compat("openai", config, system, messages, on_chunk)


def _stream_xai(config, system, messages, on_chunk):
    return _stream_openai_compat("xai", config, system, messages, on_chunk)


# --- Anthropic (Claude) -------------------------------------------------------
#
# Anthropic speaks its own Messages API, not the OpenAI dialect: auth is an
# `x-api-key` header (plus a pinned `anthropic-version`), the system prompt is
# a top-level field, `max_tokens` is required, the reply comes as a list of
# typed content blocks, and streaming is SSE with `content_block_delta`
# events. Error bodies use the same {"error": {"message": ...}} envelope as
# the OpenAI-compatible providers, so `_openai_compat_error` is reused.

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def _anthropic_request_parts(config, system, messages, stream):
    """Shared auth check + Messages API request body for the Anthropic API."""
    key = api_key("anthropic")
    if not key:
        raise RuntimeError(
            "No Anthropic API key found. Set it in the AI Reviewer settings "
            "(or add ANTHROPIC_API_KEY to the add-on's .env file)."
        )

    model = config.get("anthropic", {}).get("model", "claude-haiku-4-5")
    headers = {
        "Content-Type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": model,
        "max_tokens": 16384,
        "messages": messages,
        "stream": stream,
    }
    if system:
        body["system"] = system
    return headers, body


def _stream_anthropic(config, system, messages, on_chunk):
    headers, body = _anthropic_request_parts(config, system, messages, stream=True)
    parts = []
    try:
        with requests.post(
            ANTHROPIC_URL,
            headers=headers,
            json=body,
            stream=True,
            timeout=120,
        ) as response:
            if response.status_code != 200:
                raise _openai_compat_error(response, "Anthropic")
            for obj in _sse_json(response):
                kind = obj.get("type")
                if kind == "content_block_delta":
                    delta_obj = obj.get("delta", {})
                    if delta_obj.get("type") == "text_delta":
                        delta = delta_obj.get("text", "")
                        if delta:
                            parts.append(delta)
                            on_chunk(delta)
                elif kind == "error":
                    detail = obj.get("error", {}).get("message", "")
                    raise RuntimeError(f"Anthropic API error: {detail}")
                elif kind == "message_stop":
                    break
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            "Could not reach the Anthropic API. Check your internet connection.")
    if not parts:
        raise RuntimeError("Anthropic returned no usable text.")
    return "".join(parts)


def _custom_url(config):
    """Full chat/completions URL from the user's base endpoint.

    Tolerates a trailing slash and an endpoint that already points at
    /chat/completions. Empty endpoint -> a friendly configuration error.
    """
    endpoint = (config.get("custom", {}).get("endpoint") or "").strip()
    if not endpoint:
        raise RuntimeError(
            "No Custom endpoint set. Set the base URL (e.g. "
            "https://api.example.com/v1) in the AI Reviewer settings."
        )
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/chat/completions"):
        return endpoint
    return endpoint + "/chat/completions"


def _custom_request_parts(config, system, messages, stream):
    """Request parts for a user-configured OpenAI-compatible endpoint.

    The API key is optional: a self-hosted server may need no auth. The model
    has no default, so an empty one is a friendly configuration error.
    """
    model = (config.get("custom", {}).get("model") or "").strip()
    if not model:
        raise RuntimeError(
            "No Custom model set. Set the model name in the AI Reviewer settings."
        )
    key = api_key("custom")
    return _openai_compat_request_parts(key, model, system, messages, stream)


def _stream_custom(config, system, messages, on_chunk):
    url = _custom_url(config)
    headers, body = _custom_request_parts(config, system, messages, stream=True)
    return _openai_compat_stream(url, headers, body, "Custom", on_chunk)
