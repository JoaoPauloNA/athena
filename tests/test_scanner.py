"""Testes do scanner cross-platform de CLIs."""
import sys

import athena.providers as prov


def test_looks_like_ai_cli_keywords():
    assert prov._looks_like_ai_cli("claude")
    assert prov._looks_like_ai_cli("cursor-agent")
    assert prov._looks_like_ai_cli("opencode")
    assert prov._looks_like_ai_cli("kimi")


def test_looks_like_ai_cli_blocklist():
    assert not prov._looks_like_ai_cli("git")
    assert not prov._looks_like_ai_cli("node")
    assert not prov._looks_like_ai_cli("code")  # VS Code, não CLI de IA


def test_looks_like_ai_cli_whole_word():
    """'agent' casa, mas 'agentxtrap' não (match de palavra inteira)."""
    assert not prov._looks_like_ai_cli("agentxtrap")
    assert prov._looks_like_ai_cli("agent")
    assert prov._looks_like_ai_cli("cursor-agent")


def test_qwen_provider_uses_safe_mode_model_and_prompt():
    command = prov._build_command(
        prov.PROVIDERS["qwen"],
        "responda apenas OK",
        binary="qwen",
        model="qwenproxy-3.8-max",
    )
    assert command == [
        "qwen",
        "--safe-mode",
        "--model",
        "qwenproxy-3.8-max",
        "-p",
        "responda apenas OK",
    ]


def test_qwen_provider_without_model_uses_prompt_only():
    command = prov._build_command(
        prov.PROVIDERS["qwen"],
        "responda apenas OK",
        binary="qwen",
        model=None,
    )
    assert command == [
        "qwen",
        "-p",
        "responda apenas OK",
    ]


def test_looks_like_ai_cli_windows_extensions(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert prov._looks_like_ai_cli("claude.cmd")
    assert prov._looks_like_ai_cli("gemini.exe")
    assert not prov._looks_like_ai_cli("git.exe")


def test_get_all_binaries_dedupes(monkeypatch, tmp_path):
    (tmp_path / "fakecli").write_text("#!/bin/sh\n")
    monkeypatch.setattr(
        prov, "_enriched_env",
        lambda extra=None: {"PATH": str(tmp_path)},
    )
    binaries = prov._get_all_binaries_in_path()
    assert binaries == ["fakecli"]


def test_get_all_binaries_windows_dedup(monkeypatch, tmp_path):
    """claude.exe + claude.cmd contam como um único binário no Windows."""
    monkeypatch.setattr(sys, "platform", "win32")
    (tmp_path / "claude.exe").write_text("x")
    (tmp_path / "claude.cmd").write_text("x")
    monkeypatch.setattr(prov, "_enriched_env", lambda extra=None: {"PATH": str(tmp_path)})
    assert len(prov._get_all_binaries_in_path()) == 1
