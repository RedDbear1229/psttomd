"""
tests/test_config_cli_generic.py — pst2md-config 범용 get/set/unset/path
커맨드 및 config_schema.KNOWN_KEYS 검증.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from config_cli import main
from lib.config_schema import KNOWN_KEYS, convert_value, mask_sensitive


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """홈 디렉터리를 tmp_path 로 격리."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("MAIL_ARCHIVE", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# KNOWN_KEYS 레지스트리 구조 검증
# ---------------------------------------------------------------------------

class TestKnownKeys:
    def test_registry_has_expected_keys(self):
        # 주요 섹션별 대표 키가 모두 등록되어 있어야 한다
        for k in (
            "archive.root", "archive.roots", "pst_backend",
            "tools.fzf", "tools.glow", "win32com.outlook_profile",
            "mailview.glow_style", "mailview.auto_index",
            "mailview.preview_viewer",
            "llm.provider", "llm.endpoint", "llm.token", "llm.model",
            "llm.timeout", "llm.max_retries", "llm.concurrency",
            "llm.scope.summary_max_chars", "llm.scope.tag_max_count",
            "llm.scope.related_max_count",
            "llm.scope.skip_body_shorter_than", "llm.scope.skip_folders",
        ):
            assert k in KNOWN_KEYS, f"missing key: {k}"

    def test_spec_path_section_key_consistent(self):
        for path, spec in KNOWN_KEYS.items():
            assert spec.path == path
            if spec.section:
                assert path == f"{spec.section}.{spec.key}"
            else:
                assert path == spec.key

    def test_sensitive_flag_on_token(self):
        assert KNOWN_KEYS["llm.token"].sensitive is True

    def test_choices_defined_for_enum_keys(self):
        assert KNOWN_KEYS["pst_backend"].choices
        assert KNOWN_KEYS["llm.provider"].choices
        assert KNOWN_KEYS["mailview.preview_viewer"].choices


# ---------------------------------------------------------------------------
# convert_value 타입 변환
# ---------------------------------------------------------------------------

class TestConvertValue:
    def test_str_passthrough(self):
        spec = KNOWN_KEYS["llm.model"]
        assert convert_value(spec, "gpt-4o") == "gpt-4o"

    def test_int_conversion(self):
        spec = KNOWN_KEYS["llm.timeout"]
        assert convert_value(spec, "120") == 120

    def test_int_invalid(self):
        spec = KNOWN_KEYS["llm.timeout"]
        with pytest.raises(ValueError):
            convert_value(spec, "abc")

    def test_bool_true_variants(self):
        spec = KNOWN_KEYS["mailview.auto_index"]
        for raw in ("true", "yes", "y", "1", "ON"):
            assert convert_value(spec, raw) is True

    def test_bool_false_variants(self):
        spec = KNOWN_KEYS["mailview.auto_index"]
        for raw in ("false", "no", "n", "0", "OFF"):
            assert convert_value(spec, raw) is False

    def test_bool_invalid(self):
        spec = KNOWN_KEYS["mailview.auto_index"]
        with pytest.raises(ValueError):
            convert_value(spec, "maybe")

    def test_list_comma_split(self):
        spec = KNOWN_KEYS["llm.scope.skip_folders"]
        assert convert_value(spec, "Junk, Spam,  Archive") == ["Junk", "Spam", "Archive"]

    def test_list_empty(self):
        spec = KNOWN_KEYS["llm.scope.skip_folders"]
        assert convert_value(spec, "") == []

    def test_choice_valid(self):
        spec = KNOWN_KEYS["llm.provider"]
        assert convert_value(spec, "anthropic") == "anthropic"

    def test_choice_invalid(self):
        spec = KNOWN_KEYS["llm.provider"]
        with pytest.raises(ValueError):
            convert_value(spec, "openai-legacy")


# ---------------------------------------------------------------------------
# mask_sensitive
# ---------------------------------------------------------------------------

class TestMaskSensitive:
    def test_long_token_masked(self):
        assert mask_sensitive("sk-abc12345xyz") == "***5xyz"

    def test_empty(self):
        assert mask_sensitive("") == ""
        assert mask_sensitive(None) == ""

    def test_short_token_fully_masked(self):
        assert mask_sensitive("abc") == "***"


# ---------------------------------------------------------------------------
# set (generic)
# ---------------------------------------------------------------------------

