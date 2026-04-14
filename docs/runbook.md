# 운영 Runbook

## Phase 0 — 환경 설치 (WSL Ubuntu)

```bash
# 시스템 패키지
sudo apt update && sudo apt install -y \
    libpff-dev python3-pff \
    sqlite3 fzf ripgrep bat \
    curl

# glow (Markdown 렌더러)
sudo snap install glow
# 또는 Go 설치된 경우:
# go install github.com/charmbracelet/glow@latest

# uv 설치 (Python 환경 관리)
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc   # 또는 새 터미널 열기

# Python 의존성 설치 (pyproject.toml 기준)
cd ~/pst2md
uv sync --extra linux   # libpff-python 포함

# 환경변수 (~/.bashrc 또는 ~/.zshrc)
export MAIL_ARCHIVE="$HOME/mail-archive"
```

## Phase 1 — PoC (첫 PST 변환)

```bash
# 1. 가장 작은 PST 선택 (예: 1~5GB짜리)
PST="/mnt/c/Users/YOU/Documents/Outlook/archive_2020.pst"

# 2. dry-run으로 통계 확인
uv run pst2md --pst "$PST" --dry-run

# 3. 실제 변환
uv run pst2md --pst "$PST" --out ~/mail-archive

# 4. 인덱스 구축
uv run build-index --archive ~/mail-archive

# 5. 검색 테스트
uv run mailgrep "견적" --limit 5
uv run mailview "프로젝트"
uv run mailstat summary
```

## Phase 2 — 전체 PST 배치 변환

```bash
# PST 목록 확인
ls -lh "/mnt/c/Users/YOU/Documents/Outlook/"*.pst

# 각 PST 순차 변환 (--resume으로 중단 재개 가능)
for pst in /mnt/c/Users/YOU/Documents/Outlook/*.pst; do
    echo "=== 변환: $pst ==="
    uv run pst2md --pst "$pst" --out ~/mail-archive --resume
done

# 인덱스 재구축
uv run build-index --archive ~/mail-archive --rebuild
```

## 월간 운영 배치

```bash
# dry-run 먼저 확인
uv run archive-monthly --pst "/mnt/c/.../Outlook Files/outlook.pst"

# 이상 없으면 실행
uv run archive-monthly --pst "/mnt/c/.../Outlook Files/outlook.pst" --execute

# Outlook에서 수동 작업:
# 1. 변환된 날짜 범위 메일 선택 → 삭제
# 2. 파일 → 계정 설정 → 데이터 파일 → 해당 PST → 설정 → 지금 압축
```

## Obsidian 위키 갱신

```bash
# 전체 MOC 재생성
uv run enrich --archive ~/mail-archive

# 개별 갱신
uv run enrich --people    # 인물 페이지만
uv run enrich --threads   # 스레드 페이지만
uv run enrich --projects  # 프로젝트 페이지만
```

## 무결성 검증

```bash
# 샘플 200개 검증 (기본)
uv run verify --archive ~/mail-archive

# 전체 검증
uv run verify --archive ~/mail-archive --full
```

## Windows 백업 (rsync)

```bash
# WSL에서 Windows 드라이브로 백업
rsync -av --progress \
    ~/mail-archive/ \
    "/mnt/d/Backup/mail-archive/"

# 또는 restic 사용 (증분 백업)
restic -r /mnt/d/Backup/restic-mail init  # 최초 1회
restic -r /mnt/d/Backup/restic-mail backup ~/mail-archive
```

## 트러블슈팅

### PST가 열리지 않을 때
- Outlook 완전 종료 확인: `tasklist.exe | grep -i outlook`
- 파일 잠금 확인: `lsof "/mnt/c/..."` (WSL에서 동작 안 할 수 있음)
- PST 복사 후 처리: `cp "$PST" ~/temp.pst && python pst2md.py --pst ~/temp.pst`

### 한글 깨질 때
- `PYTHONIOENCODING=utf-8` 환경변수 설정
- `--folder` 옵션으로 문제 폴더만 재변환

### 인덱스 불일치
```bash
python scripts/build_index.py --archive ~/mail-archive --rebuild
```

### 디스크 부족
```bash
# 첨부 파일 용량 확인
mailstat attachments

# 대용량 첨부 이동
du -sh ~/mail-archive/attachments_large/
```
