#!/usr/bin/env python3
"""
아카이브 무결성 검증 스크립트

아카이브 Markdown 파일의 샘플 또는 전체를 읽어 다음 항목을 검증한다:

  1. frontmatter YAML 파싱 가능 여부
  2. 필수 필드(msgid, date, from, subject) 존재 여부
  3. 첨부 파일 SHA-256 해시 일치 여부
  4. 한글 인코딩 정상 여부 (UTF-8 read 성공)
  5. 파일 수 vs DB 레코드 수 일치 여부

사용법:
  python verify_integrity.py [--archive ~/mail-archive] [--sample 200] [--full]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sqlite3
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from lib.config import load_config

#: frontmatter 에 반드시 있어야 할 필드 목록
REQUIRED_FIELDS = ["msgid", "date", "from", "subject"]


# ---------------------------------------------------------------------------
# DB 연결
# ---------------------------------------------------------------------------

def get_conn(archive_root: Path) -> Optional[sqlite3.Connection]:
    """인덱스 DB 에 연결한다.

    DB 가 없으면 None 을 반환해 DB 검증을 건너뛸 수 있도록 한다.

    Args:
        archive_root: 아카이브 루트 디렉터리.

    Returns:
        sqlite3.Connection 또는 None (DB 파일 없을 때).
    """
    db = archive_root / "index.sqlite"
    if not db.exists():
        return None
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# frontmatter 파서
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> dict | None:
    """Markdown 텍스트에서 YAML frontmatter 를 최소 파싱한다.

    '---' 구분자 쌍 사이의 내용을 key: value 형식으로 읽는다.
    값의 타입 변환은 하지 않고 문자열 그대로 반환한다.

    Args:
        text: Markdown 파일 전체 텍스트.

    Returns:
        필드명 → 값 문자열 dict. frontmatter 없으면 None.
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---\n", 3)
    if end == -1:
        return None
    fm = text[3:end]
    meta: dict = {}
    for line in fm.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        meta[key.strip()] = val.strip()
    return meta


# ---------------------------------------------------------------------------
# 파일 검증
# ---------------------------------------------------------------------------

def check_file(md_path: Path, archive_root: Path) -> list[str]:
    """단일 Markdown 파일의 무결성을 검사하고 오류 목록을 반환한다.

    검사 항목:
      - UTF-8 인코딩으로 읽기 가능 여부
      - frontmatter 파싱 가능 여부
      - 필수 필드(REQUIRED_FIELDS) 존재 및 비어 있지 않음
      - attachments 섹션에 기록된 파일의 SHA-256 해시 일치

    Args:
        md_path:      검사할 Markdown 파일 경로.
        archive_root: 첨부 파일 경로 해석 기준 루트.

    Returns:
        발견된 오류 메시지 리스트. 오류가 없으면 빈 리스트.
    """
    errors: list[str] = []

    try:
        text = md_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return [f"인코딩 오류: {e}"]

    fm = parse_frontmatter(text)
    if fm is None:
        return ["frontmatter 파싱 실패"]

    # 필수 필드 존재 확인
    for field in REQUIRED_FIELDS:
        if field not in fm or not fm[field]:
            errors.append(f"필수 필드 없음: {field}")

    # 첨부 파일 SHA-256 해시 검증
    att_section_match = re.search(r"attachments:\n((?:  -.*\n?)+)", text)
    if att_section_match:
        for line in att_section_match.group(1).splitlines():
            path_m = re.search(r'path: "([^"]+)"', line)
            sha_m  = re.search(r'sha256: "([a-f0-9]{16})', line)
            if path_m and sha_m:
                att_path = archive_root / path_m.group(1)
                if att_path.exists():
                    actual_sha = hashlib.sha256(att_path.read_bytes()).hexdigest()
                    if not actual_sha.startswith(sha_m.group(1)):
                        errors.append(f"첨부 해시 불일치: {att_path.name}")
                else:
                    errors.append(f"첨부 파일 없음: {path_m.group(1)}")

    return errors


# ---------------------------------------------------------------------------
# CLI 진입점
# ---------------------------------------------------------------------------

def main() -> None:
    """명령행 인자를 파싱하고 무결성 검증을 실행한다."""
    parser = argparse.ArgumentParser(description="아카이브 무결성 검증")
    _default_archive = load_config()["archive"]["root"]
    parser.add_argument("--archive", default=_default_archive, help="아카이브 루트")
    parser.add_argument("--sample",  type=int, default=200, help="샘플 수 (기본: 200)")
    parser.add_argument("--full",    action="store_true", help="전체 파일 검증")
    args = parser.parse_args()

    archive_root = Path(args.archive)
    archive_dir = archive_root / "archive"

    if not archive_dir.exists():
        sys.exit(f"아카이브 없음: {archive_dir}")

    all_files = list(archive_dir.rglob("*.md"))
    total = len(all_files)
    print(f"MD 파일 총 {total:,}개 발견")

    # 샘플 또는 전체 선택
    if args.full:
        sample = all_files
        print(f"전체 {total:,}개 검증 중...")
    else:
        sample = random.sample(all_files, min(args.sample, total))
        print(f"샘플 {len(sample)}개 검증 중...")

    fail_count = 0
    for md in sample:
        errs = check_file(md, archive_root)
        if errs:
            fail_count += 1
            rel = md.relative_to(archive_root)
            for e in errs:
                print(f"  FAIL [{rel}] {e}")

    print(f"\n검증 완료: {len(sample)}개 중 {fail_count}개 실패")

    # DB 레코드 수 vs 파일 수 비교
    conn = get_conn(archive_root)
    if conn:
        db_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.close()
        diff = abs(db_count - total)
        print(f"DB 레코드: {db_count:,}개 / MD 파일: {total:,}개")
        if diff > 0:
            print(f"  주의: {diff}개 차이 있음 (build-index --rebuild 로 재동기화)")
        else:
            print("  DB ↔ 파일 수 일치")

    if fail_count == 0:
        print("\n무결성 검증 통과.")
    else:
        print(f"\n경고: {fail_count}개 파일에 문제가 있습니다.")
        sys.exit(1)


if __name__ == "__main__":
    main()
