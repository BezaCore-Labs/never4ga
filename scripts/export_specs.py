"""Copy the specifications from the vault into docs/specs/.

The vault is canonical. This copies the Never4gA workspace's `Architecture/`
byte for byte and records each file's SHA-256 in `docs/specs/manifest.json`, so
a public clone can read the design without the vault, and a test can tell when
the copy was edited here or has fallen behind its source.

It redacts nothing. When a specification says something that may not be
public, the fix is made in the specification, and `test_public_tree` refuses
the export until it is.

Repository infrastructure, like `.github/workflows/gate.yml`: not a `never4ga`
command, and not in the wheel.

    python scripts/export_specs.py                 # finds the source itself
    python scripts/export_specs.py --source DIR    # or is told

The source is `--source`, else `NEVER4GA_SPECS_SOURCE`, else the
`Architecture/` beside the workspace this repository resolves to, asked of the
`never4ga` CLI. The last needs the vault on this machine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
TARGET = REPOSITORY / "docs" / "specs"
MANIFEST = "manifest.json"
README = "README.md"
SOURCE_VARIABLE = "NEVER4GA_SPECS_SOURCE"

_README_TEXT = """\
# Specifications

**Generated. Do not edit these files here.**

These are Never4gA's specifications, copied byte for byte from the
maintainer's vault, where they are canonical. `manifest.json`
records where each came from and its SHA-256. A test fails when a file here
stops matching it, so an edit made here is caught rather than silently lost at
the next export.

To propose a change to a specification, open an issue. Accepted changes are
made at the source and exported.

Start with `00-spec-index.md`. `core/` is normative and `details/` elaborates.
Decisions are cited by number (ADR-00NN). The decision records themselves are
not published.
"""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cli(*arguments: str, env: Mapping[str, str] | None = None) -> dict[str, object] | None:
    executable = shutil.which("never4ga", path=(env or os.environ).get("PATH"))
    if executable is None:
        return None
    completed = subprocess.run(
        [executable, "--json", *arguments],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        check=False,
        env=None if env is None else dict(env),
    )
    if completed.returncode != 0:
        return None
    loaded = json.loads(completed.stdout)
    return loaded if isinstance(loaded, dict) else None


def find_source(env: Mapping[str, str] | None = None) -> tuple[Path, str] | None:
    """The specifications directory, and its vault-relative name, or ``None``.

    ``None`` is the ordinary answer anywhere the vault is not, CI included.
    ``env`` is the environment to ask in; the test suite passes the one it
    started with, because every test otherwise runs in a throwaway HOME.
    """
    environment = os.environ if env is None else env
    named = environment.get(SOURCE_VARIABLE)
    if named:
        path = Path(named).expanduser().resolve()
        return (path, path.name) if path.is_dir() else None
    found = find_workspace(env)
    if found is None:
        return None
    directory, relative = found
    path = directory / "Architecture"
    return (path, f"{relative}/Architecture") if path.is_dir() else None


def find_workspace(env: Mapping[str, str] | None = None) -> tuple[Path, str] | None:
    """The workspace directory this repository resolves to, and its vault path.

    Asked of the `never4ga` CLI, so it needs the vault on this machine.
    `scripts/public_check.py` finds the private markers through it too.
    """
    status = _cli("status", env=env)
    resolved = _cli("workspace", "resolve", "--path", str(REPOSITORY), env=env)
    if status is None or resolved is None:
        return None
    vault = Path(str(status.get("vault", "")))
    manifest = str(resolved.get("workspace_path", ""))
    if not manifest:
        return None
    relative = Path(manifest).parent
    directory = vault / relative
    return (directory, relative.as_posix()) if directory.is_dir() else None


def source_files(source: Path) -> dict[str, str]:
    """Every file under `source`, by POSIX relative path, with its SHA-256."""
    return {
        path.relative_to(source).as_posix(): digest(path)
        for path in sorted(source.rglob("*"))
        if path.is_file() and not any(part.startswith(".") for part in path.parts)
    }


def export(source: Path, name: str) -> dict[str, str]:
    files = source_files(source)
    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)
    for relative in files:
        destination = TARGET / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
    (TARGET / README).write_text(_README_TEXT, encoding="utf-8")
    manifest = {"source": name, "files": files}
    (TARGET / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", help="the specifications directory to copy")
    arguments = parser.parse_args(argv)
    if arguments.source:
        path = Path(arguments.source).expanduser().resolve()
        found = (path, path.name) if path.is_dir() else None
    else:
        found = find_source()
    if found is None:
        print(
            f"no specifications found: pass --source, set {SOURCE_VARIABLE}, or run "
            "where `never4ga` can resolve this repository to its workspace",
            file=sys.stderr,
        )
        return 1
    source, name = found
    files = export(source, name)
    print(f"exported {len(files)} files from {name} to {TARGET.relative_to(REPOSITORY)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
