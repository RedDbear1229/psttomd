#!/usr/bin/env bash
# archive_monthly.sh — 월간 아카이브 배치
#
# 12개월 이상 경과한 메일을 지정 PST에서 변환하고 인덱스를 갱신한다.
# PST에서의 삭제 및 compact는 수동 확인 후 진행하도록 dry-run 기본.
#
# 사용법:
#   ./archive_monthly.sh --pst /mnt/c/.../archive_old.pst [--execute]
#
# 옵션:
#   --pst <경로>     변환할 PST 파일 (필수)
#   --execute        실제 변환 실행 (기본: dry-run)
#   --archive <경로> 아카이브 루트 (기본: ~/mail-archive)
#   --cutoff <날짜>  이 날짜 이후 메일 제외 (기본: 12개월 전)

set -euo pipefail

ARCHIVE_ROOT="${MAIL_ARCHIVE:-$HOME/mail-archive}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$ARCHIVE_ROOT/logs/archive_$(date +%Y%m%d_%H%M%S).log"

PST_PATH=""
EXECUTE=false
CUTOFF=$(date -d "12 months ago" +%Y-%m-%d 2>/dev/null || date -v-12m +%Y-%m-%d)

# 인자 파싱
while [[ $# -gt 0 ]]; do
    case "$1" in
        --pst)     PST_PATH="$2";     shift 2 ;;
        --archive) ARCHIVE_ROOT="$2"; shift 2 ;;
        --cutoff)  CUTOFF="$2";       shift 2 ;;
        --execute) EXECUTE=true;      shift ;;
        *) echo "알 수 없는 인자: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$PST_PATH" ]]; then
    echo "오류: --pst 가 필요합니다." >&2
    exit 1
fi

if [[ ! -f "$PST_PATH" ]]; then
    echo "오류: PST 파일 없음: $PST_PATH" >&2
    exit 1
fi

mkdir -p "$ARCHIVE_ROOT/logs"

echo "========================================"
echo "PST 월간 아카이브 배치"
echo "  PST: $PST_PATH"
echo "  출력: $ARCHIVE_ROOT"
echo "  cutoff: $CUTOFF (이 날짜 이후 메일 제외)"
echo "  모드: $([ "$EXECUTE" = true ] && echo '실행' || echo 'DRY-RUN')"
echo "========================================"

# Outlook 프로세스 체크 (Windows에서 실행 시)
if command -v tasklist.exe &>/dev/null; then
    if tasklist.exe 2>/dev/null | grep -qi "OUTLOOK.EXE"; then
        echo "경고: Outlook이 실행 중입니다. PST가 잠겨 있을 수 있습니다."
        echo "Outlook을 종료 후 다시 실행해 주세요."
        exit 1
    fi
fi

# 변환 실행
if $EXECUTE; then
    echo ""
    echo "[1/3] PST 변환 시작..."
    python3 "$SCRIPT_DIR/pst2md.py" \
        --pst "$PST_PATH" \
        --out "$ARCHIVE_ROOT" \
        --cutoff "$CUTOFF" \
        --resume \
        2>&1 | tee "$LOG_FILE"

    echo ""
    echo "[2/3] 인덱스 갱신..."
    python3 "$SCRIPT_DIR/build_index.py" \
        --archive "$ARCHIVE_ROOT" \
        2>&1 | tee -a "$LOG_FILE"

    echo ""
    echo "[3/3] Obsidian MOC 갱신..."
    python3 "$SCRIPT_DIR/enrich.py" \
        --archive "$ARCHIVE_ROOT" \
        2>&1 | tee -a "$LOG_FILE"

    echo ""
    echo "완료! 로그: $LOG_FILE"
    echo ""
    echo "다음 수동 작업:"
    echo "  1. Outlook에서 변환된 메일 삭제"
    echo "  2. Outlook → 파일 → 계정 설정 → 데이터 파일 → PST 선택 → 설정 → 지금 압축"
    echo "  3. PST 크기 확인: $(du -sh "$PST_PATH" 2>/dev/null || echo '확인 필요')"

else
    echo ""
    echo "[DRY-RUN] 실제 변환 없이 통계만 확인합니다..."
    python3 "$SCRIPT_DIR/pst2md.py" \
        --pst "$PST_PATH" \
        --out "$ARCHIVE_ROOT" \
        --cutoff "$CUTOFF" \
        --dry-run \
        2>&1

    echo ""
    echo "실제 실행: $0 --pst \"$PST_PATH\" --execute"
fi
