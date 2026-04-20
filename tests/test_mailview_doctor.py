"""
tests/test_mailview_doctor.py — mailview --doctor 스모크

실제 CLI 실행은 하지 않고 CliRunner 로 run_doctor() 를 통해 출력이
정상적으로 생성되는지만 확인한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from mailview import build_fzf_preview_cmd  # noqa: E402
from mailview import main as mailview_main  # noqa: E402
from mailview import run_doctor  # noqa: E402


class TestDoctorOutput:
    def test_doctor_runs_without_error(self, capsys) -> None:
        """run_doctor() 가 예외 없이 실행되고 stdout 에 결과를 쓴다."""
        run_doctor()
        captured = capsys.readouterr()
        assert "mailview --doctor" in captured.out
        assert "Platform" in captured.out
        assert "fzf" in captured.out
        assert "한글 입력 체크리스트" in captured.out

    def test_doctor_reports_env_vars(self, capsys, monkeypatch) -> None:
        """환경변수 값이 출력에 반영된다."""
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.setenv("TERM", "xterm-256color")
        run_doctor()
        out = capsys.readouterr().out
        assert "en_US.UTF-8" in out
        assert "xterm-256color" in out

    def test_doctor_handles_missing_binaries(self, capsys, monkeypatch) -> None:
        """shutil.which 가 None 을 반환해도 크래시 없이 '(not found)' 출력."""
        import mailview as mv
        monkeypatch.setattr(mv.shutil, "which", lambda _: None)
        run_doctor()
        out = capsys.readouterr().out
        assert "(not found)" in out


class TestDoctorCLI:
    def test_doctor_flag_via_cli(self) -> None:
        """`mailview --doctor` 가 run_doctor 출력을 반환하고 exit 0."""
        runner = CliRunner()
        result = runner.invoke(mailview_main, ["--doctor"])
        assert result.exit_code == 0
        assert "mailview --doctor" in result.output
        assert "Python" in result.output


@pytest.mark.skipif(sys.platform == "win32", reason="Linux/WSL preview shell-pipe test")
class TestPreviewCmd:
    def test_preview_uses_awk_and_width_var(self, monkeypatch) -> None:
        """awk 가 있으면 frontmatter 제거 파이프 + FZF_PREVIEW_COLUMNS 사용."""
        import mailview as mv
        monkeypatch.setattr(
            mv, "detect_platform", lambda: "linux",
        )
        monkeypatch.setattr(
            mv.shutil, "which",
            lambda name: "/usr/bin/awk" if name == "awk" else None,
        )
        cmd = build_fzf_preview_cmd("/usr/bin/glow", None, "dark")
        assert "awk" in cmd
        assert "FZF_PREVIEW_COLUMNS" in cmd
        assert "--width" in cmd
        # frontmatter 구분자 2개까지 skip 하는 패턴
        assert "c++;next" in cmd
        assert "c>=2" in cmd

    def test_preview_falls_back_when_awk_missing(self, monkeypatch) -> None:
        """awk 미탑재 시 전체 파일을 glow 에 직접 전달."""
        import mailview as mv
        monkeypatch.setattr(mv, "detect_platform", lambda: "linux")
        monkeypatch.setattr(mv.shutil, "which", lambda _: None)
        cmd = build_fzf_preview_cmd("/usr/bin/glow", None, "dark")
        assert "awk" not in cmd
        assert "FZF_PREVIEW_COLUMNS" in cmd  # width 자동 계산은 유지

    def test_preview_windows_unchanged(self, monkeypatch) -> None:
        """Windows 는 awk 미사용 (cmd.exe 비호환) → 기존 형식."""
        import mailview as mv
        monkeypatch.setattr(mv, "detect_platform", lambda: "windows")
        cmd = build_fzf_preview_cmd("C:/glow.exe", None, "dark")
        assert "awk" not in cmd
        assert "FZF_PREVIEW_COLUMNS" not in cmd
