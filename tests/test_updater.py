"""Tests for burrow self-update system."""

import json
import pytest
from pathlib import Path

from burrow import protocol
from burrow.updater import (
    current_version,
    _parse_version,
    version_newer,
    _bump_version,
    bump_version_files,
    git_current_sha,
    git_current_branch,
    BURROW_ROOT,
)


class TestVersionParsing:
    def test_parse_simple(self):
        assert _parse_version("1.2.3") == (1, 2, 3)

    def test_parse_major_minor(self):
        assert _parse_version("0.5") == (0, 5)

    def test_parse_with_prefix(self):
        assert _parse_version("v1.2.3") == (1, 2, 3)


class TestVersionComparison:
    def test_newer_patch(self):
        assert version_newer("0.5.1", "0.5.0") is True

    def test_newer_minor(self):
        assert version_newer("0.6.0", "0.5.9") is True

    def test_newer_major(self):
        assert version_newer("1.0.0", "0.99.99") is True

    def test_same(self):
        assert version_newer("0.5.0", "0.5.0") is False

    def test_older(self):
        assert version_newer("0.4.0", "0.5.0") is False


class TestVersionBump:
    def test_bump_patch(self):
        assert _bump_version("0.5.0", "patch") == "0.5.1"

    def test_bump_minor(self):
        assert _bump_version("0.5.3", "minor") == "0.6.0"

    def test_bump_major(self):
        assert _bump_version("0.5.3", "major") == "1.0.0"

    def test_bump_two_part(self):
        assert _bump_version("0.5", "patch") == "0.5.1"


class TestCurrentVersion:
    def test_current_matches_protocol(self):
        assert current_version() == protocol.VERSION

    def test_version_is_semver(self):
        v = current_version()
        parts = v.split(".")
        assert len(parts) == 3
        for p in parts:
            assert p.isdigit()


class TestGitHelpers:
    def test_git_sha_not_empty(self):
        sha = git_current_sha()
        assert sha and sha != "unknown"

    def test_git_branch_not_empty(self):
        branch = git_current_branch()
        assert branch and branch != "unknown"


class TestBumpVersionFiles:
    def test_bump_and_restore(self):
        """Bump version, verify files changed, then restore."""
        old_version = current_version()

        # Read original file contents
        pyproject = (BURROW_ROOT / "pyproject.toml").read_text()
        proto = (BURROW_ROOT / "burrow" / "protocol.py").read_text()
        plugin = (BURROW_ROOT / ".claude-plugin" / "plugin.json").read_text()
        test_file = (BURROW_ROOT / "tests" / "test_protocol.py").read_text()

        try:
            new_version = bump_version_files("patch")
            expected = _bump_version(old_version, "patch")
            assert new_version == expected

            # Verify pyproject.toml was updated
            new_pyproject = (BURROW_ROOT / "pyproject.toml").read_text()
            assert f'version = "{new_version}"' in new_pyproject

            # Verify protocol.py was updated
            new_proto = (BURROW_ROOT / "burrow" / "protocol.py").read_text()
            assert f'VERSION = "{new_version}"' in new_proto

            # Verify plugin.json was updated
            new_plugin = json.loads(
                (BURROW_ROOT / ".claude-plugin" / "plugin.json").read_text())
            assert new_plugin["version"] == new_version

            # Verify test was updated
            new_test = (BURROW_ROOT / "tests" / "test_protocol.py").read_text()
            assert f'assert protocol.VERSION == "{new_version}"' in new_test

        finally:
            # Restore original files
            (BURROW_ROOT / "pyproject.toml").write_text(pyproject)
            (BURROW_ROOT / "burrow" / "protocol.py").write_text(proto)
            (BURROW_ROOT / ".claude-plugin" / "plugin.json").write_text(plugin)
            (BURROW_ROOT / "tests" / "test_protocol.py").write_text(test_file)


class TestUpdateProtocol:
    def test_update_available_message(self):
        msg = protocol.update_available("0.6.0", "0.5.0",
                                         changelog="fix bugs")
        assert msg["type"] == "update_available"
        assert msg["version"] == "0.6.0"
        assert msg["current"] == "0.5.0"
        assert msg["changelog"] == "fix bugs"

    def test_update_status_message(self):
        msg = protocol.update_status("0.6.0", "updated")
        assert msg["type"] == "update_status"
        assert msg["version"] == "0.6.0"
        assert msg["status"] == "updated"

    def test_update_status_with_error(self):
        msg = protocol.update_status("0.6.0", "failed", error="git pull failed")
        assert msg["error"] == "git pull failed"
