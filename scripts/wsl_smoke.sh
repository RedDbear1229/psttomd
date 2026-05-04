#!/usr/bin/env bash
# scripts/wsl_smoke.sh — WSL/Linux 환경 스모크 테스트 (P8)
#
# 설치 직후 또는 환경 변경 후 한 번 실행해 다음을 검증한다:
#   1. pst2md 변환 (tests/data/test.pst → 임시 아카이브)
#   2. build-index --rebuild (FTS5 prefix='2 3 4' 인덱스 생성)
#   3. mailgrep 한글 부분일치 검색
#   4. mailview --doctor 진단 출력 (✓ 만 나오면 정상)
#   5. 파일수 vs DB 행수 일치
#   6. P5 카운트 drift 감지: mtime 보존 새 MD 파일 → rebuild 권장 경고
#
# 사용법:
#   bash scripts/wsl_smoke.sh           # 임시 디렉터리에서 실행 후 자동 정리
#   bash scripts/wsl_smoke.sh --keep    # 임시 디렉터리 유지 (디버깅용)
#
# 종료 코드:
#   0 — 모든 검증 성공
#   1 — 실패 (어떤 단계가 실패했는지 stderr 에 출력)

set -u
set -o pipefail

# ── 인수 파싱 ────────────────────────────────────────────────────────────
KEEP=0
for arg in "$@"; do
    case "$arg" in
        --keep|-k) KEEP=1 ;;
        --help|-h)
            sed -n '2,18p' "$0"
            exit 0
            ;;
        *)
            echo "알 수 없는 옵션: $arg" >&2
            exit 2
            ;;
    esac
done

# ── 환경 점검 ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PST="$REPO_ROOT/tests/data/test.pst"

if [[ ! -f "$PST" ]]; then
    echo "✗ tests/data/test.pst 가 없습니다 — repo 루트에서 실행하세요." >&2
    exit 1
fi

for cmd in pst2md build-index mailgrep mailview sqlite3; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "✗ '$cmd' 명령을 찾을 수 없습니다 — 'pip install -e .' 또는 'uv sync' 후 다시 시도하세요." >&2
        exit 1
    fi
done

# ── 임시 아카이브 디렉터리 ──────────────────────────────────────────────
SMOKE_DIR="$(mktemp -d -t pst2md-smoke-XXXXXX)"
trap 'if [[ $KEEP -eq 0 ]]; then rm -rf "$SMOKE_DIR"; fi' EXIT

echo "── pst2md WSL 스모크 ($SMOKE_DIR) ─────────────────────"
FAIL=0

step() {
    echo
    echo "▶ $*"
}

ok() {
    echo "  ✓ $*"
}

fail() {
    echo "  ✗ $*" >&2
    FAIL=1
}

# ── 1) 변환 ─────────────────────────────────────────────────────────────
step "1) pst2md 변환"
if pst2md --pst "$PST" --out "$SMOKE_DIR" --no-index >/dev/null 2>&1; then
    md_count=$(find "$SMOKE_DIR/archive" -name "*.md" 2>/dev/null | wc -l)
    if [[ "$md_count" -gt 0 ]]; then
        ok "변환 완료 — $md_count 개 MD 파일 생성"
    else
        fail "MD 파일이 1개도 생성되지 않았습니다"
    fi
else
    fail "pst2md 가 실패했습니다"
fi

# ── 2) 인덱스 재구축 ───────────────────────────────────────────────────
step "2) build-index --rebuild"
if build-index --archive "$SMOKE_DIR" --rebuild >/dev/null 2>&1; then
    ok "인덱스 생성 성공"
else
    fail "build-index 가 실패했습니다"
fi

# ── 3) prefix index 보유 확인 ──────────────────────────────────────────
step "3) FTS5 prefix='2 3 4' 인덱스 확인"
fts_sql=$(sqlite3 "$SMOKE_DIR/index.sqlite" \
    "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages_fts'" 2>/dev/null || true)
if [[ "$fts_sql" == *"prefix="* ]]; then
    ok "prefix index 적용됨"
