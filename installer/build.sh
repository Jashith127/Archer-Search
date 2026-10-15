#!/usr/bin/env bash
# Linux counterpart of build.ps1: the same installer\dist\ArcherSetup.exe, compiled with Roslyn
# from the .NET 8 SDK against Microsoft's .NET Framework 4.8 reference assemblies (NuGet), so a
# release can be built without Windows. Needs: dotnet-sdk-8.0, curl, unzip, uv.
# The exe still targets .NET Framework 4.x and runs only on Windows.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(dirname "$here")"
obj="$here/obj"
dist="$here/dist"
rm -rf "$obj"
mkdir -p "$obj" "$dist"

csc="$(ls -d /usr/lib/dotnet/sdk/*/Roslyn/bincore/csc.dll /usr/share/dotnet/sdk/*/Roslyn/bincore/csc.dll 2>/dev/null | sort -V | tail -1 || true)"
[ -n "$csc" ] || { echo "Roslyn (csc.dll) not found: install dotnet-sdk-8.0" >&2; exit 1; }

# .NET Framework 4.8 reference assemblies, cached next to the build output.
refpkg="$here/.refs/net48"
if [ ! -d "$refpkg/build/.NETFramework/v4.8" ]; then
    mkdir -p "$refpkg"
    curl -sSL https://api.nuget.org/v3-flatcontainer/microsoft.netframework.referenceassemblies.net48/1.0.3/microsoft.netframework.referenceassemblies.net48.1.0.3.nupkg -o "$obj/refs.zip"
    unzip -q -o "$obj/refs.zip" -d "$refpkg"
fi
fw="$refpkg/build/.NETFramework/v4.8"

version="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$root/pyproject.toml" | head -1)"
printf '%s' "$version" > "$obj/version.txt"

# The app payload: the same files as build.ps1, forward-slash entry names, best compression.
(cd "$root" && uv run --quiet python - "$obj/payload.zip" <<'EOF'
import pathlib, sys, zipfile
root = pathlib.Path(".")
files = [root / f for f in ("pyproject.toml", "uv.lock", ".python-version", "README.md", ".env.example", "scripts/uninstall.ps1")]
files += sorted(p for p in (root / "src").rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for f in files:
        z.write(f, f.as_posix())
print(f"Payload: {len(files)} files, {pathlib.Path(sys.argv[1]).stat().st_size // 1024:,} KB")
EOF
)

# Icon from Archer's own generator, so the exe matches the tray and shortcuts.
(cd "$root" && uv run --quiet python -m archer.icon "$obj/archer.ico")
[ -f "$obj/archer.ico" ] || { echo "Could not generate archer.ico" >&2; exit 1; }

args=(/nologo /noconfig /nostdlib+ /target:winexe /optimize+ /platform:anycpu /langversion:5
      "/out:$dist/ArcherSetup.exe" "/win32icon:$obj/archer.ico"
      "/resource:$obj/payload.zip,payload.zip" "/resource:$obj/version.txt,version.txt")
for f in "$here"/ui/*; do [ -f "$f" ] && args+=("/resource:$f,ui/$(basename "$f")"); done
for f in "$root"/src/archer/static/fonts/*; do args+=("/resource:$f,ui/fonts/$(basename "$f")"); done
for r in mscorlib System System.Core System.Drawing System.Windows.Forms System.Web.Extensions \
         System.IO.Compression System.IO.Compression.FileSystem System.Management Microsoft.CSharp; do
    args+=("/reference:$fw/$r.dll")
done
dotnet "$csc" "${args[@]}" "$here"/src/*.cs
size=$(( $(stat -c %s "$dist/ArcherSetup.exe") / 1024 ))
echo "Built $dist/ArcherSetup.exe (${size} KB), Archer $version"
