"""Native SearXNG: install, start and stop with downloads, uv and the server process faked."""
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tarfile

import pytest

from archer import searxng_local as sx

PREFIX = "searxng-abc"


@pytest.fixture(autouse=True)
def root(monkeypatch, tmp_path):
    monkeypatch.setattr(sx, "ROOT", tmp_path / "searxng")
    monkeypatch.setattr(sx, "MAX_ROOT", 400)
    monkeypatch.setattr(sx, "_proc", None)
    sx._log.clear()
    return tmp_path / "searxng"


def _tarball(entries: dict[str, bytes | None], links: tuple[str, ...] = ()) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        for name in links:
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
    return buf.getvalue()


GOOD = {
    f"{PREFIX}/": None,
    f"{PREFIX}/searx/": None,
    f"{PREFIX}/searx/webapp.py": b"app = None\n",
    f"{PREFIX}/searx/engines/ddg.py": b"",
    f"{PREFIX}/requirements.txt": b"flask==3\n",
    f"{PREFIX}/requirements-server.txt": b"granian==2\n",
    f"{PREFIX}/docs/index.rst": b"skipped",
    f"{PREFIX}/utils/templates/etc/uwsgi/searxng.ini:socket": b"skipped, and not a valid NTFS name",
}


def test_extract_takes_only_searx_and_requirements(tmp_path):
    archive = tmp_path / "a.tar.gz"
    archive.write_bytes(_tarball(GOOD))
    dest = tmp_path / "src"
    assert sx.extract(archive, dest) == 4
    assert (dest / "searx" / "webapp.py").read_text() == "app = None\n"
    assert (dest / "requirements-server.txt").is_file()
    assert not (dest / "docs").exists() and not (dest / "utils").exists()


@pytest.mark.parametrize("entries,links", [
    ({**GOOD, f"{PREFIX}/searx/../../evil.py": b"x"}, ()),
    (GOOD, (f"{PREFIX}/searx/link.py",)),
])
def test_extract_refuses_unsafe_entries(tmp_path, entries, links):
    archive = tmp_path / "a.tar.gz"
    archive.write_bytes(_tarball(entries, links))
    with pytest.raises(sx.SetupError):
        sx.extract(archive, tmp_path / "src")
    assert not (tmp_path / "evil.py").exists()


def test_extract_needs_webapp(tmp_path):
    archive = tmp_path / "a.tar.gz"
    archive.write_bytes(_tarball({f"{PREFIX}/requirements.txt": b""}))
    with pytest.raises(sx.SetupError):
        sx.extract(archive, tmp_path / "src")


class _Stream:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_bytes(self):
        yield self.body


def test_download_checks_sha256(monkeypatch, tmp_path):
    body = _tarball(GOOD)
    monkeypatch.setattr(sx.httpx, "stream", lambda *a, **k: _Stream(body))
    dest = tmp_path / "dl.tar.gz"
    sx.download(sx.Pin("a" * 40, hashlib.sha256(body).hexdigest()), dest, lambda line: None)
    assert dest.read_bytes() == body
    with pytest.raises(sx.SetupError, match="checksum"):
        sx.download(sx.Pin("a" * 40, "0" * 64), dest, lambda line: None)
    assert not dest.exists()


def test_build_venv_compiles_shims_and_the_standard_library_after_copying(monkeypatch, tmp_path):
    home = tmp_path / "cpython-3.12"
    (home / "Lib").mkdir(parents=True)
    release = tmp_path / "rel"
    site = release / "venv" / "Lib" / "site-packages"
    runs = []

    def run(cmd, progress, cwd=None, timeout=900, check=True):
        if cmd[1] == "venv":
            site.mkdir(parents=True)
            (release / "venv" / "pyvenv.cfg").write_text(f"home = {home}\nversion_info = 3.12\n", encoding="utf-8")
        runs.append((cmd, check, sorted(p.name for p in site.glob("*.py")) if site.is_dir() else []))

    monkeypatch.setattr(sx, "uv_path", lambda: "uv")
    monkeypatch.setattr(sx, "_run", run)
    sx.build_venv(release, lambda line: None)
    compiles = [(cmd, check, present) for cmd, check, present in runs if "compileall" in cmd]
    assert [cmd[-1] for cmd, _, _ in compiles] == [str(release / "src" / "searx"), str(site), str(home / "Lib")]
    shims, stdlib = compiles[1], compiles[2]
    assert "-l" in shims[0] and {"pwd.py", "sitecustomize.py"} <= set(shims[2])  # copied before compiling
    assert stdlib[1] is False and sx.STDLIB_SKIP in stdlib[0]  # its test files may fail; that's fine


