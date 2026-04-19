"""
tests/test_attachments.py — attachments.py 스모크 테스트

SHA-256 CAS 저장, magic bytes 추론, 파일명 sanitize, 멱등 쓰기 동작 확인.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib.attachments import (
    LARGE_THRESHOLD,
    _guess_ext,
    _sanitize_filename,
    attachment_yaml_entry,
    store_attachment,
)


class TestGuessExt:
    def test_png(self) -> None:
        assert _guess_ext(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20) == ".png"

    def test_jpg(self) -> None:
        assert _guess_ext(b"\xff\xd8\xff" + b"\x00" * 20) == ".jpg"

    def test_pdf(self) -> None:
        assert _guess_ext(b"%PDF-1.4\n") == ".pdf"

    def test_unknown(self) -> None:
        assert _guess_ext(b"random garbage") == ""

    def test_empty(self) -> None:
        assert _guess_ext(b"") == ""


class TestSanitizeFilename:
    def test_path_traversal_stripped(self) -> None:
        assert _sanitize_filename("../../../etc/passwd") == "passwd"

    def test_windows_reserved_chars(self) -> None:
        result = _sanitize_filename('bad<>:"/|?*.txt')
        assert "<" not in result
        assert ">" not in result
        assert '"' not in result

    def test_control_chars_replaced(self) -> None:
        result = _sanitize_filename("a\x00b\x1fc.txt")
        assert "\x00" not in result
        assert "\x1f" not in result

    def test_empty_fallback(self) -> None:
        assert _sanitize_filename("") == "attachment"

    def test_length_capped(self) -> None:
        long = "a" * 500 + ".txt"
        assert len(_sanitize_filename(long)) <= 200


class TestStoreAttachment:
    def test_basic_write(self, tmp_path: Path) -> None:
        data = b"hello world"
        meta = store_attachment(data, "file.txt", tmp_path / "attachments")

        assert meta["name"] == "file.txt"
        assert meta["size"] == len(data)
        assert meta["sha256"] == hashlib.sha256(data).hexdigest()
        assert meta["large"] is False
        assert meta["path"].startswith("attachments/")

        # 실제 파일이 존재
        saved = tmp_path / meta["path"]
        assert saved.exists()
        assert saved.read_bytes() == data

    def test_cas_dedup(self, tmp_path: Path) -> None:
        """동일 내용의 파일은 하나만 저장된다 (멱등 쓰기)."""
        data = b"duplicate content"
        att_root = tmp_path / "attachments"

        m1 = store_attachment(data, "a.txt", att_root)
        m2 = store_attachment(data, "b.txt", att_root)

        # 해시와 저장 경로는 동일
        assert m1["sha256"] == m2["sha256"]
        assert m1["path"] == m2["path"]
        # 다만 각각의 meta 는 원본 이름을 유지
        assert m1["name"] == "a.txt"
        assert m2["name"] == "b.txt"

    def test_magic_bytes_extension_fallback(self, tmp_path: Path) -> None:
        """확장자 없는 파일명은 magic bytes 로 추론된다."""
        data = b"%PDF-1.4\n...rest"
        meta = store_attachment(data, "noext", tmp_path / "attachments")
        assert meta["path"].endswith(".pdf")

    def test_path_traversal_blocked(self, tmp_path: Path) -> None:
        """경로 순회 시도는 sanitize 로 차단된다."""
        meta = store_attachment(b"x", "../../evil.sh", tmp_path / "attachments")
        assert ".." not in meta["name"]
        # 저장 경로가 tmp_path 밖으로 나가지 않음
        saved = tmp_path / meta["path"]
        assert saved.is_relative_to(tmp_path)

    def test_large_file_separate_dir(self, tmp_path: Path) -> None:
        """50 MB 이상은 attachments_large 로 분리된다."""
        data = b"x" * (LARGE_THRESHOLD + 1)
        meta = store_attachment(data, "big.bin", tmp_path / "attachments")
        assert meta["large"] is True
        assert meta["path"].startswith("attachments_large/")


class TestYamlEntry:
    def test_entry_format(self) -> None:
        meta = {
            "name":   "report.pdf",
            "sha256": "a" * 64,
            "size":   1024,
            "path":   "attachments/aa/aaaa.pdf",
        }
        entry = attachment_yaml_entry(meta)
        assert entry.startswith("  - {")
        assert "report.pdf" in entry
        assert "size: 1024" in entry
        # sha256 은 16자만 노출
        assert "a" * 16 + "..." in entry
