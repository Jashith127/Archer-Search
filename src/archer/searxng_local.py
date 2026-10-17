"""SearXNG run natively on Windows and owned by Archer: install a pinned release, start it, stop it.

This replaces Docker Desktop for SearXNG. Measured on the same settings, SearXNG alone takes about
150 MB, where Docker Desktop's VM and helpers take about 2.9 GB. Upstream doesn't support Windows,
so Archer pins one commit it has tested. That commit's tarball is checked against a sha256 before
anything from it runs.

Everything lives under %LOCALAPPDATA%\\Archer\\searxng, a short fixed root (curl_cffi's DLLs fail to
load from long paths) that doesn't depend on where Archer itself is installed:

    settings.yml            the instance's settings, kept across releases
    state.json              {"current": <sha12>, "port": 8888}
    releases/<sha12>/src    searx/ and the requirements files from the pinned commit
    releases/<sha12>/venv   its own Python 3.12, built by uv

The server runs inside a Windows Job Object that kills the whole tree when it closes. That tree is
the venv's launcher, the Granian main process and its multiprocessing worker. So a stop, or Archer
crashing, never leaves a worker holding the port. Its output stays in memory only. Engine warnings
can carry query URLs, and with history paused nothing about a search may reach the disk.
"""
import argparse
import collections
import ctypes
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from archer.config import ROOT as ARCHER_ROOT


@dataclass(frozen=True)
class Pin:
    commit: str
    sha256: str  # of codeload.github.com's tarball for the commit

    @property
    def short(self) -> str:
        return self.commit[:12]

    @property
    def url(self) -> str:
        return f"https://codeload.github.com/searxng/searxng/tar.gz/{self.commit}"


# Tested natively on Windows 11 with Python 3.12 (2026-10-17). Bump only after the same check.
PIN = Pin("8252c0bf6d5d97e833868099272eed0bb75828b2", "e25049f13f853a73b024567ad980d03ac13dc4505bb437892c571457ad15f081")

ROOT = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Archer" / "searxng"
MAX_ROOT = 60  # deeper roots push curl_cffi's DLLs past what Windows will load
PORTS = range(8888, 8900)
PYTHON = "3.12"
SHIMS = Path(__file__).with_name("searxng_shim")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_SUSPENDED = 0x4
# Standard-library folders SearXNG never imports; site-packages is the venv's job.
STDLIB_SKIP = r"[\\/](test|tests|idlelib|tkinter|turtledemo|lib2to3|ensurepip|site-packages)[\\/]"

Progress = Callable[[str], None]


class SetupError(Exception):
    """Install couldn't finish; the message is meant for the user."""


# ------------------------------------------------------------------ state

def _state_file() -> Path:
    return ROOT / "state.json"


def load_state() -> dict:
    try:
        data = json.loads(_state_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = _state_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, _state_file())


@dataclass(frozen=True)
class Release:
    name: str

    @property
    def dir(self) -> Path:
        return ROOT / "releases" / self.name

    @property
    def src(self) -> Path:
        return self.dir / "src"

    @property
    def venv(self) -> Path:
        return self.dir / "venv"

    @property
    def python(self) -> Path:
        return self.venv / "Scripts" / "python.exe"

    def complete(self) -> bool:
        return (self.dir / "release.json").is_file() and self.python.is_file()


def current() -> Release | None:
    name = load_state().get("current")
    release = Release(name) if isinstance(name, str) and name else None
    return release if release and release.complete() else None


def installed() -> bool:
    return current() is not None


def port() -> int:
    value = load_state().get("port")
    return value if isinstance(value, int) and value in PORTS else PORTS[0]


def set_port(value: int) -> None:
    state = load_state()
    state["port"] = value
    save_state(state)


def url() -> str:
    return f"http://127.0.0.1:{port()}"


# ---------------------------------------------------------------- install

def default_settings() -> str:
    """The instance's settings.yml. SearXNG refuses to start with its default secret, and Archer reads
    results as JSON. Same content as the installer's Docker setup."""
    return ("# SearXNG for Archer, created by Archer. Docs: https://docs.searxng.org/admin/settings/\n"
            "use_default_settings: true\n"
            "general:\n"
            "  debug: false\n"
            "server:\n"
            f"  secret_key: \"{secrets.token_hex(32)}\"\n"
            "  limiter: false\n"
            "  image_proxy: false\n"
            "search:\n"
            "  autocomplete: \"duckduckgo\"\n"
            "  formats:\n"
            "    - html\n"
            "    - json\n")