else
    fail "messages_fts 에 prefix 옵션이 없음 — 한글 부분일치 검색 약함"
fi

# ── 4) 행수 일치 ───────────────────────────────────────────────────────
step "4) 파일 수 vs DB 행 수"
db_rows=$(sqlite3 "$SMOKE_DIR/index.sqlite" "SELECT COUNT(*) FROM messages" 2>/dev/null || echo 0)
file_count=$(find "$SMOKE_DIR/archive" -name "*.md" 2>/dev/null | wc -l)
if [[ "$db_rows" -eq "$file_count" ]] && [[ "$db_rows" -gt 0 ]]; then
    ok "files=$file_count == DB=$db_rows"
else
    fail "files=$file_count != DB=$db_rows"
fi

# ── 5) mailgrep 한글 부분일치 (test fixture 에 한글 토큰이 있을 때만) ──
step "5) mailgrep 한글 부분일치 검색"
if mailgrep --archive "$SMOKE_DIR" "테" >/dev/null 2>&1; then
    ok "mailgrep 실행 성공 (exit=0)"
else
    rc=$?
    # 매칭이 0건이면 mailgrep 도 0 으로 종료하므로, 실패는 실제 오류일 때만
    fail "mailgrep 실행 실패 (exit=$rc)"
fi

# ── 6) mailview --doctor (✓ 만 있어야 정상) ────────────────────────────
# run_doctor() 는 --archive 플래그 대신 MAIL_ARCHIVE env 로만 archive.root 를
# 오버라이드한다 (lib/config.py).
step "6) mailview --doctor"
doctor_out=$(MAIL_ARCHIVE="$SMOKE_DIR" mailview --doctor 2>&1 || true)
if echo "$doctor_out" | grep -q "fts prefix.*✓"; then
    ok "doctor: prefix index ✓"
else
    fail "doctor 가 prefix index ✓ 를 출력하지 않음"
    echo "$doctor_out" | sed 's/^/    /' >&2
fi
if echo "$doctor_out" | grep -q "diff=+0"; then
    ok "doctor: index rows diff=+0"
else
    fail "doctor 가 diff=+0 을 출력하지 않음"
fi

# ── 7) P5: count drift 감지 ────────────────────────────────────────────
step "7) P5 카운트 drift 감지 (mtime 보존 새 파일)"
drift_dir="$SMOKE_DIR/archive/2099/01/01"
mkdir -p "$drift_dir"
drift_md="$drift_dir/_smoke_drift.md"
printf -- "---\nsubject: smoke-drift\n---\nbody\n" > "$drift_md"
# DB 보다 오래된 mtime 으로 강제 (cp -p / 복원 시뮬레이션)
db_mtime_epoch=$(stat -c %Y "$SMOKE_DIR/index.sqlite" 2>/dev/null || stat -f %m "$SMOKE_DIR/index.sqlite")
old_epoch=$((db_mtime_epoch - 3600))
touch -d "@$old_epoch" "$drift_md" 2>/dev/null || touch -t "$(date -r $old_epoch +%Y%m%d%H%M.%S 2>/dev/null || echo "200001010000")" "$drift_md"
drift_warn=$(MAIL_ARCHIVE="$SMOKE_DIR" mailview --doctor 2>&1 || true)
if echo "$drift_warn" | grep -q "diff=+1"; then
    ok "doctor 가 diff=+1 을 정확히 보고함"
else
    fail "doctor 가 drift 를 감지하지 못함"
fi
rm -f "$drift_md"

# ── 결과 ────────────────────────────────────────────────────────────────
echo
if [[ $FAIL -eq 0 ]]; then
    echo "── 모든 검증 통과 ✓ ──"
    if [[ $KEEP -eq 1 ]]; then
        echo "임시 아카이브 유지됨: $SMOKE_DIR"
    fi
    exit 0
else
    echo "── 일부 검증 실패 ✗ ──" >&2
    echo "임시 아카이브 위치 (조사용): $SMOKE_DIR" >&2
    KEEP=1
    exit 1
fi
