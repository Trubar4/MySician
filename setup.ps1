# One-shot setup for a fresh Windows machine.
#
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
#
# The .venv is deliberately not in the repository -- it holds binaries built
# for one machine's Python -- so a fresh clone has the code and nothing to run
# it with. Every module then reports itself missing one at a time, which reads
# like a broken app rather than an empty environment. This builds the
# environment in one go and says plainly which step failed if one does.
#
# Written for Windows PowerShell 5.1 as well as 7: no ternaries, no null
# coalescing, and every external command checked by exit code rather than by
# letting an error preference decide. Messages are in German, like the rest of
# the tooling the player runs.

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repo

function Say($text)  { Write-Host $text }
function Good($text) { Write-Host "  OK   $text" -ForegroundColor Green }
function Bad($text)  { Write-Host "  X    $text" -ForegroundColor Red }
function Note($text) { Write-Host "       $text" -ForegroundColor DarkGray }

function Try-Run {
    <#
        Run an external command, swallowing both "it failed" and "it is not
        installed at all" -- which are different exceptions and must not stop
        a script whose whole job is to find out which tools exist.
        Returns @{ Ok = $bool; Out = $string }.
    #>
    param([string]$Exe, [string[]]$Arguments)
    $global:LASTEXITCODE = 0
    try {
        $output = & $Exe @Arguments 2>$null
    } catch {
        return @{ Ok = $false; Out = "" }
    }
    $ok = ($LASTEXITCODE -eq 0)
    if ($null -eq $output) { $output = "" }
    return @{ Ok = $ok; Out = ($output | Out-String).Trim() }
}

Say ""
Say "=================================================================="
Say " MySician - Einrichtung"
Say "=================================================================="
Say ""

# -- Are we in the right folder? -------------------------------------------
if (-not (Test-Path "pickhero/__init__.py")) {
    Bad "Dieses Skript liegt nicht im MySician-Ordner."
    Note "Es gehoert neben den Ordner 'pickhero'."
    exit 1
}
Good "MySician-Ordner gefunden: $repo"

# -- Which branch? ----------------------------------------------------------
# A warning only. Switching branches for someone is not this script's job,
# but working on the wrong one has already cost two sessions.
if (Test-Path "UPLOAD_BRANCH") {
    $lines = @(Get-Content "UPLOAD_BRANCH" | Where-Object { $_ -and -not $_.TrimStart().StartsWith("#") })
    $wanted = ""
    if ($lines.Count -gt 0) { $wanted = $lines[0].Trim() }
    $branch = Try-Run "git" @("rev-parse", "--abbrev-ref", "HEAD")
    if ($branch.Ok -and $wanted) {
        if ($branch.Out -ne $wanted) {
            Say ""
            Bad "Du bist auf dem Branch '$($branch.Out)', gearbeitet wird auf '$wanted'."
            Note "Hol ihn dir mit diesen zwei Befehlen:"
            Note "    git fetch origin"
            Note "    git switch $wanted"
            Note "Danach dieses Skript nochmal starten."
            Say ""
        } else {
            Good "Branch: $($branch.Out)"
        }
    }
}

# -- Find a Python that can run this ----------------------------------------
# aubio and pygame need wheels that exist for 3.10-3.12. Newer Pythons have no
# aubio wheel, and building it on Windows needs a C toolchain -- a different
# afternoon entirely, and not one to discover halfway through.
$pyExe = ""
$pyArgs = @()
foreach ($version in @("3.12", "3.11", "3.10")) {
    $probe = Try-Run "py" @("-$version", "-c", "import sys; print(sys.version_info[0])")
    if ($probe.Ok -and $probe.Out -eq "3") {
        $pyExe = "py"
        $pyArgs = @("-$version")
        Good "Python $version gefunden"
        break
    }
}
if (-not $pyExe) {
    $probe = Try-Run "python" @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")
    if ($probe.Ok -and $probe.Out -match '^\d+\.\d+$') {
        $parts = $probe.Out.Split(".")
        $minor = [int]$parts[1]
        if ([int]$parts[0] -eq 3 -and $minor -ge 10 -and $minor -le 12) {
            $pyExe = "python"
            $pyArgs = @()
            Good "Python $($probe.Out) gefunden"
        } else {
            Bad "Gefunden wurde nur Python $($probe.Out), und damit gibt es kein fertiges aubio-Paket."
        }
    }
}
if (-not $pyExe) {
    Say ""
    Bad "Kein passendes Python gefunden (gebraucht wird 3.10, 3.11 oder 3.12)."
    Note "Hol dir 3.12 von https://www.python.org/downloads/"
    Note "Beim Installieren 'Add python.exe to PATH' ankreuzen."
    Note "Danach dieses Skript nochmal starten."
    exit 1
}

