"""
md_walk — 아카이브 MD 파일 순회 / 날짜 필터 공용 유틸리티

mailenrich 와 embed 가 공유하는 파일 이터레이터 / 날짜 파서를 한 곳에서 관리한다.

사용 예:
    from scripts.lib.md_walk import iter_md_files, parse_date_filter

    since = parse_date_filter("2024-01-01", "--since")
    files = iter_md_files(
        archive=root, folders=("Inbox/계약",), limit=100,
        skip_folders=["Sent"], since=since, until=None,
    )
"""
from __future__ import annotations

from pathlib import Path

import click


def parse_date_filter(s: str, label: str) -> tuple[int, int, int] | None:
    """YYYY-MM-DD 문자열을 (y, m, d) 튜플로 파싱한다.

    Args:
        s:     입력 문자열. 빈 문자열이면 None 반환.
        label: 오류 메시지에 쓸 옵션 이름 (e.g. "--since").

    Returns:
        (year, month, day) 또는 None.

    Raises:
        click.BadParameter: 형식이 잘못된 경우.
    """
    if not s:
        return None
    parts = s.split("-")
    if len(parts) != 3:
        raise click.BadParameter(f"{label} 형식이 잘못되었습니다: {s!r} (YYYY-MM-DD 필요)")
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError as exc:
        raise click.BadParameter(f"{label} 숫자 변환 실패: {s!r}") from exc


def path_date(rel: Path) -> tuple[int, int, int] | None:
    """archive/ 상대경로에서 날짜 튜플을 추출한다.

    경로 구조: ``YYYY/MM/DD/<filename>.md`` → (Y, M, D).
    ``undated/*`` 나 그 외 형식은 None.
    """
    parts = rel.parts
    if len(parts) < 4:
        return None
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def iter_md_files(
    archive: Path,
    folders: tuple[str, ...],
    limit: int,
    skip_folders: list[str],
    since: tuple[int, int, int] | None,
    until: tuple[int, int, int] | None,
) -> list[Path]:
    """필터 조건에 맞는 MD 파일 목록을 반환한다.

    Args:
        archive:      아카이브 루트 (``archive_root()`` 반환값).
        folders:      포함할 폴더 부분문자열 튜플.
        limit:        최대 개수 (0=무제한).
        skip_folders: 제외할 폴더 부분문자열.
        since:        최소 날짜 (inclusive). None 이면 미적용.
        until:        최대 날짜 (inclusive). None 이면 미적용.

    Returns:
        조건에 맞는 MD 경로 목록 (정렬됨). ``since``/``until`` 지정 시
        날짜가 식별되지 않는 (``undated/``) 파일은 제외한다.
    """
    md_dir = archive / "archive"
    if not md_dir.exists():
        return []

    files: list[Path] = []
    for md_path in sorted(md_dir.rglob("*.md")):
        rel = md_path.relative_to(md_dir)
        rel_str = str(rel)

        if folders and not any(f.lower() in rel_str.lower() for f in folders):
            continue
        if any(sf.lower() in rel_str.lower() for sf in skip_folders):
            continue

        if since is not None or until is not None:
            d = path_date(rel)
            if d is None:
                continue
            if since is not None and d < since:
                continue
            if until is not None and d > until:
                continue

        files.append(md_path)
        if limit and len(files) >= limit:
            break

    return files
