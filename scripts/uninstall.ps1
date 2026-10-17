# Stops Archer and removes its shortcuts and start-with-Windows entry. Leaves the project folder alone,
# unless -RemoveFiles (what Apps & features runs for an ArcherSetup install): then it also removes the
# folder, the Apps & features entry and the archer-searxng container that setup created.
param([switch]$RemoveFiles, [switch]$Quiet)
$root = Split-Path -Parent $PSScriptRoot
Add-Type -AssemblyName System.Windows.Forms

if ($RemoveFiles -and -not $Quiet) {
    $answer = [System.Windows.Forms.MessageBox]::Show(
        "Remove Archer from this PC?`n`nIts folder, settings and data in $root will be deleted.",
        'Uninstall Archer', 'YesNo', 'Question')
    if ($answer -ne 'Yes') { exit 1 }
}

Get-CimInstance Win32_Process |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith((Join-Path $root '.venv'), 'OrdinalIgnoreCase') } |
    ForEach-Object { taskkill /T /F /PID $_.ProcessId 2>&1 | Out-Null }  # /T: the venv launcher spawns the real interpreter as a child

# Only this copy's shortcuts and autostart entry: another Archer folder (a development checkout,
# say) keeps its own.
$mine = { param($path) $path -and $path.Trim('"').StartsWith($root, 'OrdinalIgnoreCase') }
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    $lnk = Join-Path $dir 'Archer.lnk'
    if ((Test-Path $lnk) -and (& $mine $shell.CreateShortcut($lnk).TargetPath)) { Remove-Item $lnk }
}
$run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$entry = (Get-ItemProperty -Path $run -Name 'Archer' -ErrorAction SilentlyContinue).Archer
if (& $mine $entry) { Remove-ItemProperty -Path $run -Name 'Archer' }

if (-not $RemoveFiles) {
    Write-Host 'Archer stopped and its shortcuts removed. Remember to change your browser''s default search engine.'
    exit 0
}

if (Test-Path (Join-Path $root 'searxng\settings.yml')) {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($docker) { & $docker.Source rm -f archer-searxng 2>&1 | Out-Null }
}
# SearXNG run natively on this PC lives beside Archer's own data, not in the install folder.
$native = Join-Path $env:LOCALAPPDATA 'Archer\searxng'
if (Test-Path $native) { Remove-Item -Recurse -Force $native -ErrorAction SilentlyContinue }
$uninstall = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Archer'
if (& $mine (Get-ItemProperty -Path $uninstall -ErrorAction SilentlyContinue).InstallLocation) { Remove-Item -Path $uninstall -Recurse }
Set-Location $env:TEMP
Start-Sleep -Milliseconds 500  # let killed processes release their files
Remove-Item -Recurse -Force $root -ErrorAction SilentlyContinue

if (-not $Quiet) {
    [System.Windows.Forms.MessageBox]::Show(
        "Archer was removed. Remember to change your browser's default search engine back.",
        'Uninstall Archer', 'OK', 'Information') | Out-Null
}
