# Castle Tools installer for Windows (PowerShell 5.1 or 7).
#
# Double-click install.cmd beside this file, or run:
#   powershell -ExecutionPolicy Bypass -File installer\install.ps1 [flags]
#
# This script only bootstraps: uv (Astral's official installer, told not to
# edit your PATH), a uv-managed Python 3.13, then tools\desktop_install.py
# under that Python, which does the real work for every platform. Flags pass
# straight through, in either spelling (--repair or -Repair):
#   --repair  --update  --uninstall [--purge]  --from-source  --dry-run
#   --castle-host <addr>  --tag vX.Y.Z  --help
# This script adds one of its own: --no-shortcut (skip the Start-menu entry).
#
# Kept to plain ASCII on purpose: PowerShell 5.1 reads a BOM-less script in
# the ANSI code page, and one curly quote would change what it parses.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$Src = Split-Path -Parent $PSScriptRoot
$Installer = Join-Path $Src 'tools\desktop_install.py'
if (-not (Test-Path -LiteralPath $Installer)) {
    Write-Error "install.ps1: $Installer is missing - run this from an unpacked Castle Tools folder."
    exit 1
}

# Normalise flags: -Repair, --repair and /repair all become --repair. Values
# (the word after --castle-host or --tag) pass through untouched.
$Pass = New-Object System.Collections.Generic.List[string]
$DryRun = $false
$NoShortcut = $false
$Uninstall = $false
$expectValue = $false
foreach ($a in $args) {
    $s = [string]$a
    if ($expectValue) { $Pass.Add($s); $expectValue = $false; continue }
    if ($s -match '^(--|-|/)([A-Za-z][A-Za-z-]*)$') {
        $name = $Matches[2].ToLowerInvariant()
        switch ($name) {
            'dryrun' { $name = 'dry-run' }
            'fromsource' { $name = 'from-source' }
            'noshortcut' { $name = 'no-shortcut' }
            'castlehost' { $name = 'castle-host' }
            'skipmodel' { $name = 'skip-model' }
        }
        if ($name -eq 'no-shortcut') { $NoShortcut = $true; continue }
        if ($name -eq 'dry-run') { $DryRun = $true }
        if ($name -eq 'uninstall') { $Uninstall = $true }
        if (@('castle-host', 'tag', 'ffmpeg', 'prefix', 'data-dir') -contains $name) { $expectValue = $true }
        $Pass.Add("--$name")
    } else {
        $Pass.Add($s)
    }
}

# Files unpacked from a downloaded zip carry the Mark of the Web; clear it
# on this tree so nothing below stops at a SmartScreen prompt.
if (-not $DryRun) {
    Get-ChildItem -LiteralPath $Src -Recurse -File -ErrorAction SilentlyContinue |
        Unblock-File -ErrorAction SilentlyContinue
}

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
            (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
            (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe'))) {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

$Uv = Find-Uv
if (-not $Uv) {
    if ($DryRun) {
        Write-Host '[dry-run] would install uv from https://astral.sh/uv/install.ps1, then continue'
        exit 0
    }
    Write-Host 'Installing uv (https://docs.astral.sh/uv/)...'
    $env:UV_NO_MODIFY_PATH = '1'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1' | Invoke-Expression
    $Uv = Find-Uv
    if (-not $Uv) {
        Write-Error 'install.ps1: uv installed but cannot be found; open a new window and retry.'
        exit 1
    }
}

if ($DryRun) {
    Write-Host "[dry-run] would run: $Uv python install 3.13"
} else {
    & $Uv python install 3.13
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
# PowerShell 5.1 turns a native command's redirected stderr into a
# terminating error under 'Stop', so the probes run under 'Continue'.
function Invoke-Quiet([scriptblock]$Block) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Block 2>$null } finally { $ErrorActionPreference = $old }
}
$Python = Invoke-Quiet { & $Uv python find --managed-python 3.13 }
if (-not $Python) { $Python = Invoke-Quiet { & $Uv python find 3.13 } }
if (-not $Python) {
    Write-Error 'install.ps1: uv could not provide Python 3.13.'
    exit 1
}

& $Python $Installer --uv $Uv --source $Src @Pass
$code = $LASTEXITCODE
if ($code -ne 0 -or $Uninstall -or $DryRun -or $NoShortcut) { exit $code }

# The Start-menu entry: per-user, no admin rights. Its target is the launcher
# the installer just placed in the install root, which install.json names.
$Root = $env:CASTLE_TOOLS_HOME
if (-not $Root) { $Root = Join-Path $env:LOCALAPPDATA 'Programs\CastleTools' }
for ($i = 0; $i -lt $Pass.Count - 1; $i++) {
    if ($Pass[$i] -eq '--prefix') { $Root = $Pass[$i + 1] }
}
$Launcher = Join-Path $Root 'Castle Tools.cmd'
if (Test-Path -LiteralPath $Launcher) {
    $Programs = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
    $Link = Join-Path $Programs 'Castle Tools.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($Link)
    $sc.TargetPath = $Launcher
    $sc.WorkingDirectory = $Root
    $sc.Description = 'Start Castle Tools and open it in the browser'
    $sc.Save()
    Write-Host "Start menu: Castle Tools ($Link)"
}
exit 0
