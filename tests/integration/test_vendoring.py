"""Hashing a vendored directory and recording where it came from (core/02 section 21.21).

Against a real filesystem vault rather than fakes: the thing being measured is
what is on disk, and a fake file store that agreed with the service about what
a directory contains would prove nothing about either.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.vendoring import Drift, Origin, OriginKind, UpstreamStatus
from never4ga.services import VaultInitializer
from never4ga.services.adapters import AGENTS_DIRECTORY, SKILLS_DIRECTORY
from never4ga.services.vendoring import (
    ProvenanceError,
    VendoringService,
    provenance_path,
)

SUBJECT = f"{SKILLS_DIRECTORY}/borrowed"


def fixed_clock() -> datetime:
    return datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(
        FileSystemVaultFileStore(root), FileSystemMarkdownStore(root), now=fixed_clock
    ).initialize("Test Vault")
    return root


@pytest.fixture
def service(vault: Path) -> VendoringService:
    return VendoringService(FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault))


def vendor(vault: Path, name: str = "borrowed", **files: str) -> str:
    directory = vault / SKILLS_DIRECTORY / name
    contents = files or {"SKILL_md": "# Borrowed\n"}
    for key, text in contents.items():
        path = directory / key.replace("__", "/").replace("_md", ".md").replace("_py", ".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return f"{SKILLS_DIRECTORY}/{name}"


def an_origin() -> Origin:
    return Origin(
        kind=OriginKind.GIT,
        fetched_at="2026-08-30T12:00:00Z",
        url="https://example.invalid/repo.git",
        ref="refs/heads/main",
        commit="b" * 40,
        subpath="skills/borrowed",
        license="MIT",
        author="somebody",
    )


class TestTheRecordLivesOutsideTheIsland:
    """`50_System/Skills/` is not validated or indexed (core/02 section 3.3).

    A sidecar kept inside it would be invisible to `doctor` and to the index,
    and whole-directory deployment would copy vault bookkeeping into all three
    client trees.
    """

    def test_it_sits_beside_the_system_manifest(self) -> None:
        assert str(provenance_path(SUBJECT)) == "50_System/skill-provenance_borrowed.md"

    def test_an_agent_record_is_distinguishable_from_a_skills(self) -> None:
        """A Skill and an agent may share a name; their records may not.

        Same reason the deployment manifest namespaces them. Without the class
        in the filename, the second record written silently replaces the first.
        """
        assert str(provenance_path(f"{AGENTS_DIRECTORY}/reviewer.md")) == (
            "50_System/agent-provenance_reviewer.md"
        )
        assert str(provenance_path(f"{SKILLS_DIRECTORY}/reviewer")) == (
            "50_System/skill-provenance_reviewer.md"
        )

    def test_the_record_is_not_inside_the_subject(self) -> None:
        assert SKILLS_DIRECTORY not in str(provenance_path(SUBJECT))


class TestRecordingWhatArrived:
    def test_it_hashes_every_file_not_just_the_skill(
        self, vault: Path, service: VendoringService
    ) -> None:
        subject = vendor(vault, SKILL_md="# One\n", scripts__run_py="print(1)\n")
        record = service.record(subject, an_origin())
        assert set(record.integrity.files) == {"SKILL.md", "scripts/run.py"}

    def test_a_change_anywhere_in_the_tree_changes_the_digest(
        self, vault: Path, service: VendoringService
    ) -> None:
        subject = vendor(vault, SKILL_md="# One\n", scripts__run_py="print(1)\n")
        before = service.record(subject, an_origin()).integrity.tree_digest
        (vault / SKILLS_DIRECTORY / "borrowed" / "scripts" / "run.py").write_text("print(2)\n")
        assert service.record(subject, an_origin()).integrity.tree_digest != before

    def test_an_empty_subject_is_refused(self, vault: Path, service: VendoringService) -> None:
        with pytest.raises(ProvenanceError, match="no files"):
            service.record(f"{SKILLS_DIRECTORY}/absent", an_origin())

    def test_upstream_starts_unknown(self, vault: Path, service: VendoringService) -> None:
        """Never claim a check that has not happened."""
        subject = vendor(vault)
        assert service.record(subject, an_origin()).upstream.status is UpstreamStatus.UNKNOWN


class TestARecordSurvivesTheRoundTrip:
    def test_every_origin_field_comes_back(self, vault: Path, service: VendoringService) -> None:
        subject = vendor(vault)
        written = service.record(subject, an_origin())
        service.write(written, concept_id=ConceptId.new(), base=_base())

        read = service.read(subject)
        assert read is not None
        assert read.origin == written.origin
        assert read.integrity.tree_digest == written.integrity.tree_digest
        assert read.integrity.files == written.integrity.files

    def test_a_subject_with_no_record_reads_as_none(
        self, vault: Path, service: VendoringService
    ) -> None:
        vendor(vault)
        assert service.read(SUBJECT) is None

    def test_the_licence_survives(self, vault: Path, service: VendoringService) -> None:
        """A licence obligation that is not recorded cannot be honoured."""
        subject = vendor(vault)
        service.write(
            service.record(subject, an_origin()), concept_id=ConceptId.new(), base=_base()
        )
        read = service.read(subject)
        assert read is not None and read.origin.license == "MIT"


class TestDetectingWhichSideMoved:
    def test_an_untouched_subject_has_not_drifted(
        self, vault: Path, service: VendoringService
    ) -> None:
        subject = vendor(vault)
        service.write(
            service.record(subject, an_origin()), concept_id=ConceptId.new(), base=_base()
        )
        assert service.state_of(subject).drift() is Drift.NONE

    def test_an_edited_subject_reports_local_drift(
        self, vault: Path, service: VendoringService
    ) -> None:
        subject = vendor(vault, SKILL_md="# One\n")
        service.write(
            service.record(subject, an_origin()), concept_id=ConceptId.new(), base=_base()
        )
        (vault / SKILLS_DIRECTORY / "borrowed" / "SKILL.md").write_text("# Edited\n")
        assert service.state_of(subject).drift() is Drift.LOCAL

    def test_it_names_the_file_that_changed(self, vault: Path, service: VendoringService) -> None:
        """The difference between a finding somebody can act on and one they cannot."""
        subject = vendor(vault, SKILL_md="# One\n", scripts__run_py="print(1)\n")
        service.write(
            service.record(subject, an_origin()), concept_id=ConceptId.new(), base=_base()
        )
        (vault / SKILLS_DIRECTORY / "borrowed" / "scripts" / "run.py").write_text("print(2)\n")
        assert service.state_of(subject).changed_files() == ("scripts/run.py",)

    def test_a_deleted_subject_leaves_a_record_pointing_at_nothing(
        self, vault: Path, service: VendoringService
    ) -> None:
        import shutil

        subject = vendor(vault)
        service.write(
            service.record(subject, an_origin()), concept_id=ConceptId.new(), base=_base()
        )
        shutil.rmtree(vault / SKILLS_DIRECTORY / "borrowed")
        assert service.state_of(subject).missing_record


class TestFindingWhatCouldCarryARecord:
    def test_it_finds_a_vendored_skill(self, vault: Path, service: VendoringService) -> None:
        subject = vendor(vault)
        assert subject in service.subjects()

    def test_it_looks_in_the_agents_island_too(
        self, vault: Path, service: VendoringService
    ) -> None:
        agent = vault / AGENTS_DIRECTORY / "reviewer"
        agent.mkdir(parents=True, exist_ok=True)
        (agent / "reviewer.md").write_text("# Reviewer\n")
        assert f"{AGENTS_DIRECTORY}/reviewer" in service.subjects()

    def test_a_loose_file_in_the_root_is_not_a_subject(
        self, vault: Path, service: VendoringService
    ) -> None:
        (vault / SKILLS_DIRECTORY / "stray.md").write_text("not a skill\n")
        assert all(not s.endswith("stray.md") for s in service.subjects())


class TestUnknownFieldsSurvive:
    """core/02 requires it, and a record written by a later version must read."""

    def test_a_record_with_an_extra_field_still_parses(
        self, vault: Path, service: VendoringService
    ) -> None:
        subject = vendor(vault)
        record = service.record(subject, an_origin())
        identity = ConceptId.new()
        frontmatter = service.frontmatter(record, base={"id": str(identity), **_base()})
        frontmatter["something_a_later_version_added"] = {"a": 1}

        from never4ga.domain.document import StoredDocument

        FileSystemMarkdownStore(vault).put(
            StoredDocument(
                concept_id=identity,
                path=provenance_path(subject),
                frontmatter=frontmatter,
                body="",
            )
        )
        read = service.read(subject)
        assert read is not None and read.origin.commit == "b" * 40

    def test_an_unrecognised_origin_kind_does_not_crash(
        self, vault: Path, service: VendoringService
    ) -> None:
        subject = vendor(vault)
        record = service.record(subject, an_origin())
        identity = ConceptId.new()
        frontmatter = service.frontmatter(record, base={"id": str(identity), **_base()})
        frontmatter["origin"]["kind"] = "something-new"

        from never4ga.domain.document import StoredDocument

        FileSystemMarkdownStore(vault).put(
            StoredDocument(
                concept_id=identity,
                path=provenance_path(subject),
                frontmatter=frontmatter,
                body="",
            )
        )
        assert service.read(subject) is not None


def _record(service: VendoringService, subject: str) -> None:
    service.write(service.record(subject, an_origin()), concept_id=ConceptId.new(), base=_base())


def _base() -> dict[str, object]:
    return {
        "schema": "never4ga/0.1",
        "title": "Vendored skill: borrowed",
        "created_at": "2026-08-30T12:00:00Z",
        "authority": "informational",
    }


class TestDoctorReportsWhatTheVaultCannotAccountFor:
    """`doctor`'s three provenance findings, and the one it must not raise.

    None of them is repairable. Where a vendored artifact came from is not
    derivable from its contents, so every one reports and stops -- the same
    reason `doctor` has always reported rather than repaired.
    """

    def _diagnose(self, vault: Path) -> dict[str, str]:
        from never4ga.services.doctor import Doctor

        findings = Doctor(
            FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault)
        ).diagnose()
        return {finding.code: finding.message for finding in findings.findings}

    def test_a_vendored_skill_with_no_record_is_reported(self, vault: Path) -> None:
        vendor(vault)
        assert "skill_provenance_missing" in self._diagnose(vault)

    def test_never4gas_own_skills_are_not_reported(self, vault: Path) -> None:
        """The failure this must not cause.

        `init` seeds eight Skills. They already carry provenance -- the seed
        hash in their frontmatter for the shipped-to-vault hop -- so asking them
        for a vendored record too would report eight findings on a brand new
        vault and mean nothing by any of them.
        """
        assert "skill_provenance_missing" not in self._diagnose(vault)

    def test_a_recorded_skill_is_not_reported(self, vault: Path, service: VendoringService) -> None:
        subject = vendor(vault)
        service.write(
            service.record(subject, an_origin()), concept_id=ConceptId.new(), base=_base()
        )
        assert "skill_provenance_missing" not in self._diagnose(vault)

    def test_an_edited_skill_reports_local_drift_and_names_the_file(
        self, vault: Path, service: VendoringService
    ) -> None:
        subject = vendor(vault, SKILL_md="# One\n", scripts__run_py="print(1)\n")
        service.write(
            service.record(subject, an_origin()), concept_id=ConceptId.new(), base=_base()
        )
        (vault / SKILLS_DIRECTORY / "borrowed" / "scripts" / "run.py").write_text("print(2)\n")

        findings = self._diagnose(vault)
        assert "skill_local_drift" in findings
        assert "scripts/run.py" in findings["skill_local_drift"]

    def test_a_record_outliving_its_subject_is_reported(
        self, vault: Path, service: VendoringService
    ) -> None:
        import shutil

        subject = vendor(vault)
        service.write(
            service.record(subject, an_origin()), concept_id=ConceptId.new(), base=_base()
        )
        shutil.rmtree(vault / SKILLS_DIRECTORY / "borrowed")
        assert "skill_provenance_orphaned" in self._diagnose(vault)

    def test_no_finding_claims_an_upstream_check_that_did_not_happen(
        self, vault: Path, service: VendoringService
    ) -> None:
        """A stored "unavailable" would read as authoritative.

        `doctor` runs on a schedule and must not reach the network, so upstream
        drift is never among its findings.
        """
        subject = vendor(vault)
        service.write(
            service.record(subject, an_origin()), concept_id=ConceptId.new(), base=_base()
        )
        assert "skill_upstream_drift" not in self._diagnose(vault)

    def _strip_tree_digest(self, vault: Path, subject: str) -> None:
        """What a hand-written or agent-assembled record looks like.

        `never4ga skills provenance record` always computes the digest, so the
        gap only appears when frontmatter is authored directly: `files:` present
        and no `tree_digest:`.
        """
        from never4ga.services.vendoring import provenance_path

        path = vault / str(provenance_path(subject))
        path.write_text(path.read_text().replace("  tree_digest:", "  x_tree_digest:"))

    def test_a_record_missing_its_digest_is_not_reported_as_drift(
        self, vault: Path, service: VendoringService
    ) -> None:
        """An absent digest is not a difference.

        It reads as `""`. A drift check that only asked whether the
        digests differed would call such a record permanently LOCAL, against a
        subject nobody had touched.
        """
        subject = vendor(vault, SKILL_md="# One\n")
        service.write(
            service.record(subject, an_origin()), concept_id=ConceptId.new(), base=_base()
        )
        self._strip_tree_digest(vault, subject)

        assert "skill_local_drift" not in self._diagnose(vault)

    def test_a_record_missing_its_digest_says_so(
        self, vault: Path, service: VendoringService
    ) -> None:
        """And the two need opposite responses: re-record versus investigate."""
        subject = vendor(vault, SKILL_md="# One\n")
        service.write(
            service.record(subject, an_origin()), concept_id=ConceptId.new(), base=_base()
        )
        self._strip_tree_digest(vault, subject)

        findings = self._diagnose(vault)
        assert "skill_provenance_incomplete" in findings
        assert subject in findings["skill_provenance_incomplete"]

    def test_the_finding_names_the_verb_that_fixes_it(
        self, vault: Path, service: VendoringService
    ) -> None:
        """A record without a digest gets a repair hint that names the verb.

        `changed_files` finds no per-file mismatch, because there is none, so a
        drift finding could name no file. A warning nobody can act on.
        """
        from never4ga.services.doctor import Doctor

        subject = vendor(vault, SKILL_md="# One\n")
        service.write(
            service.record(subject, an_origin()), concept_id=ConceptId.new(), base=_base()
        )
        self._strip_tree_digest(vault, subject)

        findings = Doctor(
            FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault)
        ).diagnose()
        hint = next(
            f.repair_hint for f in findings.findings if f.code == "skill_provenance_incomplete"
        )
        assert "skills provenance record" in (hint or "")

    def test_a_genuinely_edited_skill_still_reports_drift(
        self, vault: Path, service: VendoringService
    ) -> None:
        """The control. Silencing the false finding must not silence the real one."""
        subject = vendor(vault, SKILL_md="# One\n", scripts__run_py="print(1)\n")
        service.write(
            service.record(subject, an_origin()), concept_id=ConceptId.new(), base=_base()
        )
        (vault / SKILLS_DIRECTORY / "borrowed" / "scripts" / "run.py").write_text("print(2)\n")

        findings = self._diagnose(vault)
        assert "skill_local_drift" in findings
        assert "skill_provenance_incomplete" not in findings


class TestTheVendoringGate:
    """Hazardous text is refused before it is vendored.

    A `doctor` finding arrives after the material is already in the vault and
    already deployed to every client, and text that hides instructions from a
    reviewer is worth stopping at the door.
    """

    def test_a_tag_smuggled_instruction_is_refused(
        self, vault: Path, service: VendoringService
    ) -> None:
        smuggled = "Run the build \U000e0069\U000e0067\U000e006e\U000e006f\U000e0072\U000e0065 now."
        subject = vendor(vault, SKILL_md=f"# Borrowed\n\n{smuggled}\n")
        with pytest.raises(ProvenanceError, match="must not be vendored"):
            service.refuse_if_hazardous(subject)

    def test_the_refusal_says_which_file_and_where(
        self, vault: Path, service: VendoringService
    ) -> None:
        """An invisible character cannot be found by reading the file."""
        subject = vendor(vault, scripts__run_py="x = 'ad​min'\n")
        hazards = service.hazards(subject)
        assert hazards and hazards[0].startswith("scripts/run.py:1:")

    def test_a_personal_path_is_refused(self, vault: Path, service: VendoringService) -> None:
        subject = vendor(vault, SKILL_md="Run `python3 /home/alice/x/run.py`\n")
        with pytest.raises(ProvenanceError, match="must not be vendored"):
            service.refuse_if_hazardous(subject)

    def test_a_placeholder_path_passes(self, vault: Path, service: VendoringService) -> None:
        """The failure this must not cause: a template is not a leak."""
        subject = vendor(vault, SKILL_md="Run `python3 /home/username/x/run.py`\n")
        service.refuse_if_hazardous(subject)

    def test_clean_material_passes(self, vault: Path, service: VendoringService) -> None:
        subject = vendor(vault, SKILL_md="# Borrowed\n\nRun `make`.\n")
        service.refuse_if_hazardous(subject)

    def test_a_long_list_is_truncated_rather_than_dumped(
        self, vault: Path, service: VendoringService
    ) -> None:
        subject = vendor(vault, SKILL_md="​\n" * 30)
        with pytest.raises(ProvenanceError, match="and 20 more"):
            service.refuse_if_hazardous(subject)

    def test_doctor_reports_a_hazard_that_got_in_anyway(self, vault: Path) -> None:
        """The gate is not the only line: material can arrive by hand."""
        from never4ga.services.doctor import Doctor

        vendor(vault, SKILL_md="see /home/alice/notes\n")
        findings = Doctor(
            FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault)
        ).diagnose()
        assert any(f.code == "vendored_text_hazard" for f in findings.findings)


class TestAnAgentIsAFileAndAStillASubject:
    """The two islands hold different shapes.

    A Skill is a directory -- SKILL.md plus optional scripts and references. An
    agent is one Markdown file, which is the client's layout rather than a
    choice here. Both must hash through the same code, or the second class
    would need a second digest and the two records would stop being comparable.
    Nothing here may assume a subject is a directory.
    """

    def _agent(self, vault: Path, name: str = "reviewer", text: str = "# Reviewer\n") -> str:
        directory = vault / AGENTS_DIRECTORY
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.md").write_text(text, encoding="utf-8")
        return f"{AGENTS_DIRECTORY}/{name}.md"

    def test_an_agent_file_is_a_subject(self, vault: Path, service: VendoringService) -> None:
        subject = self._agent(vault)
        assert subject in service.subjects()

    def test_it_hashes_as_a_one_file_tree(self, vault: Path, service: VendoringService) -> None:
        subject = self._agent(vault)
        record = service.record(subject, an_origin())
        assert set(record.integrity.files) == {"reviewer.md"}

    def test_its_record_sits_beside_the_others(
        self, vault: Path, service: VendoringService
    ) -> None:
        """And the `.md` does not survive into the record's own name."""
        subject = self._agent(vault)
        assert str(provenance_path(subject)) == "50_System/agent-provenance_reviewer.md"

    def test_editing_it_is_local_drift(self, vault: Path, service: VendoringService) -> None:
        subject = self._agent(vault)
        _record(service, subject)
        (vault / AGENTS_DIRECTORY / "reviewer.md").write_text("# Mine\n", encoding="utf-8")
        assert service.state_of(subject).drift() is Drift.LOCAL

    def test_the_gate_covers_it_too(self, vault: Path, service: VendoringService) -> None:
        subject = self._agent(vault, text="run /home/alice/x\n")
        with pytest.raises(ProvenanceError, match="must not be vendored"):
            service.refuse_if_hazardous(subject)

    def test_a_non_markdown_file_there_is_not_a_subject(
        self, vault: Path, service: VendoringService
    ) -> None:
        directory = vault / AGENTS_DIRECTORY
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "notes.txt").write_text("scratch\n", encoding="utf-8")
        assert not any(s.endswith("notes.txt") for s in service.subjects())


