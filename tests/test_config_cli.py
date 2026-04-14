"""tests/test_config_cli.py — scripts/config_cli.py Click CLI 테스트"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from config_cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """홈 디렉터리를 tmp_path 로 격리하고 MAIL_ARCHIVE 환경변수를 제거한다."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("MAIL_ARCHIVE", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# show 커맨드
# ---------------------------------------------------------------------------

class TestCmdShow:
    def test_show_exits_zero(self, runner, isolated):
        result = runner.invoke(main, ["show"])
        assert result.exit_code == 0

    def test_show_contains_archive(self, runner, isolated):
        result = runner.invoke(main, ["show"])
        assert "archive" in result.output.lower() or "root" in result.output

    def test_show_contains_platform(self, runner, isolated):
        result = runner.invoke(main, ["show"])
        assert "플랫폼" in result.output


# ---------------------------------------------------------------------------
# set-output 커맨드
# ---------------------------------------------------------------------------

class TestCmdSetOutput:
    def test_set_output_exits_zero(self, runner, isolated, tmp_path):
        target = tmp_path / "myarchive"
        result = runner.invoke(main, ["set-output", str(target)])
        assert result.exit_code == 0

    def test_set_output_message(self, runner, isolated, tmp_path):
        target = tmp_path / "myarchive"
        result = runner.invoke(main, ["set-output", str(target)])
        assert "저장" in result.output

    def test_set_output_creates_toml(self, runner, isolated, tmp_path):
        target = tmp_path / "myarchive"
        runner.invoke(main, ["set-output", str(target)])
        config_file = isolated / ".pst2md" / "config.toml"
        assert config_file.exists()

    def test_set_output_tilde_path(self, runner, isolated):
        result = runner.invoke(main, ["set-output", "~/mail-archive"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# init 커맨드
# ---------------------------------------------------------------------------

class TestCmdInit:
    def test_init_creates_file(self, runner, isolated):
        result = runner.invoke(main, ["init"])
        assert result.exit_code == 0
        assert (isolated / ".pst2md" / "config.toml").exists()

    def test_init_skips_existing(self, runner, isolated):
        config_file = isolated / ".pst2md" / "config.toml"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text("# original\n", encoding="utf-8")
        result = runner.invoke(main, ["init"])
        assert result.exit_code == 0
        assert "이미 존재" in result.output
        # 내용이 변경되지 않아야 함
        assert config_file.read_text(encoding="utf-8") == "# original\n"

    def test_init_force_overwrites(self, runner, isolated):
        config_file = isolated / ".pst2md" / "config.toml"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text("# original\n", encoding="utf-8")
        result = runner.invoke(main, ["init", "--force"])
        assert result.exit_code == 0
        text = config_file.read_text(encoding="utf-8")
        assert "# original" not in text
        assert "[archive]" in text

    def test_init_with_output_option(self, runner, isolated, tmp_path):
        target = str(tmp_path / "custom-archive")
        result = runner.invoke(main, ["init", "--output", target])
        assert result.exit_code == 0
        text = (isolated / ".pst2md" / "config.toml").read_text(encoding="utf-8")
        assert "custom-archive" in text

    def test_init_with_backend_option(self, runner, isolated):
        result = runner.invoke(main, ["init", "--backend", "readpst"])
        assert result.exit_code == 0
        text = (isolated / ".pst2md" / "config.toml").read_text(encoding="utf-8")
        assert "readpst" in text

    def test_init_success_message(self, runner, isolated):
        result = runner.invoke(main, ["init"])
        assert "생성" in result.output
