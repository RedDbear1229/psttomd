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


# ---------------------------------------------------------------------------
# set (preview_viewer) 커맨드
# ---------------------------------------------------------------------------

class TestCmdSetViewer:
    def test_set_glow_exits_zero(self, runner, isolated):
        result = runner.invoke(main, ["set", "glow"])
        assert result.exit_code == 0

    def test_set_glow_writes_config(self, runner, isolated):
        runner.invoke(main, ["set", "glow"])
        text = (isolated / ".pst2md" / "config.toml").read_text(encoding="utf-8")
        assert 'preview_viewer = "glow"' in text

    def test_set_mdcat_writes_config(self, runner, isolated, monkeypatch):
        # mdcat 이 PATH 에 있는 척 — 경고 없이 저장만 검증
        import shutil as _sh
        monkeypatch.setattr(_sh, "which", lambda _: "/usr/bin/mdcat")
        # config_cli 모듈이 import 한 shutil 도 패치
        import config_cli as _cc
        monkeypatch.setattr(_cc.shutil, "which", lambda _: "/usr/bin/mdcat")
        result = runner.invoke(main, ["set", "mdcat"])
        assert result.exit_code == 0
        text = (isolated / ".pst2md" / "config.toml").read_text(encoding="utf-8")
        assert 'preview_viewer = "mdcat"' in text

    def test_set_mdcat_warns_when_missing(self, runner, isolated, monkeypatch):
        import config_cli as _cc
        monkeypatch.setattr(_cc.shutil, "which", lambda _: None)
        result = runner.invoke(main, ["set", "mdcat"])
        assert result.exit_code == 0
        # click.echo(err=True) 로 전달된 경고는 runner.output 에 포함된다
        assert "mdcat" in result.output
        assert "경고" in result.output or "warn" in result.output.lower()

    def test_set_invalid_viewer_rejected(self, runner, isolated):
        result = runner.invoke(main, ["set", "bat"])
        assert result.exit_code != 0

    def test_set_replaces_existing_value(self, runner, isolated):
        # 먼저 glow 로 저장한 뒤 mdcat 으로 덮어써도 한 줄만 남아야 함
        import config_cli as _cc
        from unittest.mock import patch as _patch
        with _patch.object(_cc.shutil, "which", return_value="/usr/bin/mdcat"):
            runner.invoke(main, ["set", "glow"])
            runner.invoke(main, ["set", "mdcat"])
        text = (isolated / ".pst2md" / "config.toml").read_text(encoding="utf-8")
        assert text.count('preview_viewer') == 1
        assert 'preview_viewer = "mdcat"' in text


# ---------------------------------------------------------------------------
# config_file_path 헬퍼
# ---------------------------------------------------------------------------

class TestConfigFilePath:
    def test_returns_home_pst2md_config(self, isolated):
        from lib.config import config_file_path
        p = config_file_path()
        assert p == isolated / ".pst2md" / "config.toml"

    def test_save_setting_generic(self, isolated):
        from lib.config import save_setting, load_config
        save_setting("mailview", "preview_viewer", "mdcat")
        cfg = load_config()
        assert cfg["mailview"]["preview_viewer"] == "mdcat"

    def test_save_setting_creates_missing_section(self, isolated):
        from lib.config import save_setting, init_config_file
        init_config_file()
        save_setting("mailview", "preview_viewer", "glow")
        text = (isolated / ".pst2md" / "config.toml").read_text(encoding="utf-8")
        assert "[mailview]" in text
        assert 'preview_viewer = "glow"' in text