# -- Build the environment --------------------------------------------------
if (Test-Path ".venv") {
    Good ".venv ist schon da - wird nur aktualisiert"
} else {
    Say ""
    Say "  .venv wird angelegt ..."
    $made = Try-Run $pyExe ($pyArgs + @("-m", "venv", ".venv"))
    if (-not $made.Ok) {
        Bad "Das Anlegen der .venv ist fehlgeschlagen."
        Note $made.Out
        exit 1
    }
    Good ".venv angelegt"
}

$venvPy = Join-Path $repo ".venv/Scripts/python.exe"
if (-not (Test-Path $venvPy)) {
    # Same script under PowerShell on Linux or a Mac, where venv puts the
    # interpreter somewhere else. Two lines, and it means this can be tested
    # somewhere other than the machine it is written for.
    $venvPy = Join-Path $repo ".venv/bin/python"
}
if (-not (Test-Path $venvPy)) {
    Bad "In der .venv fehlt python.exe."
    Note "Loesch den Ordner .venv und starte dieses Skript neu."
    exit 1
}

Say ""
Say "  Pakete werden installiert (beim ersten Mal ein paar Minuten) ..."
Try-Run $venvPy @("-m", "pip", "install", "--quiet", "--upgrade", "pip") | Out-Null
# numpy<2 and setuptools<74 go first and on purpose: aubio 0.4.9 is built
# against the older numpy C API, and a numpy 2 already in place is what turns
# its install into a wall of compiler output.
$base = Try-Run $venvPy @("-m", "pip", "install", "--quiet", "setuptools<74", "wheel", "numpy<2")
if (-not $base.Ok) {
    Bad "Die Grundpakete liessen sich nicht installieren."
    Note $base.Out
    exit 1
}
Good "Grundpakete installiert"

$req = Try-Run $venvPy @("-m", "pip", "install", "--quiet", "-r", "requirements.txt")
Try-Run $venvPy @("-m", "pip", "install", "--quiet", "-r", "requirements-dev.txt") | Out-Null

# -- Did it actually work? --------------------------------------------------
# pip's exit code is not the question. What matters is whether the app can
# import what it needs, so that is what gets checked.
Say ""
Say "  Wird geprueft, ob alles laeuft ..."
$missing = @()
foreach ($module in @("pygame", "aubio", "sounddevice", "numpy", "guitarpro")) {
    $check = Try-Run $venvPy @("-c", "import $module")
    if ($check.Ok) { Good $module } else { Bad $module; $missing += $module }
}

Say ""
if ($missing.Count -eq 0) {
    Say "=================================================================="
    Say " Fertig. So startest du die App:"
    Say ""
    Say "     .venv\Scripts\Activate.ps1"
    Say "     python -m pickhero"
    Say ""
    Say " Ab jetzt reichen diese zwei Zeilen. Dieses Skript brauchst du nur"
    Say " nach einem Rechnerwechsel oder wenn etwas fehlt."
    Say "=================================================================="
    exit 0
}

Say "=================================================================="
Bad ("Es fehlt noch: " + ($missing -join ", "))
if ($missing -contains "aubio") {
    Note "aubio ist der haeufigste Stolperstein - es wird auf Windows aus dem"
    Note "Quellcode gebaut. Zwei moegliche Ursachen:"
    Note "  1. Python zu neu: es geht bis 3.12. Dann 3.12 installieren,"
    Note "     den Ordner .venv loeschen und dieses Skript neu starten."
    Note "  2. Es fehlt der C++-Compiler von Microsoft. Im Visual Studio"
    Note "     Installer die Arbeitslast 'Desktopentwicklung mit C++'"
    Note "     nachinstallieren - VS Code allein reicht dafuer nicht."
    Note "     Ohne Visual Studio: 'Build Tools for Visual Studio' laden."
}
if (-not $req.Ok) {
    Note "Die genaue Fehlermeldung bekommst du ohne --quiet:"
    Note "    .venv\Scripts\python.exe -m pip install -r requirements.txt"
}
Say "=================================================================="
exit 1