def port_free(candidate: int) -> bool:
    """Nothing listens on the port in any form. A plain 127.0.0.1 bind isn't enough: Docker Desktop
    publishes on [::] (dual-stack), and Windows still lets 127.0.0.1 bind over that. Exclusive binds
    on every address catch it instantly. A connect probe would wait about a second per closed port."""
    exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET, "0.0.0.0"),
                            (socket.AF_INET6, "::1"), (socket.AF_INET6, "::")):
        try:
            probe = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            continue  # no IPv6 on this machine
        with probe:
            try:
                if exclusive is not None:
                    probe.setsockopt(socket.SOL_SOCKET, exclusive, 1)
                probe.bind((address, candidate))
            except OSError:
                return False
    return True


def free_port(preferred: int | None = None) -> int:
    for candidate in ([preferred] if preferred in PORTS else []) + list(PORTS):
        if port_free(candidate):
            return candidate
    raise SetupError(f"No free port between {PORTS[0]} and {PORTS[-1]} for SearXNG.")


def uv_path() -> str:
    for candidate in (ARCHER_ROOT / "bin" / "uv.exe", ARCHER_ROOT / "bin" / "uv"):
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("uv")
    if not found:
        raise SetupError("uv isn't installed; reinstall Archer to get it back.")
    return found


def download(pin: Pin, dest: Path, progress: Progress) -> None:
    progress(f"Downloading SearXNG {pin.commit[:7]}")
    digest = hashlib.sha256()
    try:
        with httpx.stream("GET", pin.url, follow_redirects=True, timeout=60) as resp:
            resp.raise_for_status()
            with dest.open("wb") as out:
                for chunk in resp.iter_bytes():
                    digest.update(chunk)
                    out.write(chunk)
    except httpx.HTTPError as exc:
        raise SetupError(f"Couldn't download SearXNG: {exc}") from exc
    if digest.hexdigest() != pin.sha256:
        dest.unlink(missing_ok=True)
        raise SetupError("The SearXNG download didn't match its checksum, so it wasn't used.")


def _wanted(rel: str) -> bool:
    return rel.startswith("searx/") or rel in ("requirements.txt", "requirements-server.txt")


def extract(archive: Path, dest: Path) -> int:
    """Unpack only searx/ and the requirements files, refusing anything that could land outside dest.
    The repo's `...:socket` template files aren't valid NTFS names; they're outside searx/ anyway."""
    count = 0
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            rel = member.name.partition("/")[2]  # drop the searxng-<commit>/ prefix
            if not rel or not _wanted(rel):
                continue
            parts = Path(rel).parts
            if (rel.startswith(("/", "\\")) or ".." in parts or ":" in rel
                    or not (member.isfile() or member.isdir())):
                raise SetupError(f"The SearXNG archive has an unsafe entry: {member.name}")
            target = dest.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                continue
            with source, target.open("wb") as out:
                shutil.copyfileobj(source, out)
            count += 1
    if not (dest / "searx" / "webapp.py").is_file():
        raise SetupError("The SearXNG archive is missing searx/webapp.py.")
    return count


def freeze_version(pin: Pin, src: Path) -> None:
    """A tarball has no .git, so give SearXNG its version the way its own release builds do;
    otherwise it shells out to git at every start and logs two errors."""
    tag = f"git-{pin.commit[:7]}"
    (src / "searx" / "version_frozen.py").write_text(
        f'VERSION_STRING = "{tag}"\nVERSION_TAG = "{tag}"\nDOCKER_TAG = "{tag}"\n'
        'GIT_URL = "https://github.com/searxng/searxng"\nGIT_BRANCH = "master"\n', encoding="utf-8")


def _run(cmd: list[str], progress: Progress, cwd: Path | None = None, timeout: float = 900, check: bool = True) -> None:
    try:
        done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SetupError(f"{Path(cmd[0]).name} failed: {exc}") from exc
    for line in (done.stdout + done.stderr).splitlines()[-20:]:
        if line.strip():
            progress(line.rstrip())
    if check and done.returncode != 0:
        raise SetupError(f"{Path(cmd[0]).name} {cmd[1]} exited with {done.returncode}.")


