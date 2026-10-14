# Stops Archer and removes its shortcuts and start-with-Windows entry. Leaves the project folder alone.
$root = Split-Path -Parent $PSScriptRoot

Get-CimInstance Win32_Process |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith((Join-Path $root '.venv'), 'OrdinalIgnoreCase') } |
    ForEach-Object { taskkill /T /F /PID $_.ProcessId 2>&1 | Out-Null }  # /T: the venv launcher spawns the real interpreter as a child

foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    Remove-Item (Join-Path $dir 'Archer.lnk') -ErrorAction SilentlyContinue
}
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'Archer' -ErrorAction SilentlyContinue

Write-Host 'Archer stopped and its shortcuts removed. Remember to change Helium''s default search engine.'
