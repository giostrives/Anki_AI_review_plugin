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
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)
    return tmp_path


class TestEnvFile:
    def test_missing_env_file_means_no_key(self, providers_mod, env_in_tmp):
        assert providers_mod.api_key("gemini") == ""

    @pytest.mark.parametrize("provider", [
        "gemini", "nvidia", "cerebras", "openai", "xai", "anthropic", "custom"])
    def test_key_roundtrip(self, providers_mod, env_in_tmp, provider):
        providers_mod.set_api_key(provider, "secret-123")
        assert providers_mod.api_key(provider) == "secret-123"
        providers_mod.delete_api_key(provider)
        assert providers_mod.api_key(provider) == ""

    def test_set_preserves_other_lines_and_comments(self, providers_mod, env_in_tmp):
        (env_in_tmp / ".env").write_text(
            "# my settings\nOTHER=keep me\nGEMINI_API_KEY=old\n", encoding="utf-8")
        providers_mod.set_api_key("gemini", "new")
        content = (env_in_tmp / ".env").read_text(encoding="utf-8")
        assert "# my settings" in content
        assert "OTHER=keep me" in content
        assert content.count("GEMINI_API_KEY") == 1
        assert providers_mod.api_key("gemini") == "new"

    def test_quotes_are_stripped(self, providers_mod, env_in_tmp):
        (env_in_tmp / ".env").write_text('GEMINI_API_KEY="quoted"\n', encoding="utf-8")
        assert providers_mod.api_key("gemini") == "quoted"

    def test_env_var_fallback(self, providers_mod, env_in_tmp, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "from-env")
        assert providers_mod.api_key("gemini") == "from-env"

    def test_keys_coexist_in_one_env_file(self, providers_mod, env_in_tmp):
        providers_mod.set_api_key("gemini", "gm")
        providers_mod.set_api_key("nvidia", "nv")
        assert providers_mod.api_key("gemini") == "gm"
        assert providers_mod.api_key("nvidia") == "nv"
        # Rewriting one key must not clobber the other.
        providers_mod.set_api_key("gemini", "gm2")
        assert providers_mod.api_key("nvidia") == "nv"