def build_venv(release_dir: Path, progress: Progress) -> None:
    uv = uv_path()
    venv = release_dir / "venv"
    progress(f"Creating Python {PYTHON} for SearXNG")
    _run([uv, "venv", "--quiet", "--managed-python", "--python", PYTHON, str(venv)], progress)
    progress("Installing SearXNG's packages")
    src = release_dir / "src"
    python = str(venv / "Scripts" / "python.exe")
    # Bytecode compiled now, not on the first start, which otherwise takes over 20 s.
    _run([uv, "pip", "install", "--quiet", "--compile-bytecode", "--python", python,
          "-r", str(src / "requirements.txt"), "-r", str(src / "requirements-server.txt"), "tzdata"], progress)
    _run([python, "-m", "compileall", "-q", str(src / "searx")], progress)
    site = venv / "Lib" / "site-packages"
    for shim in SHIMS.glob("*.py"):
        shutil.copy2(shim, site / shim.name)
    # The shims and uv's _virtualenv.py, which every process imports.
    _run([python, "-m", "compileall", "-q", "-l", str(site)], progress)
    # uv's managed Python ships most of its standard library without bytecode (asyncio, for one), so the
    # first start would compile the ~60 modules SearXNG imports that lack it. The .pyc files land beside
    # the sources, shared by every venv on this Python; a few test files there don't compile on purpose,
    # so failures aren't fatal.
    stdlib = _base_home(venv) / "Lib"
    if stdlib.is_dir():
        progress("Compiling Python's standard library")
        _run([python, "-m", "compileall", "-qq", "-j", "0", "-x", STDLIB_SKIP, str(stdlib)], progress, check=False)


