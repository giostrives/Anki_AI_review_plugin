"""
LLM provider abstraction.

Supports three backends, selected by config["provider"]:
  - "ollama": local Ollama server via its /api/chat HTTP endpoint.
  - "gemini": Google Gemini via its REST v1beta generateContent endpoint.
  - "nvidia": NVIDIA API Catalog via its OpenAI-compatible chat/completions
    endpoint.

All backends talk plain HTTP through `requests` (which Anki bundles), so the
add-on needs no extra Python packages. Cloud API keys are read from the
add-on's `.env` file (git-ignored).

Optional fallback: config["fallback_providers"] lists providers to try, in
order, when the primary one fails (see `stream_llm_with_fallback`).
"""
import json
import os

import requests

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


def gemini_api_key():
    """Return the Gemini API key from `.env` (or env var), or empty string."""
    return _get_api_key("GEMINI_API_KEY")


def set_gemini_api_key(key):
    return _set_api_key("GEMINI_API_KEY", key)


def delete_gemini_api_key():
    """Remove the stored Gemini API key (clears it in `.env`)."""
    set_gemini_api_key("")


def nvidia_api_key():
    """Return the NVIDIA API key from `.env` (or env var), or empty string."""
    return _get_api_key("NVIDIA_API_KEY")


def set_nvidia_api_key(key):
    return _set_api_key("NVIDIA_API_KEY", key)


def delete_nvidia_api_key():
    """Remove the stored NVIDIA API key (clears it in `.env`)."""
    set_nvidia_api_key("")


def chat_llm(config, system, messages):
    """Send a multi-turn conversation to the configured provider and return the
    model's reply.

    `messages` is a list of {"role": "user"|"assistant", "content": str} in
    chronological order. Both providers are stateless, so the whole history is
    resent on every call.

    Raises RuntimeError with a user-friendly message on configuration or
    connectivity problems.
    """
    provider = config.get("provider", "ollama")
    if provider == "gemini":
        return _chat_gemini(config, system, messages)
    if provider == "nvidia":
        return _chat_nvidia(config, system, messages)
    return _chat_ollama(config, system, messages)


def call_llm(config, system, prompt):
    """Single-turn convenience wrapper around `chat_llm`."""
    return chat_llm(config, system, [{"role": "user", "content": prompt}])


def stream_llm(config, system, messages, on_chunk):
    """Stream a conversation from the configured provider.

    `on_chunk(delta)` is called with each text fragment as it arrives. Returns
    the full accumulated reply once the stream completes. The callback runs on
    the calling (worker) thread; the caller marshals UI updates to the main
    thread.
    """
    provider = config.get("provider", "ollama")
    if provider == "gemini":
        return _stream_gemini(config, system, messages, on_chunk)
    if provider == "nvidia":
        return _stream_nvidia(config, system, messages, on_chunk)
    return _stream_ollama(config, system, messages, on_chunk)


def _provider_chain(config):
    """Ordered provider names to try: the primary, then configured fallbacks.

    Unknown names and duplicates are dropped, so a hand-edited config can't
    break the chain.
    """
    known = ("ollama", "gemini", "nvidia")
    chain = [config.get("provider", "ollama")]
    for name in config.get("fallback_providers", []):
        if name in known and name not in chain:
            chain.append(name)
    return chain


def _is_configured(name):
    """Whether a provider is worth trying at all (cloud ones need a key).

    Ollama always qualifies: it needs no key, and an unreachable server is a
    runtime failure the chain already handles by moving on.
    """
    if name == "gemini":
        return bool(gemini_api_key())
    if name == "nvidia":
        return bool(nvidia_api_key())
    return True


def chat_llm_with_fallback(config, system, messages):
    """Like `chat_llm`, but on failure tries the configured fallback providers
    in order. Returns (reply, provider_used) so the caller can tell the user
    when someone other than the primary answered.
    """
    return _run_with_fallback(
        config,
        lambda cfg: chat_llm(cfg, system, messages),
    )


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


def _run_with_fallback(config, call, midstream=None):
    """Try `call` once per provider in the chain; return (result, name)."""
    chain = _provider_chain(config)
    if len(chain) == 1:
        # No fallbacks configured: behave exactly like the single-provider
        # path, including its error messages.
        return call(config), chain[0]
    failures = []
    for name in chain:
        if not _is_configured(name):
            failures.append(f"{name}: no API key configured")
            continue
        try:
            return call({**config, "provider": name}), name
        except Exception as e:
            if midstream is not None and midstream[0]:
                # Partial output already reached the UI; don't replay it.
                raise
            failures.append(f"{name}: {e}")
    raise RuntimeError("All providers failed:\n" + "\n".join(failures))