class TestDispatch:
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
        monkeypatch.setattr(providers_mod, "_stream_openai",
                            lambda *a: calls.append("openai"))
        monkeypatch.setattr(providers_mod, "_stream_xai",
                            lambda *a: calls.append("xai"))
        monkeypatch.setattr(providers_mod, "_stream_anthropic",
                            lambda *a: calls.append("anthropic"))
        monkeypatch.setattr(providers_mod, "_stream_custom",
                            lambda *a: calls.append("custom"))
        providers_mod.stream_llm({"provider": "gemini"}, "s", [], None)
        providers_mod.stream_llm({"provider": "nvidia"}, "s", [], None)
        providers_mod.stream_llm({"provider": "cerebras"}, "s", [], None)
        providers_mod.stream_llm({"provider": "openai"}, "s", [], None)
        providers_mod.stream_llm({"provider": "xai"}, "s", [], None)
        providers_mod.stream_llm({"provider": "anthropic"}, "s", [], None)
        providers_mod.stream_llm({"provider": "custom"}, "s", [], None)
        providers_mod.stream_llm({}, "s", [], None)
        assert calls == ["gemini", "nvidia", "cerebras",
                         "openai", "xai", "anthropic", "custom", "ollama"]

    def test_gemini_without_key_raises_friendly_error(self, providers_mod, env_in_tmp):
        with pytest.raises(RuntimeError, match="No Gemini API key"):
            providers_mod._stream_gemini({}, "sys", [], lambda d: None)

    def test_nvidia_without_key_raises_friendly_error(self, providers_mod, env_in_tmp):
        with pytest.raises(RuntimeError, match="No NVIDIA API key"):
            providers_mod._stream_nvidia({}, "sys", [], lambda d: None)

    def test_cerebras_without_key_raises_friendly_error(self, providers_mod, env_in_tmp):
        with pytest.raises(RuntimeError, match="No Cerebras API key"):
            providers_mod._stream_cerebras({}, "sys", [], lambda d: None)


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
        providers_mod.set_api_key("gemini", "k")
        captured = {}
        sse_line = ("data: " + json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "ok!"}]}}]})).encode()

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured.update(url=url, headers=headers, body=json)
            return FakeResponse(200, lines=[sse_line])

        monkeypatch.setattr(providers_mod.requests, "post", fake_post)
        chunks = []
        reply = providers_mod._stream_gemini(
            {"gemini": {"model": "gemini-3.5-flash"}},
            "be nice",
            [{"role": "user", "content": "hola"},
             {"role": "assistant", "content": "¡hola!"},
             {"role": "user", "content": "¿qué tal?"}],
            chunks.append,
        )
        assert reply == "ok!"
        assert "gemini-3.5-flash:streamGenerateContent" in captured["url"]
        assert captured["headers"]["x-goog-api-key"] == "k"
        # Anthropic-style "assistant" must become Gemini's "model" role.
        assert [c["role"] for c in captured["body"]["contents"]] == ["user", "model", "user"]
        assert captured["body"]["systemInstruction"]["parts"][0]["text"] == "be nice"

    def test_api_error_surfaces_google_message(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("gemini", "k")
        monkeypatch.setattr(
            providers_mod.requests, "post",
            lambda *a, **kw: FakeResponse(429, {"error": {"message": "quota exceeded"}}))
        with pytest.raises(RuntimeError, match=r"429.*quota exceeded"):
            providers_mod._stream_gemini({}, None, [], lambda d: None)


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
        providers_mod.set_api_key("gemini", "k")
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
        providers_mod.set_api_key("gemini", "k")
        monkeypatch.setattr(providers_mod.requests, "post",
                            lambda *a, **kw: FakeResponse(200, lines=[]))
        with pytest.raises(RuntimeError, match="no usable text"):
            providers_mod._stream_gemini({}, None, [], lambda d: None)


class TestNvidiaRequest:
    def test_request_shape_and_reply(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("nvidia", "nv-k")
        captured = {}
        sse_line = ("data: " + json.dumps({"choices": [{"delta": {"content": "ok!"}}]})).encode()

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured.update(url=url, headers=headers, body=json)
            return FakeResponse(200, lines=[sse_line])

        monkeypatch.setattr(providers_mod.requests, "post", fake_post)
        chunks = []
        reply = providers_mod._stream_nvidia(
            {}, "be nice", [{"role": "user", "content": "hola"}], chunks.append)
        assert reply == "ok!"
        assert captured["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer nv-k"
        body = captured["body"]
        assert body["model"] == "deepseek-ai/deepseek-v4-flash"  # default
        assert body["temperature"] == 1
        assert body["top_p"] == 0.95
        assert body["max_tokens"] == 16384
        assert body["chat_template_kwargs"] == {"thinking": False}
        assert body["stream"] is True
        # System prompt travels as a leading OpenAI-style system message.
        assert body["messages"][0] == {"role": "system", "content": "be nice"}
        assert body["messages"][1]["content"] == "hola"

    def test_model_from_config(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("nvidia", "k")
        captured = {}
        sse_line = ("data: " + json.dumps({"choices": [{"delta": {"content": "x"}}]})).encode()

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured.update(body=json)
            return FakeResponse(200, lines=[sse_line])

        monkeypatch.setattr(providers_mod.requests, "post", fake_post)
        providers_mod._stream_nvidia(
            {"nvidia": {"model": "meta/llama-4"}}, None, [], lambda d: None)
        assert captured["body"]["model"] == "meta/llama-4"

    def test_api_error_surfaces_message(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("nvidia", "k")
        monkeypatch.setattr(
            providers_mod.requests, "post",
            lambda *a, **kw: FakeResponse(402, {"error": {"message": "out of credits"}}))
        with pytest.raises(RuntimeError, match=r"402.*out of credits"):
            providers_mod._stream_nvidia({}, None, [], lambda d: None)

    def test_sse_stream(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("nvidia", "k")

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
        providers_mod.set_api_key("nvidia", "k")
        monkeypatch.setattr(providers_mod.requests, "post",
                            lambda *a, **kw: FakeResponse(200, lines=[b"data: [DONE]"]))
        with pytest.raises(RuntimeError, match="no usable text"):
            providers_mod._stream_nvidia({}, None, [], lambda d: None)


class TestCerebrasRequest:
    def test_request_shape_and_reply(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("cerebras", "cb-k")
        captured = {}
        sse_line = ("data: " + json.dumps({"choices": [{"delta": {"content": "ok!"}}]})).encode()

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured.update(url=url, headers=headers, body=json)
            return FakeResponse(200, lines=[sse_line])

        monkeypatch.setattr(providers_mod.requests, "post", fake_post)
        chunks = []
        reply = providers_mod._stream_cerebras(
            {}, "be nice", [{"role": "user", "content": "hola"}], chunks.append)
        assert reply == "ok!"
        assert captured["url"] == "https://api.cerebras.ai/v1/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer cb-k"
        body = captured["body"]
        assert body["model"] == "gpt-oss-120b"  # default
        assert body["stream"] is True
        # System prompt travels as a leading OpenAI-style system message.
        assert body["messages"][0] == {"role": "system", "content": "be nice"}
        assert body["messages"][1]["content"] == "hola"

    def test_model_from_config(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("cerebras", "k")
        captured = {}
        sse_line = ("data: " + json.dumps({"choices": [{"delta": {"content": "x"}}]})).encode()

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured.update(body=json)
            return FakeResponse(200, lines=[sse_line])

        monkeypatch.setattr(providers_mod.requests, "post", fake_post)
        providers_mod._stream_cerebras(
            {"cerebras": {"model": "gemma-4-31b"}}, None, [], lambda d: None)
        assert captured["body"]["model"] == "gemma-4-31b"

    def test_api_error_surfaces_message(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("cerebras", "k")
        monkeypatch.setattr(
            providers_mod.requests, "post",
            lambda *a, **kw: FakeResponse(402, {"error": {"message": "out of credits"}}))
        with pytest.raises(RuntimeError, match=r"402.*out of credits"):
            providers_mod._stream_cerebras({}, None, [], lambda d: None)

    def test_sse_stream(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("cerebras", "k")

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
        providers_mod.set_api_key("cerebras", "k")
        monkeypatch.setattr(providers_mod.requests, "post",
                            lambda *a, **kw: FakeResponse(200, lines=[b"data: [DONE]"]))
        with pytest.raises(RuntimeError, match="no usable text"):
            providers_mod._stream_cerebras({}, None, [], lambda d: None)


class TestOpenAIRequest:
    def test_request_shape_and_reply(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("openai", "oa-k")
        captured = {}
        sse_line = ("data: " + json.dumps({"choices": [{"delta": {"content": "ok!"}}]})).encode()

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured.update(url=url, headers=headers, body=json)
            return FakeResponse(200, lines=[sse_line])

        monkeypatch.setattr(providers_mod.requests, "post", fake_post)
        chunks = []
        reply = providers_mod._stream_openai(
            {}, "be nice", [{"role": "user", "content": "hola"}], chunks.append)
        assert reply == "ok!"
        assert captured["url"] == "https://api.openai.com/v1/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer oa-k"
        body = captured["body"]
        assert body["model"] == "gpt-5.1"  # default
        assert body["stream"] is True
        assert body["messages"][0] == {"role": "system", "content": "be nice"}
        assert body["messages"][1]["content"] == "hola"

    def test_model_from_config(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("openai", "k")
        captured = {}
        sse_line = ("data: " + json.dumps({"choices": [{"delta": {"content": "x"}}]})).encode()

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured.update(body=json)
            return FakeResponse(200, lines=[sse_line])

        monkeypatch.setattr(providers_mod.requests, "post", fake_post)
        providers_mod._stream_openai(
            {"openai": {"model": "gpt-5-mini"}}, None, [], lambda d: None)
        assert captured["body"]["model"] == "gpt-5-mini"

    def test_without_key_raises_friendly_error(self, providers_mod, env_in_tmp):
        with pytest.raises(RuntimeError, match="No OpenAI API key"):
            providers_mod._stream_openai({}, "sys", [], lambda d: None)


class TestXaiRequest:
    def test_request_shape_and_reply(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("xai", "xai-k")
        captured = {}
        sse_line = ("data: " + json.dumps({"choices": [{"delta": {"content": "ok!"}}]})).encode()

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured.update(url=url, headers=headers, body=json)
            return FakeResponse(200, lines=[sse_line])

        monkeypatch.setattr(providers_mod.requests, "post", fake_post)
        chunks = []
        reply = providers_mod._stream_xai(
            {}, "be nice", [{"role": "user", "content": "hola"}], chunks.append)
        assert reply == "ok!"
        assert captured["url"] == "https://api.x.ai/v1/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer xai-k"
        body = captured["body"]
        assert body["model"] == "grok-4"  # default
        assert body["stream"] is True
        assert body["messages"][0] == {"role": "system", "content": "be nice"}

    def test_model_from_config(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("xai", "k")
        captured = {}
        sse_line = ("data: " + json.dumps({"choices": [{"delta": {"content": "x"}}]})).encode()

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured.update(body=json)
            return FakeResponse(200, lines=[sse_line])

        monkeypatch.setattr(providers_mod.requests, "post", fake_post)
        providers_mod._stream_xai(
            {"xai": {"model": "grok-3-mini"}}, None, [], lambda d: None)
        assert captured["body"]["model"] == "grok-3-mini"

    def test_without_key_raises_friendly_error(self, providers_mod, env_in_tmp):
        with pytest.raises(RuntimeError, match="No xAI API key"):
            providers_mod._stream_xai({}, "sys", [], lambda d: None)


class TestAnthropicRequest:
    def test_request_shape_and_reply(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("anthropic", "ant-k")
        captured = {}
        sse_line = ("data: " + json.dumps({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "ok!"}})).encode()

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured.update(url=url, headers=headers, body=json)
            return FakeResponse(200, lines=[sse_line])

        monkeypatch.setattr(providers_mod.requests, "post", fake_post)
        chunks = []
        reply = providers_mod._stream_anthropic(
            {}, "be nice", [{"role": "user", "content": "hola"}], chunks.append)
        assert reply == "ok!"
        assert captured["url"] == "https://api.anthropic.com/v1/messages"
        assert captured["headers"]["x-api-key"] == "ant-k"
        assert captured["headers"]["anthropic-version"] == "2023-06-01"
        body = captured["body"]
        assert body["model"] == "claude-haiku-4-5"  # default
        assert body["stream"] is True
        assert body["max_tokens"] > 0
        # System prompt is a top-level field, not a chat message.
        assert body["system"] == "be nice"
        assert body["messages"] == [{"role": "user", "content": "hola"}]

    def test_model_from_config(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("anthropic", "k")
        captured = {}
        sse_line = ("data: " + json.dumps({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "x"}})).encode()

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured.update(body=json)
            return FakeResponse(200, lines=[sse_line])

        monkeypatch.setattr(providers_mod.requests, "post", fake_post)
        providers_mod._stream_anthropic(
            {"anthropic": {"model": "claude-sonnet-5"}}, None, [], lambda d: None)
        assert captured["body"]["model"] == "claude-sonnet-5"

    def test_empty_content_raises(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("anthropic", "k")
        monkeypatch.setattr(providers_mod.requests, "post",
                            lambda *a, **kw: FakeResponse(200, lines=[]))
        with pytest.raises(RuntimeError, match="no usable text"):
            providers_mod._stream_anthropic({}, None, [], lambda d: None)

    def test_api_error_surfaced(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("anthropic", "k")
        monkeypatch.setattr(
            providers_mod.requests, "post",
            lambda *a, **kw: FakeResponse(401, {"error": {"message": "bad key"}}))
        with pytest.raises(RuntimeError, match=r"401.*bad key"):
            providers_mod._stream_anthropic({}, None, [], lambda d: None)

    def test_sse_stream(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("anthropic", "k")

        def sse(obj):
            return ("data: " + json.dumps(obj)).encode()

        lines = [
            b"event: message_start",
            sse({"type": "message_start", "message": {}}),
            sse({"type": "content_block_start", "index": 0,
                 "content_block": {"type": "text", "text": ""}}),
            sse({"type": "content_block_delta", "index": 0,
                 "delta": {"type": "text_delta", "text": "Ho"}}),
            sse({"type": "content_block_delta", "index": 0,
                 "delta": {"type": "thinking_delta", "thinking": "..."}}),
            sse({"type": "content_block_delta", "index": 0,
                 "delta": {"type": "text_delta", "text": "la"}}),
            sse({"type": "content_block_stop", "index": 0}),
            sse({"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
            sse({"type": "message_stop"}),
        ]
        monkeypatch.setattr(providers_mod.requests, "post",
                            lambda *a, **kw: FakeResponse(200, lines=lines))
        chunks = []
        reply = providers_mod._stream_anthropic({}, None, [], chunks.append)
        assert reply == "Hola"
        assert chunks == ["Ho", "la"]

    def test_stream_error_event_raises(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("anthropic", "k")
        lines = [("data: " + json.dumps(
            {"type": "error", "error": {"message": "overloaded"}})).encode()]
        monkeypatch.setattr(providers_mod.requests, "post",
                            lambda *a, **kw: FakeResponse(200, lines=lines))
        with pytest.raises(RuntimeError, match="overloaded"):
            providers_mod._stream_anthropic({}, None, [], lambda d: None)

    def test_without_key_raises_friendly_error(self, providers_mod, env_in_tmp):
        with pytest.raises(RuntimeError, match="No Anthropic API key"):
            providers_mod._stream_anthropic({}, "sys", [], lambda d: None)


class TestCustomRequest:
    def _fake(self, providers_mod, monkeypatch, captured):
        sse_line = ("data: " + json.dumps({"choices": [{"delta": {"content": "ok!"}}]})).encode()

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured.update(url=url, headers=headers, body=json)
            return FakeResponse(200, lines=[sse_line])
        monkeypatch.setattr(providers_mod.requests, "post", fake_post)

    def test_url_joining_from_base(self, providers_mod, env_in_tmp, monkeypatch):
        captured = {}
        self._fake(providers_mod, monkeypatch, captured)
        providers_mod._stream_custom(
            {"custom": {"endpoint": "https://api.example.com/v1", "model": "m"}},
            None, [], lambda d: None)
        assert captured["url"] == "https://api.example.com/v1/chat/completions"

    def test_url_joining_tolerates_trailing_slash(self, providers_mod, env_in_tmp, monkeypatch):
        captured = {}
        self._fake(providers_mod, monkeypatch, captured)
        providers_mod._stream_custom(
            {"custom": {"endpoint": "https://api.example.com/v1/", "model": "m"}},
            None, [], lambda d: None)
        assert captured["url"] == "https://api.example.com/v1/chat/completions"

    def test_url_already_ends_in_chat_completions(self, providers_mod, env_in_tmp, monkeypatch):
        captured = {}
        self._fake(providers_mod, monkeypatch, captured)
        providers_mod._stream_custom(
            {"custom": {"endpoint": "https://api.example.com/v1/chat/completions",
                        "model": "m"}},
            None, [], lambda d: None)
        assert captured["url"] == "https://api.example.com/v1/chat/completions"

    def test_auth_header_present_with_key(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("custom", "cu-k")
        captured = {}
        self._fake(providers_mod, monkeypatch, captured)
        providers_mod._stream_custom(
            {"custom": {"endpoint": "https://api.example.com/v1", "model": "m"}},
            None, [], lambda d: None)
        assert captured["headers"]["Authorization"] == "Bearer cu-k"

    def test_auth_header_absent_without_key(self, providers_mod, env_in_tmp, monkeypatch):
        captured = {}
        self._fake(providers_mod, monkeypatch, captured)
        providers_mod._stream_custom(
            {"custom": {"endpoint": "https://api.example.com/v1", "model": "m"}},
            None, [], lambda d: None)
        assert "Authorization" not in captured["headers"]

    def test_empty_endpoint_raises(self, providers_mod, env_in_tmp):
        with pytest.raises(RuntimeError, match="endpoint"):
            providers_mod._stream_custom(
                {"custom": {"endpoint": "", "model": "m"}}, None, [], lambda d: None)

    def test_empty_model_raises(self, providers_mod, env_in_tmp):
        with pytest.raises(RuntimeError, match="model"):
            providers_mod._stream_custom(
                {"custom": {"endpoint": "https://api.example.com/v1", "model": ""}},
                None, [], lambda d: None)

    def test_stream_uses_joined_url(self, providers_mod, env_in_tmp, monkeypatch):
        def sse(delta):
            return ("data: " + json.dumps({"choices": [{"delta": delta}]})).encode()
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured.update(url=url)
            return FakeResponse(200, lines=[sse({"content": "Ho"}),
                                            sse({"content": "la"}), b"data: [DONE]"])

        monkeypatch.setattr(providers_mod.requests, "post", fake_post)
        chunks = []
        reply = providers_mod._stream_custom(
            {"custom": {"endpoint": "https://api.example.com/v1/", "model": "m"}},
            None, [], chunks.append)
        assert reply == "Hola"
        assert captured["url"] == "https://api.example.com/v1/chat/completions"


class TestIsConfigured:
    def test_openai_needs_key(self, providers_mod, env_in_tmp):
        assert providers_mod._is_configured("openai", {}) is False
        providers_mod.set_api_key("openai", "k")
        assert providers_mod._is_configured("openai", {}) is True

    def test_anthropic_needs_key(self, providers_mod, env_in_tmp):
        assert providers_mod._is_configured("anthropic", {}) is False
        providers_mod.set_api_key("anthropic", "k")
        assert providers_mod._is_configured("anthropic", {}) is True

    def test_xai_needs_key(self, providers_mod, env_in_tmp):
        assert providers_mod._is_configured("xai", {}) is False
        providers_mod.set_api_key("xai", "k")
        assert providers_mod._is_configured("xai", {}) is True

    def test_custom_needs_endpoint_not_key(self, providers_mod, env_in_tmp):
        assert providers_mod._is_configured("custom", {}) is False
        assert providers_mod._is_configured(
            "custom", {"custom": {"endpoint": "   "}}) is False
        assert providers_mod._is_configured(
            "custom", {"custom": {"endpoint": "https://api.example.com/v1"}}) is True

    def test_ollama_always_configured(self, providers_mod, env_in_tmp):
        assert providers_mod._is_configured("ollama", {}) is True


class TestProviderModels:
    def test_every_provider_has_labels_and_models(self, provider_models_mod):
        for provider in provider_models_mod.PROVIDERS:
            assert provider in provider_models_mod.PROVIDER_LABELS
            assert provider_models_mod.PROVIDER_LABELS[provider]
            assert provider in provider_models_mod.MODEL_OPTIONS
            # "custom" is a free-form provider: the user types the model name,
            # so it ships with no preset list.
            if provider != "custom":
                assert len(provider_models_mod.MODEL_OPTIONS[provider]) > 0

    def test_cerebras_is_registered(self, provider_models_mod):
        assert "cerebras" in provider_models_mod.PROVIDERS

    def test_new_providers_registered(self, provider_models_mod):
        for provider in ("openai", "xai", "custom"):
            assert provider in provider_models_mod.PROVIDERS
            assert provider in provider_models_mod.PROVIDER_LABELS
            assert provider in provider_models_mod.MODEL_OPTIONS

    def test_ollama_is_labeled_local_llm(self, provider_models_mod):
        assert "Local" in provider_models_mod.PROVIDER_LABELS["ollama"]

    def test_local_default_endpoint(self, provider_models_mod):
        assert provider_models_mod.OLLAMA_DEFAULT_ENDPOINT == "http://localhost:11434"


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
        monkeypatch.setattr(providers_mod, "_stream_ollama", boom)
        with pytest.raises(RuntimeError, match="^original ollama error$"):
            providers_mod.stream_llm_with_fallback({}, "s", [], lambda d: None)

    def test_primary_success_reports_primary(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("gemini", "k")
        monkeypatch.setattr(providers_mod, "_stream_gemini", lambda *a: "hi")
        cfg = {"provider": "gemini", "fallback_providers": ["ollama"]}
        assert providers_mod.stream_llm_with_fallback(cfg, "s", [], lambda d: None) == ("hi", "gemini")

    def test_unconfigured_provider_is_skipped(self, providers_mod, env_in_tmp, monkeypatch):
        # No NVIDIA key set: its backend must not even be called.
        def never(*a):
            raise AssertionError("nvidia backend called without a key")
        monkeypatch.setattr(providers_mod, "_stream_nvidia", never)

        def boom(*a):
            raise RuntimeError("ollama down")
        monkeypatch.setattr(providers_mod, "_stream_ollama", boom)
        providers_mod.set_api_key("gemini", "k")
        monkeypatch.setattr(providers_mod, "_stream_gemini", lambda *a: "hi")
        cfg = {"provider": "ollama", "fallback_providers": ["nvidia", "gemini"]}
        assert providers_mod.stream_llm_with_fallback(cfg, "s", [], lambda d: None) == ("hi", "gemini")

    def test_all_fail_aggregates_errors(self, providers_mod, env_in_tmp, monkeypatch):
        providers_mod.set_api_key("gemini", "k")

        def boom(msg):
            def f(*a):
                raise RuntimeError(msg)
            return f
        monkeypatch.setattr(providers_mod, "_stream_ollama", boom("no server"))
        monkeypatch.setattr(providers_mod, "_stream_gemini", boom("quota"))
        cfg = {"provider": "ollama", "fallback_providers": ["gemini", "nvidia"]}
        with pytest.raises(RuntimeError) as exc:
            providers_mod.stream_llm_with_fallback(cfg, "s", [], lambda d: None)
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
        monkeypatch.setattr(providers_mod, "api_key", lambda p: "k")
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
        monkeypatch.setattr(providers_mod, "api_key", lambda p: "k")
        cfg = {"provider": "ollama", "fallback_providers": ["gemini"]}
        with pytest.raises(RuntimeError, match="connection reset"):
            providers_mod.stream_llm_with_fallback(cfg, "s", [], lambda d: None)
