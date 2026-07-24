import os
import subprocess
import sys
from pathlib import Path

from app.blinkit import BlinkitClient, _looks_like_profile_conflict
from app.config import Settings


def _client(profile: Path) -> BlinkitClient:
    return BlinkitClient(Settings(_env_file=None, browser_profile_dir=profile))


def _write_lock(profile: Path, pid: int) -> None:
    profile.mkdir(parents=True, exist_ok=True)
    lock = profile / "SingletonLock"
    if lock.is_symlink() or lock.exists():
        lock.unlink()
    os.symlink(f"testhost-{pid}", lock)


def test_singleton_lock_pid_parses_symlink(tmp_path):
    _write_lock(tmp_path, 4242)
    assert _client(tmp_path)._singleton_lock_pid() == 4242


def test_singleton_lock_pid_none_when_absent(tmp_path):
    assert _client(tmp_path)._singleton_lock_pid() is None


def test_remove_stale_lock_removes_dead_holder(tmp_path):
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()  # pid is now dead

    _write_lock(tmp_path, proc.pid)
    client = _client(tmp_path)

    assert client._remove_stale_lock() is True
    assert not (tmp_path / "SingletonLock").is_symlink()


def test_remove_stale_lock_keeps_live_holder(tmp_path):
    _write_lock(tmp_path, os.getpid())  # our own, very-much-alive pid
    client = _client(tmp_path)

    assert client._remove_stale_lock() is False
    assert (tmp_path / "SingletonLock").is_symlink()


def test_remove_stale_lock_no_lock(tmp_path):
    assert _client(tmp_path)._remove_stale_lock() is False


def test_looks_like_profile_conflict_matches_playwright_message():
    message = (
        "BrowserType.launch_persistent_context: Opening in existing browser "
        "session. This usually means that the profile is already in use by "
        "another instance of Chromium."
    )
    assert _looks_like_profile_conflict(message) is True
    assert _looks_like_profile_conflict("Executable doesn't exist") is False
