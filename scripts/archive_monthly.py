#!/usr/bin/env python3
"""
archive_monthly.py — 월간 PST 아카이브 배치 (크로스플랫폼)

12개월 이상 경과한 메일을 변환하고, 인덱스와 Obsidian MOC 를 순서대로 갱신한다.
기본은 dry-run 으로 동작해 실수를 방지하며, --execute 플래그로 실제 변환을 실행한다.

사용법:
  python archive_monthly.py --pst /mnt/c/.../archive.pst [--execute]

  # Windows (PowerShell)
  python archive_monthly.py --pst "C:\\Users\\YOU\\Documents\\Outlook\\archive.pst" --execute

옵션:
  --pst <경로>      변환할 PST 파일 (필수)
  --execute         실제 변환 실행 (기본: dry-run)
  --archive <경로>  아카이브 루트 (기본: config.toml)
  --cutoff <날짜>   이 날짜 이후 메일 제외 (기본: 오늘로부터 12개월 전)
  --no-enrich       enrich.py 건너뜀 (Obsidian MOC 갱신 생략)
  --backend <이름>  PST 백엔드 강제 지정 (auto|pypff|readpst|win32com)
"""
from __future__ import annotations

import argparse
import calendar
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib.config import load_config, archive_root, detect_platform

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 날짜 유틸리티
# ---------------------------------------------------------------------------

def twelve_months_ago() -> str:
    """오늘로부터 정확히 12개월 전 날짜를 "YYYY-MM-DD" 로 반환한다.

    dateutil 이 설치되어 있으면 relativedelta 를 사용하고,
    없으면 calendar 모듈로 월말 날짜를 직접 계산한다.

    Returns:
        "YYYY-MM-DD" 형식의 날짜 문자열.
    """
    try:
        from dateutil.relativedelta import relativedelta  # type: ignore[import]
        dt = datetime.now() - relativedelta(months=12)
    except ImportError:
        # dateutil 없는 환경 — 수동 계산
        now = datetime.now()
        year = now.year
        month = now.month - 12
        # 음수 월 처리: month ≤ 0 이면 연도 감소
        while month <= 0:
            month += 12
            year -= 1
        # 대상 월의 마지막 날짜를 초과하지 않도록 조정 (예: 3월 31일 → 2월 28일)
        max_day = calendar.monthrange(year, month)[1]
        day = min(now.day, max_day)
        dt = datetime(year, month, day)
    return dt.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Outlook 프로세스 확인
# ---------------------------------------------------------------------------

def check_outlook_running() -> bool:
    """Outlook.exe 프로세스 실행 여부를 크로스플랫폼으로 확인한다.

    Windows: tasklist /FI 명령으로 직접 확인.
    WSL:     tasklist.exe 를 통해 Windows 프로세스 목록 확인.
    Linux:   Outlook 자체가 없으므로 항상 False.

    Returns:
        Outlook 이 실행 중이면 True.
    """
    plat = detect_platform()
    try:
        if plat == "windows":
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq OUTLOOK.EXE", "/NH"],
                capture_output=True, text=True,
            )
            return "OUTLOOK.EXE" in result.stdout.upper()
        if plat == "wsl":
            result = subprocess.run(
                ["tasklist.exe"],
                capture_output=True, text=True,
            )
            return "OUTLOOK.EXE" in result.stdout.upper()
    except FileNotFoundError:
        pass   # tasklist / tasklist.exe 가 없는 환경
    return False


# ---------------------------------------------------------------------------
# 단계별 실행
# ---------------------------------------------------------------------------

def run_step(label: str, cmd: list[str], log_path: Path) -> None:
    """서브프로세스 명령을 실행하고 stdout+stderr 를 로그 파일에 기록한다.

    실패(반환 코드 != 0) 시 로그 경로를 출력하고 프로세스를 종료한다.

    Args:
        label:    단계 이름 (예: "1/3 PST 변환").
        cmd:      subprocess.run 에 전달할 명령 리스트.
        log_path: 출력을 추가(append)할 로그 파일 경로.

    Raises:
        SystemExit: 명령이 비정상 종료된 경우.
    """
    log.info("[%s] 시작...", label)
    with log_path.open("a", encoding="utf-8") as lf:
        lf.write(f"\n{'='*40}\n[{label}]\n{'='*40}\n")
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # stderr 를 stdout 에 합쳐 캡처
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        lf.write(result.stdout or "")
        if result.returncode != 0:
            log.error("[%s] 실패 (코드 %d)", label, result.returncode)
            log.error("로그 확인: %s", log_path)
            sys.exit(result.returncode)
    log.info("[%s] 완료", label)


# ---------------------------------------------------------------------------
# CLI 진입점
# ---------------------------------------------------------------------------