def test_build_venv_skips_the_standard_library_without_pyvenv_cfg(monkeypatch, tmp_path):
    runs = []
    monkeypatch.setattr(sx, "uv_path", lambda: "uv")
    monkeypatch.setattr(sx, "_run", lambda cmd, progress, **kw: runs.append(cmd))
    monkeypatch.setattr(sx, "SHIMS", tmp_path / "no-shims")
    (tmp_path / "rel" / "venv" / "Lib" / "site-packages").mkdir(parents=True)
    sx.build_venv(tmp_path / "rel", lambda line: None)
    assert sum("compileall" in cmd for cmd in runs) == 2


def _fake_install(monkeypatch, fail_venv=False):
    calls = {"download": 0}
    body = _tarball(GOOD)

    def download(pin, dest, progress):
        calls["download"] += 1
        dest.write_bytes(body)

    def build_venv(release_dir, progress):
        if fail_venv:
            raise sx.SetupError("uv pip install exited with 1.")
        scripts = release_dir / "venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "python.exe").write_bytes(b"")

    monkeypatch.setattr(sx, "download", download)
    monkeypatch.setattr(sx, "build_venv", build_venv)
    monkeypatch.setattr(sx, "free_port", lambda preferred=None: 8889)
    return calls


def test_install_makes_a_current_release_settings_and_port(monkeypatch, root):
    calls = _fake_install(monkeypatch)
    release = sx.install(progress=lambda line: None)
    assert release.complete() and release.name == sx.PIN.short
    assert sx.current() == release
    assert sx.load_state() == {"current": sx.PIN.short, "port": 8889}
    assert sx.url() == "http://127.0.0.1:8889"
    text = (root / "settings.yml").read_text()
    assert "- json" in text and "limiter: false" in text and "ultrasecretkey" not in text
    assert json.loads((release.dir / "release.json").read_text())["commit"] == sx.PIN.commit
    assert "git-8252c0b" in (release.src / "searx" / "version_frozen.py").read_text()

    # Installing again reuses the release and keeps the settings (and their secret).
    sx.install(progress=lambda line: None)
    assert calls["download"] == 1
    assert (root / "settings.yml").read_text() == text
    assert sx.status() == {"installed": True, "running": False, "url": "http://127.0.0.1:8889", "port": 8889,
                           "release": sx.PIN.short, "pinned": sx.PIN.short, "update": False}


def test_failed_install_leaves_nothing_half_built(monkeypatch, root):
    _fake_install(monkeypatch, fail_venv=True)
    with pytest.raises(sx.SetupError):
        sx.install(progress=lambda line: None)
    assert not sx.installed()
    assert list((root / "releases").iterdir()) == []
    assert sx.load_state() == {}


def test_install_refuses_a_root_too_long_to_load_dlls(monkeypatch):
    monkeypatch.setattr(sx, "MAX_ROOT", 10)
    with pytest.raises(sx.SetupError, match="too long"):
        sx.install(progress=lambda line: None)


class _FakeProc:
    def __init__(self, exits=False):
        self.killed = False
        self.exits = exits
        self.stdout = iter(["[INFO] Starting granian\n", "[INFO] Listening at: http://127.0.0.1:8889\n"])

    def poll(self):
        return 1 if self.exits or self.killed else None

    def kill_tree(self):
        self.killed = True


def _installed(monkeypatch):
    _fake_install(monkeypatch)
    sx.install(progress=lambda line: None)
    monkeypatch.setattr(sx, "port_free", lambda port: True)


