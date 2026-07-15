# zall install.ps1 — One-command install for Windows
# Usage: powershell -ExecutionPolicy Bypass -File scripts\install.ps1

Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║   zall — Falsifiable Coding Agent    ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

$ZALL_HOME = "$env:USERPROFILE\.zall"

# ── 1. Check Python ──
$python = $null
try {
    $python = (Get-Command python -ErrorAction Stop).Source
} catch {
    try {
        $python = (Get-Command python3 -ErrorAction Stop).Source
    } catch {
        Write-Host "  ✗ Python not found. Install Python >= 3.10 from:" -ForegroundColor Red
        Write-Host "    https://www.python.org/downloads/"
        exit 1
    }
}

$pyver = & $python --version 2>&1
Write-Host "  ✓ $pyver"

# Check version >= 3.10
$verParts = (& $python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
$major, $minor = $verParts.Split('.')
if ([int]$major -lt 3 -or ([int]$major -eq 3 -and [int]$minor -lt 10)) {
    Write-Host "  ✗ Python >= 3.10 required, found $verParts" -ForegroundColor Red
    exit 1
}

# ── 2. Create virtual environment ──
if (Test-Path "$ZALL_HOME\venv") {
    Write-Host "  · zall venv already exists at $ZALL_HOME\venv"
} else {
    Write-Host "  · Creating virtual environment at $ZALL_HOME\venv ..."
    & $python -m venv "$ZALL_HOME\venv"
}

$pip = "$ZALL_HOME\venv\Scripts\pip.exe"
$zall = "$ZALL_HOME\venv\Scripts\zall.exe"

# ── 3. Install zall ──
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $scriptDir

Write-Host "  · Installing zall from $projectDir ..."
& $pip install --quiet --upgrade pip
& $pip install --quiet -e $projectDir

Write-Host "  ✓ zall installed"

# ── 4. Add to PATH (user-level) ──
$venvScripts = "$ZALL_HOME\venv\Scripts"
$currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($currentPath -notlike "*$venvScripts*") {
    [Environment]::SetEnvironmentVariable("PATH", "$venvScripts;$currentPath", "User")
    Write-Host "  ✓ Added $venvScripts to user PATH"
    Write-Host "  · Restart your terminal or run: `$env:PATH = `"$venvScripts;`$env:PATH`""
} else {
    Write-Host "  · zall is already in your PATH"
}

# ── 5. Run onboarding ──
Write-Host ""
Write-Host "  Running first-time setup..."
& $zall init 2>$null

Write-Host ""
Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║   zall installed successfully!        ║" -ForegroundColor Cyan
Write-Host "  ║                                      ║" -ForegroundColor Cyan
Write-Host "  ║   Run 'zall' to start the REPL       ║" -ForegroundColor Cyan
Write-Host "  ║   Run 'zall `"task`"' for one-shot     ║" -ForegroundColor Cyan
Write-Host "  ║   Run 'zall --help' for options       ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor Cyan