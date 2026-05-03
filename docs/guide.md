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
   - [pst2md-config — 설정 관리](#79-pst2md-config--설정-관리)
   - [mailenrich — LLM 요약/태그](#710-mailenrich--llm-요약태그)
   - [mailenrich-config — LLM 설정](#711-mailenrich-config--llm-설정)
   - [embed — Embedding 생성](#712-embed--embedding-생성)
8. [Obsidian 연동](#8-obsidian-연동)
9. [아카이브 구조 및 Markdown 스키마](#9-아카이브-구조-및-markdown-스키마)
10. [월간 운영 절차](#10-월간-운영-절차)
11. [트러블슈팅](#11-트러블슈팅)
12. [FAQ](#12-faq)
13. [WSL 운영 권장사항](#13-wsl-운영-권장사항)

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
(fzf + mdcat/glow)      (FTS5 검색)
    │
    ├──▶ Obsidian Vault
    │    (그래프·위키·Dataview)
    │
    └──▶ mailenrich (선택)
         LLM 요약 / 태그 / 관련문서 백링크
         (OpenAI · Anthropic · Ollama)
```

- 최근 12개월 메일만 Outlook/PST에 유지 → PST 10~15GB 수준으로 축소
- 이전 메일은 Markdown 아카이브에서 ripgrep·FTS5로 밀리초 단위 검색
- 모든 메일이 일반 텍스트 파일 → 백업·버전관리·스크립트 처리 가능
- (선택) `mailenrich` 로 각 메일에 LLM 요약·의미 태그·관련 스레드 링크를
  frontmatter 에 자동 주입 → Obsidian Dataview 검색·정렬 품질 향상

### 지원 환경

| 환경 | PST 파서 | 뷰어 |
|---|---|---|
| Linux / WSL | libpff-python (pypff) | fzf + mdcat/glow |
| WSL (대안) | readpst CLI | fzf + mdcat/glow |
| Windows Native | Outlook COM API (pywin32) | fzf + mdcat/glow (Windows Terminal) |

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
| glow | Markdown 렌더링 폴백 | `snap install glow` |
| mdcat | 이미지 인라인 렌더링(선택, 기본 뷰어) | `cargo install mdcat-ng` |
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

필수/권장 CLI 도구:

| 도구 | winget ID |
|---|---|
| fzf | `fzf` |
| glow | `charmbracelet.glow` |
| mdcat | `cargo install mdcat-ng` |
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

# 2. 뷰어
sudo snap install glow
# snap 없을 때:
# go install github.com/charmbracelet/glow@latest
# 이미지 인라인 렌더링을 쓰려면 cargo 설치 후:
# cargo install mdcat-ng

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
# 이미지 인라인 렌더링을 쓰려면 Rust/cargo 설치 후:
# cargo install mdcat-ng

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
# PST 파서 백엔드
# 주의: pst_backend 는 [archive] 안이 아니라 TOML 최상위 키입니다.
# auto     — 플랫폼에 따라 자동 선택 (Linux/WSL→pypff, Windows→win32com)
# pypff    — libpff-python 직접 사용 (Linux/WSL)
# readpst  — readpst CLI → EML → mail-parser (WSL/Linux)
# win32com — Outlook COM API (Windows, Outlook 설치 필요)
pst_backend = "auto"

# 아카이브 루트 디렉터리
[archive]
root = "/home/user/mail-archive"       # Linux/WSL
# root = "C:/Users/YOU/mail-archive"  # Windows (슬래시 사용)

# CLI 도구 경로 (PATH에 없는 경우 절대 경로 지정)
[tools]
fzf     = "fzf"
glow    = "glow"
bat     = "bat"
sqlite3 = "sqlite3"
rg      = "rg"
# mdcat 경로를 직접 지정하려면 수동으로 추가할 수 있습니다.
# pst2md-config set/get 은 아직 tools.mdcat 을 관리하지 않습니다.
# mdcat = "mdcat"

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

# 제목/본문 전용 검색
mailgrep --subject "견적서"
mailgrep --body "계약 검토"

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
  --body QUERY       본문 전용 검색
  --subject QUERY    제목 전용 검색
  --raw-fts          FTS5 raw 쿼리 사용 (AND/OR/NOT/*/: 등)
  --limit N          최대 결과 수 (기본: 50)
  --json             JSON Lines 출력 (파이프 처리용)
  --paths-only       파일 경로만 출력 (mailview 파이프용)
  --archive PATH     아카이브 루트 (config.toml 기본값 오버라이드)
  --smart            from:/to:/after:/before:/folder:/subject:/has:attachment 파싱
  --all-archives     archive.root + archive.roots 전체 검색
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

기본 검색은 `C++`, `a@b.com`, `2024-05` 같은 문자가 들어가도 안전하도록
입력을 자동으로 인용합니다. FTS5 연산자를 직접 쓰려면 `--raw-fts` 를 붙입니다.

```bash
# AND 검색 (두 단어 모두 포함)
mailgrep "견적 AND 계약" --raw-fts

# 구문 검색
mailgrep '"프로젝트 A"'

# 특정 컬럼 검색 (subject: 제목에서만)
mailgrep "subject:견적서" --raw-fts

# 일반 사용자는 제목 전용 옵션 권장
mailgrep --subject "견적서"
```

---

### 7.2 mailview — 인터랙티브 뷰어

fzf로 메일을 선택하고 mdcat(기본, 이미지 인라인) 또는 glow 로 렌더링합니다.

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
| `Enter` | 선택한 메일을 렌더링 (viewer = mdcat 기본, 또는 glow — 아래 참고) |
| `Ctrl-S` | 제목 검색(DB) 모드 |
| `Ctrl-B` | 본문 검색(DB) 모드, 미리보기에 매칭 라인 강조 |
| `Ctrl-F` | 폴더 브라우저 (fzf 0.47+ 권장) |
| `Ctrl-T` | 같은 스레드 전체 보기 (fzf 0.47+ 권장) |
| `Ctrl-A` | 첨부 파일 목록 열기 |
| `Ctrl-U` | URL 추출 및 열기 |
| `Ctrl-P` | bat/less로 frontmatter 포함 원문 표시 |
| `Ctrl-O` | `$EDITOR` (Linux) / `notepad` (Windows)로 열기 |
| `Ctrl-K` | 태그 수정 |
| `Ctrl-D` | 선택 메일 삭제 (확인 후 삭제) |
| `Ctrl-X` | Tab으로 선택한 메일 일괄 삭제 |
| `Alt-I` | 아카이브 통계 팝업 |
| `Alt-T` | 태그 브라우저 (fzf 0.47+ 권장) |
| `Alt-S` / `Alt-F` | 제목순 / 발신자순 정렬 |
| `Alt-1` / `Alt-2` / `Alt-3` / `Alt-4` | 오늘 / 최근 7일 / 최근 30일 / 최근 1년 |
| `↑↓` | 목록 이동 |
| `/` | 목록 내 추가 필터 |
| `Ctrl-R` / `Esc` | 쿼리·필터 초기화 |
| `:q`+Enter | 종료 (Linux/WSL 전용, vim 스타일) |
| `?` | 도움말 팝업 |

오른쪽 패널에 선택 메일 미리보기가 실시간으로 표시됩니다.
미리보기는 YAML frontmatter 를 숨기고 본문부터 렌더링합니다 (`awk` 필요).

#### 뷰어 선택 (미리보기 + Enter)

`config.toml [mailview] preview_viewer` 한 값이 **미리보기와 Enter 전체 열람**
양쪽 모두를 결정합니다.

| 값 | 미리보기 | Enter 전체 열람 | 이미지 |
|---|---|---|---|
| `mdcat` (기본) | `mdcat --local --columns $FZF_PREVIEW_COLUMNS` | `mdcat --local` (pager 미사용) | 인라인 렌더 |
| `glow` | glow 파이프 | `glow -p`(pager) | 텍스트 링크만 |

```bash
pst2md-config set-viewer mdcat   # 기본. 이미지 인라인 (Kitty/WezTerm/iTerm2/sixel)
pst2md-config set-viewer glow    # sixel 미지원 터미널·pager 선호 시
```

mdcat 이 없으면 자동으로 glow 로 폴백되어 기존 환경은 깨지지 않습니다.

**이미지 렌더링 조건** (mdcat):

- 지원 터미널: Kitty, WezTerm, iTerm2, Windows Terminal 1.22+(sixel),
  xterm+sixel, mlterm. 이외 터미널에서는 자리표시자 텍스트만 보입니다
  (에러 없이 동작).
- `mdcat --local` (`-l`) 은 원격 이미지 fetch 를 차단 → 트래킹 픽셀 방어.
  메일의 첨부/임베디드 이미지(`pst2md` 가 로컬로 저장)는 정상 표시.
- Enter 렌더링은 pager 를 쓰지 않고 stdout 으로 직접 출력합니다
  (less 경유 시 그래픽 코드가 깨지기 때문). 스크롤은 터미널 기본 기능 사용.

설치 (mdcat-ng — sixel 기능이 기본 활성화된 mdcat 후속 fork, 바이너리명은 동일하게 `mdcat`):

```bash
cargo install mdcat-ng       # Linux / WSL / macOS / Windows 공통 (cargo 필요)
```

> mdcat-ng 는 현재 cargo 배포만 제공됩니다 (winget/brew/apt 패키지 없음).
> 원본 mdcat (apt/brew/winget) 도 본 프로젝트와 호환되지만, sixel 을 빌드
> 타임에 활성화한 mdcat-ng 를 권장합니다.

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
  --body QUERY       본문 전용 검색
  --subject QUERY    제목 전용 검색
  --archive PATH     아카이브 루트
  --dedupe           중복 메일 감지 및 정리
  --dry-run          삭제/정리 작업 미리보기
  --doctor           플랫폼/locale/fzf·glow·mdcat/아카이브 진단 후 종료
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
  --save-out         --out 경로를 config.toml 에 영구 저장
  --cutoff DATE      이 날짜 이후 메일 제외 (YYYY-MM-DD)
  --folder REGEX     폴더 경로 필터 (정규식)
  --resume           체크포인트에서 이어 시작
  --dry-run          파일 미생성, 통계만 출력
  --backend NAME     PST 백엔드 강제 지정 (pypff|readpst|win32com|auto)
  --no-index         변환 후 자동 증분 인덱싱 건너뛰기
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

### 7.9 pst2md-config — 설정 관리

`~/.pst2md/config.toml` 을 명령행으로 조회·수정합니다. 에디터를
열지 않고도 아카이브 루트·백엔드·도구 경로·LLM 파라미터 등 20개 이상의
설정을 빠르게 바꿀 수 있습니다.

#### 기본 사용법

```bash
# 현재 설정 전체 출력 (토큰 등 민감값은 마스킹)
pst2md-config show

# 섹션만 보기
pst2md-config show archive          # [archive] 만
pst2md-config show llm              # [llm] 과 [llm.scope]
pst2md-config show mailview         # [mailview]

# 단일 키 조회
pst2md-config get archive.root
pst2md-config get llm.model
pst2md-config get tools.fzf

# 단일 키 설정 (자동 타입 변환: bool / int / list / choice)
pst2md-config set archive.root ~/mail-archive
pst2md-config set pst_backend pypff
pst2md-config set mailview.auto_index false
pst2md-config set mailview.preview_viewer mdcat
pst2md-config set llm.provider ollama
pst2md-config set llm.endpoint http://localhost:11434
pst2md-config set llm.concurrency 8
pst2md-config set llm.scope.skip_folders '["Junk","Spam","Deleted Items"]'

# 키 제거 (기본값으로 되돌리기)
pst2md-config unset mailview.glow_style

# 설정 파일 경로만 출력 (스크립트용)
pst2md-config path

# 기본 에디터($EDITOR)로 config.toml 열기
pst2md-config edit

# 편의 alias
pst2md-config set-output ~/mail-archive          # = set archive.root ...
pst2md-config set-viewer mdcat                   # = set mailview.preview_viewer mdcat
pst2md-config set-viewer glow

# 초기화
pst2md-config init
pst2md-config init --force
pst2md-config init --output ~/mail-archive --backend pypff
```

#### 하위 명령 전체

| 명령 | 설명 |
|---|---|
| `show [SECTION]` | 전체 또는 지정 섹션(`archive`, `llm`, `mailview`, `tools`, `win32com` 등) 출력. 민감값은 마스킹 |
| `get KEY` | 단일 키 값 출력 (스크립트용, 민감값은 마스킹) |
| `set KEY VALUE` | 단일 키 설정. 타입은 자동 변환 (bool/int/list/choice). 미지 키는 근접 제안 |
| `unset KEY` | 키 제거. 섹션에 남은 키가 없으면 섹션도 함께 제거 |
| `path` | config 파일 절대 경로 출력 |
| `edit` | `$EDITOR` 로 config 파일 열기 (없으면 `vi`) |
| `set-output PATH` | `archive.root` alias. `~` 자동 확장 |
| `set-viewer glow\|mdcat` | `mailview.preview_viewer` alias. mdcat 미설치 시 stderr 경고 |
| `init [--force]` | `~/.pst2md/config.toml` 이 없으면 기본 템플릿 생성. `--force` 로 덮어쓰기 |

> 구형 `pst2md-config set glow` / `set mdcat` 은 1 릴리즈 동안 브릿지로 동작하며
> stderr 로 deprecation 경고를 출력합니다. 새 스크립트에서는 `set-viewer` 사용을 권장합니다.

#### 설정 가능한 키 (주요)

| 키 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `archive.root` | str | `~/mail-archive` | 출력 아카이브 루트 |
| `archive.roots` | list | `[]` | 다중 아카이브 루트 (mailgrep 등) |
| `pst_backend` | choice | `auto` | `auto`/`pypff`/`readpst`/`win32com` |
| `tools.fzf`·`glow`·`bat`·`sqlite3`·`rg` | str | 자동 탐색 | 각 도구 절대 경로 |
| `win32com.outlook_profile` | str | `""` | Outlook 프로필 이름 (Windows) |
| `mailview.glow_style` | str | `""` | glow 테마. 빈 문자열이면 번들 `mocha-glow.json`, 없으면 `dark` |
| `mailview.auto_index` | bool | `true` | 뷰어 실행 시 증분 인덱스 자동 갱신 |
| `mailview.preview_viewer` | choice | `mdcat` | `mdcat`(기본, 이미지 인라인) 또는 `glow`(sixel 미지원 터미널용) |
| `llm.provider` | choice | `openai` | `openai`/`anthropic`/`ollama` |
| `llm.endpoint`·`model` | str | 제공자별 | API 엔드포인트 / 모델 이름 |
| `llm.token` | str (민감) | `""` | API 토큰 (env `LLM_TOKEN` 이 우선) |
| `llm.timeout`·`max_retries`·`concurrency` | int | 60·3·4 | 요청 타임아웃 / 재시도 / 병렬도 |
| `llm.scope.summary_max_chars`·`tag_max_count`·`related_max_count`·`skip_body_shorter_than` | int | 300·5·5·100 | 요약/태그 상한 |
| `llm.scope.skip_folders` | list | `["Junk","Spam","Deleted Items"]` | 보강 제외 폴더 |
| `embedding.endpoint` | str | `https://api.openai.com/v1` | OpenAI 호환 `/v1/embeddings` 엔드포인트 |
| `embedding.model` | str | `text-embedding-3-small` | 모델 이름 (예: `nomic-embed-text` for Ollama) |
| `embedding.token` | str (민감) | `""` | API 토큰 (env `EMBEDDING_TOKEN` 이 우선; Ollama 는 빈 문자열) |
| `embedding.timeout`·`max_retries`·`concurrency`·`batch_size` | int | 60·3·4·64 | 요청 타임아웃 / 재시도 / 병렬도 / 배치 크기 |
| `embedding.skip_body_shorter_than` | int | `100` | 본문 길이가 이 값(바이트) 미만이면 skip |
| `embedding.skip_folders` | list | `["Junk","Spam","Deleted Items"]` | embedding 제외 폴더 |

> `tools.mdcat` 은 수동 TOML 편집으로 지정할 수 있지만, 현재 `pst2md-config set/get`
> 레지스트리에는 포함되어 있지 않습니다. 보통은 PATH 에 `mdcat` 이 있으면 충분합니다.
> 전체 키 목록은 `scripts/lib/config_schema.py` 의 `KNOWN_KEYS` 가 단일 진실원입니다.
> 미지 키를 `set` 으로 전달하면 `difflib` 기반 근접 제안을 출력합니다.

#### pst2md 변환 시 함께 저장하기

변환 커맨드에서 `--save-out` 을 붙이면 `--out` 값이 config 에 저장되어
다음부터는 `--out` 생략 가능:

```bash
# 최초 1회: 변환 + 경로 저장
pst2md --pst /path/to/archive.pst --out ~/mail-archive --save-out

# 이후부터는 --out 없이 실행
pst2md --pst /path/to/archive2.pst
```

#### 설정 파일 위치

모든 설정은 단일 파일에 모입니다 (archive / pst_backend / tools / mailview / llm / win32com):

| 플랫폼 | 경로 |
|---|---|
| Linux/WSL | `~/.pst2md/config.toml` |
| Windows   | `%USERPROFILE%\.pst2md\config.toml` |

> 코드 레벨에서는 `scripts/lib/config.py` 의 `config_file_path()` 헬퍼가 단일
> 진실 원천이므로, 경로 변경이나 테스트 격리 시 한 곳만 수정하면 됩니다.

---

### 7.10 mailenrich — LLM 요약/태그

LLM 을 호출해 각 메일 MD 파일의 frontmatter 에 `summary`, `llm_tags`,
`related` 등 의미 메타데이터를 채우고, body 뒤에 `## 요약 (LLM)` 블록을
추가합니다. 본문(body) 자체는 바이트 단위로 불변 (`llm_hash` 로 감시).

#### 시작 흐름 (Ollama 로컬, 무료)

```bash
# 1. 의존성 추가 (이미 설치 안 되었으면)
pip install 'pst2md[mailenrich]'                   # pip 환경
uv sync --group dev --extra mailenrich             # uv 환경

# 2. Ollama 서버 실행 (별도 터미널)
ollama serve
ollama pull llama3.1:8b

# 3. provider 지정
mailenrich-config set-provider ollama
mailenrich-config set-endpoint http://localhost:11434
mailenrich-config set-model llama3.1:8b

# 4. 예상 토큰·대상 확인 (LLM 호출 없음)
mailenrich --dry-run --limit 10

# 5. 실제 처리
mailenrich --limit 10
```

#### 시작 흐름 (OpenAI / Anthropic)

```bash
# 토큰은 환경변수 로 전달 (권장 — config 파일에 평문 저장 지양)
export LLM_TOKEN=sk-xxxxx

mailenrich-config set-provider openai
mailenrich-config set-model gpt-4o-mini     # 저비용
mailenrich --dry-run                         # 비용·토큰 예상
mailenrich --budget-usd 1.0 --limit 100      # 1달러 상한, 100개만
```

#### 자주 쓰는 옵션

```
mailenrich [옵션]

  --archive PATH        아카이브 루트 (기본: config.toml)
  --since YYYY-MM-DD    시작 날짜(포함)
  --until YYYY-MM-DD    종료 날짜(포함)
  --folder NAME         처리할 폴더 (여러 번 가능)
  --limit N             처리 상한 (0=무제한)
  --dry-run             LLM 호출 없이 토큰/비용 예상만
  --force               llm_hash 무시하고 재호출 (본문 변경 시)
  --budget-usd FLOAT    누적 비용 한도 (0=무제한)
  --concurrency N       동시 호출 수 (0=config 값)
  -v, --verbose         상세 로그 출력
```

#### 멱등성과 재처리

- `body_hash = sha256(body)` 를 frontmatter `llm_hash` 로 저장 → body 가
  바뀌지 않으면 자동 skip
- **pst2md 재변환으로 body 가 바뀐 경우** (예: `_clean_md_body` 업데이트로
  NBSP/zero-width 정규화 적용): `mailenrich --force` 로 일괄 재처리
- 스킵된 파일은 `skipped`, 실패는 `errors.log` 에 기록

#### 생성되는 frontmatter 예

```yaml
---
msgid: "<abc@x>"
subject: "견적서 회신"
# ... 기존 키
summary: "엔지니어링 견적 검토 후 단가 재산정 요청. 2주 내 응답 필요."
llm_tags: [견적, 협상, 긴급]
related: [t_4a9f3c2b, t_7e2d1f0a]
llm_hash: "b3c9..."
llm_enriched_at: "2026-04-20T10:12:33+00:00"
llm_model: "gpt-4o-mini"
---
```

#### 비용 관리 팁

1. **dry-run 먼저**: `mailenrich --dry-run --since 2024-01-01` 으로 총
   예상 비용 확인
2. **folder 필터**: `--folder Inbox --folder Sent` 로 필요한 폴더만
3. **Ollama 로 선험**: 로컬 모델로 프롬프트·요약 품질 검증 후 유료 API
4. **예산 한도**: `--budget-usd 5.0` 로 초과 시 자동 중단

---

### 7.11 mailenrich-config — LLM 설정

`~/.pst2md/config.toml` 의 `[llm]` 섹션 전용 관리 CLI.

#### 기본 사용법

```bash
# 현재 LLM 설정 확인 (토큰은 마스킹 표시)
mailenrich-config show

# provider 변경
mailenrich-config set-provider ollama       # 로컬 (무료)
mailenrich-config set-provider openai
mailenrich-config set-provider anthropic

# 엔드포인트 / 모델
mailenrich-config set-endpoint http://localhost:11434
mailenrich-config set-model llama3.1:8b
mailenrich-config set-model gpt-4o-mini
mailenrich-config set-model claude-haiku-4-5-20251001

# 토큰 (권장: 환경변수 LLM_TOKEN 사용, 이 명령은 로컬 전용 편의 기능)
mailenrich-config set-token sk-xxxxx

# [llm] 섹션이 없을 때 기본 템플릿 추가
mailenrich-config init
mailenrich-config init --force              # 기존 섹션 덮어쓰기
```

#### 하위 명령 전체

| 명령 | 설명 |
|---|---|
| `show` | provider/endpoint/model/token(마스킹)/timeout/concurrency 및 `[llm.scope]` 출력 |
| `set-provider {openai,anthropic,ollama}` | provider 변경 |
| `set-endpoint URL` | API base URL |
| `set-model NAME` | 모델 이름 |
| `set-token TOKEN` | API 토큰 (env `LLM_TOKEN` 이 우선) |
| `init [--force]` | `[llm]` + `[llm.scope]` 기본 섹션 주입 |

#### 토큰 우선순위

**env `LLM_TOKEN` > config.toml `[llm].token`**

보안상 토큰은 환경변수를 권장합니다:

```bash
# 영구 적용 (bash)
echo 'export LLM_TOKEN=sk-xxxxx' >> ~/.bashrc

# 세션 한정
export LLM_TOKEN=sk-xxxxx

# config 파일에 저장할 수밖에 없다면
chmod 600 ~/.pst2md/config.toml
```

#### Provider 별 체크리스트

| Provider | endpoint 기본 | 추천 model | 토큰 필요 |
|---|---|---|---|
| openai | `https://api.openai.com/v1` | `gpt-4o-mini` | ✓ |
| anthropic | `https://api.anthropic.com` | `claude-haiku-4-5-20251001` | ✓ |
| ollama | `http://localhost:11434` | `llama3.1:8b` · `qwen2.5:7b` | ✗ |

---

### 7.12 embed — Embedding 생성

MD 본문을 OpenAI 호환 `/v1/embeddings` 엔드포인트로 float 벡터화해 `index.sqlite`
의 `embeddings` 테이블에 저장한다. provider 분기 없이 **endpoint + token + model**
만으로 OpenAI · Ollama · LM Studio 등 어떤 호환 서버에서도 동작한다.

#### 중복 분석 방지

`msgid` 별로 `(body_hash, model)` 쌍을 저장하므로:

- 본문이 변하지 않고 모델도 같으면 → **자동 skip** (HTTP 호출 없음)
- 본문이 바뀌면 → 자동 재생성 (body SHA-256 변화 감지)
- 모델이 바뀌면 → 자동 재생성 (예: `text-embedding-3-small` → `-large`)
- `--force` → 위 규칙 무시하고 강제 재실행

수만 건 아카이브에서도 두 번째 실행은 사실상 SQL SELECT 한 번으로 끝난다.

#### 설정

```bash
# OpenAI (유료, 가장 빠름)
pst2md-config set embedding.endpoint https://api.openai.com/v1
pst2md-config set embedding.model    text-embedding-3-small
export EMBEDDING_TOKEN=sk-xxxx

# Ollama (로컬 무료)
pst2md-config set embedding.endpoint http://localhost:11434/v1
pst2md-config set embedding.model    nomic-embed-text
# 토큰 불필요

# LM Studio
pst2md-config set embedding.endpoint http://localhost:1234/v1
pst2md-config set embedding.model    <load 한 모델 이름>
```

#### 실행

```bash
embed --dry-run                          # 후보 수 + 예상 토큰/비용
embed --limit 100                        # 최대 100개 처리
embed --since 2024-01-01                 # 날짜 필터
embed --folder 'Inbox/계약'              # 폴더 필터 (중복 지정 가능)
embed --force                            # body_hash 무시 강제 재실행
embed --concurrency 8 --batch-size 128   # 병렬 + 배치 크기 조정
```

#### 저장 형식

`embeddings` 테이블 (index.sqlite):

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `msgid` | TEXT PRIMARY KEY | 메시지 식별자 |
| `body_hash` | TEXT | body SHA-256 (중복 판정용) |
| `model` | TEXT | 사용한 모델 이름 |
| `dim` | INTEGER | 벡터 차원 |
| `vector` | BLOB | float32 little-endian (numpy 의존성 없음) |
| `created_at` | TEXT | UTC ISO 타임스탬프 |

벡터 복원 (Python):

```python
import array, sqlite3
conn = sqlite3.connect("~/mail-archive/index.sqlite")
row = conn.execute("SELECT vector, dim FROM embeddings WHERE msgid=?", (msgid,)).fetchone()
vec = list(array.array("f", row[0]))   # length == row[1]
```

#### 토큰 우선순위

**env `EMBEDDING_TOKEN` > config.toml `[embedding].token`** (LLM 토큰과 별개).

#### 로그

`<archive>/.embed.log.jsonl` — 배치별 (msgids, model, dim, input_tokens, status).

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

2. Windows Native 환경이라면 win32com 백엔드로 전환:
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

```bash
# WSL 셸에서 실행
pst2md --pst "/mnt/c/Users/YOU/archive.pst" --backend readpst
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

---

## 13. WSL 운영 권장사항

`mailview`/`fzf`/`mailgrep` 기반 검색·열람 워크플로우의 1차 타깃은 WSL이다.
다음 권장사항을 따르면 SQLite FTS5 검색 지연과 파일 순회 속도가 native와
동등해진다.

### 13.1 아카이브는 WSL ext4 에 둔다

```bash
# 권장
pst2md --pst "/mnt/c/Users/YOU/Documents/Outlook Files/archive.pst" \
       --out  "$HOME/mail-archive"
pst2md-config set archive.root "$HOME/mail-archive"

# 비권장: --out /mnt/c/...
# → SQLite WAL fsync, rglob, fzf preview 모두 9P 프로토콜 경유 → 5~30배 느림
```

| 작업 | ext4 (`~/mail-archive`) | NTFS (`/mnt/c/...`) |
|---|---|---|
| `mailgrep` (전문 검색) | < 100ms | 1~5s |
| `mailview` 시작 | < 200ms | 1~3s |
| `build-index --rebuild` 1만건 | 5~10s | 30~60s |
| `archive_dir.rglob("*.md")` | native | 매우 느림 |

PST 원본은 `/mnt/c/Users/YOU/Documents/Outlook Files/...` 에 그대로 두고
**입력 전용**으로만 쓴다. 변환된 MD 와 인덱스만 ext4 에 저장한다.

### 13.2 백엔드는 pypff 기본

```bash
pst2md-config set pst_backend pypff   # WSL 기본
```

- `pypff` (libpff-python) 는 WSL 환경에서 변환이 가장 빠르고 OLE 임베디드
  객체까지 안정 처리.
- 설치 실패 시에만 `readpst` 폴백 (EML 경유 → mail-parser 파싱).
- Windows Native 에서만 `win32com` (Outlook COM API) 사용 — Outlook 설치 필요.

### 13.3 한글 입력 / 검색

검색 품질의 두 축: **입력** 과 **인덱스**.

**입력 (한글 IME)**:
- 터미널 폰트에 CJK 글리프 포함 (`Noto Sans CJK`, `D2Coding`).
- `LANG=ko_KR.UTF-8`, `TERM=xterm-256color` 또는 `tmux-256color`.
- WSL 의 경우 `fcitx5-hangul` 또는 Windows IME 의 조합중 문자 전송이
  정상 동작하는지 확인. 자세한 점검 절차는
  [docs/hangul-input.md](hangul-input.md) 참고.

**인덱스 (FTS5)**:
- 새 인덱스는 `tokenize='unicode61', prefix='2 3 4'` 로 생성된다 — 2/3/4
  글자 토큰 prefix 가 미리 인덱싱되어 한글 짧은 query 가 prefix 매칭으로
  견적서·견적가 등을 잡는다.
- 구버전 인덱스는 prefix 옵션이 없어 검색 품질이 떨어진다 →
  `build-index --rebuild` 한 번 실행하면 새 스키마로 마이그레이션.
- `mailgrep` 안전 모드 (기본) 는 각 토큰을 phrase 로 인용하고 끝에 `*`
  를 자동 부착한다. AND/OR/NOT/`*`/`:` 같은 FTS5 연산자를 직접 쓰려면
  `mailgrep --raw-fts '검색식'`.

### 13.4 mailview 검색 모드 (Linux/WSL 전용)

| 키 | 동작 |
|---|---|
| `Ctrl-S` | **제목검색 (DB-backed)** — 매 입력마다 FTS5 subject 컬럼 재조회 |
| `Ctrl-B` | **본문검색 (DB-backed)** — 매 입력마다 FTS5 body 컬럼 재조회 |
| `Esc` / `Ctrl-R` | 일반 모드 복귀 (fzf 자체 필터링) |

`change:transform(...)` 핸들러가 prompt 를 보고 모드를 분기하므로
fzf 0.47+ 가 필요하다 (Linux/WSL). Windows Native 는 transform 미지원이라
1회성 reload 동작이 유지된다.

### 13.5 인덱스 무결성 / 복구

```bash
mailview --doctor
```

다음을 진단한다:

- DB messages 행수 vs `archive/` 의 MD 파일 수 — diff ≠ 0 이면
  `build-index --rebuild` 권장 메시지 출력.
- FTS5 prefix index 보유 여부 — 없으면 `build-index --rebuild` 권장.
- fzf / glow / mdcat / bat / awk 경로 및 버전.
- LANG / LC_CTYPE / TERM 등 한글 입력 환경변수.

외부 복사·복원·`pst2md --no-index` 후에는 staging.jsonl 이 없어
auto-index 가 동작하지 않는다. 이때 `mailview` 가 stderr 로 경고하면
`build-index --rebuild` 한 번이면 정상화된다.

### 13.6 WSL 스모크 테스트

설치 직후 다음을 한 번 돌려보면 환경이 정상인지 확인된다:

```bash
# 1) 변환 (테스트 fixture)
pst2md --pst tests/data/test.pst --out "$HOME/mv-smoke"

# 2) 인덱스 재구축
build-index --archive "$HOME/mv-smoke" --rebuild

# 3) 한글 부분일치 검색 (prefix index 동작 확인)
mailgrep --archive "$HOME/mv-smoke" "테"

# 4) 환경 진단 — ✓ 만 출력되어야 정상
mailview --archive "$HOME/mv-smoke" --doctor

# 5) 행수 일치 확인
sqlite3 "$HOME/mv-smoke/index.sqlite" "SELECT COUNT(*) FROM messages"
find "$HOME/mv-smoke/archive" -name "*.md" | wc -l
```

`mailview --doctor` 가 ⚠ 를 출력하면 그 줄의 권장 명령을 실행한 뒤
다시 진단하면 된다.
