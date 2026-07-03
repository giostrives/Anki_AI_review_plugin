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
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
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

    def test_nvidia_key_roundtrip(self, providers_mod, env_in_tmp):
        providers_mod.set_nvidia_api_key("nv-123")
        assert providers_mod.nvidia_api_key() == "nv-123"
        providers_mod.delete_nvidia_api_key()
        assert providers_mod.nvidia_api_key() == ""

    def test_keys_coexist_in_one_env_file(self, providers_mod, env_in_tmp):
        providers_mod.set_gemini_api_key("gm")
        providers_mod.set_nvidia_api_key("nv")
        assert providers_mod.gemini_api_key() == "gm"
        assert providers_mod.nvidia_api_key() == "nv"
        # Rewriting one key must not clobber the other.
        providers_mod.set_gemini_api_key("gm2")
        assert providers_mod.nvidia_api_key() == "nv"

    def test_cerebras_key_roundtrip(self, providers_mod, env_in_tmp):
        providers_mod.set_cerebras_api_key("cb-123")
        assert providers_mod.cerebras_api_key() == "cb-123"
        providers_mod.delete_cerebras_api_key()
        assert providers_mod.cerebras_api_key() == ""


class TestDispatch:
    def test_chat_routes_by_provider(self, providers_mod, monkeypatch):
        calls = []
        monkeypatch.setattr(providers_mod, "_chat_ollama",
                            lambda *a: calls.append("ollama"))
        monkeypatch.setattr(providers_mod, "_chat_gemini",
                            lambda *a: calls.append("gemini"))
        monkeypatch.setattr(providers_mod, "_chat_nvidia",
                            lambda *a: calls.append("nvidia"))
        monkeypatch.setattr(providers_mod, "_chat_cerebras",
                            lambda *a: calls.append("cerebras"))
        providers_mod.chat_llm({"provider": "gemini"}, "sys", [])
        providers_mod.chat_llm({"provider": "ollama"}, "sys", [])
        providers_mod.chat_llm({"provider": "nvidia"}, "sys", [])
        providers_mod.chat_llm({"provider": "cerebras"}, "sys", [])
        providers_mod.chat_llm({}, "sys", [])  # default is ollama
        assert calls == ["gemini", "ollama", "nvidia", "cerebras", "ollama"]

    def test_stream_routes_by_provider(self, providers_mod, monkeypatch):
        calls = []
        monkeypatch.setattr(providers_mod, "_stream_ollama",
                            lambda *a: calls.append("ollama"))
        monkeypatch.setattr(providers_mod, "_stream_gemini",
                            lambda *a: calls.append("gemini"))
        monkeypatch.setattr(providers_mod, "_stream_nvidia",
                            lambda *a: calls.append("nvidia"))
        monkeypatch.setattr(providers_mod, "_stream_cerebras",
                            lambda *a: calls.append("cerebras"))
        providers_mod.stream_llm({"provider": "gemini"}, "s", [], None)
        providers_mod.stream_llm({"provider": "nvidia"}, "s", [], None)
        providers_mod.stream_llm({"provider": "cerebras"}, "s", [], None)
        providers_mod.stream_llm({}, "s", [], None)
        assert calls == ["gemini", "nvidia", "cerebras", "ollama"]

    def test_gemini_without_key_raises_friendly_error(self, providers_mod, env_in_tmp):
        with pytest.raises(RuntimeError, match="No Gemini API key"):
            providers_mod._chat_gemini({}, "sys", [])

    def test_nvidia_without_key_raises_friendly_error(self, providers_mod, env_in_tmp):
        with pytest.raises(RuntimeError, match="No NVIDIA API key"):
            providers_mod._chat_nvidia({}, "sys", [])

    def test_cerebras_without_key_raises_friendly_error(self, providers_mod, env_in_tmp):
        with pytest.raises(RuntimeError, match="No Cerebras API key"):
            providers_mod._chat_cerebras({}, "sys", [])


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


