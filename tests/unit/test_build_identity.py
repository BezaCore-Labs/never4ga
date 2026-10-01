"""A running service and the CLI talking to it must be the same build.

`_connect` refuses to delegate to a service that owns a different vault,
because the answer would be confident and wrong. A service running different
code is the same failure with a different cause: a user who upgrades the
package keeps getting the old behaviour from a daemon started before the
upgrade, and nothing says so.

The version string cannot answer this, because it stays the same across many
commits. The fingerprint is over the installed source itself.
"""

from __future__ import annotations

import os
from pathlib import Path

from never4ga.build import build_id, build_matches


class TestItIdentifiesTheSource:
    def test_it_is_stable_across_calls(self) -> None:
        assert build_id() == build_id()

    def test_it_is_a_short_readable_token(self) -> None:
        token = build_id()

        assert token
        assert len(token) <= 32
        assert token.isalnum() or "." in token or "+" in token

    def test_it_changes_when_a_source_file_changes(self, tmp_path: Path) -> None:
        """The property the version string does not have."""
        package = tmp_path / "pkg"
        (package / "sub").mkdir(parents=True)
        (package / "__init__.py").write_text("x = 1\n")
        (package / "sub" / "thing.py").write_text("y = 2\n")

        before = build_id(root=package, version="0.1.0")
        (package / "sub" / "thing.py").write_text("y = 3\n")
        after = build_id(root=package, version="0.1.0")

        assert before != after

    def test_it_changes_when_the_version_changes(self, tmp_path: Path) -> None:
        package = tmp_path / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("x = 1\n")

        assert build_id(root=package, version="0.1.0") != build_id(root=package, version="0.2.0")

    def test_it_ignores_bytecode_and_caches(self, tmp_path: Path) -> None:
        """`__pycache__` changes whenever Python feels like it.

        A fingerprint that moved on import would report every service as stale
        and be switched off within a day.
        """
        package = tmp_path / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("x = 1\n")
        before = build_id(root=package, version="0.1.0")

        cache = package / "__pycache__"
        cache.mkdir()
        (cache / "__init__.cpython-314.pyc").write_bytes(b"\x00\x01")

        assert build_id(root=package, version="0.1.0") == before

    def test_restoring_a_file_restores_the_fingerprint(self, tmp_path: Path) -> None:
        """`git checkout -- .` writes a new mtime over bytes that did not change.

        A rebase that touches a file and puts it back, a stash pop and a
        `git switch` do the same. A false mismatch is costly: the CLI stops
        delegating, the in-process path refuses the work because a service
        already owns the vault index, and the only remedy is a needless restart.
        """
        package = tmp_path / "pkg"
        package.mkdir()
        source = package / "__init__.py"
        source.write_text("x = 1\n")
        before = build_id(root=package, version="0.1.0")

        stat = source.stat()
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

        assert build_id(root=package, version="0.1.0") == before

    def test_it_changes_when_content_changes_beneath_an_unmoved_stat(self, tmp_path: Path) -> None:
        """A change that preserves path, size and mtime still changes the id.

        The fingerprint hashes content, not file metadata, and hashing this
        package is cheap next to a CLI invocation.
        """
        package = tmp_path / "pkg"
        package.mkdir()
        source = package / "__init__.py"
        source.write_text("x = 1\n")
        before = build_id(root=package, version="0.1.0")
        stat = source.stat()

        source.write_text("x = 2\n")
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))

        assert build_id(root=package, version="0.1.0") != before

    def test_a_missing_root_does_not_raise(self, tmp_path: Path) -> None:
        """Asking who you are must never be the thing that fails."""
        assert build_id(root=tmp_path / "gone", version="0.1.0")

    def test_an_unreadable_file_does_not_raise(self, tmp_path: Path) -> None:
        """A fingerprint over less than everything is still a fingerprint.

        Reading contents gives this more ways to fail than a stat would, and
        none of them may stop a process identifying itself.
        """
        package = tmp_path / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("x = 1\n")
        unreadable = package / "locked.py"
        unreadable.write_text("y = 2\n")
        unreadable.chmod(0o000)

        try:
            assert build_id(root=package, version="0.1.0")
        finally:
            unreadable.chmod(0o644)


class TestTheCliWillNotDelegateToADifferentBuild:
    """The CLI declines to delegate to a service running different code.

    `cli._connect` refuses a service that owns a different vault for the same
    reason: the answer would be confident and wrong. The remedy is the same.
    Decline to delegate, and say why rather than quietly answering by another
    route.
    """

    def test_a_matching_build_is_accepted(self) -> None:
        assert build_matches({"build": build_id()}) is True

    def test_a_different_build_is_rejected(self) -> None:
        assert build_matches({"build": "0000deadbeef"}) is False

    def test_a_service_too_old_to_report_one_is_rejected(self) -> None:
        """Absent is not "probably fine".

        A health response with no `build` comes from a service older than the
        field, so it is stale in exactly the way this checks for.
        """
        assert build_matches({"vault_id": "x"}) is False
