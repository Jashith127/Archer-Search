# Builds installer\dist\ArcherSetup.exe with the C# compiler that ships with Windows (.NET Framework 4.x).
# The exe carries the installer UI, Archer's fonts, WebView2 (its window) and the app itself
# (src, pyproject.toml, uv.lock).
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$root = Split-Path -Parent $here
$obj = Join-Path $here 'obj'
$dist = Join-Path $here 'dist'
Remove-Item -Recurse -Force $obj -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $obj, $dist | Out-Null

$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
if (-not (Test-Path $csc)) { throw 'The .NET Framework 4 C# compiler (csc.exe) was not found.' }

$version = (Select-String -Path (Join-Path $root 'pyproject.toml') -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Set-Content -Path (Join-Path $obj 'version.txt') -Value $version -NoNewline -Encoding ascii

Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem

# Microsoft's WebView2 package (the installer window), pinned and checksummed, cached in installer\.refs
# as build.sh does.
$wv2Version = '1.0.4191.47'
$wv2Sha256 = 'F492BBF547D0DA329553B6727435B677579B1E9F91CC9E4A1AD029366D5F23D0'
$wv2 = Join-Path $here ".refs\webview2-$wv2Version"
if (-not (Test-Path (Join-Path $wv2 'lib\net462\Microsoft.Web.WebView2.Core.dll'))) {
    $pkg = Join-Path $obj 'webview2.nupkg'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -OutFile $pkg "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$wv2Version/microsoft.web.webview2.$wv2Version.nupkg"
    if ((Get-FileHash $pkg -Algorithm SHA256).Hash -ne $wv2Sha256) { throw 'The WebView2 package does not match its checksum.' }
    Remove-Item -Recurse -Force $wv2 -ErrorAction SilentlyContinue
    [System.IO.Compression.ZipFile]::ExtractToDirectory($pkg, $wv2)
}
$wv2Lib = 'Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll' | ForEach-Object { Join-Path $wv2 "lib\net462\$_" }

# The app payload. Entry names use forward slashes (Compress-Archive in PowerShell 5 writes backslashes).
$payload = Join-Path $obj 'payload.zip'
$zip = [System.IO.Compression.ZipFile]::Open($payload, 'Create')
try {
    $files = @('pyproject.toml', 'uv.lock', '.python-version', 'README.md', '.env.example', 'scripts\uninstall.ps1') |
        ForEach-Object { Get-Item (Join-Path $root $_) }
    $files += Get-ChildItem -Recurse -File (Join-Path $root 'src') |
        Where-Object { $_.FullName -notmatch '\\__pycache__\\' -and $_.Extension -ne '.pyc' }
    foreach ($f in $files) {
        $name = $f.FullName.Substring($root.Length + 1).Replace('\', '/')
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $f.FullName, $name, 'Optimal') | Out-Null
    }
} finally { $zip.Dispose() }
Write-Host ("Payload: {0} files, {1:N0} KB" -f $files.Count, ((Get-Item $payload).Length / 1KB))

# Icon from Archer's own generator, so the exe matches the tray and shortcuts.
$ico = Join-Path $obj 'archer.ico'
Push-Location $root
try { uv run --quiet python -m archer.icon $ico } finally { Pop-Location }
if (-not (Test-Path $ico)) { throw 'Could not generate archer.ico' }

$resources = @("/resource:$payload,payload.zip", "/resource:$(Join-Path $obj 'version.txt'),version.txt")
foreach ($f in Get-ChildItem -File (Join-Path $here 'ui')) { $resources += "/resource:$($f.FullName),ui/$($f.Name)" }
foreach ($f in Get-ChildItem -File (Join-Path $root 'src\archer\static\fonts')) { $resources += "/resource:$($f.FullName),ui/fonts/$($f.Name)" }
foreach ($f in $wv2Lib) { $resources += "/resource:$f,lib/$(Split-Path -Leaf $f)" }
foreach ($arch in 'x64', 'arm64', 'x86') { $resources += "/resource:$(Join-Path $wv2 "runtimes\win-$arch\native\WebView2Loader.dll"),native/$arch/WebView2Loader.dll" }

$out = Join-Path $dist 'ArcherSetup.exe'
$refs = 'System.dll', 'System.Core.dll', 'System.Drawing.dll', 'System.Windows.Forms.dll', 'System.Web.Extensions.dll',
        'System.IO.Compression.dll', 'System.IO.Compression.FileSystem.dll', 'System.Management.dll', 'Microsoft.CSharp.dll' |
        ForEach-Object { "/reference:$_" }
$refs += $wv2Lib | ForEach-Object { "/reference:$_" }
$sources = Get-ChildItem (Join-Path $here 'src\*.cs') | ForEach-Object { $_.FullName }
& $csc /nologo /target:winexe /optimize+ /platform:anycpu "/out:$out" "/win32icon:$ico" @refs @resources @sources
if ($LASTEXITCODE -ne 0) { throw 'csc failed' }
Write-Host ("Built {0} ({1:N0} KB), Archer {2}" -f $out, ((Get-Item $out).Length / 1KB), $version)
