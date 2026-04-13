# install_windows.ps1 — Windows 설치 스크립트
#
# 사용법 (PowerShell 관리자 권한):
#   Set-ExecutionPolicy Bypass -Scope Process -Force
#   .\install_windows.ps1
#
# 옵션:
#   -ArchiveRoot <경로>   아카이브 저장 위치 (기본: %USERPROFILE%\mail-archive)
#   -Backend <이름>       PST 백엔드: win32com (기본) | readpst
#   -SkipWinget           winget 도구 설치 건너뜀

param(
    [string]$ArchiveRoot = "$env:USERPROFILE\mail-archive",
    [string]$Backend = "win32com",
    [switch]$SkipWinget
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "mailtomd Windows 설치"
Write-Host "  설치 위치: $ScriptDir"
Write-Host "  아카이브:  $ArchiveRoot"
Write-Host "  PST 백엔드: $Backend"
Write-Host "========================================" -ForegroundColor Cyan

# --- Python 확인 ---
Write-Host ""
Write-Host "[1/4] Python 확인..." -ForegroundColor Yellow
try {
    $pyVer = python --version 2>&1
    Write-Host "  $pyVer"
} catch {
    Write-Host "  오류: Python이 설치되어 있지 않습니다." -ForegroundColor Red
    Write-Host "  https://www.python.org/downloads/ 에서 Python 3.10+ 설치 후 재실행하세요."
    exit 1
}

# --- winget 도구 설치 ---
if (-not $SkipWinget) {
    Write-Host ""
    Write-Host "[2/4] CLI 도구 설치 (winget)..." -ForegroundColor Yellow

    $tools = @(
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
            Write-Host "    ✓ $($tool.Name)" -ForegroundColor Green
        } catch {
            Write-Host "    ! $($tool.Name) 설치 실패 (수동 설치 필요)" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host ""
    Write-Host "[2/4] winget 설치 건너뜀 (-SkipWinget)" -ForegroundColor Yellow
}

# --- Python 패키지 설치 ---
Write-Host ""
Write-Host "[3/4] Python 패키지 설치..." -ForegroundColor Yellow

Set-Location $ScriptDir

if ($Backend -eq "win32com") {
    pip install -e ".[win32]" --quiet
    Write-Host "  pywin32 포함 설치 완료"
} else {
    pip install -e "." --quiet
    Write-Host "  기본 패키지 설치 완료"
}

# pywin32 post-install 스크립트 실행
if ($Backend -eq "win32com") {
    try {
        python -m pywin32_postinstall -install 2>&1 | Out-Null
        Write-Host "  pywin32 초기화 완료"
    } catch {
        Write-Host "  ! pywin32 초기화 실패 — 수동 실행: python -m pywin32_postinstall -install" -ForegroundColor Yellow
    }
}

# --- 설정 파일 생성 ---
Write-Host ""
Write-Host "[4/4] 설정 파일 생성..." -ForegroundColor Yellow

$ConfigDir = "$env:USERPROFILE\.mailtomd"
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
$ConfigFile = "$ConfigDir\config.toml"
$ArchiveForToml = $ArchiveRoot -replace '\\', '/'

if (-not (Test-Path $ConfigFile)) {
    $ConfigContent = @"
# mailtomd 설정 파일
# 생성: $(Get-Date -Format 'yyyy-MM-dd')
# 플랫폼: windows

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

# 아카이브 디렉터리 생성
New-Item -ItemType Directory -Force -Path $ArchiveRoot | Out-Null

# --- 완료 ---
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "설치 완료!" -ForegroundColor Green
Write-Host ""
Write-Host "사용 예시 (PowerShell):"
Write-Host "  # PST 변환 (Outlook 실행 중 가능 - win32com)"
Write-Host "  pst2md --pst `"C:\Users\YOU\Documents\Outlook\archive.pst`""
Write-Host ""
Write-Host "  # 인덱스 구축"
Write-Host "  build-index"
Write-Host ""
Write-Host "  # 검색"
Write-Host "  mailgrep `"견적서`" --from 홍길동 --after 2023-01-01"
Write-Host "  mailview"
Write-Host "  mailstat summary"
Write-Host ""
Write-Host "  # 월간 배치 (dry-run)"
Write-Host "  archive-monthly --pst `"C:\...\archive.pst`""
Write-Host "  archive-monthly --pst `"C:\...\archive.pst`" --execute"
Write-Host "========================================" -ForegroundColor Green
