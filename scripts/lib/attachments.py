"""
첨부 파일 SHA-256 CAS (Content-Addressable Storage) 관리

첨부 파일을 내용 해시 기반으로 저장해 중복 파일을 자동으로 제거한다.
50MB 이상 대용량 파일은 별도 디렉터리(attachments_large)로 분리해
일반 아카이브와 독립적으로 백업·관리할 수 있도록 한다.

저장 경로 구조:
    <archive_root>/attachments/<sha256[:2]>/<sha256><ext>
    <archive_root>/attachments_large/<sha256[:2]>/<sha256><ext>

사용 예:
    from lib.attachments import store_attachment, attachment_yaml_entry

    meta = store_attachment(data, "report.pdf", archive_root / "attachments")
    yaml_line = attachment_yaml_entry(meta)
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

#: 대용량 분류 임계값 — 이 크기 이상은 attachments_large 로 이동
LARGE_THRESHOLD = 50 * 1024 * 1024   # 50 MB

# magic bytes → 확장자 매핑 (파일명에 확장자 없을 때 폴백)
_MAGIC_EXT: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n",     ".png"),
    (b"\xff\xd8\xff",           ".jpg"),
    (b"GIF87a",                 ".gif"),
    (b"GIF89a",                 ".gif"),
    (b"RIFF",                   ".wav"),   # WAV/AVI — RIFF 공통 시그니처
    (b"PK\x03\x04",             ".zip"),
    (b"\x50\x4b\x05\x06",       ".zip"),
    (b"%PDF",                   ".pdf"),
    (b"\xd0\xcf\x11\xe0",       ".doc"),   # OLE2 (doc/xls/ppt)
    (b"PK\x03\x04\x14\x00\x06", ".xlsx"),  # OOXML (xlsx/docx/pptx)
    (b"\x1f\x8b",               ".gz"),
    (b"BZh",                    ".bz2"),
    (b"\x00\x00\x00\x0cftyp",   ".mp4"),
    (b"\x42\x4d",               ".bmp"),
]


def _guess_ext(data: bytes) -> str:
    """파일 앞부분 magic bytes 로 확장자를 추론한다.

    Args:
        data: 파일 바이트 (앞 16 바이트면 충분).

    Returns:
        ".pdf" 같은 소문자 확장자 문자열. 알 수 없으면 "" 반환.
    """
    for magic, ext in _MAGIC_EXT:
        if data[:len(magic)] == magic:
            return ext
    return ""


def _sanitize_filename(name: str) -> str:
    """첨부 파일명에서 경로 순회 및 OS 위험 문자를 제거한다.

    os.path.basename 으로 경로 구분자를 제거한 뒤,
    Windows/Linux 공통 금지 문자를 밑줄(_)로 교체한다.
    결과가 빈 문자열이면 "attachment" 를 반환한다.

    Args:
        name: 원본 첨부 파일명 (경로 포함 가능).

    Returns:
        안전한 파일명 문자열 (최대 200자).
    """
    # 경로 구분자 제거: "../../../etc/passwd" → "passwd"
    name = os.path.basename(name)
    # Windows 금지 문자 + 제어 문자 → 밑줄
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name[:200] or "attachment"


def store_attachment(
    data: bytes,
    original_name: str,
    attachments_root: Path,
) -> dict[str, Any]:
    """첨부 파일을 CAS 디렉터리에 저장하고 메타데이터 dict 를 반환한다.

    동일한 내용의 파일(동일 SHA-256)이 이미 존재하면 덮어쓰지 않고
    기존 파일 경로를 그대로 반환한다 (멱등 쓰기).

    저장 경로:
      - 일반 (< 50MB): <archive_root>/attachments/<hex2>/<sha256><ext>
      - 대용량 (≥ 50MB): <archive_root>/attachments_large/<hex2>/<sha256><ext>

    Args:
        data:             첨부 파일 바이트.
        original_name:    원본 파일명 (경로 포함 시 basename 만 사용).
        attachments_root: attachments 디렉터리 경로
                          (일반적으로 <archive_root>/attachments).

    Returns:
        다음 키를 가진 dict:
          - name   (str)  : 정리된 파일명
          - sha256 (str)  : 전체 SHA-256 hex 문자열
          - size   (int)  : 바이트 단위 파일 크기
          - path   (str)  : archive_root 기준 상대 경로
          - large  (bool) : LARGE_THRESHOLD 이상 여부
    """
    sha256 = hashlib.sha256(data).hexdigest()
    safe_name = _sanitize_filename(original_name)
    _, ext = os.path.splitext(safe_name)
    ext = ext.lower()
    # 파일명에 확장자 없으면 magic bytes 로 추론
    if not ext and data:
        ext = _guess_ext(data)

    size = len(data)
    is_large = size >= LARGE_THRESHOLD

    # 대용량 파일은 별도 디렉터리로 분리
    subdir = "attachments_large" if is_large else "attachments"
    # 해시 앞 2자리로 서브디렉터리 분산 (파일 수 과다 방지)
    dest_dir = attachments_root.parent / subdir / sha256[:2]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{sha256}{ext}"

    # 멱등 쓰기: 동일 해시 파일이 이미 존재하면 생략
    if not dest_path.exists():
        dest_path.write_bytes(data)

    rel_path = str(dest_path.relative_to(attachments_root.parent))

    return {
        "name":   safe_name,
        "sha256": sha256,
        "size":   size,
        "path":   rel_path,
        "large":  is_large,
    }


def attachment_yaml_entry(meta: dict[str, Any]) -> str:
    """첨부 파일 메타데이터를 YAML frontmatter 리스트 항목 문자열로 변환한다.

    SHA-256 은 앞 16자리만 표시해 가독성을 높인다 (충돌 확률 무시 가능 수준).

    Args:
        meta: store_attachment() 가 반환한 메타데이터 dict.

    Returns:
        "  - {name: \"...\", sha256: \"...\", size: ..., path: \"...\"}" 형태의 문자열.

    Example:
        >>> attachment_yaml_entry({"name": "report.pdf", "sha256": "abc123...", "size": 1024, "path": "attachments/ab/abc123.pdf"})
        '  - {name: "report.pdf", sha256: "abc123....", size: 1024, path: "attachments/ab/abc123.pdf"}'
    """
    return (
        f"  - {{name: \"{meta['name']}\", "
        f"sha256: \"{meta['sha256'][:16]}...\", "
        f"size: {meta['size']}, "
        f"path: \"{meta['path']}\"}}"
    )
