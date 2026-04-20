"""tests/test_config.py — scripts/lib/config.py 테스트"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# scripts 경로를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from lib.config import (
    _deep_merge,
    detect_platform,
    load_config,
    archive_root,
    archive_roots,
    db_path,
    save_archive_root,
    init_config_file,
    DEFAULT_CONFIG,
)


# ---------------------------------------------------------------------------
# _deep_merge
# ---------------------------------------------------------------------------

class TestDeepMerge:
    def test_scalar_override(self):
        base = {"a": 1, "b": 2}
        result = _deep_merge(base, {"b": 99})
        assert result == {"a": 1, "b": 99}

    def test_nested_dict_merged(self):
        base = {"archive": {"root": "/old", "extra": "keep"}}
        override = {"archive": {"root": "/new"}}
        result = _deep_merge(base, override)
        assert result["archive"]["root"] == "/new"
        assert result["archive"]["extra"] == "keep"

    def test_new_key_added(self):
        result = _deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_original_not_mutated(self):
        base = {"a": {"x": 1}}
        override = {"a": {"x": 2}}
        _deep_merge(base, override)
        assert base["a"]["x"] == 1

    def test_list_replaced_not_merged(self):
        base = {"tags": [1, 2, 3]}
        result = _deep_merge(base, {"tags": [4, 5]})
        assert result["tags"] == [4, 5]

    def test_empty_override(self):
        base = {"a": 1}
        assert _deep_merge(base, {}) == {"a": 1}

    def test_empty_base(self):
        assert _deep_merge({}, {"a": 1}) == {"a": 1}


# ---------------------------------------------------------------------------
# detect_platform
# ---------------------------------------------------------------------------

class TestDetectPlatform:
    def test_returns_string(self):
        plat = detect_platform()
        assert plat in ("windows", "wsl", "linux")

    def test_linux_or_wsl_on_posix(self):
        # POSIX 환경에서는 linux 또는 wsl 로 분류된다
        if sys.platform == "win32":
            return
        plat = detect_platform()
        assert plat in ("linux", "wsl")


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_returns_dict(self):
        cfg = load_config()
        assert isinstance(cfg, dict)

    def test_has_required_keys(self):
        cfg = load_config()
        assert "archive" in cfg
        assert "root" in cfg["archive"]
        assert "pst_backend" in cfg

    def test_archive_root_is_string(self):
        cfg = load_config()
        assert isinstance(cfg["archive"]["root"], str)

    def test_tilde_expanded(self):
        cfg = load_config()
        assert "~" not in cfg["archive"]["root"]

    def test_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAIL_ARCHIVE", str(tmp_path))
        cfg = load_config()
        assert cfg["archive"]["root"] == str(tmp_path)

    def test_env_override_tilde(self, monkeypatch):
        monkeypatch.setenv("MAIL_ARCHIVE", "~/custom-mail")
        cfg = load_config()
        assert "~" not in cfg["archive"]["root"]
        assert "custom-mail" in cfg["archive"]["root"]

    def test_does_not_mutate_default(self):
        original_root = DEFAULT_CONFIG["archive"]["root"]
        cfg = load_config()
        cfg["archive"]["root"] = "/mutated"
        assert DEFAULT_CONFIG["archive"]["root"] == original_root

    def test_toml_file_applied(self, tmp_path, monkeypatch):
        """TOML 파일이 있으면 archive.root 가 반영된다."""
        config_dir = tmp_path / ".pst2md"
        config_dir.mkdir()
        toml_file = config_dir / "config.toml"
        toml_file.write_text(
            '[archive]\nroot = "/from/toml"\n', encoding="utf-8"
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("MAIL_ARCHIVE", raising=False)
        cfg = load_config()
        assert cfg["archive"]["root"] == "/from/toml"


# ---------------------------------------------------------------------------
# archive_root / db_path
# ---------------------------------------------------------------------------

class TestArchiveRoot:
    def test_returns_path(self):
        root = archive_root()
        assert isinstance(root, Path)

    def test_uses_cfg(self):
        cfg = {"archive": {"root": "/my/archive"}}
        assert archive_root(cfg) == Path("/my/archive")

    def test_db_path_under_archive(self):
        cfg = {"archive": {"root": "/my/archive"}}
        assert db_path(cfg) == Path("/my/archive/index.sqlite")

    def test_db_path_no_cfg(self):
        p = db_path()
        assert p.name == "index.sqlite"


# ---------------------------------------------------------------------------
# save_archive_root
# ---------------------------------------------------------------------------

class TestSaveArchiveRoot:
    def test_creates_file_if_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = save_archive_root("/new/root")
        assert result.exists()
        text = result.read_text(encoding="utf-8")
        assert "/new/root" in text

    def test_updates_existing_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # 먼저 생성
        save_archive_root("/old/root")
        # 업데이트
        save_archive_root("/updated/root")
        text = (tmp_path / ".pst2md" / "config.toml").read_text(encoding="utf-8")
        assert "/updated/root" in text
        assert "/old/root" not in text

    def test_backslash_converted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        save_archive_root("C:\\Users\\me\\mail")
        text = (tmp_path / ".pst2md" / "config.toml").read_text(encoding="utf-8")
        assert "C:/Users/me/mail" in text

    def test_preserves_other_sections(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        config_dir = tmp_path / ".pst2md"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[archive]\nroot = "/old"\n\n[tools]\nfzf = "fzf"\n',
            encoding="utf-8",
        )
        save_archive_root("/new/path")
        text = config_file.read_text(encoding="utf-8")
        assert 'fzf = "fzf"' in text
        assert "/new/path" in text


# ---------------------------------------------------------------------------
# init_config_file
# ---------------------------------------------------------------------------

class TestInitConfigFile:
    def test_creates_toml(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = init_config_file()
        assert result.exists()

    def test_no_overwrite_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        config_dir = tmp_path / ".pst2md"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text("# original\n", encoding="utf-8")
        init_config_file()
        assert config_file.read_text(encoding="utf-8") == "# original\n"

    def test_force_overwrites(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        config_dir = tmp_path / ".pst2md"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text("# original\n", encoding="utf-8")
        init_config_file(force=True)
        text = config_file.read_text(encoding="utf-8")
        assert "# original" not in text
        assert "[archive]" in text

    def test_custom_archive_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        init_config_file(archive="/custom/path")
        text = (tmp_path / ".pst2md" / "config.toml").read_text(encoding="utf-8")
        assert "/custom/path" in text

    def test_custom_backend(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        init_config_file(backend="readpst")
        text = (tmp_path / ".pst2md" / "config.toml").read_text(encoding="utf-8")
        assert "readpst" in text

    def test_contains_required_sections(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        init_config_file(force=True)
        text = (tmp_path / ".pst2md" / "config.toml").read_text(encoding="utf-8")
        assert "[archive]" in text
        assert "pst_backend" in text
        assert "[tools]" in text


# ---------------------------------------------------------------------------
# archive_roots
# ---------------------------------------------------------------------------

class TestArchiveRoots:
    def test_single_archive_returns_list_of_one(self, tmp_path, monkeypatch):
        cfg = {"archive": {"root": str(tmp_path), "roots": []}}
        roots = archive_roots(cfg)
        assert len(roots) == 1
        assert roots[0] == tmp_path

    def test_multiple_roots_merged(self, tmp_path):
        extra = tmp_path / "extra"
        cfg = {"archive": {"root": str(tmp_path), "roots": [str(extra)]}}
        roots = archive_roots(cfg)
        assert len(roots) == 2
        assert extra in roots

    def test_duplicate_roots_deduplicated(self, tmp_path):
        cfg = {"archive": {"root": str(tmp_path), "roots": [str(tmp_path)]}}
        roots = archive_roots(cfg)
        assert len(roots) == 1

    def test_home_tilde_expanded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg = {"archive": {"root": str(tmp_path), "roots": ["~/extra"]}}
        roots = archive_roots(cfg)
        assert any(r == tmp_path / "extra" for r in roots)
