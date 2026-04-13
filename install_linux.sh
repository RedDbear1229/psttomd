#!/usr/bin/env bash
# install_linux.sh — Linux/WSL 설치 스크립트
#
# 사용법:
#   chmod +x install_linux.sh && ./install_linux.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCHIVE_ROOT="${MAIL_ARCHIVE:-$HOME/mail-archive}"

echo "========================================"
echo "mailtomd Linux/WSL 설치"
echo "  설치 위치: $SCRIPT_DIR"
echo "  아카이브:  $ARCHIVE_ROOT"
echo "========================================"

# --- 시스템 패키지 ---
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
        python3 python3-pip python3-venv
elif command -v dnf &>/dev/null; then
    sudo dnf install -y sqlite fzf ripgrep bat python3 python3-pip
elif command -v pacman &>/dev/null; then
    sudo pacman -Sy --noconfirm sqlite fzf ripgrep bat python python-pip
else
    echo "  경고: 패키지 매니저를 인식할 수 없습니다. 수동 설치가 필요할 수 있습니다."
fi

# --- glow ---
echo ""
echo "[2/4] glow 설치 확인..."
if ! command -v glow &>/dev/null; then
    if command -v snap &>/dev/null; then
        sudo snap install glow
    elif command -v go &>/dev/null; then
        go install github.com/charmbracelet/glow@latest
    else
        # GitHub Releases에서 직접 다운로드 (amd64)
        GLOW_VER="2.0.0"
        ARCH=$(uname -m)
        case "$ARCH" in
            x86_64) GLOW_ARCH="amd64" ;;
            aarch64) GLOW_ARCH="arm64" ;;
            *) GLOW_ARCH="amd64" ;;
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

# --- Python 가상환경 ---
echo ""
echo "[3/4] Python 가상환경 및 패키지 설치..."
cd "$SCRIPT_DIR"

if [[ ! -d ".venv" ]]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

pip install --quiet --upgrade pip
pip install --quiet -e ".[linux]"

echo "  패키지 설치 완료"

# --- 설정 파일 초기화 ---
echo ""
echo "[4/4] 설정 파일 초기화..."
python3 -c "
import sys
sys.path.insert(0, 'scripts')
from lib.config import init_config_file
p = init_config_file(archive='$ARCHIVE_ROOT')
print(f'  설정 파일: {p}')
"

# --- PATH 안내 ---
echo ""
echo "========================================"
echo "설치 완료!"
echo ""
echo "~/.bashrc 또는 ~/.zshrc 에 다음을 추가하세요:"
echo ""
echo "  source $SCRIPT_DIR/.venv/bin/activate"
echo "  export MAIL_ARCHIVE=\"$ARCHIVE_ROOT\""
echo "  export PATH=\"$SCRIPT_DIR/scripts:\$PATH\""
echo ""
echo "또는 pip 설치 후 전역 명령어로 사용:"
echo "  pip install -e .[linux]"
echo "  pst2md --pst /mnt/c/.../archive.pst"
echo "  mailgrep \"견적서\""
echo "========================================"
