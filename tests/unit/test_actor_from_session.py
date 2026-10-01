"""`core/02` §35: AI-generated canonical content records an agent producer.

An agent that does not pass `--actor` must not produce a document claiming a
person wrote it. Never4gA cannot infer the producer from the process alone, but
a session id on the command line names a session a client opened with
`context startup --client <id>`, and the store recorded which.

The resolution order is explicit, then observed, then the default:

1. ``--actor`` wins, always. It is the only way to record a model as well as a
   client, which `core/02` §5.2's own example does.
2. ``--session`` names a session that recorded a producer at startup.
3. Neither: ``human:owner``, unchanged, which is right for a person typing.

A session's *client* is not promoted to producer. `core/02` section 5.2 wants
``<producer>/<version>`` and a client id has no version, so deriving one would
fabricate the field. That case is an error naming the fix.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.cli import main
from never4ga.domain.identity import ConceptId, SessionId
from never4ga.domain.sessions import Session
from never4ga.platform_paths import PlatformPaths
from never4ga.services import VaultInitializer

SESSION = "01a04bd1-7357-7002-8bbc-8e82bcb91959"


def fixed_clock() -> datetime:
    return datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(
        FileSystemVaultFileStore(root), FileSystemMarkdownStore(root), now=fixed_clock
    ).initialize("Test Vault")
    return root


def open_session(
    vault: Path, *, client: str | None, actor: str = "human:owner", workspace: str | None = None
) -> None:
    """Record a session the way `context startup --client` would have.

    A startup that named a client but no `--actor` records `human:owner` as its
    actor, because the session's actor is just `--actor` echoed.
    """
    from never4ga.composition import FileSessionStore

    documents = FileSystemMarkdownStore(vault)
    manifest = next(
        d for d in documents.iter_documents() if d.frontmatter.get("type") == "system_manifest"
    )
    store = FileSessionStore(PlatformPaths.resolve().sessions_database(manifest.concept_id))
    store.open(
        Session.opened(
            workspace=(manifest.concept_id if workspace is None else ConceptId.parse(workspace)),
            actor=actor,
            started_at=fixed_clock(),
            client=client,
            session_id=SessionId.parse(SESSION),
        )
    )


def create(vault: Path, *, actor: str | None = None, session: str | None = None) -> int:
    """`--actor` is global; `--session` sits on the verb, as checkpoint's does."""
    globals_ = ["--actor", actor] if actor else []
    verb = ["--session", session] if session else []
    return main(
        ["--vault", str(vault), *globals_, "concept", "create", "knowledge", "A Note", *verb]
    )


def producer(vault: Path) -> str:
    documents = FileSystemMarkdownStore(vault)
    note = next(d for d in documents.iter_documents() if d.frontmatter.get("title") == "A Note")
    generated = note.frontmatter["generated"]
    assert isinstance(generated, dict)
    by = generated["by"]
    assert isinstance(by, str)
    return by


class TestWhoTheDocumentSaysWroteIt:
    def test_an_explicit_actor_wins(self, vault: Path) -> None:
        open_session(vault, client="claude-code")
        assert create(vault, actor="claude-code/claude-opus-5", session=SESSION) == 0
        assert producer(vault) == "claude-code/claude-opus-5"

    def test_a_session_with_no_producer_is_refused_not_guessed(self, vault: Path) -> None:
        """A client id is not a producer: core/02 section 5.2 wants a version.

        `claude-code` alone fails schema validation, and inventing a version
        would fabricate the field. The caller is told what to pass instead of
        being attributed to a person.
        """
        open_session(vault, client="claude-code")
        assert create(vault, session=SESSION) != 0

    def test_a_session_that_named_an_actor_is_preferred_over_its_client(self, vault: Path) -> None:
        """A startup given `--actor` recorded the precise producer already.

        core/02 section 5.2's own example is client *and* model, so the more
        specific value wins over the client that merely implies it.
        """
        open_session(vault, client="claude-code", actor="claude-code/claude-opus-5")
        assert create(vault, session=SESSION) == 0
        assert producer(vault) == "claude-code/claude-opus-5"

    def test_no_session_and_no_actor_is_still_the_owner(self, vault: Path) -> None:
        """A person typing is the case `human:owner` was always right for."""
        assert create(vault) == 0
        assert producer(vault) == "human:owner"

    def test_a_session_that_named_nothing_at_all_is_refused_too(self, vault: Path) -> None:
        open_session(vault, client=None)
        assert create(vault, session=SESSION) != 0

    def test_an_unknown_session_is_refused_rather_than_ignored(self, vault: Path) -> None:
        """An unknown session is an error, not a silent `human:owner`.

        The agent asked to be attributed and named a session that does not
        exist, so any producer written would be a guess.
        """
        assert create(vault, session=SESSION) != 0


