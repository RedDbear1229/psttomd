#!/usr/bin/env bash
# install_linux.sh — Linux/WSL 설치 스크립트 (uv 기반)
#
# 사용법:
#   chmod +x install_linux.sh && ./install_linux.sh
#
# 의존성 관리: uv (https://docs.astral.sh/uv)
#   - Python 가상환경 자동 생성 (.venv/)
#   - uv sync --extra linux  → pyproject.toml 기준 재현 가능 설치

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCHIVE_ROOT="${MAIL_ARCHIVE:-$HOME/mail-archive}"

echo "========================================"
echo "mailtomd Linux/WSL 설치 (uv)"
echo "  설치 위치: $SCRIPT_DIR"
echo "  아카이브:  $ARCHIVE_ROOT"
echo "========================================"

# ── 1/4 시스템 패키지 ────────────────────────────────────────────────────
echo ""
echo "[1/4] 시스템 패키지 설치..."
if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y \
        libpff-dev \
        pst-utils \
        sqlite3 \
        fzf \
        ripgrep \
        bat \
        curl
elif command -v dnf &>/dev/null; then
    sudo dnf install -y sqlite fzf ripgrep bat curl
elif command -v pacman &>/dev/null; then
    sudo pacman -Sy --noconfirm sqlite fzf ripgrep bat curl
else
    echo "  경고: 패키지 매니저를 인식할 수 없습니다. 수동 설치가 필요할 수 있습니다."
fi

# ── 2/4 glow ─────────────────────────────────────────────────────────────
echo ""
echo "[2/4] glow 설치 확인..."
if ! command -v glow &>/dev/null; then
    if command -v snap &>/dev/null; then
        sudo snap install glow
    elif command -v go &>/dev/null; then
        go install github.com/charmbracelet/glow@latest
    else
        GLOW_VER="2.0.0"
        case "$(uname -m)" in
            x86_64)  GLOW_ARCH="amd64" ;;
            aarch64) GLOW_ARCH="arm64" ;;
            *)       GLOW_ARCH="amd64" ;;
        esac
        echo "  glow 바이너리 다운로드 중 (v${GLOW_VER})..."
        curl -fsSL \
            "https://github.com/charmbracelet/glow/releases/download/v${GLOW_VER}/glow_Linux_${GLOW_ARCH}.tar.gz" \
            | tar -xz -C /tmp glow
        sudo mv /tmp/glow /usr/local/bin/glow
    fi
    echo "  glow 설치 완료: $(glow --version 2>/dev/null || echo '확인 필요')"
else
    echo "  glow 이미 설치됨: $(glow --version 2>/dev/null)"
fi

# ── 3/4 uv + Python 패키지 ───────────────────────────────────────────────
echo ""
echo "[3/4] uv 및 Python 패키지 설치..."

# uv 설치 (미설치 시)
if ! command -v uv &>/dev/null; then
    echo "  uv 설치 중..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # 현재 셸에 PATH 반영
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
echo "  uv: $(uv --version)"

cd "$SCRIPT_DIR"

# .venv 생성 + 의존성 설치 (pyproject.toml 기준)
# --extra linux: libpff-python (pypff 백엔드) 포함
uv sync --extra linux
echo "  패키지 설치 완료: $(uv run python --version)"

# ── 4/4 설정 파일 초기화 ─────────────────────────────────────────────────
echo ""
echo "[4/4] 설정 파일 초기화..."
uv run python -c "
import sys
sys.path.insert(0, 'scripts')
from lib.config import init_config_file
p = init_config_file(archive='$ARCHIVE_ROOT')
print(f'  설정 파일: {p}')
"

# ── 완료 안내 ─────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "설치 완료!"
echo ""
echo "사용 방법 (1) — uv run (venv 활성화 불필요):"
echo "  uv run pst2md --pst /mnt/c/.../archive.pst"
echo "  uv run mailgrep \"견적서\""
echo "  uv run mailview"
echo "  uv run mailstat summary"
echo ""
echo "사용 방법 (2) — venv 활성화 후 직접 실행:"
echo "  source $SCRIPT_DIR/.venv/bin/activate"
echo "  pst2md --pst /mnt/c/.../archive.pst"
echo ""
echo "~/.bashrc 또는 ~/.zshrc 에 다음을 추가하면 편리합니다:"
echo "  export MAIL_ARCHIVE=\"$ARCHIVE_ROOT\""
echo "  source $SCRIPT_DIR/.venv/bin/activate"
echo "========================================"