def main() -> None:
    """명령행 인자를 파싱하고 월간 배치를 실행한다."""
    cfg = load_config()
    plat = detect_platform()

    parser = argparse.ArgumentParser(description="월간 PST 아카이브 배치 (크로스플랫폼)")
    parser.add_argument("--pst",       required=True, help="변환할 PST 파일 경로")
    parser.add_argument("--archive",   default=cfg["archive"]["root"], help="아카이브 루트")
    parser.add_argument("--cutoff",    default="", help="이 날짜 이후 제외 (기본: 12개월 전)")
    parser.add_argument("--execute",   action="store_true", help="실제 실행 (기본: dry-run)")
    parser.add_argument("--no-enrich", action="store_true", help="Obsidian MOC 갱신 건너뜀")
    parser.add_argument(
        "--backend",
        choices=["auto", "pypff", "readpst", "win32com"],
        default="",
        help="PST 백엔드 강제 지정",
    )
    args = parser.parse_args()

    if args.archive:
        cfg["archive"]["root"] = args.archive
    if args.backend:
        cfg["pst_backend"] = args.backend

    pst_path = Path(args.pst)
    if not pst_path.exists():
        sys.exit(f"오류: PST 파일 없음 → {pst_path}")

    # cutoff 기본값: 오늘로부터 12개월 전
    cutoff = args.cutoff or twelve_months_ago()
    out_root = Path(cfg["archive"]["root"])
    out_root.mkdir(parents=True, exist_ok=True)

    # 로그 파일: <archive_root>/logs/archive_YYYYMMDD_HHMMSS.log
    log_dir = out_root / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    print("=" * 50)
    print("PST 월간 아카이브 배치")
    print(f"  PST:     {pst_path}")
    print(f"  출력:    {out_root}")
    print(f"  cutoff:  {cutoff} (이 날짜 이후 메일 제외)")
    print(f"  플랫폼:  {plat}")
    print(f"  백엔드:  {cfg.get('pst_backend', 'auto')}")
    print(f"  모드:    {'실행' if args.execute else 'DRY-RUN'}")
    print("=" * 50)

    # Outlook 잠금 경고 (win32com 이 아닌 백엔드에서 PST 를 직접 열 때)
    if check_outlook_running() and cfg.get("pst_backend", "auto") != "win32com":
        print("\n경고: Outlook이 실행 중입니다.")
        print("  win32com 백엔드가 아닌 경우 PST 파일이 잠겨 있을 수 있습니다.")
        print("  계속하려면 Enter, 취소하려면 Ctrl-C를 누르세요.")
        try:
            input()
        except KeyboardInterrupt:
            sys.exit(0)

    scripts_dir = Path(__file__).parent
    python = sys.executable

    # ── 단계별 명령 구성 ────────────────────────────────────────────────
    pst2md_cmd = [
        python, str(scripts_dir / "pst2md.py"),
        "--pst",    str(pst_path),
        "--out",    str(out_root),
        "--cutoff", cutoff,
        "--resume",   # 이전 실행 이후 추가된 메시지만 처리
    ]
    if args.backend:
        pst2md_cmd += ["--backend", args.backend]

    build_index_cmd = [
        python, str(scripts_dir / "build_index.py"),
        "--archive", str(out_root),
    ]

    enrich_cmd = [
        python, str(scripts_dir / "enrich.py"),
        "--archive", str(out_root),
    ]

    # ── Dry-run: 통계만 출력하고 종료 ───────────────────────────────────
    if not args.execute:
        pst2md_cmd.append("--dry-run")
        print("\n[DRY-RUN] 실제 변환 없이 통계만 확인합니다...")
        subprocess.run(pst2md_cmd)
        print(f"\n실제 실행하려면:\n  python {__file__} --pst \"{pst_path}\" --execute")
        return

    # ── 실제 실행: 3단계 순서 보장 ──────────────────────────────────────
    run_step("1/3 PST 변환",    pst2md_cmd,       log_file)
    run_step("2/3 인덱스 갱신", build_index_cmd,  log_file)

    if not args.no_enrich:
        run_step("3/3 Obsidian MOC 갱신", enrich_cmd, log_file)

    # ── 완료 요약 ────────────────────────────────────────────────────────
    pst_size = pst_path.stat().st_size
    archive_dir = out_root / "archive"
    md_count = sum(1 for _ in archive_dir.rglob("*.md")) if archive_dir.exists() else 0

    print("\n=== 완료 ===")
    print(f"  로그:        {log_file}")
    print(f"  MD 파일 수:  {md_count:,}개")
    print(f"  PST 크기:    {pst_size / 1024**3:.1f} GB")
    print()
    print("다음 수동 작업 (PST 크기 축소):")
    if plat in ("windows", "wsl"):
        print("  1. Outlook에서 변환된 날짜 범위 메일 선택 → 삭제")
        print("  2. 파일 → 계정 설정 → 데이터 파일 → 해당 PST → 설정 → 지금 압축")
    else:
        print("  1. 변환 완료 후 원본 PST를 읽기 전용 백업으로 보관하거나 삭제")


if __name__ == "__main__":
    main()