def test_start_waits_for_healthz_then_stop_kills_the_tree(monkeypatch):
    _installed(monkeypatch)
    spawned = {}
    proc = _FakeProc()

    def spawn(cmd, cwd, env):
        spawned.update(cmd=cmd, cwd=cwd, env=env)
        return proc

    answers = iter([False, False, True])
    monkeypatch.setattr(sx, "_spawn", spawn)
    monkeypatch.setattr(sx, "_healthy", lambda port: next(answers))
    monkeypatch.setattr(sx.time, "sleep", lambda s: None)

    assert sx.start()
    assert sx.running()
    assert spawned["cmd"][0].endswith("python.exe") and spawned["cmd"][1:] == ["-m", "granian", "searx.webapp:app"]
    assert spawned["cwd"] == sx.current().src
    env = spawned["env"]
    assert env["GRANIAN_HOST"] == "127.0.0.1" and env["GRANIAN_PORT"] == "8889" and env["GRANIAN_WORKERS"] == "1"
    assert env["SEARXNG_SETTINGS_PATH"].endswith("settings.yml")
    assert "VIRTUAL_ENV" not in env

    assert sx.start()  # already running: no second spawn
    sx.stop()
    assert proc.killed and not sx.running()


def test_start_without_install_does_nothing(monkeypatch):
    monkeypatch.setattr(sx, "_spawn", lambda *a: pytest.fail("spawned"))
    assert not sx.start()


def test_start_refuses_a_port_another_program_holds(monkeypatch):
    _installed(monkeypatch)
    monkeypatch.setattr(sx, "port_free", lambda port: False)
    monkeypatch.setattr(sx, "_spawn", lambda *a: pytest.fail("spawned"))
    assert not sx.start()
    assert "in use" in sx.log_lines()[-1]


def test_start_reports_an_early_exit(monkeypatch):
    _installed(monkeypatch)
    monkeypatch.setattr(sx, "_spawn", lambda *a: _FakeProc(exits=True))
    monkeypatch.setattr(sx, "_healthy", lambda port: False)
    assert not sx.start()
    assert "exited" in sx.log_lines()[-1]


def test_start_gives_up_and_kills_a_server_that_never_answers(monkeypatch):
    _installed(monkeypatch)
    proc = _FakeProc()
    monkeypatch.setattr(sx, "_spawn", lambda *a: proc)
    monkeypatch.setattr(sx, "_healthy", lambda port: False)
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(sx.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(sx.time, "sleep", lambda s: None)
    assert not sx.start(wait=20)
    assert proc.killed and not sx.running()


def test_start_ignores_another_server_answering_after_ours_died(monkeypatch):
    # The port looked free, but our process died and something else answers /healthz.
    _installed(monkeypatch)
    proc = _FakeProc()
    monkeypatch.setattr(sx, "_spawn", lambda *a: proc)

    def healthy(port):
        proc.exits = True
        return True

    monkeypatch.setattr(sx, "_healthy", healthy)
    monkeypatch.setattr(sx.time, "sleep", lambda s: None)
    assert not sx.start()


@pytest.mark.parametrize("family,address", [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::")])
def test_port_free_sees_listeners_a_loopback_bind_misses(family, address):
    # Docker Desktop publishes on [::]; a plain 127.0.0.1 bind still succeeds over that.
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        listener.bind((address, 0))
        listener.listen()
        taken = listener.getsockname()[1]
        assert not sx.port_free(taken)
    assert sx.port_free(taken)


def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")
def test_job_process_kills_grandchildren(tmp_path):
    # The parent starts a child and prints its pid; killing the tree must take the child too.
    code = ("import subprocess, sys, time\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print(c.pid, flush=True)\n"
            "time.sleep(60)\n")
    proc = sx.JobProcess([sys.executable, "-c", code], tmp_path, dict(os.environ))
    child = int(proc.stdout.readline())
    assert proc.poll() is None and _alive(child)
    proc.kill_tree()
    assert proc.poll() is not None
    assert not _alive(child)