def _base_home(venv: Path) -> Path:
    """The folder of the Python a venv was made from, per its pyvenv.cfg."""
    try:
        for line in (venv / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "home":
                return Path(value.strip())
    except OSError:
        pass
    return venv / "missing-home"


def install(pin: Pin = PIN, progress: Progress = print) -> Release:
    """Install `pin` as a release beside any others and make it current if there's none yet.
    Idempotent: a complete release is reused. A partial one is rebuilt from scratch."""
    if len(str(ROOT)) > MAX_ROOT:
        raise SetupError(f"SearXNG's folder path is too long for Windows to load its libraries: {ROOT}")
    release = Release(pin.short)
    if not release.complete():
        staging = ROOT / "releases" / f"{pin.short}.partial"
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(release.dir, ignore_errors=True)
        (staging / "src").mkdir(parents=True)
        try:
            with tempfile.TemporaryDirectory(prefix="archer-searxng-") as tmp:
                archive = Path(tmp) / "searxng.tar.gz"
                download(pin, archive, progress)
                progress(f"Unpacked {extract(archive, staging / 'src')} files")
            freeze_version(pin, staging / "src")
            build_venv(staging, progress)
            (staging / "release.json").write_text(
                json.dumps({"commit": pin.commit, "installed": int(time.time())}), encoding="utf-8")
            os.replace(staging, release.dir)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    settings_file = ROOT / "settings.yml"
    if not settings_file.is_file():
        settings_file.write_text(default_settings(), encoding="utf-8")
        progress(f"Wrote {settings_file}")
    state = load_state()
    if not current():
        state["current"] = release.name
    if not isinstance(state.get("port"), int) or state["port"] not in PORTS:
        state["port"] = free_port()
    save_state(state)
    progress(f"SearXNG {pin.commit[:7]} is installed; it will listen on http://127.0.0.1:{state['port']}")
    return release


# -------------------------------------------------------------- job object

class _Limits(ctypes.Structure):
    # JOBOBJECT_EXTENDED_LIMIT_INFORMATION, flattened; only LimitFlags is set.
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
                ("IoCounters", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class JobProcess:
    """A process started suspended, put in a kill-on-close job, then resumed, so every child it
    spawns is in the job before it can escape. `kill_tree` ends all of them at once."""

    def __init__(self, cmd: list[str], cwd: Path, env: dict):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle_t = ctypes.c_void_p
        kernel32.CreateJobObjectW.restype = handle_t
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.SetInformationJobObject.argtypes = [handle_t, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        kernel32.AssignProcessToJobObject.argtypes = [handle_t, handle_t]
        kernel32.TerminateJobObject.argtypes = [handle_t, ctypes.c_uint]
        kernel32.CloseHandle.argtypes = [handle_t]
        self._k = kernel32
        self._job = kernel32.CreateJobObjectW(None, None)
        if not self._job:
            raise OSError(ctypes.get_last_error(), "CreateJobObject failed")
        limits = _Limits(LimitFlags=0x2000)  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(self._job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self._close()
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        self.proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                     creationflags=NO_WINDOW | CREATE_SUSPENDED)
        handle = ctypes.c_void_p(int(self.proc._handle))  # noqa: SLF001 (Popen has no public handle)
        if not kernel32.AssignProcessToJobObject(self._job, handle):
            self.proc.kill()
            self._close()
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
        ntdll.NtResumeProcess(handle)
        self.stdout = self.proc.stdout

    def poll(self) -> int | None:
        return self.proc.poll()

    def kill_tree(self) -> None:
        if self._job:
            self._k.TerminateJobObject(self._job, 1)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        self._close()

    def _close(self) -> None:
        if self._job:
            self._k.CloseHandle(self._job)
            self._job = None


def _spawn(cmd: list[str], cwd: Path, env: dict):
    return JobProcess(cmd, cwd, env)


def _healthy(port_: int) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{port_}/healthz", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


# ------------------------------------------------------------------ server

_lock = threading.Lock()
_proc = None
_log: collections.deque[str] = collections.deque(maxlen=300)


def _environment(port_: int) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GRANIAN_", "SEARXNG_", "VIRTUAL_ENV", "PYTHON"))}
    env.update({
        "SEARXNG_SETTINGS_PATH": str(ROOT / "settings.yml"),
        "GRANIAN_INTERFACE": "wsgi",
        "GRANIAN_HOST": "127.0.0.1",  # loopback only, like Archer itself
        "GRANIAN_PORT": str(port_),
        "GRANIAN_WORKERS": "1",
        "GRANIAN_BLOCKING_THREADS": "4",
        "GRANIAN_WEBSOCKETS": "false",
        "PYTHONIOENCODING": "utf-8",
    })
    return env


def _pump(stream) -> None:
    try:
        for line in stream:
            _log.append(line.rstrip())
    except (OSError, ValueError):
        pass


def running() -> bool:
    return _proc is not None and _proc.poll() is None


def log_lines() -> list[str]:
    return list(_log)


def start(wait: float = 60) -> bool:
    """Start the current release and wait until it answers /healthz. True if it's serving."""
    global _proc
    with _lock:
        if running():
            return True
        release = current()
        if release is None:
            return False
        port_ = port()
        # Ours never outlives its job, so anything already on the port belongs to another program
        # (a Docker SearXNG, say), even if it answers like SearXNG.
        if not port_free(port_):
            _log.append(f"Port {port_} is in use by another program.")
            return False
        _log.clear()
        try:
            # `python -m granian`, not Scripts\granian.exe: uv's script trampoline can't resolve the
            # managed Python's version junction and exits before Granian starts.
            _proc = _spawn([str(release.python), "-m", "granian", "searx.webapp:app"], release.src, _environment(port_))
        except OSError as exc:
            _log.append(f"Couldn't start SearXNG: {exc}")
            _proc = None
            return False
        threading.Thread(target=_pump, args=(_proc.stdout,), daemon=True, name="searxng-log").start()
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if _proc.poll() is not None:
                _log.append(f"SearXNG exited with {_proc.poll()} while starting.")
                _proc = None
                return False
            if _healthy(port_) and _proc.poll() is None:
                return True
            time.sleep(0.25)
        _log.append(f"SearXNG didn't answer within {wait:.0f} s.")
        _proc.kill_tree()
        _proc = None
        return False


def stop() -> None:
    global _proc
    with _lock:
        if _proc is not None:
            _proc.kill_tree()
            _proc = None


def status() -> dict:
    release = current()
    return {"installed": release is not None, "running": running(), "url": url(), "port": port(),
            "release": release.name if release else None, "pinned": PIN.short,
            "update": bool(release and release.name != PIN.short)}


# --------------------------------------------------------------------- cli

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m archer.searxng_local",
                                     description="Install and run SearXNG natively for Archer.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="install the pinned SearXNG release")
    sub.add_parser("status", help="print the install state as JSON")
    sub.add_parser("serve", help="run SearXNG in the foreground until Ctrl+C")
    args = parser.parse_args(argv)

    if args.command == "install":
        try:
            install(progress=lambda line: print(line, flush=True))
        except SetupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"url": url()}))
        return 0
    if args.command == "status":
        print(json.dumps(status()))
        return 0
    if not start():
        print("\n".join(log_lines()[-30:]) or "SearXNG isn't installed.", file=sys.stderr)
        return 1
    print(f"SearXNG is serving at {url()} (Ctrl+C stops it)", flush=True)
    try:
        while running():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
