# Installs Archer's dependencies, adds Desktop and Start menu shortcuts, then starts it.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'uv is not installed. Install it from https://docs.astral.sh/uv/ and run install.cmd again.'
    exit 1
}

Push-Location $root
try {
    # uv reports progress on stderr, which PowerShell 5.1 would turn into a terminating error.
    $ErrorActionPreference = 'Continue'
    uv sync 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw 'uv sync failed' }
    $ErrorActionPreference = 'Stop'

    $data = Join-Path $root 'data'
    New-Item -ItemType Directory -Force $data | Out-Null
    $ico = Join-Path $data 'archer.ico'
    & (Join-Path $root '.venv\Scripts\python.exe') -m archer.icon $ico

    $exe = Join-Path $root '.venv\Scripts\archerw.exe'
    $shell = New-Object -ComObject WScript.Shell
    foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
        $lnk = $shell.CreateShortcut((Join-Path $dir 'Archer.lnk'))
        $lnk.TargetPath = $exe
        $lnk.Arguments = ''
        $lnk.WorkingDirectory = $root
        $lnk.IconLocation = $ico
        $lnk.Description = 'Archer search engine and companion app'
        $lnk.Save()
    }

    Start-Process $exe
    Write-Host 'Archer is installed and running. Setup is opening in the companion app.'
}
finally {
    Pop-Location
}
