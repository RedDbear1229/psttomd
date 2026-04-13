# 운영 Runbook

## Phase 0 — 환경 설치 (WSL Ubuntu)

```bash
# 시스템 패키지
sudo apt update && sudo apt install -y \
    libpff-dev python3-pff \
    sqlite3 fzf ripgrep bat \
    pandoc

# glow (Markdown 렌더러)
sudo snap install glow
# 또는 Go 설치된 경우:
# go install github.com/charmbracelet/glow@latest

# Python 의존성
cd ~/mailtomd
python3 -m venv .venv && source .venv/bin/activate
pip install libpff-python beautifulsoup4 html2text python-slugify tqdm \
            mail-parser python-dateutil chardet

# CLI 도구를 PATH에 추가 (~/.bashrc 또는 ~/.zshrc)
export MAIL_ARCHIVE="$HOME/mail-archive"
export PATH="$HOME/mailtomd/scripts:$PATH"
```

## Phase 1 — PoC (첫 PST 변환)

```bash
# 1. 가장 작은 PST 선택 (예: 1~5GB짜리)
PST="/mnt/c/Users/YOU/Documents/Outlook/archive_2020.pst"

# 2. dry-run으로 통계 확인
python scripts/pst2md.py --pst "$PST" --dry-run

# 3. 실제 변환
python scripts/pst2md.py --pst "$PST" --out ~/mail-archive

# 4. 인덱스 구축
python scripts/build_index.py --archive ~/mail-archive

# 5. 검색 테스트
mailgrep "견적" --limit 5
mailview "프로젝트"
mailstat summary
```

## Phase 2 — 전체 PST 배치 변환

```bash
# PST 목록 확인
ls -lh "/mnt/c/Users/YOU/Documents/Outlook/"*.pst

# 각 PST 순차 변환 (--resume으로 중단 재개 가능)
for pst in /mnt/c/Users/YOU/Documents/Outlook/*.pst; do
    echo "=== 변환: $pst ==="
    python scripts/pst2md.py --pst "$pst" --out ~/mail-archive --resume
done

# 인덱스 재구축
python scripts/build_index.py --archive ~/mail-archive --rebuild
```

## 월간 운영 배치

```bash
# dry-run 먼저 확인
./scripts/archive_monthly.sh --pst "/mnt/c/.../Outlook Files/outlook.pst"

# 이상 없으면 실행
./scripts/archive_monthly.sh --pst "/mnt/c/.../Outlook Files/outlook.pst" --execute

# Outlook에서 수동 작업:
# 1. 변환된 날짜 범위 메일 선택 → 삭제
# 2. 파일 → 계정 설정 → 데이터 파일 → 해당 PST → 설정 → 지금 압축
```

## Obsidian 위키 갱신

```bash
# 전체 MOC 재생성
python scripts/enrich.py --archive ~/mail-archive

# 개별 갱신
python scripts/enrich.py --people    # 인물 페이지만
python scripts/enrich.py --threads   # 스레드 페이지만
python scripts/enrich.py --projects  # 프로젝트 페이지만
```

## 무결성 검증

```bash
# 샘플 200개 검증 (기본)
python scripts/verify_integrity.py --archive ~/mail-archive

# 전체 검증
python scripts/verify_integrity.py --archive ~/mail-archive --full
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
