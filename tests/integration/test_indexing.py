"""The index, end to end: a real vault on disk and a real SQLite database.

These run through `main()` because that is the composition root, the only
place that picks concrete adapters, so what they exercise is what a user gets.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from never4ga.cli import EXIT_FAILED, EXIT_OK, main
from never4ga.platform_paths import PlatformPaths
from never4ga.services.indexing import IndexService

Run = Callable[..., "Result"]


class Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err

    @property
    def json(self) -> Any:
        return json.loads(self.out or self.err)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str, as_json: bool = False) -> Result:
        code = main(["--vault", str(vault), *(["--json"] if as_json else []), *arguments])
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return invoke


@pytest.fixture
def initialized(run: Run) -> Run:
    run("init")
    return run


def note(vault: Path, name: str, body: str, **frontmatter: Any) -> Path:
    """Write a concept the way a user would: by saving a file."""
    from never4ga.domain.identity import ConceptId

    path = vault / "30_Knowledge" / "Notes" / f"{name}.md"
    fields = {
        "type": "knowledge",
        "id": str(ConceptId.new()),
        "schema": "never4ga/0.1",
        "title": name.replace("-", " ").title(),
        "created_at": "2026-08-23T12:00:00Z",
        **frontmatter,
    }
    rendered = "\n".join(f"{key}: {value}" for key, value in fields.items())
    path.write_text(f"---\n{rendered}\n---\n\n{body}\n", encoding="utf-8")
    return path


def _line_of(path: Path, needle: str) -> int:
    lines = path.read_text(encoding="utf-8").splitlines()
    return next(number for number, line in enumerate(lines, start=1) if line == needle)


def database_of(vault: Path) -> Path:
    from never4ga.adapters.filesystem import FileSystemMarkdownStore
    from never4ga.layout import SYSTEM_MANIFEST

    manifest = FileSystemMarkdownStore(vault).get_by_path(SYSTEM_MANIFEST)
    assert manifest is not None
    return PlatformPaths.resolve().index_database(manifest.concept_id)


class TestIndexingAVault:
    def test_a_note_becomes_searchable(self, initialized: Run, vault: Path) -> None:
        note(vault, "mechanical-context", "# Context\nMechanical acquisition beats guessing.")
        initialized("index")
        results = initialized("search", "mechanical", as_json=True).json["results"]
        assert [result["title"] for result in results] == ["Mechanical Context"]

    def test_an_exact_identifier_is_found_and_reported_as_such(
        self, initialized: Run, vault: Path
    ) -> None:
        note(vault, "decision", "# Decision\nSee ADR-0002 for the ruling.")
        note(vault, "distractor", "# Other\nSee ADR-00021, a different document.")
        initialized("index")
        results = initialized("search", "ADR-0002", as_json=True).json["results"]
        assert [result["reason"] for result in results] == ["exact_identifier_match"]
        assert len(results) == 1

    def test_a_result_carries_an_excerpt_and_its_source_lines(
        self, initialized: Run, vault: Path
    ) -> None:
        note(vault, "thing", "# Heading\nThe prose that should be quoted back.")
        initialized("index")
        (result,) = initialized("search", "quoted", as_json=True).json["results"]
        assert "should be quoted back" in result["excerpt"]
        # File-relative: the note's frontmatter block occupies the first lines.
        heading_line = _line_of(vault / "30_Knowledge" / "Notes" / "thing.md", "# Heading")
        assert result["lines"] == [heading_line, heading_line + 1]
        assert result["heading_path"] == ["Heading"]


class TestIncrementalIndexing:
    def test_an_unchanged_vault_reindexes_nothing(self, initialized: Run, vault: Path) -> None:
        note(vault, "thing", "# One\nbody")
        initialized("index")
        report = initialized("index", "--changed", as_json=True).json
        assert report["indexed"] == 0
        assert report["unchanged"] >= 1

    def test_an_edited_note_is_reindexed(self, initialized: Run, vault: Path) -> None:
        path = note(vault, "thing", "# One\nobsolete wording")
        initialized("index")
        path.write_text(
            path.read_text(encoding="utf-8").replace("obsolete", "current"), encoding="utf-8"
        )
        assert initialized("index", "--changed", as_json=True).json["indexed"] == 1
        assert initialized("search", "obsolete", as_json=True).json["results"] == []
        assert initialized("search", "current", as_json=True).json["results"]

    def test_a_deleted_file_disappears_from_the_index(self, initialized: Run, vault: Path) -> None:
        path = note(vault, "thing", "# One\nmechanical")
        initialized("index")
        path.unlink()
        assert initialized("index", as_json=True).json["removed"] == 1
        assert initialized("search", "mechanical", as_json=True).json["results"] == []

    def test_a_move_keeps_identity(self, initialized: Run, vault: Path) -> None:
        # details/data-indexing-maintenance.md section 14. Obsidian moves files;
        # the index must recognise the concept rather than mint a second one.
        path = note(vault, "thing", "# One\nmechanical")
        initialized("index")
        before = initialized("search", "mechanical", as_json=True).json["results"][0]["id"]

        destination = vault / "90_Archive" / "Knowledge" / "Notes"
        destination.mkdir(parents=True)
        shutil.move(str(path), str(destination / path.name))

        report = initialized("index", as_json=True).json
        assert report["removed"] == 0

        # Identity survives the move, which is what this test is named for.
        # Search no longer answers for it, because the destination is the
        # archive, which core/07 Stage B excludes from retrieval. So the
        # identity is checked where it lives, not through search.
        assert initialized("search", "mechanical", as_json=True).json["results"] == []
        concept = initialized("concept", "get", before, as_json=True).json
        assert concept["id"] == before
        assert concept["path"].startswith("90_Archive/")
        # Manifest, home, the Domain Registry, and the moved note.
        assert initialized("index", "--status", as_json=True).json["indexed_documents"] == 4


class TestRebuildLeavesDurableStateAlone:
    """`rebuild` discards projections. Not everything beside them is one.

    The session store is machine-local *durable* state. Nothing in the vault
    ever held a checkpoint, so nothing can rebuild one, and a `rebuild` that
    swept the directory would destroy what it could not restore.
    """

    def test_the_session_database_survives_a_rebuild(self, initialized: Run, vault: Path) -> None:
        from datetime import UTC, datetime

        from never4ga.adapters.sqlite.session_store import SQLiteSessionStore
        from never4ga.adapters.sqlite.sessions import open_sessions
        from never4ga.domain.identity import ConceptId
        from never4ga.domain.sessions import Checkpoint, Session

        note(vault, "thing", "# One\nmechanical")
        initialized("index")

        vault_id = ConceptId.parse(initialized("status", as_json=True).json["vault_id"])
        database = PlatformPaths.resolve().sessions_database(vault_id)
        database.parent.mkdir(parents=True, exist_ok=True)

        connection = open_sessions(database)
        try:
            store = SQLiteSessionStore(connection)
            session = Session.opened(
                workspace=ConceptId.new(),
                actor="claude-code/claude-opus-5",
                started_at=datetime.now(UTC),
            )
            store.open(session)
            store.append(
                Checkpoint(
                    session=session.id,
                    recorded_at=datetime.now(UTC),
                    note="something only this file knows",
                )
            )
        finally:
            connection.close()

        assert initialized("rebuild").code == EXIT_OK

        connection = open_sessions(database)
        try:
            survived = SQLiteSessionStore(connection).checkpoints(session.id)
            assert [c.note for c in survived] == ["something only this file knows"]
        finally:
            connection.close()


class TestRebuild:
    def test_rebuilding_produces_the_same_answers(self, initialized: Run, vault: Path) -> None:
        note(vault, "thing", "# One\nmechanical\n\n## Two\ncontext")
        initialized("index")
        before = initialized("search", "mechanical context", as_json=True).json["results"]
        initialized("rebuild")
        assert initialized("search", "mechanical context", as_json=True).json["results"] == before

    def test_deleting_the_database_costs_nothing_but_a_rebuild(
        self, initialized: Run, vault: Path
    ) -> None:
        # details/data-indexing-maintenance.md section 17, the invariant the
        # whole index rests on.
        note(vault, "target", "# Target\nthe other note")
        note(vault, "source", "# Source\nmechanical, see [target](target.md)")
        initialized("index")
        before = initialized("search", "mechanical", as_json=True).json["results"]
        status_before = initialized("index", "--status", as_json=True).json

        database = database_of(vault)
        assert database.is_file()
        database.unlink()

        initialized("index")
        assert initialized("search", "mechanical", as_json=True).json["results"] == before
        after = initialized("index", "--status", as_json=True).json
        assert after["indexed_documents"] == status_before["indexed_documents"]
        assert after["broken_links"] == status_before["broken_links"]

    def test_the_database_lives_outside_the_vault(self, initialized: Run, vault: Path) -> None:
        # core/05 section 7: derived state never enters the Git-backed vault.
        note(vault, "thing", "# One\nbody")
        initialized("index")
        assert not database_of(vault).is_relative_to(vault)


class TestStalenessIsReportedNotRepaired:
    def test_search_warns_but_does_not_reindex(self, initialized: Run, vault: Path) -> None:
        note(vault, "thing", "# One\nmechanical")
        initialized("index")
        note(vault, "later", "# Later\nadded after indexing")

        assert initialized("search", "mechanical", as_json=True).json["index_is_stale"]
        assert initialized("search", "added", as_json=True).json["results"] == []
        assert initialized("index", "--status", as_json=True).json["stale"]

    def test_status_exits_nonzero_when_stale(self, initialized: Run, vault: Path) -> None:
        note(vault, "thing", "# One\nmechanical")
        assert initialized("index", "--status").code == EXIT_FAILED
        initialized("index")
        assert initialized("index", "--status").code == EXIT_OK

    def test_a_touched_file_with_the_same_content_is_not_stale(
        self, initialized: Run, vault: Path
    ) -> None:
        # details/data-indexing-maintenance.md section 13: `path + size + mtime`
        # "may avoid unnecessary reads", but change detection is content-hash
        # based, and mtime is not durable semantic provenance. Anything that
        # rewrites mtime without rewriting bytes (`git checkout`, a pull, a
        # formatter, rsync) must not make the index look behind the vault.
        path = note(vault, "thing", "# One\nmechanical")
        initialized("index")
        stat = path.stat()
        os.utime(path, (stat.st_atime + 120, stat.st_mtime + 120))

        report = initialized("index", "--status", as_json=True).json
        assert report["changed"] == []
        assert not report["stale"]

    def test_a_touched_file_does_not_stay_stale_after_an_incremental_pass(
        self, initialized: Run, vault: Path
    ) -> None:
        # The watcher runs `--changed`. It skips a document whose content hash
        # is unchanged, so unless that pass also refreshes the recorded stat the
        # cheap comparison disagrees with it forever and nothing but a full
        # reindex can settle it.
        path = note(vault, "thing", "# One\nmechanical")
        initialized("index")
        stat = path.stat()
        os.utime(path, (stat.st_atime + 120, stat.st_mtime + 120))

        assert initialized("index", "--changed", as_json=True).json["indexed"] == 0
        assert initialized("index", "--status").code == EXIT_OK

    def test_settling_a_touched_file_stops_it_being_reread(
        self, initialized: Run, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Confirming a stat mismatch costs a read. That is the right price for
        # an answer, but the incremental pass has already established the hash
        # is unchanged, so it must record what it learned. Otherwise every
        # read command pays that price again for the life of the index.
        path = note(vault, "thing", "# One\nmechanical")
        initialized("index")
        stat = path.stat()
        os.utime(path, (stat.st_atime + 120, stat.st_mtime + 120))
        initialized("index", "--changed")

        verifications = 0
        original = IndexService._really_changed

        def counting(self: IndexService, suspect: Any, by_path: Any, foreign: Any) -> Any:
            nonlocal verifications
            if suspect:
                verifications += 1
            return original(self, suspect, by_path, foreign)

        monkeypatch.setattr(IndexService, "_really_changed", counting)
        assert initialized("index", "--status").code == EXIT_OK
        assert verifications == 0


class TestMaintenanceFindings:
    def test_a_broken_link_is_reported(self, initialized: Run, vault: Path) -> None:
        note(vault, "thing", "# One\nsee [nothing](nowhere.md)")
        initialized("index")
        (link,) = initialized("index", "--status", as_json=True).json["broken_links"]
        assert link["target"] == "30_Knowledge/Notes/nowhere.md"

    def test_doctor_reports_a_broken_link(self, initialized: Run, vault: Path) -> None:
        note(vault, "thing", "# One\nsee [nothing](nowhere.md)")
        initialized("index")
        codes = {
            finding["code"] for finding in initialized("doctor", as_json=True).json["findings"]
        }
        assert "broken_link" in codes

    # core/01 section 4 makes `index.md` reserved OKF navigation, so it never
    # carries concept frontmatter and never becomes a concept. A link to one
    # must still resolve. Otherwise `doctor` would call every navigation link
    # in a spec-conforming vault broken, while a link to the same directory
    # was fine.

    def test_a_link_to_reserved_navigation_is_not_broken(
        self, initialized: Run, vault: Path
    ) -> None:
        note(vault, "thing", "# One\nsee [the notes](index.md)")
        (vault / "30_Knowledge" / "Notes" / "index.md").write_text("# Notes\n")
        initialized("index")
        assert initialized("index", "--status", as_json=True).json["broken_links"] == []

    def test_doctor_does_not_report_a_link_to_reserved_navigation(
        self, initialized: Run, vault: Path
    ) -> None:
        note(vault, "thing", "# One\nsee [the vault](../../index.md)")
        initialized("index")
        codes = {
            finding["code"] for finding in initialized("doctor", as_json=True).json["findings"]
        }
        assert "broken_link" not in codes

    def test_a_link_to_a_missing_concept_is_still_broken(
        self, initialized: Run, vault: Path
    ) -> None:
        # The exemption is for reserved navigation by name, not for everything.
        note(vault, "thing", "# One\nsee [gone](nowhere.md)")
        initialized("index")
        assert initialized("index", "--status", as_json=True).json["broken_links"] != []

    def test_doctor_reports_a_stale_index(self, initialized: Run, vault: Path) -> None:
        note(vault, "thing", "# One\nbody")
        initialized("index")
        note(vault, "later", "# Later\nadded after indexing")
        codes = {
            finding["code"] for finding in initialized("doctor", as_json=True).json["findings"]
        }
        assert "index_is_stale" in codes

    def test_doctor_needs_no_index_to_run(self, initialized: Run, vault: Path) -> None:
        # And must not create one: `doctor` observes, it does not build.
        note(vault, "thing", "# One\nbody")
        assert initialized("doctor").code == EXIT_OK
        assert not database_of(vault).exists()

    def test_a_duplicate_identity_is_reported(self, initialized: Run, vault: Path) -> None:
        first = note(vault, "thing", "# One\nbody")
        duplicate = vault / "30_Knowledge" / "Notes" / "copy.md"
        duplicate.write_text(first.read_text(encoding="utf-8"), encoding="utf-8")
        codes = {issue["code"] for issue in initialized("index", as_json=True).json["issues"]}
        assert "duplicate_id" in codes

    def test_a_relation_to_nothing_is_reported(self, initialized: Run, vault: Path) -> None:
        from never4ga.domain.identity import ConceptId

        note(
            vault,
            "thing",
            "# One\nbody",
            relations=f"[{{type: depends_on, target: {ConceptId.new()}}}]",
        )
        initialized("index")
        assert initialized("index", "--status", as_json=True).json["unresolved_relations"]


class TestOtherPeoplesVaults:
    """Syntax Never4gA reads but never writes (`core/02` §31).

    Obsidian writes wikilinks by default, so a vault that arrives from anywhere
    else has them. A vault whose links are invisible gets a `doctor` reporting
    zero broken links, which is a confident wrong answer.
    """

    def test_a_wikilink_resolves_by_filename(self, initialized: Run, vault: Path) -> None:
        note(vault, "target", "# Target\nthe other note")
        note(vault, "source", "# Source\nsee [[target]]")
        initialized("index")
        assert initialized("index", "--status", as_json=True).json["broken_links"] == []

    def test_a_broken_wikilink_is_reported_by_the_name_written(
        self, initialized: Run, vault: Path
    ) -> None:
        note(vault, "source", "# Source\nsee [[nowhere]]")
        initialized("index")
        (link,) = initialized("index", "--status", as_json=True).json["broken_links"]
        assert (link["target"], link["named"]) == ("nowhere", True)

    def test_doctor_names_a_broken_wikilink(self, initialized: Run, vault: Path) -> None:
        note(vault, "source", "# Source\nsee [[nowhere]]")
        initialized("index")
        findings = initialized("doctor", as_json=True).json["findings"]
        (broken,) = [f for f in findings if f["code"] == "broken_link"]
        assert "nowhere" in broken["message"]

    def test_a_setext_heading_becomes_a_chunk_boundary(self, initialized: Run, vault: Path) -> None:
        note(vault, "thing", "Overview\n========\n\nfirst part\n\nDetail\n------\n\nsecond part")
        initialized("index")
        (result,) = initialized("search", "second", as_json=True).json["results"]
        assert result["heading_path"] == ["Overview", "Detail"]

    def test_a_vault_written_the_other_way_round_still_works(
        self, initialized: Run, vault: Path
    ) -> None:
        # Setext headings and wikilinks together: an Obsidian vault as found.
        note(vault, "target", "Target\n======\n\nthe other note")
        note(vault, "source", "Source\n======\n\nmechanical, see [[target]]")
        initialized("index")
        (result,) = initialized("search", "mechanical", as_json=True).json["results"]
        assert result["heading_path"] == ["Source"]
        assert initialized("index", "--status", as_json=True).json["broken_links"] == []


class TestReadingAConcept:
    def test_a_concept_is_readable_before_anything_is_indexed(
        self, initialized: Run, vault: Path
    ) -> None:
        # core/00 #1: Markdown is canonical, so nothing about reading a concept
        # may depend on a projection existing.
        path = note(vault, "thing", "# One\nthe canonical prose")
        concept_id = path.read_text(encoding="utf-8").split("id: ")[1].split("\n")[0]
        result = initialized("concept", "get", concept_id, as_json=True).json
        assert "the canonical prose" in result["body"]
        assert result["index_is_stale"]

    def test_relations_are_reported_in_both_directions(self, initialized: Run, vault: Path) -> None:
        target = note(vault, "target", "# Target\nbody")
        target_id = target.read_text(encoding="utf-8").split("id: ")[1].split("\n")[0]
        source = note(
            vault,
            "source",
            "# Source\nbody",
            relations=f"[{{type: depends_on, target: {target_id}}}]",
        )
        source_id = source.read_text(encoding="utf-8").split("id: ")[1].split("\n")[0]
        initialized("index")

        outgoing = initialized("concept", "get", source_id, as_json=True).json["relations"]
        incoming = initialized("concept", "get", target_id, as_json=True).json["relations"]
        assert [n["id"] for n in outgoing["outgoing"]] == [target_id]
        assert [n["id"] for n in incoming["incoming"]] == [source_id]


class TestAnUninitializedVault:
    def test_indexing_says_what_is_missing(self, run: Run) -> None:
        result = run("index", as_json=True)
        assert result.code == EXIT_FAILED
        assert result.json["error"]["code"] == "vault_not_initialized"
