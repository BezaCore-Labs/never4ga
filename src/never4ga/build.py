"""Which build of Never4gA this process is running.

A leaf beside `platform_paths` and `config`, on the same terms: it computes one
fact about the machine, touches nothing else, and every layer above may ask.

**Why a fingerprint rather than the version.** The version string stays the
same across many development builds, so it cannot answer the question the CLI
has: is the service it is about to hand work to running the same code? A
long-running daemon keeps the code it started with, and a CLI that delegates to
it would otherwise answer confidently from stale code with nothing reporting
the mismatch.

The fingerprint is over the installed `.py` files: their relative paths and
their **contents**.

**Contents, not stats.** Restoring a file to bytes it already had
(`git checkout -- .`, `git switch`, a rebase, a stash pop) writes a new mtime
over identical content. A stat-based fingerprint would then report the service
as stale while it runs exactly what the tree holds. The CLI would stop
delegating, and the in-process path would refuse the work, because a service
that owns the vault index does not accept a second writer. Hashing the contents
costs about a millisecond per process.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import blake2b
from pathlib import Path
from typing import Any, Final

__all__ = ["BUILD_ID_LENGTH", "STARTUP_BUILD_ID", "build_id", "build_matches"]

#: Short enough to print beside a URL in `service status`, long enough that two
#: builds colliding is not a thing anybody will see.
BUILD_ID_LENGTH: Final = 12

#: Skipped wholesale. `__pycache__` changes whenever Python feels like it, and a
#: fingerprint that moved on import would report every service as stale and be
#: switched off within a day.
_IGNORED_DIRECTORIES: Final = frozenset({"__pycache__", ".mypy_cache", ".ruff_cache"})


def build_id(*, root: Path | None = None, version: str | None = None) -> str:
    """A short token identifying a build of the package.

    Never raises. Asking a process who it is must not be the thing that fails,
    so an unreadable file or a missing tree contributes nothing and the answer
    still comes back. A fingerprint over less than everything is still a
    fingerprint, and two processes computing it the same way still agree.

    **This reads the filesystem every time it is called.** For "which build am
    I running", use :data:`STARTUP_BUILD_ID` instead; see the note on it.
    """
    package = root if root is not None else Path(__file__).resolve().parent
    digest = blake2b(digest_size=BUILD_ID_LENGTH // 2)
    digest.update((version or _version()).encode())

    for path in sorted(_source_files(package)):
        try:
            content = path.read_bytes()
        except OSError:
            continue
        relative = path.relative_to(package).as_posix()
        digest.update(f"{relative}:{len(content)}:".encode())
        digest.update(content)
    return digest.hexdigest()


def _source_files(package: Path) -> list[Path]:
    try:
        return [
            path
            for path in package.rglob("*.py")
            if not _IGNORED_DIRECTORIES.intersection(path.parts)
        ]
    except OSError:
        return []


def _version() -> str:
    """The packaged version, or a placeholder when it cannot be read.

    Metadata lookups fail in odd environments, such as a source checkout with
    nothing installed or a frozen bundle, and none of those should stop a
    process from identifying itself.
    """
    try:
        from importlib.metadata import version

        return version("never4ga")
    except Exception:
        return "unknown"


#: What this process fingerprinted while it was starting.
#:
#: **The distinction is the whole guard.** :func:`build_id` describes the source
#: tree now; this describes it as it was when this process imported its code. A
#: daemon and a CLI running from one checkout share a filesystem, so the
#: on-demand value is identical for both however stale the daemon is, and a
#: guard comparing on-demand values could never fire.
#:
#: Computed at import, not on first use: a lazily cached value would be captured
#: at the first health request, which for a long-running service is after any
#: number of edits.
STARTUP_BUILD_ID: Final = build_id()


def build_matches(health: Mapping[str, Any]) -> bool:
    """Whether a service's health response reports this same build.

    An absent `build` is a mismatch rather than a pass. A response without the
    field comes from a service older than this check, which is exactly the
    staleness it exists to catch.
    """
    reported = health.get("build")
    return isinstance(reported, str) and reported == STARTUP_BUILD_ID
