# pst2md

Outlook PST 파일을 Markdown으로 변환해 CLI와 Obsidian으로 관리하는 아카이브 시스템.

```
PST (90GB)  →  Markdown + SQLite FTS5  →  fzf + mdcat/glow CLI  +  Obsidian Vault
```

**지원 환경**: Linux · WSL · Windows Native

> **권장 워크플로우 — WSL**: 검색·열람 (`mailview`/`fzf`/`mailgrep`) 은 WSL
> 에서 가장 빠르고 한글 입력이 안정적이다. 자세한 권장사항은
> [WSL 운영 권장사항](#wsl-운영-권장사항) 참조.

---

## 빠른 시작

### Linux / WSL

```bash
./install_linux.sh

# 변환은 PST 원본 위치에서 (PST 는 /mnt/c 에 둬도 OK)
pst2md --pst "/mnt/c/Users/YOU/Documents/Outlook Files/archive.pst" \
       --out  "$HOME/mail-archive"
build-index
mailgrep "견적서" --from 홍길동
mailview
mailstat summary
```

### Windows (PowerShell)

```powershell
.\install_windows.ps1

pst2md --pst "C:\Users\YOU\Documents\Outlook Files\archive.pst"
build-index
mailgrep "견적서" --from 홍길동
mailview
mailstat summary
```

---

## CLI 도구

| 명령 | 설명 |
|---|---|
| `pst2md --pst <파일>` | PST → Markdown 변환 |
| `build-index` | SQLite FTS5 인덱스 구축 |
| `mailgrep <키워드> [옵션]` | 전문검색 |
| `mailview [키워드]` | fzf 선택 → mdcat/glow 렌더링 |
| `mailstat <서브커맨드>` | 통계 (summary/monthly/senders 등) |
| `enrich` | Obsidian people/threads/projects MOC 생성 |
| `verify` | 아카이브 무결성 검증 |
| `archive-monthly --pst <파일>` | 월간 배치 (12개월+ 메일 아카이브) |

---

## PST 백엔드

| 플랫폼 | 백엔드 | 설정값 |
|---|---|---|
| Linux / WSL | libpff-python | `pypff` |
| WSL (대안) | readpst CLI | `readpst` |
| Windows Native | Outlook COM API | `win32com` |

`~/.pst2md/config.toml`에서 변경:
```toml
pst_backend = "auto"   # 플랫폼에 따라 자동 선택
```

---

## 파일 구조

```
pst2md/
├── scripts/
│   ├── pst2md.py            # PST → MD 변환기
│   ├── build_index.py       # SQLite FTS5 인덱스
│   ├── enrich.py            # Obsidian MOC 생성
│   ├── mailgrep.py          # 검색 CLI (Python/click)
│   ├── mailview.py          # fzf + mdcat/glow 뷰어
│   ├── mailstat.py          # 통계 CLI
│   ├── archive_monthly.py   # 월간 배치
│   ├── verify_integrity.py  # 무결성 검증
│   ├── mailgrep             # 검색 CLI (bash, Linux 전용)
│   ├── mailview             # 뷰어 (bash, Linux 전용)
│   ├── mailstat             # 통계 (bash, Linux 전용)
│   ├── archive_monthly.sh   # 월간 배치 (bash, Linux 전용)
│   └── lib/
│       ├── config.py        # 설정 로더 + 플랫폼 감지
│       ├── pst_backend.py   # PST 파서 추상화
│       ├── normalize.py     # 주소/날짜/인코딩 정규화
│       └── attachments.py   # SHA-256 CAS
├── docs/
│   ├── guide.md             # 상세 사용 설명서
│   └── runbook.md           # 운영 절차
├── install_linux.sh         # Linux/WSL 설치 스크립트
├── install_windows.ps1      # Windows 설치 스크립트
└── pyproject.toml
```

---

## WSL 운영 권장사항

검색 워크플로우 (`mailview`/`fzf`/`mailgrep`) 가 가장 안정적·빠르게 동작하는
타깃 환경은 WSL 이다. 다음 권장사항을 따르면 SQLite FTS5 검색 지연과
파일 순회 속도가 큰 차이를 보인다.

### 아카이브는 WSL ext4 에 둔다

```bash
# 권장
pst2md --pst "/mnt/c/Users/YOU/Documents/Outlook Files/archive.pst" \
       --out  "$HOME/mail-archive"
pst2md-config set archive.root "$HOME/mail-archive"

# 비권장: --out /mnt/c/...  (NTFS 경유 — SQLite WAL 과 rglob 가 느림)
```

- `~/mail-archive` 같은 ext4 위치는 인덱스 mtime 비교, FTS5 WAL 동기화,
  `rglob("*.md")` 순회가 모두 native 속도로 동작한다.
- `/mnt/c` 는 9P 프로토콜 경유라 같은 작업이 5~30배 느려질 수 있다.
- PST 원본은 `/mnt/c/Users/YOU/Documents/Outlook Files/...` 에 그대로 두고
  **입력 전용**으로만 사용한다.

### 백엔드는 pypff 기본

```bash
pst2md-config set pst_backend pypff   # WSL 기본
```

- `pypff` (libpff-python) 는 변환이 가장 빠르고 OLE 임베디드 객체까지 안정
  처리. 설치 실패 시에만 `readpst` 폴백.
- Windows Native 에서만 `win32com` (Outlook COM) 사용.

### 한글 입력

- 터미널 폰트에 CJK 글리프 포함 (`Noto Sans CJK`, `D2Coding` 등).
- `LANG=ko_KR.UTF-8`, `TERM=xterm-256color` 또는 `tmux-256color`.
- 자세한 점검 절차: [docs/hangul-input.md](docs/hangul-input.md).

### 인덱스 무결성 / 복구

`mailview --doctor` 가 다음을 진단한다:

- DB messages 행수 vs `archive/` 의 MD 파일 수 — diff 가 0 이 아니면
  `build-index --rebuild` 권장.
- FTS5 prefix index (`prefix='2 3 4'`) 보유 여부 — 없으면 한글 짧은
  query 검색 품질이 떨어지므로 `build-index --rebuild` 권장.

외부 복사·복원·`pst2md --no-index` 후에는 staging.jsonl 이 없어
auto-index 가 동작하지 않는다. 이 경우 `mailview` 가 stderr 로 안내한다.

### WSL 스모크 테스트

설치 직후 다음을 한 번 돌려보면 환경이 정상인지 확인된다:

```bash
pst2md --pst tests/data/test.pst --out "$HOME/mv-smoke"
build-index --archive "$HOME/mv-smoke" --rebuild
mailgrep --archive "$HOME/mv-smoke" "테스트"
mailview --doctor
```

`mailview --doctor` 가 ✓ 만 출력하면 검색 환경이 정상이다.

---

## 상세 문서

**[docs/guide.md](docs/guide.md)** — 설치부터 운영까지 전체 사용 설명서

목차:
- 시스템 요구사항
- Linux/WSL 설치
- Windows 설치
- config.toml 설정 옵션
- 첫 번째 변환 (PoC)
- 전체 PST 배치 변환
- CLI 도구 전체 옵션 상세
- Obsidian 연동 및 Dataview 쿼리
- 아카이브 구조 및 Markdown 스키마
- 월간 운영 절차 및 체크리스트
- 트러블슈팅
- FAQ