class TestTheLogWrapWrites:
    """`wrap` writes a document too, and it already knows the session.

    It resolves the producer the same way, except that it never refuses over
    it. Refusing would lose a session's only durable record over a metadata
    field, so it writes the log and then says what is wrong.
    """

    def a_workspace(self, vault: Path) -> str:
        assert main(["--vault", str(vault), "workspace", "create", "WS", "--type", "product"]) == 0
        documents = FileSystemMarkdownStore(vault)
        workspace = next(
            d for d in documents.iter_documents() if d.frontmatter.get("type") == "workspace"
        )
        return str(workspace.concept_id)

    def wrap(self, vault: Path, session: str) -> int:
        assert (
            main(
                [
                    "--vault",
                    str(vault),
                    "checkpoint",
                    "--session",
                    session,
                    "something happened",
                ]
            )
            == 0
        )
        return main(["--vault", str(vault), "wrap", "--session", session, "--title", "A Session"])

    def log(self, vault: Path) -> str:
        documents = FileSystemMarkdownStore(vault)
        entry = next(
            d for d in documents.iter_documents() if d.frontmatter.get("type") == "activity_log"
        )
        generated = entry.frontmatter["generated"]
        assert isinstance(generated, dict)
        by = generated["by"]
        assert isinstance(by, str)
        return by

    def test_it_records_the_session_producer(self, vault: Path) -> None:
        workspace = self.a_workspace(vault)
        open_session(
            vault,
            client="claude-code",
            actor="claude-code/claude-opus-5",
            workspace=workspace,
        )
        assert self.wrap(vault, SESSION) == 0
        assert self.log(vault) == "claude-code/claude-opus-5"

    def test_it_still_writes_when_the_session_named_no_producer(self, vault: Path) -> None:
        """The log matters more than the field; it falls back to `human:owner`."""
        open_session(vault, client="claude-code", workspace=self.a_workspace(vault))
        assert self.wrap(vault, SESSION) == 0
        assert self.log(vault) == "human:owner"

    def test_the_first_wrap_honours_an_explicit_actor(self, vault: Path) -> None:
        """`--actor` must win on the first wrap, not only on a rewrite.

        The session here recorded `human:owner`. An explicit `--actor` on the
        wrap must still override what the session recorded.
        """
        open_session(vault, client="claude-code", workspace=self.a_workspace(vault))
        assert main(["--vault", str(vault), "checkpoint", "--session", SESSION, "it happened"]) == 0
        assert (
            main(
                [
                    "--vault",
                    str(vault),
                    "--actor",
                    "codex/gpt-5",
                    "wrap",
                    "--session",
                    SESSION,
                    "--title",
                    "A Session",
                ]
            )
            == 0
        )
        assert self.log(vault) == "codex/gpt-5"

    def test_a_second_wrap_records_the_same_producer_as_the_first(self, vault: Path) -> None:
        """Creating and rewriting a log resolve the producer from one source.

        Otherwise a first and second wrap of one session could disagree, and
        `--actor` could be ignored on every wrap after the first.
        """
        open_session(vault, client="claude-code", workspace=self.a_workspace(vault))
        assert (
            main(
                [
                    "--vault",
                    str(vault),
                    "--actor",
                    "claude-code/claude-opus-5",
                    "checkpoint",
                    "--session",
                    SESSION,
                    "something happened",
                ]
            )
            == 0
        )
        for _ in range(2):
            assert (
                main(
                    [
                        "--vault",
                        str(vault),
                        "--actor",
                        "claude-code/claude-opus-5",
                        "wrap",
                        "--session",
                        SESSION,
                        "--title",
                        "A Session",
                    ]
                )
                == 0
            )
        assert self.log(vault) == "claude-code/claude-opus-5"