class TestTheDeepPassReadsTheVaultOnce:
    """A diagnosis walks the vault once, however many subjects it asks about.

    `subject_files` walks the whole vault. Called once per subject per
    question, the cost is O(subjects x vault), and the periodic tier holds the
    write lock for as long as the diagnosis runs.

    The scope is one pass, deliberately. A cache living longer than a
    diagnosis would answer from a vault that had since changed, and the daemon
    holds this service across runs.
    """

    def _walks(self, vault: Path) -> int:
        """How many times a diagnosis walks the vault to answer about subjects."""
        from never4ga.services.doctor import Doctor

        files = FileSystemVaultFileStore(vault)
        walks = 0
        original = files.iter_paths

        def counted() -> Iterator[VaultPath]:
            nonlocal walks
            walks += 1
            return original()

        files.iter_paths = counted  # type: ignore[method-assign]
        Doctor(files, FileSystemMarkdownStore(vault)).diagnose()
        return walks

    def test_the_walk_count_does_not_grow_with_the_number_of_subjects(
        self, vault: Path, service: VendoringService
    ) -> None:
        """The assertion that survives a faster machine, unlike a timing one."""
        for name in ("one", "two", "three", "four", "five"):
            vendor(vault, name=name)
        few = self._walks(vault)

        for name in ("six", "seven", "eight", "nine", "ten"):
            vendor(vault, name=name)
        many = self._walks(vault)

        assert many == few, (
            f"doubling the subjects changed the walks from {few} to {many}; "
            "the vault is being re-read per subject"
        )

    def test_it_still_finds_what_it_found_before(self, vault: Path) -> None:
        """The control. Reading once must not mean seeing less."""
        from never4ga.services.doctor import Doctor

        vendor(vault, name="borrowed")
        findings = Doctor(
            FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault)
        ).diagnose()
        assert "skill_provenance_missing" in {f.code for f in findings.findings}
