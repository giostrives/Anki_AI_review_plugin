"""Conversation/error logging (conversations.py)."""
import json


def test_disabled_by_default(conversations_mod):
    assert conversations_mod.is_enabled({}) is False
    assert conversations_mod.is_enabled({"logging_enabled": False}) is False
    assert conversations_mod.is_enabled({"logging_enabled": True}) is True


def test_default_dir_honors_config_override(conversations_mod, tmp_path):
    target = tmp_path / "logs"
    cfg = {"conversations_dir": str(target)}
    assert conversations_mod.default_dir(cfg) == str(target)
    assert target.is_dir()  # created on demand


def test_session_path_is_a_jsonl_in_the_log_dir(conversations_mod, tmp_path):
    cfg = {"conversations_dir": str(tmp_path)}
    path = conversations_mod.new_session_path(cfg)
    assert path.startswith(str(tmp_path))
    assert path.endswith(".jsonl")


def test_append_conversation_writes_parseable_jsonl(conversations_mod, tmp_path):
    path = tmp_path / "session.jsonl"
    rec1 = {"word": "comprometido", "messages": [{"role": "user", "content": "hola"}]}
    rec2 = {"word": "grueso", "messages": []}
    conversations_mod.append_conversation(str(path), rec1)
    conversations_mod.append_conversation(str(path), rec2)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(l)["word"] for l in lines] == ["comprometido", "grueso"]


def test_append_conversation_never_raises(conversations_mod, tmp_path, capsys):
    """Persistence failures must not break a review."""
    bad_path = str(tmp_path / "no" / "such" / "dir" / "x.jsonl")
    conversations_mod.append_conversation(bad_path, {"a": 1})  # must not raise
    assert "Failed to save conversation" in capsys.readouterr().out


def test_append_error_noop_when_logging_disabled(conversations_mod, tmp_path):
    cfg = {"logging_enabled": False, "conversations_dir": str(tmp_path)}
    conversations_mod.append_error(cfg, "boom")
    assert not (tmp_path / "errors.log").exists()


def test_append_error_writes_when_enabled(conversations_mod, tmp_path):
    cfg = {"logging_enabled": True, "conversations_dir": str(tmp_path)}
    conversations_mod.append_error(cfg, "Evaluation failed: boom")
    content = (tmp_path / "errors.log").read_text(encoding="utf-8")
    assert "Evaluation failed: boom" in content
