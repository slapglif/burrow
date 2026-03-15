"""Burrow self-update — check for updates, pull from git, reinstall."""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from burrow import protocol

log = logging.getLogger("burrow.updater")

# Where the burrow source lives
BURROW_ROOT = Path(__file__).parent.parent.resolve()
REPO_URL = "https://github.com/slapglif/burrow.git"
PYPROJECT = BURROW_ROOT / "pyproject.toml"


def current_version() -> str:
    """Get the currently installed version."""
    return protocol.VERSION


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse semver string to tuple for comparison."""
    return tuple(int(x) for x in re.findall(r'\d+', v))


def version_newer(remote: str, local: str) -> bool:
    """Return True if remote version is newer than local."""
    return _parse_version(remote) > _parse_version(local)


def _bump_version(version: str, part: str = "patch") -> str:
    """Bump a semver version string."""
    parts = list(_parse_version(version))
    while len(parts) < 3:
        parts.append(0)
    if part == "major":
        parts[0] += 1
        parts[1] = 0
        parts[2] = 0
    elif part == "minor":
        parts[1] += 1
        parts[2] = 0
    else:  # patch
        parts[2] += 1
    return f"{parts[0]}.{parts[1]}.{parts[2]}"


def _run_git(*args, cwd=None) -> subprocess.CompletedProcess:
    """Run a git command and return result."""
    return subprocess.run(
        ["git"] + list(args),
        cwd=cwd or str(BURROW_ROOT),
        capture_output=True, text=True, timeout=30,
    )


def git_current_sha() -> str:
    """Get the current git commit SHA."""
    r = _run_git("rev-parse", "--short", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def git_current_branch() -> str:
    """Get the current git branch."""
    r = _run_git("rev-parse", "--abbrev-ref", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else "unknown"


async def check_remote_version() -> dict:
    """Check GitHub for the latest version without pulling.
    Returns {available, remote_version, local_version, changelog}."""
    local = current_version()

    # Fetch remote tags/commits to see latest version
    result = await asyncio.to_thread(
        _run_git, "fetch", "--tags", "origin", "master")
    if result.returncode != 0:
        return {"available": False, "error": f"fetch failed: {result.stderr.strip()}",
                "local_version": local}

    # Read remote pyproject.toml for version
    result = await asyncio.to_thread(
        _run_git, "show", "origin/master:pyproject.toml")
    if result.returncode != 0:
        return {"available": False, "error": "could not read remote version",
                "local_version": local}

    remote_version = local  # fallback
    for line in result.stdout.splitlines():
        m = re.match(r'version\s*=\s*"([^"]+)"', line.strip())
        if m:
            remote_version = m.group(1)
            break

    # Get changelog from commit messages since current version
    changelog = ""
    result_log = await asyncio.to_thread(
        _run_git, "log", "--oneline", f"HEAD..origin/master")
    if result_log.returncode == 0 and result_log.stdout.strip():
        changelog = result_log.stdout.strip()

    is_newer = version_newer(remote_version, local)
    return {
        "available": is_newer,
        "remote_version": remote_version,
        "local_version": local,
        "changelog": changelog,
        "sha": git_current_sha(),
        "branch": git_current_branch(),
    }


async def self_update(force: bool = False) -> dict:
    """Pull latest code from git and reinstall.
    Returns {success, old_version, new_version, error}."""
    old_version = current_version()

    # Check if there are local tracked changes (ignore untracked files)
    status = await asyncio.to_thread(_run_git, "diff", "--stat")
    staged = await asyncio.to_thread(_run_git, "diff", "--cached", "--stat")
    has_changes = bool(status.stdout.strip() or staged.stdout.strip())
    if has_changes and not force:
        return {"success": False, "old_version": old_version,
                "error": "local changes detected — use force=True to override"}

    # Stash any local changes if forcing
    if status.stdout.strip() and force:
        await asyncio.to_thread(_run_git, "stash", "push", "-m", "burrow-auto-update")

    # Pull latest
    pull = await asyncio.to_thread(_run_git, "pull", "--rebase", "origin", "master")
    if pull.returncode != 0:
        return {"success": False, "old_version": old_version,
                "error": f"git pull failed: {pull.stderr.strip()}"}

    # Reinstall deps
    install = await asyncio.to_thread(
        subprocess.run,
        ["uv", "pip", "install", "-e", ".", "-q"],
        cwd=str(BURROW_ROOT),
        capture_output=True, text=True, timeout=120,
    )
    if install.returncode != 0:
        return {"success": False, "old_version": old_version,
                "error": f"pip install failed: {install.stderr.strip()}"}

    # Read the new version from pyproject.toml
    new_version = old_version
    try:
        content = PYPROJECT.read_text()
        for line in content.splitlines():
            m = re.match(r'version\s*=\s*"([^"]+)"', line.strip())
            if m:
                new_version = m.group(1)
                break
    except Exception:
        pass

    log.info("Updated %s -> %s", old_version, new_version)
    return {
        "success": True,
        "old_version": old_version,
        "new_version": new_version,
        "sha": git_current_sha(),
        "needs_restart": new_version != old_version,
    }


def bump_version_files(part: str = "patch") -> str:
    """Bump version in pyproject.toml, protocol.py, and plugin.json.
    Returns the new version string."""
    old = current_version()
    new = _bump_version(old, part)

    # Update pyproject.toml
    pyproject = PYPROJECT.read_text()
    pyproject = re.sub(
        r'version\s*=\s*"[^"]+"',
        f'version = "{new}"',
        pyproject, count=1)
    PYPROJECT.write_text(pyproject)

    # Update protocol.py
    proto_file = BURROW_ROOT / "burrow" / "protocol.py"
    proto = proto_file.read_text()
    proto = re.sub(
        r'VERSION\s*=\s*"[^"]+"',
        f'VERSION = "{new}"',
        proto, count=1)
    proto_file.write_text(proto)

    # Update plugin.json
    plugin_file = BURROW_ROOT / ".claude-plugin" / "plugin.json"
    if plugin_file.exists():
        pj = json.loads(plugin_file.read_text())
        pj["version"] = new
        plugin_file.write_text(json.dumps(pj, indent=2) + "\n")

    # Update test assertion
    test_file = BURROW_ROOT / "tests" / "test_protocol.py"
    if test_file.exists():
        test = test_file.read_text()
        test = re.sub(
            r'assert protocol\.VERSION == "[^"]+"',
            f'assert protocol.VERSION == "{new}"',
            test, count=1)
        test_file.write_text(test)

    log.info("Bumped version %s -> %s", old, new)
    return new
