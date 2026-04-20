# mailview 한글 입력 트러블슈팅

`mailview` 의 fzf 쿼리 창에 한글(또는 조합형 스크립트 — 일본어 IME, CJK) 을 입력할 때
조합 단계에서 글자가 깨지거나 커서가 튀는 문제가 보고되고 있다.

본 문서는 **근본 원인 확정 전의 진단 가이드** 다. 코드 수정 없이 환경 조정으로
해결되는 경우가 많고, 안 풀릴 경우 워크어라운드가 있다.

---

## 증상 패턴

1. **중복 자음** — `ㅇㅇㅇ안녕` 처럼 조합 중인 초성이 확정 전 여러 번 화면에 남는다
2. **조합 중 글자 깨짐** — `안ㄴ` / `ㅇㅏㄴ` 처럼 조합이 끝나기 전에 자모가 분리되어 보인다
3. **커서 튀기** — 한 글자 입력 후 커서 위치가 앞뒤로 한 칸 이동한다 (wcwidth 오차)

---

## 원인 후보 (추정)

| 계층 | 가설 |
|---|---|
| IME → 터미널 | 조합 중 문자(pre-edit string) 전송 타이밍이 터미널 입력 버퍼와 어긋남 |
| 터미널 → fzf | 터미널이 CJK wide glyph 폭을 2 로 보고하지 않아 커서 위치 계산이 틀어짐 |
| fzf 내부    | fzf 의 UTF-8 조합 문자 렌더링이 일부 플랫폼(Termux aarch64) 에서 빈약 |

확정하려면 각 계층을 분리 재현해야 한다. 아래 체크리스트가 우선.

---

## 체크리스트

### 1. 터미널 · locale 점검

```bash
mailview --doctor
```

출력 중:
- `LANG` / `LC_CTYPE` — `en_US.UTF-8` 또는 `ko_KR.UTF-8` 로 설정되어 있어야 함
- `TERM` — `xterm-256color` / `tmux-256color` 권장. `screen` / `linux` 는 wide glyph 지원이 빈약
- `fzf` — 버전이 0.53 이상이면 UTF-8 처리가 개선된 빌드

### 2. 환경별 권장 설정

**Termux (aarch64-linux-android)**

```bash
pkg install noto-cjk                         # CJK 폰트
echo "export LANG=en_US.UTF-8" >> ~/.bashrc
echo "export LC_CTYPE=en_US.UTF-8" >> ~/.bashrc
termux-reload-settings                       # 폰트 반영
```

Gboard 입력 방식: **"한국어(두벌식)" 키보드 선택** — 기본 "한국어(천지인)" 은 조합 이벤트를
더 자주 발생시켜 증상이 잘 보인다.

**Linux native**

```bash
sudo apt install fonts-noto-cjk             # Debian/Ubuntu
# 또는
sudo dnf install google-noto-cjk-fonts      # Fedora

export LANG=en_US.UTF-8
```

터미널 에뮬레이터: **Alacritty, kitty, GNOME Terminal** 은 CJK wide glyph 를
정확히 처리한다. **xterm / urxvt** 는 별도 설정 필요.

**WSL2 (Windows Terminal)**

- Windows Terminal settings → Profile → Appearance → "Font face" 에 CJK 지원 폰트 (NanumGothicCoding, D2Coding, Cascadia Code 등)
- `wsl.conf` 에 `LANG=en_US.UTF-8` 반영

### 3. tmux / screen 사용 시

```
# ~/.tmux.conf
set -g default-terminal "tmux-256color"
set -ga terminal-overrides ",*:Tc"   # truecolor
```

`screen` 은 가능하면 사용하지 말 것. wide glyph 처리가 오래된 코드라 CJK 에 약함.

---

## 워크어라운드

한글 입력이 어려운 환경에선 **fzf 쿼리 대신 CLI 인자로 검색**:

```bash
# 1. mailgrep 으로 먼저 검색 (한글 검색어)
mailgrep "계약서" --json | head -5

# 2. 결과에서 msgid 복사 → mailview 에 직접 지정
mailview --thread t_abc123de
mailview --folder "Inbox/계약"
mailview "계약" --after 2023-01-01
```

쿼리를 mailview 쉘 입력창이 아닌 **bash 프롬프트**에 입력하므로 IME 조합 문제를
터미널이 아니라 bash readline 이 처리한다. Gboard / IBus / macOS IME 는 bash
readline 과 궁합이 좋다.

---

## 디버그 정보 수집

문제 지속 시 아래 정보를 GitHub Issue 에 첨부:

```bash
mailview --doctor > doctor.log
env | grep -E 'LANG|LC_|TERM|TERMUX' >> doctor.log
# Termux
getprop ro.build.version.release 2>/dev/null >> doctor.log
# 입력 재현 영상 (선택) — termux-record 또는 asciinema
```

향후 원인이 확정되면 개선 plan 을 별건으로 수립한다.
