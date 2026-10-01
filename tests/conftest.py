"""Shared fixtures.

With real SQLite and real platform paths, a test suite could write into the
machine it runs on. This one does not: every test gets its own XDG base
directories, so a derived index lands in ``tmp_path`` and never in the
developer's home.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from never4ga.domain.identity import ConceptId

#: core/05 section 7's four base directories.
_XDG_VARIABLES = (
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
)


@pytest.fixture(scope="session")
def nowhere_in_particular(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A directory that is not, and will never be, a vault."""
    return tmp_path_factory.mktemp("cwd")


@pytest.fixture(autouse=True)
def isolated_platform_paths(
    tmp_path: Path, nowhere_in_particular: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point every platform path at this test's own directory.

    Autouse and unconditional. A test that reaches
    :meth:`never4ga.platform_paths.PlatformPaths.resolve` without arguments --
    the CLI does, as the composition root -- would otherwise resolve the real
    ones, and a test suite that writes to `~/.local/share` is a test suite that
    can damage the machine it is verifying.
    """
    base = tmp_path / "platform"
    for variable in _XDG_VARIABLES:
        monkeypatch.setenv(variable, str(base / variable.removeprefix("XDG_").casefold()))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # The same argument, for the same reason. A developer running the suite has
    # NEVER4GA_VAULT pointing at a real vault, and a test that omits `--vault`
    # would otherwise resolve to it -- `init`, `index` and `rebuild` included.
    # A test that names no vault must get this test's own directory.
    monkeypatch.delenv("NEVER4GA_VAULT", raising=False)
    # And the same for the embedder. `OLLAMA_HOST` may be set on a developer's
    # machine and is read as a fallback by `[embedding]`, so a suite that left
    # it alone would decide "is an embedder configured" from whoever is running
    # it -- passing here and failing in CI, or the reverse.
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    # And the last resort under that is the current directory, which during a
    # test run is the repository. `init` there scatters a vault skeleton across
    # the working tree; `index` there indexes the source. Neither belongs to any
    # test, so the fallback is pointed somewhere disposable too -- and somewhere
    # outside `tmp_path`, because a test is free to use that as a vault root and
    # count what sits in it.
    monkeypatch.chdir(nowhere_in_particular)
    return base


@pytest.fixture
def concept_ids() -> list[ConceptId]:
    return [ConceptId.new() for _ in range(6)]
