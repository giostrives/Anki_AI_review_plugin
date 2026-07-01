"""Provider plumbing (providers.py): .env handling, dispatch, request
building, and stream parsing — all offline, with fakes for requests."""
import json

import pytest


@pytest.fixture
def env_in_tmp(providers_mod, tmp_path, monkeypatch):
    """Point the module's .env location at a temp dir and keep the process
    env var out of the way."""
    monkeypatch.setattr(providers_mod, "_addon_dir", str(tmp_path))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return tmp_path


class TestEnvFile:
    def test_missing_env_file_means_no_key(self, providers_mod, env_in_tmp):
        assert providers_mod.gemini_api_key() == ""

    def test_set_then_read_key(self, providers_mod, env_in_tmp):
        providers_mod.set_gemini_api_key("secret-123")
        assert providers_mod.gemini_api_key() == "secret-123"

    def test_set_preserves_other_lines_and_comments(self, providers_mod, env_in_tmp):
        (env_in_tmp / ".env").write_text(
            "# my settings\nOTHER=keep me\nGEMINI_API_KEY=old\n", encoding="utf-8")
        providers_mod.set_gemini_api_key("new")
        content = (env_in_tmp / ".env").read_text(encoding="utf-8")
        assert "# my settings" in content
        assert "OTHER=keep me" in content
        assert content.count("GEMINI_API_KEY") == 1
        assert providers_mod.gemini_api_key() == "new"

    def test_delete_clears_key(self, providers_mod, env_in_tmp):
        providers_mod.set_gemini_api_key("secret")
        providers_mod.delete_gemini_api_key()
        assert providers_mod.gemini_api_key() == ""

    def test_quotes_are_stripped(self, providers_mod, env_in_tmp):
        (env_in_tmp / ".env").write_text('GEMINI_API_KEY="quoted"\n', encoding="utf-8")
        assert providers_mod.gemini_api_key() == "quoted"

    def test_env_var_fallback(self, providers_mod, env_in_tmp, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "from-env")
        assert providers_mod.gemini_api_key() == "from-env"


class TestDispatch:
    def test_chat_routes_by_provider(self, providers_mod, monkeypatch):
        calls = []
        monkeypatch.setattr(providers_mod, "_chat_ollama",
                            lambda *a: calls.append("ollama"))
        monkeypatch.setattr(providers_mod, "_chat_gemini",
                            lambda *a: calls.append("gemini"))
        providers_mod.chat_llm({"provider": "gemini"}, "sys", [])
        providers_mod.chat_llm({"provider": "ollama"}, "sys", [])
        providers_mod.chat_llm({}, "sys", [])  # default is ollama
        assert calls == ["gemini", "ollama", "ollama"]

    def test_stream_routes_by_provider(self, providers_mod, monkeypatch):
        calls = []
        monkeypatch.setattr(providers_mod, "_stream_ollama",
                            lambda *a: calls.append("ollama"))
        monkeypatch.setattr(providers_mod, "_stream_gemini",
                            lambda *a: calls.append("gemini"))
        providers_mod.stream_llm({"provider": "gemini"}, "s", [], None)
        providers_mod.stream_llm({}, "s", [], None)
        assert calls == ["gemini", "ollama"]

    def test_gemini_without_key_raises_friendly_error(self, providers_mod, env_in_tmp):
        with pytest.raises(RuntimeError, match="No Gemini API key"):
            providers_mod._chat_gemini({}, "sys", [])


class FakeResponse:
    """Stand-in for requests.Response, usable as a context manager."""

    def __init__(self, status_code=200, json_body=None, lines=None):
        self.status_code = status_code
        self._json = json_body
        self._lines = lines or []
        self.text = json.dumps(json_body) if json_body else ""

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestGeminiRequest:
    def test_roles_and_system_instruction(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_gemini_api_key("k")
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured.update(url=url, headers=headers, body=json)
            return FakeResponse(200, {
                "candidates": [{"content": {"parts": [{"text": "ok!"}]}}]})

        monkeypatch.setattr(providers_mod.requests, "post", fake_post)
        reply = providers_mod._chat_gemini(
            {"gemini": {"model": "gemini-3.5-flash"}},
            "be nice",
            [{"role": "user", "content": "hola"},
             {"role": "assistant", "content": "¡hola!"},
             {"role": "user", "content": "¿qué tal?"}],
        )
        assert reply == "ok!"
        assert "gemini-3.5-flash:generateContent" in captured["url"]
        assert captured["headers"]["x-goog-api-key"] == "k"
        # Anthropic-style "assistant" must become Gemini's "model" role.
        assert [c["role"] for c in captured["body"]["contents"]] == ["user", "model", "user"]
        assert captured["body"]["systemInstruction"]["parts"][0]["text"] == "be nice"

    def test_api_error_surfaces_google_message(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_gemini_api_key("k")
        monkeypatch.setattr(
            providers_mod.requests, "post",
            lambda *a, **kw: FakeResponse(429, {"error": {"message": "quota exceeded"}}))
        with pytest.raises(RuntimeError, match=r"429.*quota exceeded"):
            providers_mod._chat_gemini({}, None, [])


class TestStreamParsing:
    def test_ollama_stream_accumulates_deltas(self, providers_mod, monkeypatch):
        lines = [
            json.dumps({"message": {"content": "Hola"}}).encode(),
            b"",  # keep-alive blank line is skipped
            b"not json",  # junk line is skipped
            json.dumps({"message": {"content": " mundo"}}).encode(),
            json.dumps({"done": True}).encode(),
            json.dumps({"message": {"content": "IGNORED"}}).encode(),  # after done
        ]
        monkeypatch.setattr(providers_mod.requests, "post",
                            lambda *a, **kw: FakeResponse(200, lines=lines))
        chunks = []
        reply = providers_mod._stream_ollama({}, "sys", [], chunks.append)
        assert reply == "Hola mundo"
        assert chunks == ["Hola", " mundo"]

    def test_gemini_sse_stream(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_gemini_api_key("k")
        def sse(text):
            return ("data: " + json.dumps(
                {"candidates": [{"content": {"parts": [{"text": text}]}}]})).encode()
        lines = [sse("Ho"), b": comment", sse("la"), b"data: [DONE]"]
        monkeypatch.setattr(providers_mod.requests, "post",
                            lambda *a, **kw: FakeResponse(200, lines=lines))
        chunks = []
        reply = providers_mod._stream_gemini({}, None, [], chunks.append)
        assert reply == "Hola"
        assert chunks == ["Ho", "la"]

    def test_gemini_empty_stream_raises(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_gemini_api_key("k")
        monkeypatch.setattr(providers_mod.requests, "post",
                            lambda *a, **kw: FakeResponse(200, lines=[]))
        with pytest.raises(RuntimeError, match="no usable text"):
            providers_mod._stream_gemini({}, None, [], lambda d: None)