class TestCmdSetGeneric:
    def test_set_str_key(self, runner, isolated):
        res = runner.invoke(main, ["set", "llm.model", "gpt-4o"])
        assert res.exit_code == 0
        text = (isolated / ".pst2md" / "config.toml").read_text()
        assert 'model = "gpt-4o"' in text

    def test_set_int_key(self, runner, isolated):
        res = runner.invoke(main, ["set", "llm.timeout", "90"])
        assert res.exit_code == 0
        text = (isolated / ".pst2md" / "config.toml").read_text()
        assert "timeout = 90" in text

    def test_set_bool_key(self, runner, isolated):
        res = runner.invoke(main, ["set", "mailview.auto_index", "false"])
        assert res.exit_code == 0
        text = (isolated / ".pst2md" / "config.toml").read_text()
        assert "auto_index = false" in text

    def test_set_list_key(self, runner, isolated):
        res = runner.invoke(main, ["set", "llm.scope.skip_folders", "A,B,C"])
        assert res.exit_code == 0
        text = (isolated / ".pst2md" / "config.toml").read_text()
        assert 'skip_folders = ["A", "B", "C"]' in text

    def test_set_choice_invalid(self, runner, isolated):
        res = runner.invoke(main, ["set", "llm.provider", "bogus"])
        assert res.exit_code != 0
        assert "openai" in res.output or "하나여야" in res.output

    def test_set_unknown_key_suggests(self, runner, isolated):
        res = runner.invoke(main, ["set", "llm.modle", "gpt-4o"])
        assert res.exit_code != 0
        assert "llm.model" in res.output  # difflib suggestion

    def test_set_missing_value(self, runner, isolated):
        res = runner.invoke(main, ["set", "llm.model"])
        assert res.exit_code != 0

    def test_set_token_warns(self, runner, isolated):
        res = runner.invoke(main, ["set", "llm.token", "sk-abcdef1234"])
        assert res.exit_code == 0
        # stderr warning is mixed into output by CliRunner (default mix_stderr=True)
        assert "민감" in res.output or "LLM_TOKEN" in res.output

    def test_legacy_bridge_set_glow(self, runner, isolated):
        res = runner.invoke(main, ["set", "glow"])
        assert res.exit_code == 0
        assert "deprecated" in res.output or "set-viewer" in res.output
        text = (isolated / ".pst2md" / "config.toml").read_text()
        assert 'preview_viewer = "glow"' in text

    def test_archive_root_alias_via_set(self, runner, isolated, tmp_path):
        target = tmp_path / "myarchive"
        res = runner.invoke(main, ["set", "archive.root", str(target)])
        assert res.exit_code == 0
        text = (isolated / ".pst2md" / "config.toml").read_text()
        assert str(target) in text


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------

class TestCmdGet:
    def test_get_default(self, runner, isolated):
        res = runner.invoke(main, ["get", "llm.model"])
        assert res.exit_code == 0
        assert "gpt-4o-mini" in res.output

    def test_get_after_set(self, runner, isolated):
        runner.invoke(main, ["set", "llm.model", "gpt-4o"])
        res = runner.invoke(main, ["get", "llm.model"])
        assert res.exit_code == 0
        assert "gpt-4o" in res.output

    def test_get_token_masked(self, runner, isolated):
        runner.invoke(main, ["set", "llm.token", "sk-abcdef1234"])
        res = runner.invoke(main, ["get", "llm.token"])
        assert res.exit_code == 0
        assert "sk-abcdef1234" not in res.output
        assert "***" in res.output

    def test_get_unknown_key(self, runner, isolated):
        res = runner.invoke(main, ["get", "llm.bogus"])
        assert res.exit_code != 0


# ---------------------------------------------------------------------------
# unset
# ---------------------------------------------------------------------------

class TestCmdUnset:
    def test_unset_removes_line(self, runner, isolated):
        runner.invoke(main, ["set", "llm.model", "gpt-4o"])
        res = runner.invoke(main, ["unset", "llm.model"])
        assert res.exit_code == 0
        text = (isolated / ".pst2md" / "config.toml").read_text()
        assert 'model = "gpt-4o"' not in text

    def test_unset_nonexistent_is_ok(self, runner, isolated):
        res = runner.invoke(main, ["unset", "llm.model"])
        assert res.exit_code == 0
        assert "없습니다" in res.output or "이미" in res.output

    def test_unset_unknown_key_errors(self, runner, isolated):
        res = runner.invoke(main, ["unset", "llm.bogus"])
        assert res.exit_code != 0


# ---------------------------------------------------------------------------
# path
# ---------------------------------------------------------------------------

class TestCmdPath:
    def test_path_outputs_config_location(self, runner, isolated):
        res = runner.invoke(main, ["path"])
        assert res.exit_code == 0
        assert ".pst2md/config.toml" in res.output.replace("\\", "/")


# ---------------------------------------------------------------------------
# show [SECTION]
# ---------------------------------------------------------------------------

class TestCmdShowFilter:
    def test_show_all(self, runner, isolated):
        res = runner.invoke(main, ["show"])
        assert res.exit_code == 0
        assert "[archive]" in res.output
        assert "[llm]" in res.output
        assert "[mailview]" in res.output

    def test_show_llm_only(self, runner, isolated):
        res = runner.invoke(main, ["show", "llm"])
        assert res.exit_code == 0
        assert "[llm]" in res.output
        assert "[archive]" not in res.output
        # llm.scope 는 prefix match 로 포함
        assert "[llm.scope]" in res.output

    def test_show_unknown_section_errors(self, runner, isolated):
        res = runner.invoke(main, ["show", "bogus"])
        assert res.exit_code != 0


# ---------------------------------------------------------------------------
# set-viewer (별칭)
# ---------------------------------------------------------------------------

class TestCmdSetViewerAlias:
    def test_set_viewer_glow(self, runner, isolated):
        res = runner.invoke(main, ["set-viewer", "glow"])
        assert res.exit_code == 0
        text = (isolated / ".pst2md" / "config.toml").read_text()
        assert 'preview_viewer = "glow"' in text

    def test_set_viewer_invalid(self, runner, isolated):
        res = runner.invoke(main, ["set-viewer", "bat"])
        assert res.exit_code != 0
