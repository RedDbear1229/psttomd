# pst2md 사용 설명서

> Outlook PST 파일을 Markdown으로 변환해 CLI와 Obsidian으로 관리하는 아카이브 시스템

## 목차

1. [개요](#1-개요)
2. [시스템 요구사항](#2-시스템-요구사항)
3. [설치](#3-설치)
   - [Linux / WSL](#31-linux--wsl)
   - [Windows Native](#32-windows-native)
4. [설정 파일 (config.toml)](#4-설정-파일-configtoml)
5. [첫 번째 변환 (PoC)](#5-첫-번째-변환-poc)
6. [전체 PST 배치 변환](#6-전체-pst-배치-변환)
7. [CLI 도구 상세](#7-cli-도구-상세)
   - [mailgrep — 검색](#71-mailgrep--검색)
   - [mailview — 인터랙티브 뷰어](#72-mailview--인터랙티브-뷰어)
   - [mailstat — 통계](#73-mailstat--통계)
   - [pst2md — 변환기](#74-pst2md--변환기)
   - [build-index — 인덱스 빌더](#75-build-index--인덱스-빌더)
   - [enrich — Obsidian 위키화](#76-enrich--obsidian-위키화)
   - [verify — 무결성 검증](#77-verify--무결성-검증)
   - [archive-monthly — 월간 배치](#78-archive-monthly--월간-배치)
8. [Obsidian 연동](#8-obsidian-연동)
9. [아카이브 구조 및 Markdown 스키마](#9-아카이브-구조-및-markdown-스키마)
10. [월간 운영 절차](#10-월간-운영-절차)
11. [트러블슈팅](#11-트러블슈팅)
12. [FAQ](#12-faq)

---

## 1. 개요

### 해결하는 문제

Outlook PST 파일이 수십 GB로 불어나면 다음 문제가 발생합니다.

- 검색 인덱스 손상 및 검색 실패
- Outlook 기동·동기화 지연
- 파일 잠금으로 인한 백업 실패
- 바이너리 포맷이라 버전 관리·스크립트 처리 불가

### 해결 방식

```
PST (90GB)
    │
    ▼ pst2md (변환)
Markdown 아카이브 + SQLite FTS5 인덱스
    │                    │
    ▼                    ▼
mailview                mailgrep
(fzf + glow)            (FTS5 검색)
    │
    ▼
Obsidian Vault
(그래프·위키·Dataview)
```

- 최근 12개월 메일만 Outlook/PST에 유지 → PST 10~15GB 수준으로 축소
- 이전 메일은 Markdown 아카이브에서 ripgrep·FTS5로 밀리초 단위 검색
- 모든 메일이 일반 텍스트 파일 → 백업·버전관리·스크립트 처리 가능

### 지원 환경

| 환경 | PST 파서 | 뷰어 |
|---|---|---|
| Linux / WSL | libpff-python (pypff) | glow + fzf |
| WSL (대안) | readpst CLI | glow + fzf |
| Windows Native | Outlook COM API (pywin32) | glow + fzf (Windows Terminal) |

---

## 2. 시스템 요구사항

### 공통

| 항목 | 최소 사양 |
|---|---|
| Python | 3.10 이상 |
| 여유 디스크 | PST 총 용량의 1.5배 (변환 중 임시 공간 포함) |
| RAM | 4GB 이상 권장 (대용량 PST 처리 시) |

### Linux / WSL

```
Ubuntu 22.04 LTS 이상 (권장)
또는 Debian 11+, Fedora 36+
```

필수 CLI 도구:

| 도구 | 용도 | 설치 |
|---|---|---|
| fzf | 인터랙티브 선택 | `apt install fzf` |
| glow | Markdown 렌더링 | `snap install glow` |
| ripgrep | 파일 내 검색 | `apt install ripgrep` |
| bat | 원문 표시 | `apt install bat` |
| sqlite3 | 인덱스 조회 | `apt install sqlite3` |

### Windows

```
Windows 10 21H2 이상 / Windows 11
PowerShell 5.1 이상
Microsoft Outlook 설치됨 (win32com 백엔드 사용 시)
winget (Windows Package Manager)
```

필수 CLI 도구 (winget으로 자동 설치):

| 도구 | winget ID |
|---|---|
| fzf | `fzf` |
| glow | `charmbracelet.glow` |
| bat | `sharkdp.bat` |
| ripgrep | `BurntSushi.ripgrep.MSVC` |
| sqlite3 | `SQLite.SQLite` |

---

## 3. 설치

### 3.1 Linux / WSL

#### 자동 설치 (권장)

```bash
git clone <repo> ~/pst2md
cd ~/pst2md
chmod +x install_linux.sh
./install_linux.sh
```

설치 스크립트가 다음을 수행합니다.

1. 시스템 패키지 설치 (libpff, fzf, ripgrep, bat, sqlite3)
2. glow 설치 (snap 또는 직접 다운로드)
3. Python 가상환경 생성 (`.venv/`)
4. `pip install -e ".[linux]"` 실행
5. `~/.pst2md/config.toml` 초기 생성

#### 수동 설치 (uv)

```bash
# 1. 시스템 패키지
sudo apt update && sudo apt install -y \
    libpff-dev pst-utils \
    sqlite3 fzf ripgrep bat

# 2. glow
sudo snap install glow
# snap 없을 때:
# go install github.com/charmbracelet/glow@latest

# 3. uv 설치 (Python 환경 관리)
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc

# 4. Python 의존성
cd ~/pst2md
uv sync --extra linux

# 5. 설정 파일 생성
python3 -c "
import sys; sys.path.insert(0,'scripts')
from lib.config import init_config_file
print(init_config_file())
"
```

> **Android/Termux 환경**: uv는 `aarch64-linux-android`를 지원하지 않습니다.
> `pip install` 을 직접 사용하세요 → [트러블슈팅: Android/Termux 환경](#android--termux-환경)

#### PATH 설정

`~/.bashrc` 또는 `~/.zshrc` 에 추가:

```bash
# pst2md (uv 환경)
export PATH="$HOME/.local/bin:$PATH"   # uv 경로
export MAIL_ARCHIVE="$HOME/mail-archive"
```

적용:

```bash
source ~/.bashrc
```

---

### 3.2 Windows Native

#### 자동 설치 (권장)

PowerShell을 **관리자 권한**으로 열고 실행:

```powershell
# 실행 정책 일시 허용
Set-ExecutionPolicy Bypass -Scope Process -Force

# 설치
cd C:\Users\YOU\pst2md
.\install_windows.ps1
```

아카이브 위치를 지정하려면:

```powershell
.\install_windows.ps1 -ArchiveRoot "D:\mail-archive"
```

Outlook이 없는 환경에서 readpst를 사용하려면 (WSL 필요):

```powershell
.\install_windows.ps1 -Backend readpst
```

#### 수동 설치

```powershell
# 1. winget으로 CLI 도구 설치
winget install fzf
winget install charmbracelet.glow
winget install sharkdp.bat
winget install BurntSushi.ripgrep.MSVC
winget install SQLite.SQLite

# 2. uv 설치
winget install astral-sh.uv

# 3. Python 의존성
cd C:\Users\YOU\pst2md
uv sync --extra win32

# 4. pywin32 초기화 (win32com 백엔드 사용 시 필수)
uv run python -m pywin32_postinstall -install

# 5. 설정 파일 생성
uv run python -c "
import sys; sys.path.insert(0,'scripts')
from lib.config import init_config_file
print(init_config_file())
"
```

#### PATH 설정 (PowerShell 프로파일)

`$PROFILE` 파일에 추가:

```powershell
# PowerShell 프로파일 열기
notepad $PROFILE

# 추가할 내용:
$env:MAIL_ARCHIVE = "C:\Users\YOU\mail-archive"
# pip install 후 전역 명령어로 자동 등록됨
```

---

## 4. 설정 파일 (config.toml)

위치: `~/.pst2md/config.toml` (Linux) / `%USERPROFILE%\.pst2md\config.toml` (Windows)

### 전체 옵션

```toml
# 아카이브 루트 디렉터리
[archive]
root = "/home/user/mail-archive"       # Linux/WSL
# root = "C:/Users/YOU/mail-archive"  # Windows (슬래시 사용)

# PST 파서 백엔드
# auto     — 플랫폼에 따라 자동 선택 (Linux→pypff, Windows→win32com)
# pypff    — libpff-python 직접 사용 (Linux/WSL)
# readpst  — readpst CLI → EML → mail-parser (WSL/Linux)
# win32com — Outlook COM API (Windows, Outlook 설치 필요)
pst_backend = "auto"

# CLI 도구 경로 (PATH에 없는 경우 절대 경로 지정)
[tools]
fzf     = "fzf"
glow    = "glow"
bat     = "bat"
sqlite3 = "sqlite3"
rg      = "rg"

# win32com 백엔드 설정 (Windows 전용)
[win32com]
outlook_profile = ""   # 빈 문자열 = 기본 Outlook 프로파일
```

### 환경변수 오버라이드

`MAIL_ARCHIVE` 환경변수가 있으면 `config.toml`의 `archive.root`를 덮어씁니다.

```bash
# 임시로 다른 아카이브 사용
MAIL_ARCHIVE=/mnt/external/mail-archive mailgrep "견적서"
```

---

## 5. 첫 번째 변환 (PoC)

처음에는 작은 PST 파일(1~5GB)로 테스트하는 것을 권장합니다.

### PST 파일 위치 확인

**Windows/WSL:**
```
일반적인 위치:
  C:\Users\<이름>\Documents\Outlook Files\
  C:\Users\<이름>\AppData\Local\Microsoft\Outlook\
```

**WSL에서 확인:**
```bash
ls /mnt/c/Users/*/Documents/Outlook\ Files/*.pst 2>/dev/null
ls /mnt/c/Users/*/AppData/Local/Microsoft/Outlook/*.pst 2>/dev/null
```

### Step 0: 샘플 PST로 동작 검증 (선택)

실제 PST 파일을 투입하기 전에 포함된 샘플로 파이프라인을 먼저 검증합니다.

```bash
# dry-run 테스트
uv run pst2md --pst tests/data/test.pst --out ~/mail-archive-test --dry-run
# 예상 출력: total: 4, converted: 4, error: 0

# 실제 변환 후 resume 확인
uv run pst2md --pst tests/data/test.pst --out ~/mail-archive-test
uv run pst2md --pst tests/data/test.pst --out ~/mail-archive-test --resume
# 예상 출력: total: 4, skipped: 4, converted: 0

rm -rf ~/mail-archive-test
```

### Step 1: dry-run으로 사전 확인

```bash
# Linux/WSL
uv run pst2md \
    --pst "/mnt/c/Users/YOU/Documents/Outlook Files/archive_2020.pst" \
    --dry-run

# Windows
uv run pst2md `
    --pst "C:\Users\YOU\Documents\Outlook Files\archive_2020.pst" `
    --dry-run
```

출력 예시:
```
=== 변환 결과 ===
  total       : 12,450
  converted   : 12,340
  skipped     :     89
  error        :     21
  attachments :  3,201
```

### Step 2: 실제 변환

```bash
# Linux/WSL (Outlook 종료 후 실행)
uv run pst2md \
    --pst "/mnt/c/Users/YOU/Documents/Outlook Files/archive_2020.pst"

# Windows (Outlook 실행 중에도 가능 - win32com 백엔드)
uv run pst2md `
    --pst "C:\Users\YOU\Documents\Outlook Files\archive_2020.pst"
```

### Step 3: 인덱스 구축

```bash
uv run build-index
```

> **참고**: `pst2md` 는 변환 완료 후 자동으로 증분 인덱싱을 수행한다.
> 대량 배치(수만 건) 를 순차 변환할 때는 `--no-index` 로 건너뛴 뒤 마지막에
> `build-index --rebuild` 를 한 번 돌리는 쪽이 효율적이다.

### Step 4: 동작 확인

```bash
# 검색 테스트
uv run mailgrep "테스트" --limit 5

# 뷰어 테스트
uv run mailview

# 통계 확인
uv run mailstat summary
```

---

## 6. 전체 PST 배치 변환

### PST 목록 확인

```bash
# WSL
ls -lh "/mnt/c/Users/YOU/Documents/Outlook Files/"*.pst

# Windows
dir "C:\Users\YOU\Documents\Outlook Files\*.pst"
```

### 순차 변환

**Linux/WSL:**
```bash
PST_DIR="/mnt/c/Users/YOU/Documents/Outlook Files"

for pst in "$PST_DIR"/*.pst; do
    echo "==============================="
    echo "변환: $pst"
    echo "==============================="
    uv run pst2md \
        --pst "$pst" \
        --resume \
        --no-index    # 배치 중에는 인덱싱 건너뛰기
done

# 전체 완료 후 인덱스 재구축
uv run build-index --rebuild
```

**Windows (PowerShell):**
```powershell
$pstDir = "C:\Users\YOU\Documents\Outlook Files"

Get-ChildItem "$pstDir\*.pst" | ForEach-Object {
    Write-Host "==============================="
    Write-Host "변환: $($_.FullName)"
    Write-Host "==============================="
    uv run pst2md --pst "$($_.FullName)" --resume
}

# 전체 완료 후 인덱스 재구축
uv run build-index --rebuild
```

### cutoff 날짜 지정 (12개월 이전만 변환)

최근 메일은 Outlook에 유지하고 오래된 메일만 변환하려면:

```bash
# 2024-01-01 이전 메일만 변환
python scripts/pst2md.py \
    --pst "/mnt/c/.../archive.pst" \
    --cutoff 2024-01-01
```

### 중단 후 재개

변환 중 인터럽트된 경우 `--resume` 옵션으로 이어서 실행합니다.
이미 변환된 Message-ID는 `.state.json` 체크포인트를 통해 건너뜁니다.

```bash
python scripts/pst2md.py --pst "/mnt/c/.../archive.pst" --resume
```

---

## 7. CLI 도구 상세

### 7.1 mailgrep — 검색

SQLite FTS5 기반 고속 전문검색.

#### 기본 사용법

```bash
# 키워드 검색
mailgrep "견적서"

# 발신자 필터
mailgrep "계약" --from 홍길동

# 날짜 범위
mailgrep "회의록" --after 2023-01-01 --before 2023-12-31

# 폴더 지정
mailgrep "결재" --folder "Inbox/Project-A"

# 스레드 전체 조회
mailgrep --thread t_4a9f3c2b
```

#### 전체 옵션

```
mailgrep [키워드] [옵션]

옵션:
  --from TEXT        발신자 이름/주소 부분 일치
  --to TEXT          수신자 주소 부분 일치
  --after DATE       이 날짜 이후 (YYYY-MM-DD)
  --before DATE      이 날짜 이전 (YYYY-MM-DD)
  --folder TEXT      폴더 경로 부분 일치
  --thread TEXT      스레드 ID 완전 일치
  --limit N          최대 결과 수 (기본: 50)
  --json             JSON Lines 출력 (파이프 처리용)
  --paths-only       파일 경로만 출력 (mailview 파이프용)
  --archive PATH     아카이브 루트 (config.toml 기본값 오버라이드)
```

#### 출력 형식

```
날짜         발신자                            제목
---------- -------------------------------- --------------------------------------------------
2024-03-15 홍길동 <hong@example.com>          프로젝트 A 견적 회신
  → /home/user/mail-archive/archive/2024/03/15/20240315-0932__...md
```

#### JSON 출력 (스크립트 연동)

```bash
# jq로 파이프 처리
mailgrep "견적" --json | jq '.subject'

# 특정 날짜 이후 메일 개수
mailgrep --after 2024-01-01 --json --limit 9999 | wc -l
```

#### FTS5 고급 검색

FTS5 연산자를 그대로 사용할 수 있습니다.

```bash
# AND 검색 (두 단어 모두 포함)
mailgrep "견적 AND 계약"

# 구문 검색
mailgrep '"프로젝트 A"'

# 특정 컬럼 검색 (subject: 제목에서만)
mailgrep "subject:견적서"
```

---

### 7.2 mailview — 인터랙티브 뷰어

fzf로 메일을 선택하고 glow로 렌더링합니다.

#### 기본 사용법

```bash
# 최근 100통 목록
mailview

# 키워드 검색 후 선택
mailview "견적서"

# 필터 조합
mailview "계약" --from 홍길동 --after 2024-01-01
```

#### 키 바인딩 (fzf 화면)

| 키 | 동작 |
|---|---|
| `Enter` | 선택한 메일을 glow로 전체 열람 |
| `Ctrl-P` | bat/less로 frontmatter 포함 원문 표시 |
| `Ctrl-O` | `$EDITOR` (Linux) / `notepad` (Windows)로 열기 |
| `↑↓` | 목록 이동 |
| `/` | 목록 내 추가 필터 |
| `Ctrl-R` / `Esc` | 쿼리·필터 초기화 |
| `:q`+Enter | 종료 (Linux/WSL 전용, vim 스타일) |

오른쪽 패널에 선택 메일 미리보기가 실시간으로 표시됩니다.
미리보기는 YAML frontmatter 를 숨기고 본문부터 glow 로 렌더링합니다 (`awk` 필요).

#### 진단 / 한글 입력 문제

```bash
mailview --doctor       # 플랫폼/locale/fzf/glow/bat/awk 버전 점검
```

한글 입력이 잘 되지 않을 때는 [docs/hangul-input.md](hangul-input.md) 를 참고.

#### 전체 옵션

```
mailview [키워드] [옵션]

옵션:
  --from TEXT        발신자 필터
  --after DATE       날짜 필터
  --before DATE      날짜 필터
  --folder TEXT      폴더 필터
  --thread TEXT      스레드 ID
  --archive PATH     아카이브 루트
```

---

### 7.3 mailstat — 통계

#### 서브커맨드 목록

```bash
mailstat summary       # 전체 요약 (기본)
mailstat monthly       # 월별 수신량 (최근 36개월)
mailstat senders       # 상위 발신자 Top 20
mailstat senders --top 50   # Top 50
mailstat folders       # 폴더별 통계
mailstat threads       # 긴 스레드 Top 20
mailstat attachments   # 첨부 파일 용량
mailstat range         # 날짜 범위 확인
```

#### summary 출력 예시

```
=== 아카이브 요약 ===
  총 메일:      234,512통
  고유 발신자:   8,234명
  스레드 수:    67,890개
  PST 파일:         12개
  기간:         2010-03-01 ~ 2023-12-31
  archive       : 18.3 GB (234,512파일)
  attachments   :  4.1 GB (12,301파일)
  attachments_large:  2.8 GB (  134파일)
```

---

### 7.4 pst2md — 변환기

#### 기본 사용법

```bash
# 기본 변환
pst2md --pst /path/to/archive.pst

# cutoff 지정 (2024년 이전만)
pst2md --pst /path/to/archive.pst --cutoff 2024-01-01

# 특정 폴더만 (정규식)
pst2md --pst /path/to/archive.pst --folder "Inbox/Project-A"

# 중단 후 재개
pst2md --pst /path/to/archive.pst --resume

# 통계만 확인 (파일 미생성)
pst2md --pst /path/to/archive.pst --dry-run
```

#### 전체 옵션

```
pst2md --pst <경로> [옵션]

필수:
  --pst PATH         PST 파일 경로

선택:
  --out PATH         출력 루트 (기본: config.toml의 archive.root)
  --cutoff DATE      이 날짜 이후 메일 제외 (YYYY-MM-DD)
  --folder REGEX     폴더 경로 필터 (정규식)
  --resume           체크포인트에서 이어 시작
  --dry-run          파일 미생성, 통계만 출력
  --backend NAME     PST 백엔드 강제 지정 (pypff|readpst|win32com|auto)
```

#### 백엔드 선택

```bash
# 자동 선택 (기본: 플랫폼에 따라)
pst2md --pst archive.pst

# pypff 강제 지정 (Linux/WSL)
pst2md --pst archive.pst --backend pypff

# Windows에서 readpst 사용 (WSL readpst 경유)
pst2md --pst archive.pst --backend readpst

# Outlook COM API (Windows)
pst2md --pst archive.pst --backend win32com
```

---

### 7.5 build-index — 인덱스 빌더

#### 기본 사용법

```bash
# 스테이징 파일(index_staging.jsonl) 처리 (pst2md 변환 후 자동 생성)
build-index

# 전체 아카이브 디렉터리 재스캔
build-index --rebuild

# 아카이브 경로 지정
build-index --archive /mnt/d/mail-archive
```

#### 전체 옵션

```
build-index [옵션]

  --archive PATH     아카이브 루트 (기본: config.toml)
  --rebuild          전체 재구축 (기존 인덱스 초기화)
  --incremental      스테이징 파일만 처리 (기본 동작)
```

---

### 7.6 enrich — Obsidian 위키화

#### 기본 사용법

```bash
# 전체 MOC 생성/갱신
enrich

# 인물 페이지만
enrich --people

# 스레드 요약 페이지만
enrich --threads

# 프로젝트 태그 페이지만
enrich --projects
```

생성되는 파일:

```
~/mail-archive/
├── people/
│   └── hong@example.com.md   # 인물별 스레드 타임라인
├── threads/
│   └── t_4a9f3c2b.md         # 스레드 참여자·첨부 요약
└── projects/
    ├── 계약.md
    ├── 견적.md
    └── 회의.md
```

---

### 7.7 verify — 무결성 검증

```bash
# 샘플 200개 검증 (기본)
verify

# 샘플 수 지정
verify --sample 500

# 전체 파일 검증 (시간 소요)
verify --full
```

검증 항목:

- frontmatter YAML 파싱 가능 여부
- 필수 필드 존재 (msgid, date, from, subject)
- 첨부 파일 SHA-256 해시 일치
- 한글 인코딩 정상
- DB 레코드 수 ↔ MD 파일 수 일치

---

### 7.8 archive-monthly — 월간 배치

12개월 이상 경과한 메일을 PST에서 변환하고 Outlook PST를 슬림하게 유지합니다.

#### 기본 사용법

```bash
# 1단계: dry-run으로 변환 대상 확인
archive-monthly --pst "/mnt/c/.../Outlook Files/outlook.pst"

# 2단계: 실제 실행
archive-monthly --pst "/mnt/c/.../Outlook Files/outlook.pst" --execute

# cutoff 날짜 직접 지정
archive-monthly --pst "/mnt/c/.../outlook.pst" --cutoff 2023-01-01 --execute
```

**Windows (PowerShell):**
```powershell
# dry-run
archive-monthly --pst "C:\Users\YOU\Documents\Outlook Files\outlook.pst"

# 실행
archive-monthly --pst "C:\Users\YOU\Documents\Outlook Files\outlook.pst" --execute
```

#### 전체 옵션

```
archive-monthly --pst <경로> [옵션]

필수:
  --pst PATH         변환할 PST 파일 경로

선택:
  --execute          실제 실행 (기본: dry-run)
  --cutoff DATE      이 날짜 이후 제외 (기본: 오늘로부터 12개월 전)
  --archive PATH     아카이브 루트
  --no-enrich        Obsidian MOC 갱신 건너뜀
  --backend NAME     PST 백엔드 강제 지정
```

#### 배치 완료 후 Outlook에서 수동 작업

1. 변환된 날짜 범위의 메일 선택 → 삭제 (예: 2022년 이전 메일)
2. `파일` → `계정 설정` → `데이터 파일` 탭 → 해당 PST 선택 → `설정` → `지금 압축`
3. Outlook 재기동 후 PST 크기 확인

---

## 8. Obsidian 연동

### Vault 열기

**Windows:**

1. Obsidian 실행
2. `Open folder as vault` 클릭
3. 경로 입력: `\\wsl$\Ubuntu\home\<사용자명>\mail-archive`

또는 Windows 파일 탐색기에서 `\\wsl$\Ubuntu\home\<사용자명>\mail-archive`를 즐겨찾기에 추가 후 드래그&드롭.

**Linux:**

```bash
obsidian ~/mail-archive &
```

### 권장 플러그인 (Community Plugins)

Obsidian 설정 → Community plugins → Browse에서 설치:

| 플러그인 | 역할 |
|---|---|
| **Dataview** | 메일 쿼리, 통계 대시보드 |
| **Omnisearch** | Vault 내 전문검색 |
| **Graph Analysis** | 인물·프로젝트 중심성 분석 |
| **Templater** | 새 메일 수동 입력 템플릿 |

### 유용한 Dataview 쿼리

노트 아무 곳에나 붙여넣어 사용합니다.

**최근 30일 수신 메일:**
```dataview
TABLE date, from, subject
FROM "archive"
WHERE date >= date(today) - dur(30 days)
SORT date DESC
LIMIT 50
```

**첨부 파일이 있는 메일:**
```dataview
TABLE date, from, subject
FROM "archive"
WHERE attachments != []
SORT date DESC
LIMIT 30
```

**발신자별 메일 수:**
```dataview
TABLE length(rows) as count, rows.from[0] as sender
FROM "archive"
GROUP BY from
SORT count DESC
LIMIT 20
```

**특정 인물의 스레드:**
```dataview
TABLE date, subject, thread
FROM "archive"
WHERE contains(from, "hong@example.com") OR contains(to, "hong@example.com")
SORT date DESC
```

### Graph View 활용

`enrich` 실행 후 그래프 뷰를 열면:
- `people/` 노드: 인물 중심성 (메일 많이 주고받은 사람일수록 크게 표시)
- `threads/` 노드: 스레드 연결 구조
- `projects/` 노드: 프로젝트별 메일 클러스터

---

## 9. 아카이브 구조 및 Markdown 스키마

### 디렉터리 구조

```
~/mail-archive/
├── archive/
│   └── 2024/
│       └── 03/
│           └── 15/
│               └── 20240315-0932__project-a-quote__4a9f3c2b.md
├── attachments/
│   └── ab/
│       └── abcd1234...pdf          # SHA-256 기반 CAS
├── attachments_large/              # 50MB 초과 첨부
│   └── fe/
│       └── fedc9876...zip
├── people/
│   └── hong@example.com.md
├── threads/
│   └── t_4a9f3c2b.md
├── projects/
│   ├── 계약.md
│   └── 견적.md
├── docs/
│   └── obsidian-setup.md
├── index.sqlite                    # FTS5 전문검색 인덱스
├── .state.json                     # 변환 체크포인트
└── .obsidian/                      # Obsidian 설정
```

### Markdown 파일 구조

```markdown
---
msgid: "<unique-message-id@host>"
date: 2024-03-15T09:32:00+09:00
from: "홍길동 <hong@example.com>"
to: ["kim@example.com", "lee@example.com"]
cc: []
subject: "프로젝트 A 견적 회신"
folder: "Inbox/Project-A"
thread: "t_4a9f3c2b"
in_reply_to: "<previous@host>"
references: ["<root@host>", "<previous@host>"]
attachments:
  - {name: "견적서.pdf", sha256: "abcd1234...", size: 123456, path: "attachments/ab/abcd...pdf"}
tags: ["inbox", "project-a"]
source_pst: "archive_2023.pst"
---

# 프로젝트 A 견적 회신

본문 내용...

> 인용된 이전 메일...

---

관련: [[t_4a9f3c2b]] · [[hong@example.com|홍길동]] · [[project-a]]
```

### 파일명 규칙

```
YYYYMMDD-HHMM__<제목-슬러그>__<msgid-해시8>.md

예시:
  20240315-0932__project-a-quote-reply__4a9f3c2b.md
```

- **날짜+시간 프리픽스**: 디렉터리 내 시간순 정렬
- **슬러그**: 한글·특수문자를 ASCII로 변환, 최대 40자
- **해시**: Message-ID의 SHA-1 앞 8자리, 동일 제목 간 충돌 방지

---

## 10. 월간 운영 절차

### 권장 일정

| 주기 | 작업 |
|---|---|
| 매월 1일 | `archive-monthly` dry-run 결과 확인 |
| 매월 첫 주말 | `archive-monthly --execute` 실행 + Outlook compact |
| 분기 1회 | `verify --full` 전체 무결성 검증 |
| 분기 1회 | Windows로 rsync 백업 |

### 월간 배치 체크리스트

```
□ archive-monthly dry-run 실행 → 변환 대상 메일 수 확인
□ Outlook 백업 (PST 파일 수동 복사)
□ archive-monthly --execute 실행
□ mailstat summary 로 변환 결과 확인
□ verify --sample 500 무결성 검증
□ Outlook에서 12개월+ 경과 메일 삭제
□ Outlook PST compact (파일 → 계정 설정 → 데이터 파일 → 지금 압축)
□ Outlook 재기동 후 PST 크기 감소 확인
□ rsync로 Windows/외장 스토리지에 백업
```

### Windows로 백업

**WSL에서:**
```bash
# Windows 드라이브로 복사
rsync -av --progress \
    ~/mail-archive/ \
    "/mnt/d/Backup/mail-archive/"

# 또는 restic으로 증분 백업
restic -r /mnt/d/Backup/restic-mail init    # 최초 1회
restic -r /mnt/d/Backup/restic-mail backup ~/mail-archive
restic -r /mnt/d/Backup/restic-mail snapshots
```

**Windows PowerShell:**
```powershell
robocopy C:\Users\YOU\mail-archive D:\Backup\mail-archive /MIR /MT:8
```

---

## 11. 트러블슈팅

### PST 파일을 열 수 없음

```
오류: unable to open file / file is locked
```

**원인 및 해결:**

1. Outlook이 실행 중 → Outlook 완전 종료 후 재시도
   ```bash
   # WSL에서 확인
   tasklist.exe | grep -i outlook
   ```

2. pypff 백엔드에서 win32com으로 전환:
   ```bash
   pst2md --pst archive.pst --backend win32com
   ```

3. PST를 WSL 내부로 복사 후 처리:
   ```bash
   cp "/mnt/c/Users/YOU/Documents/Outlook Files/archive.pst" ~/temp.pst
   pst2md --pst ~/temp.pst
   rm ~/temp.pst
   ```

---

### 한글이 깨짐

```
subject: "¿¸¼º ¾Ëç¸² ¸ÞÀÏ"
```

**원인:** CP949/EUC-KR로 인코딩된 한국어 메일

**해결:**
```bash
# 환경변수 설정
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

# 재변환 (--resume으로 기존 변환 건너뜀)
pst2md --pst archive.pst --resume
```

---

### 인덱스와 파일 수 불일치

```bash
# 인덱스 재구축
build-index --rebuild

# 확인
mailstat summary
verify --sample 200
```

---

### glow가 없다는 오류

```bash
# Ubuntu/Debian
sudo snap install glow

# snap 없을 때 - GitHub Releases에서 직접 다운로드
curl -fsSL https://github.com/charmbracelet/glow/releases/latest/download/glow_Linux_x86_64.tar.gz \
    | tar -xz -C /tmp glow
sudo mv /tmp/glow /usr/local/bin/

# Windows
winget install charmbracelet.glow
```

---

### fzf에서 미리보기가 안 보임

`mailview`에서 오른쪽 패널이 비어 있는 경우:

```bash
# glow 경로 확인
which glow
glow --version

# config.toml에서 절대 경로 지정
# [tools]
# glow = "/usr/local/bin/glow"
```

---

### win32com 백엔드 오류 (Windows)

```
오류: pywin32 가 없습니다
```

```powershell
pip install pywin32
python -m pywin32_postinstall -install
# 이후 PowerShell 재시작
```

---

### 디스크 공간 부족

```bash
# 첨부 파일 용량 확인
uv run mailstat attachments

# 대용량 첨부를 외부 스토리지로 이동
mv ~/mail-archive/attachments_large /mnt/external/
ln -s /mnt/external/attachments_large ~/mail-archive/attachments_large
```

---

### --resume이 재실행 시 다시 변환함

`--resume` 재실행에도 `converted: N, skipped: 0` 이 반복되는 경우:

1. `.state.json` 의 `done_msgids` 가 비어있는지 확인:
   ```bash
   cat ~/mail-archive/.state.json
   ```

2. **v0.3.0 이전 버전**에서 변환된 아카이브인 경우, Message-ID 없는 아이템(Calendar, Contacts 등)은
   상태가 저장되지 않아 발생합니다. v0.3.0 이상으로 업데이트 후 재실행하면 수정됩니다:
   ```bash
   # 최신 코드 Pull 후
   uv run pst2md --pst archive.pst --resume
   # 이후 실행부터 정상 skip
   ```

---

### 첨부 파일명이 attachment_0 등으로 표시됨

pypff 가 첨부 파일명을 `None` 으로 반환하는 경우입니다.
주로 OLE 임베디드 객체(Calendar 첨부, Outlook 항목 포함)에서 발생합니다.
v0.3.0 이상에서는 MAPI record_sets 에서 표시 이름(`PR_DISPLAY_NAME`)을 추출합니다.

일반 이메일 첨부(`ATTACH_BY_VALUE`)는 `PR_ATTACH_LONG_FILENAME` 에서 파일명이 정상 추출됩니다.

---

### Android / Termux 환경

uv 는 `aarch64-linux-android` 를 지원하지 않습니다. pip를 직접 사용합니다.

```bash
# ncurses 버전 충돌 해결 후 Python 설치
pkg install -y ncurses=6.5.20240831-3
pkg install -y python

# ld 심링크 (libpff-python 빌드에 필요)
ln -sf $(which lld) $(dirname $(which lld))/ld
ln -sf $(which llvm-ar) $(dirname $(which llvm-ar))/ar

# 의존성 설치
pip install click tomli tqdm html2text beautifulsoup4 \
    python-slugify chardet python-dateutil mail-parser
pip install libpff-python   # 소스 빌드 (5분 내외)

# 실행
python scripts/pst2md.py --pst tests/data/test.pst --dry-run
```

---

## 12. FAQ

**Q. 변환 후 원본 PST는 어떻게 하나요?**

Outlook에서 해당 기간의 메일을 삭제하고 PST를 compact합니다.
원본 PST 파일 자체는 읽기 전용 cold backup으로 보관하는 것을 권장합니다.
변환된 MD 파일로는 복원하기 어려운 메타데이터(MAPI 속성, 암호화 메일 등)가 있을 수 있습니다.

---

**Q. 변환 중 실패한 메일은 어떻게 되나요?**

변환 실패 메일은 `~/mail-archive/errors/` 디렉터리에 기록됩니다.
원본 PST에는 영향이 없습니다. 로그를 확인 후 개별 재처리하거나 무시할 수 있습니다.

---

**Q. S/MIME 암호화 메일은 변환되나요?**

암호화된 메일은 복호화 키가 없으면 본문을 추출할 수 없습니다.
변환 시 에러로 기록되며 원본 PST에 잔존합니다.
Outlook이 열려 있는 환경에서 win32com 백엔드를 사용하면 Outlook이 자동으로 복호화하여 처리할 수 있습니다.

---

**Q. 같은 PST를 두 번 변환하면 중복이 생기나요?**

Message-ID 기반 멱등성이 보장됩니다.
`--resume` 사용 시 `.state.json` 의 done_msgids 로 이미 변환된 메일을 건너뜁니다.
Calendar · Contacts 같이 Message-ID 가 없는 아이템도 발신자+제목+날짜 조합의 결정론적 해시로 중복을 방지합니다.
`build-index` 단계에서도 `INSERT OR IGNORE` 로 DB 중복을 최종 방어합니다.

---

**Q. Obsidian 없이 CLI만 사용할 수 있나요?**

네. `mailgrep`, `mailview`, `mailstat`은 Obsidian과 완전히 독립적으로 동작합니다.
Obsidian은 선택적 UI 레이어입니다.

---

**Q. Windows에서 Outlook 없이 PST를 변환할 수 있나요?**

WSL이 설치된 환경에서 `readpst` 백엔드를 사용하면 됩니다.
WSL에서 `sudo apt install pst-utils`로 readpst를 설치 후:

```powershell
pst2md --pst "C:\Users\YOU\archive.pst" --backend readpst
```

---

**Q. 첨부 파일은 어디에 저장되나요?**

`~/mail-archive/attachments/<sha256 앞 2자>/<sha256전체>.<확장자>` 경로에 저장됩니다.
SHA-256 기반 CAS(Content-Addressable Storage)로 동일 파일은 1개만 저장됩니다.
50MB 초과 파일은 `attachments_large/`에 별도 저장됩니다.
원본 파일명에 확장자가 없으면 magic bytes(파일 앞부분 바이너리 패턴)로 PNG · JPG · PDF 등을 자동 추론해 확장자를 붙입니다.

---

**Q. 검색이 느린 경우 어떻게 하나요?**

```bash
# 인덱스 재구축
build-index --rebuild

# DB 최적화
sqlite3 ~/mail-archive/index.sqlite "PRAGMA optimize; VACUUM;"

# 아카이브가 /mnt/c (Windows FS)에 있다면 WSL ext4로 이동
# (WSL ext4 vs /mnt: 검색 속도 5~10배 차이)
```