def _chat_ollama(config, system, messages):
    # Backward compatible: fall back to the old flat config keys.
    ollama = config.get("ollama", {})
    endpoint = ollama.get("endpoint") or config.get("ollama_endpoint", "http://localhost:11434")
    model = ollama.get("model") or config.get("model", "gemma3")

    chat_messages = []
    if system:
        chat_messages.append({"role": "system", "content": system})
    chat_messages.extend(messages)

    try:
        response = requests.post(
            f"{endpoint}/api/chat",
            json={
                "model": model,
                "messages": chat_messages,
                "stream": False,
            },
            timeout=120,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Could not connect to Ollama at {endpoint}.\n"
            "Make sure it is running with: ollama serve"
        )
    except Exception as e:
        raise RuntimeError(f"Ollama error: {e}")


def _chat_gemini(config, system, messages):
    api_key = gemini_api_key()
    if not api_key:
        raise RuntimeError(
            "No Gemini API key found. Set it in the AI Reviewer settings "
            "(or add GEMINI_API_KEY to the add-on's .env file)."
        )

    model = config.get("gemini", {}).get("model", "gemini-3.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    # Gemini uses "model" for the assistant role; the system text goes separately.
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

    try:
        response = requests.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=120,
        )
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Could not reach the Gemini API. Check your internet connection.")

    if response.status_code != 200:
        # Surface Google's error message when present.
        try:
            detail = response.json().get("error", {}).get("message", "")
        except Exception:
            detail = response.text[:200]
        raise RuntimeError(f"Gemini API error ({response.status_code}): {detail}")

    data = response.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        # e.g. blocked by a safety filter, or an empty candidate.
        raise RuntimeError(f"Gemini returned no usable text. Response: {str(data)[:200]}")


def _stream_ollama(config, system, messages, on_chunk):
    ollama = config.get("ollama", {})
    endpoint = ollama.get("endpoint") or config.get("ollama_endpoint", "http://localhost:11434")
    model = ollama.get("model") or config.get("model", "gemma3")

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
            f"Could not connect to Ollama at {endpoint}.\n"
            "Make sure it is running with: ollama serve"
        )
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Ollama error: {e}")
    return "".join(parts)


def _stream_gemini(config, system, messages, on_chunk):
    api_key = gemini_api_key()
    if not api_key:
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
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
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
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
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


NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"


def _nvidia_request_parts(config, system, messages, stream):
    """Shared auth check + OpenAI-style request body for the NVIDIA API."""
    api_key = nvidia_api_key()
    if not api_key:
        raise RuntimeError(
            "No NVIDIA API key found. Set it in the AI Reviewer settings "
            "(or add NVIDIA_API_KEY to the add-on's .env file)."
        )

    model = config.get("nvidia", {}).get("model", "deepseek-ai/deepseek-v4-flash")

    chat_messages = []
    if system:
        chat_messages.append({"role": "system", "content": system})
    chat_messages.extend(messages)

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": chat_messages,
        "temperature": 1,
        "top_p": 0.95,
        "max_tokens": 16384,
        # Ask DeepSeek-style models to skip chain-of-thought output.
        "chat_template_kwargs": {"thinking": False},
        "stream": stream,
    }
    return headers, body


def _nvidia_error(response):
    try:
        detail = response.json().get("error", {}).get("message", "")
    except Exception:
        detail = response.text[:200]
    return RuntimeError(f"NVIDIA API error ({response.status_code}): {detail}")


def _chat_nvidia(config, system, messages):
    headers, body = _nvidia_request_parts(config, system, messages, stream=False)

    try:
        response = requests.post(NVIDIA_URL, headers=headers, json=body, timeout=120)
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Could not reach the NVIDIA API. Check your internet connection.")

    if response.status_code != 200:
        raise _nvidia_error(response)

    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"NVIDIA returned no usable text. Response: {str(data)[:200]}")


def _stream_nvidia(config, system, messages, on_chunk):
    headers, body = _nvidia_request_parts(config, system, messages, stream=True)

    parts = []
    try:
        with requests.post(
            NVIDIA_URL,
            headers=headers,
            json=body,
            stream=True,
            timeout=120,
        ) as response:
            if response.status_code != 200:
                raise _nvidia_error(response)
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
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                try:
                    delta = obj["choices"][0].get("delta", {}).get("content", "")
                except (KeyError, IndexError, TypeError):
                    continue
                if delta:
                    parts.append(delta)
                    on_chunk(delta)
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Could not reach the NVIDIA API. Check your internet connection.")
    if not parts:
        raise RuntimeError("NVIDIA returned no usable text.")
    return "".join(parts)