class TestNvidiaRequest:
    def test_request_shape_and_reply(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_nvidia_api_key("nv-k")
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured.update(url=url, headers=headers, body=json)
            return FakeResponse(200, {
                "choices": [{"message": {"role": "assistant", "content": "ok!"}}]})

        monkeypatch.setattr(providers_mod.requests, "post", fake_post)
        reply = providers_mod._chat_nvidia(
            {}, "be nice", [{"role": "user", "content": "hola"}])
        assert reply == "ok!"
        assert captured["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer nv-k"
        body = captured["body"]
        assert body["model"] == "deepseek-ai/deepseek-v4-flash"  # default
        assert body["temperature"] == 1
        assert body["top_p"] == 0.95
        assert body["max_tokens"] == 16384
        assert body["chat_template_kwargs"] == {"thinking": False}
        assert body["stream"] is False
        # System prompt travels as a leading OpenAI-style system message.
        assert body["messages"][0] == {"role": "system", "content": "be nice"}
        assert body["messages"][1]["content"] == "hola"

    def test_model_from_config(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_nvidia_api_key("k")
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured.update(body=json)
            return FakeResponse(200, {"choices": [{"message": {"content": "x"}}]})

        monkeypatch.setattr(providers_mod.requests, "post", fake_post)
        providers_mod._chat_nvidia(
            {"nvidia": {"model": "meta/llama-4"}}, None, [])
        assert captured["body"]["model"] == "meta/llama-4"

    def test_api_error_surfaces_message(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_nvidia_api_key("k")
        monkeypatch.setattr(
            providers_mod.requests, "post",
            lambda *a, **kw: FakeResponse(402, {"error": {"message": "out of credits"}}))
        with pytest.raises(RuntimeError, match=r"402.*out of credits"):
            providers_mod._chat_nvidia({}, None, [])

    def test_malformed_body_raises(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_nvidia_api_key("k")
        monkeypatch.setattr(providers_mod.requests, "post",
                            lambda *a, **kw: FakeResponse(200, {"choices": []}))
        with pytest.raises(RuntimeError, match="no usable text"):
            providers_mod._chat_nvidia({}, None, [])

    def test_sse_stream(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_nvidia_api_key("k")

        def sse(delta):
            return ("data: " + json.dumps({"choices": [{"delta": delta}]})).encode()

        lines = [
            sse({"role": "assistant"}),  # role-only first event, no content
            sse({"content": "Ho"}),
            b": comment",
            sse({"content": "la"}),
            b"data: [DONE]",
        ]
        monkeypatch.setattr(providers_mod.requests, "post",
                            lambda *a, **kw: FakeResponse(200, lines=lines))
        chunks = []
        reply = providers_mod._stream_nvidia({}, None, [], chunks.append)
        assert reply == "Hola"
        assert chunks == ["Ho", "la"]

    def test_empty_stream_raises(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_nvidia_api_key("k")
        monkeypatch.setattr(providers_mod.requests, "post",
                            lambda *a, **kw: FakeResponse(200, lines=[b"data: [DONE]"]))
        with pytest.raises(RuntimeError, match="no usable text"):
            providers_mod._stream_nvidia({}, None, [], lambda d: None)


class TestCerebrasRequest:
    def test_request_shape_and_reply(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_cerebras_api_key("cb-k")
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured.update(url=url, headers=headers, body=json)
            return FakeResponse(200, {
                "choices": [{"message": {"role": "assistant", "content": "ok!"}}]})

        monkeypatch.setattr(providers_mod.requests, "post", fake_post)
        reply = providers_mod._chat_cerebras(
            {}, "be nice", [{"role": "user", "content": "hola"}])
        assert reply == "ok!"
        assert captured["url"] == "https://api.cerebras.ai/v1/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer cb-k"
        body = captured["body"]
        assert body["model"] == "gpt-oss-120b"  # default
        assert body["stream"] is False
        # System prompt travels as a leading OpenAI-style system message.
        assert body["messages"][0] == {"role": "system", "content": "be nice"}
        assert body["messages"][1]["content"] == "hola"

    def test_model_from_config(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_cerebras_api_key("k")
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured.update(body=json)
            return FakeResponse(200, {"choices": [{"message": {"content": "x"}}]})

        monkeypatch.setattr(providers_mod.requests, "post", fake_post)
        providers_mod._chat_cerebras(
            {"cerebras": {"model": "gemma-4-31b"}}, None, [])
        assert captured["body"]["model"] == "gemma-4-31b"

    def test_api_error_surfaces_message(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_cerebras_api_key("k")
        monkeypatch.setattr(
            providers_mod.requests, "post",
            lambda *a, **kw: FakeResponse(402, {"error": {"message": "out of credits"}}))
        with pytest.raises(RuntimeError, match=r"402.*out of credits"):
            providers_mod._chat_cerebras({}, None, [])

    def test_malformed_body_raises(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_cerebras_api_key("k")
        monkeypatch.setattr(providers_mod.requests, "post",
                            lambda *a, **kw: FakeResponse(200, {"choices": []}))
        with pytest.raises(RuntimeError, match="no usable text"):
            providers_mod._chat_cerebras({}, None, [])

    def test_sse_stream(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_cerebras_api_key("k")

        def sse(delta):
            return ("data: " + json.dumps({"choices": [{"delta": delta}]})).encode()

        lines = [
            sse({"role": "assistant"}),  # role-only first event, no content
            sse({"content": "Ho"}),
            b": comment",
            sse({"content": "la"}),
            b"data: [DONE]",
        ]
        monkeypatch.setattr(providers_mod.requests, "post",
                            lambda *a, **kw: FakeResponse(200, lines=lines))
        chunks = []
        reply = providers_mod._stream_cerebras({}, None, [], chunks.append)
        assert reply == "Hola"
        assert chunks == ["Ho", "la"]

    def test_empty_stream_raises(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_cerebras_api_key("k")
        monkeypatch.setattr(providers_mod.requests, "post",
                            lambda *a, **kw: FakeResponse(200, lines=[b"data: [DONE]"]))
        with pytest.raises(RuntimeError, match="no usable text"):
            providers_mod._stream_cerebras({}, None, [], lambda d: None)


class TestProviderModels:
    def test_every_provider_has_labels_and_models(self, provider_models_mod):
        for provider in provider_models_mod.PROVIDERS:
            assert provider in provider_models_mod.PROVIDER_LABELS
            assert provider_models_mod.PROVIDER_LABELS[provider]
            assert provider in provider_models_mod.MODEL_OPTIONS
            assert len(provider_models_mod.MODEL_OPTIONS[provider]) > 0

    def test_cerebras_is_registered(self, provider_models_mod):
        assert "cerebras" in provider_models_mod.PROVIDERS


class TestFallback:
    def test_chain_is_primary_plus_fallbacks_deduped(self, providers_mod):
        cfg = {"provider": "gemini",
               "fallback_providers": ["gemini", "bogus", "ollama", "nvidia", "ollama", "cerebras"]}
        assert providers_mod._provider_chain(cfg) == ["gemini", "ollama", "nvidia", "cerebras"]

    def test_chain_without_fallbacks_is_primary_only(self, providers_mod):
        assert providers_mod._provider_chain({}) == ["ollama"]

    def test_no_fallback_passes_error_through_verbatim(self, providers_mod, monkeypatch):
        def boom(*a):
            raise RuntimeError("original ollama error")
        monkeypatch.setattr(providers_mod, "_chat_ollama", boom)
        with pytest.raises(RuntimeError, match="^original ollama error$"):
            providers_mod.chat_llm_with_fallback({}, "s", [])

    def test_falls_back_to_next_provider(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_gemini_api_key("k")

        def boom(*a):
            raise RuntimeError("gemini down")
        monkeypatch.setattr(providers_mod, "_chat_gemini", boom)
        monkeypatch.setattr(providers_mod, "_chat_ollama", lambda *a: "saved!")
        cfg = {"provider": "gemini", "fallback_providers": ["ollama"]}
        assert providers_mod.chat_llm_with_fallback(cfg, "s", []) == ("saved!", "ollama")

    def test_primary_success_reports_primary(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_gemini_api_key("k")
        monkeypatch.setattr(providers_mod, "_chat_gemini", lambda *a: "hi")
        cfg = {"provider": "gemini", "fallback_providers": ["ollama"]}
        assert providers_mod.chat_llm_with_fallback(cfg, "s", []) == ("hi", "gemini")

    def test_unconfigured_provider_is_skipped(self, providers_mod, env_in_tmp, monkeypatch):
        # No NVIDIA key set: its backend must not even be called.
        def never(*a):
            raise AssertionError("nvidia backend called without a key")
        monkeypatch.setattr(providers_mod, "_chat_nvidia", never)

        def boom(*a):
            raise RuntimeError("ollama down")
        monkeypatch.setattr(providers_mod, "_chat_ollama", boom)
        providers_mod.set_gemini_api_key("k")
        monkeypatch.setattr(providers_mod, "_chat_gemini", lambda *a: "hi")
        cfg = {"provider": "ollama", "fallback_providers": ["nvidia", "gemini"]}
        assert providers_mod.chat_llm_with_fallback(cfg, "s", []) == ("hi", "gemini")

    def test_all_fail_aggregates_errors(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_gemini_api_key("k")

        def boom(msg):
            def f(*a):
                raise RuntimeError(msg)
            return f
        monkeypatch.setattr(providers_mod, "_chat_ollama", boom("no server"))
        monkeypatch.setattr(providers_mod, "_chat_gemini", boom("quota"))
        cfg = {"provider": "ollama", "fallback_providers": ["gemini", "nvidia"]}
        with pytest.raises(RuntimeError) as exc:
            providers_mod.chat_llm_with_fallback(cfg, "s", [])
        text = str(exc.value)
        assert "All providers failed" in text
        assert "ollama: no server" in text
        assert "gemini: quota" in text
        assert "nvidia: no API key configured" in text

    def test_stream_falls_back_when_nothing_streamed(self, providers_mod, monkeypatch):
        def boom(*a):
            raise RuntimeError("dead before first byte")
        monkeypatch.setattr(providers_mod, "_stream_ollama", boom)

        def ok(config, system, messages, on_chunk):
            on_chunk("Ho")
            on_chunk("la")
            return "Hola"
        monkeypatch.setattr(providers_mod, "_stream_gemini", ok)
        monkeypatch.setattr(providers_mod, "gemini_api_key", lambda: "k")
        cfg = {"provider": "ollama", "fallback_providers": ["gemini"]}
        chunks = []
        result = providers_mod.stream_llm_with_fallback(cfg, "s", [], chunks.append)
        assert result == ("Hola", "gemini")
        assert chunks == ["Ho", "la"]

    def test_stream_midstream_failure_does_not_fall_back(self, providers_mod, monkeypatch):
        # The user already saw partial text: falling back would replay it.
        def half_then_die(config, system, messages, on_chunk):
            on_chunk("partial ")
            raise RuntimeError("connection reset")
        monkeypatch.setattr(providers_mod, "_stream_ollama", half_then_die)

        def never(*a):
            raise AssertionError("fallback must not run after mid-stream output")
        monkeypatch.setattr(providers_mod, "_stream_gemini", never)
        monkeypatch.setattr(providers_mod, "gemini_api_key", lambda: "k")
        cfg = {"provider": "ollama", "fallback_providers": ["gemini"]}
        with pytest.raises(RuntimeError, match="connection reset"):
            providers_mod.stream_llm_with_fallback(cfg, "s", [], lambda d: None)
