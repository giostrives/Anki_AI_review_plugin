"""
LLM provider abstraction.

Supports two backends, selected by config["provider"]:
  - "ollama": local Ollama server via its /api/chat HTTP endpoint.
  - "gemini": Google Gemini via its REST v1beta generateContent endpoint.

Both backends talk plain HTTP through `requests` (which Anki bundles), so the
add-on needs no extra Python packages. The Gemini API key is read from the
add-on's `.env` file (git-ignored).
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


def gemini_api_key():
    """Return the Gemini API key from `.env` (or env var), or empty string."""
    return load_env().get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")


def set_gemini_api_key(key):
    """Write GEMINI_API_KEY into the add-on's `.env`, preserving other lines.

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
                        and stripped.split("=", 1)[0].strip() == "GEMINI_API_KEY"):
                    lines.append(f"GEMINI_API_KEY={key}\n")
                    found = True
                else:
                    lines.append(raw if raw.endswith("\n") else raw + "\n")
    except FileNotFoundError:
        pass
    if not found:
        lines.append(f"GEMINI_API_KEY={key}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def delete_gemini_api_key():
    """Remove the stored Gemini API key (clears it in `.env`)."""
    set_gemini_api_key("")


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
    return _stream_ollama(config, system, messages, on_chunk)


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
