# install_windows.ps1 — Windows 설치 스크립트 (uv 기반)
#
# 사용법 (PowerShell):
#   Set-ExecutionPolicy Bypass -Scope Process -Force
#   .\install_windows.ps1
#
# 옵션:
#   -ArchiveRoot <경로>   아카이브 저장 위치 (기본: %USERPROFILE%\mail-archive)
#   -Backend <이름>       PST 백엔드: win32com (기본) | readpst
#   -SkipWinget           winget 도구 설치 건너뜀
#
# 의존성 관리: uv (https://docs.astral.sh/uv)
#   - Python 가상환경 자동 생성 (.venv/)
#   - uv sync --extra win32  → pyproject.toml 기준 재현 가능 설치

param(
    [string]$ArchiveRoot = "$env:USERPROFILE\mail-archive",
    [string]$Backend = "win32com",
    [switch]$SkipWinget
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "mailtomd Windows 설치 (uv)"
Write-Host "  설치 위치: $ScriptDir"
Write-Host "  아카이브:  $ArchiveRoot"
Write-Host "  PST 백엔드: $Backend"
Write-Host "========================================" -ForegroundColor Cyan

# ── 1/4 winget 도구 설치 ─────────────────────────────────────────────────
if (-not $SkipWinget) {
    Write-Host ""
    Write-Host "[1/4] CLI 도구 설치 (winget)..." -ForegroundColor Yellow

    $tools = @(
        @{ Id = "astral-sh.uv";                 Name = "uv" },
        @{ Id = "fzf";                          Name = "fzf" },
        @{ Id = "charmbracelet.glow";           Name = "glow" },
        @{ Id = "sharkdp.bat";                  Name = "bat" },
        @{ Id = "BurntSushi.ripgrep.MSVC";      Name = "ripgrep (rg)" },
        @{ Id = "SQLite.SQLite";                Name = "sqlite3" }
    )

    foreach ($tool in $tools) {
        Write-Host "  설치 확인: $($tool.Name)..."
        try {
            winget install --id $tool.Id -e --silent `
                --accept-package-agreements `
                --accept-source-agreements `
                2>&1 | Out-Null
            Write-Host "    v $($tool.Name)" -ForegroundColor Green
        } catch {
            Write-Host "    ! $($tool.Name) 설치 실패 (수동 설치 필요)" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host ""
    Write-Host "[1/4] winget 설치 건너뜀 (-SkipWinget)" -ForegroundColor Yellow
}

# ── 2/4 uv 확인 및 fallback 설치 ─────────────────────────────────────────
Write-Host ""
Write-Host "[2/4] uv 확인..." -ForegroundColor Yellow

# winget 설치 후 PATH 갱신
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path","User")

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "  uv를 PowerShell 설치 스크립트로 설치합니다..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    # PATH 재갱신
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User") + ";" +
                "$env:USERPROFILE\.local\bin"
}

$uvVer = uv --version 2>&1
Write-Host "  uv: $uvVer" -ForegroundColor Green

# ── 3/4 Python 패키지 설치 ───────────────────────────────────────────────
Write-Host ""
Write-Host "[3/4] Python 패키지 설치 (uv sync)..." -ForegroundColor Yellow

Set-Location $ScriptDir

if ($Backend -eq "win32com") {
    # --extra win32: pywin32 포함
    uv sync --extra win32
    Write-Host "  pywin32 포함 설치 완료"

    # pywin32 post-install 스크립트 실행
    try {
        uv run python -m pywin32_postinstall -install 2>&1 | Out-Null
        Write-Host "  pywin32 초기화 완료"
    } catch {
        Write-Host "  ! pywin32 초기화 실패 — 수동 실행:" -ForegroundColor Yellow
        Write-Host "    uv run python -m pywin32_postinstall -install"
    }
} else {
    uv sync
    Write-Host "  기본 패키지 설치 완료"
}

$pyVer = uv run python --version 2>&1
Write-Host "  Python: $pyVer"

# ── 4/4 설정 파일 생성 ───────────────────────────────────────────────────
Write-Host ""
Write-Host "[4/4] 설정 파일 생성..." -ForegroundColor Yellow

$ConfigDir = "$env:USERPROFILE\.mailtomd"
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
$ConfigFile = "$ConfigDir\config.toml"
$ArchiveForToml = $ArchiveRoot -replace '\\', '/'

if (-not (Test-Path $ConfigFile)) {
    $ConfigContent = @"
# mailtomd 설정 파일
# 생성: $(Get-Date -Format 'yyyy-MM-dd')  플랫폼: windows

[archive]
root = "$ArchiveForToml"

# PST 파서 백엔드: win32com | readpst | pypff | auto
pst_backend = "$Backend"

[tools]
fzf     = "fzf"
glow    = "glow"
bat     = "bat"
sqlite3 = "sqlite3"
rg      = "rg"

[win32com]
# Outlook 프로파일 이름 (빈 문자열 = 기본 프로파일)
outlook_profile = ""
"@
    Set-Content -Path $ConfigFile -Value $ConfigContent -Encoding UTF8
    Write-Host "  설정 파일 생성: $ConfigFile"
} else {
    Write-Host "  설정 파일 이미 존재: $ConfigFile (변경 없음)"
}

New-Item -ItemType Directory -Force -Path $ArchiveRoot | Out-Null

# ── 완료 안내 ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "설치 완료!" -ForegroundColor Green
Write-Host ""
Write-Host "사용 방법 (1) — uv run (venv 활성화 불필요):"
Write-Host "  uv run pst2md --pst `"C:\Users\YOU\Documents\Outlook\archive.pst`""
Write-Host "  uv run mailgrep `"견적서`" --from 홍길동"
Write-Host "  uv run mailview"
Write-Host "  uv run mailstat summary"
Write-Host ""
Write-Host "사용 방법 (2) — venv 활성화 후 직접 실행:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  pst2md --pst `"C:\...\archive.pst`""
Write-Host "========================================" -ForegroundColor Green
