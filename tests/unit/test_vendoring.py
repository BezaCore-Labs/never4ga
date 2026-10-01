"""The record that says where vendored material came from (core/02 section 21.21)."""

from __future__ import annotations

import pytest

from never4ga.domain.deployments import tree_hash
from never4ga.domain.vendoring import (
    DIGEST_ALGORITHM,
    Drift,
    Integrity,
    Origin,
    OriginKind,
    SkillProvenance,
    Upstream,
    UpstreamStatus,
    file_digests,
    integrity_of,
)

FILES = {"SKILL.md": "# A skill\n", "scripts/run.py": "print('hi')\n"}


def a_record(**overrides: object) -> SkillProvenance:
    base = {
        "skill_path": "50_System/Skills/example",
        "origin": Origin(
            kind=OriginKind.GIT,
            fetched_at="2026-08-30T23:00:00Z",
            url="https://example.invalid/repo.git",
            commit="a" * 40,
        ),
        "integrity": integrity_of(FILES),
    }
    return SkillProvenance(**(base | overrides))  # type: ignore[arg-type]


class TestOriginRefusesAnIncompleteGitRecord:
    """A git origin without a commit is the shape that detects nothing.

    The audited prior art recorded origin as one free-text string with no
    revision, and could not notice its own upstream moving. Refusing the
    incomplete record is what keeps that from being expressible here.
    """

    def test_a_git_origin_requires_url_and_commit(self) -> None:
        with pytest.raises(ValueError, match="url and commit"):
            Origin(kind=OriginKind.GIT, fetched_at="2026-08-30T23:00:00Z", url="x")

    def test_a_local_origin_needs_neither(self) -> None:
        origin = Origin(kind=OriginKind.LOCAL, fetched_at="2026-08-30T23:00:00Z")
        assert origin.commit is None

    def test_authored_is_distinct_from_no_record_at_all(self) -> None:
        """Two different facts that otherwise share the same silence."""
        assert OriginKind.AUTHORED in set(OriginKind)


class TestTheDigestIsTheOneAlreadyInUse:
    """One digest construction serves first-party and vendored material alike.

    A second digest differing only in its separator would make two records
    incomparable for no gain.
    """

    def test_the_tree_digest_is_tree_hash(self) -> None:
        assert integrity_of(FILES).tree_digest == tree_hash(FILES)

    def test_the_algorithm_is_named_in_the_record(self) -> None:
        assert integrity_of(FILES).algorithm == DIGEST_ALGORITHM

    def test_order_does_not_change_it(self) -> None:
        assert integrity_of(dict(reversed(list(FILES.items())))).tree_digest == tree_hash(FILES)

    def test_a_changed_file_changes_it(self) -> None:
        moved = FILES | {"SKILL.md": "# A different skill\n"}
        assert integrity_of(moved).tree_digest != integrity_of(FILES).tree_digest


class TestPerFileDigestsNameWhatChanged:
    """Without them a mismatch says only that something moved."""

    def test_there_is_one_per_file(self) -> None:
        assert set(file_digests(FILES)) == set(FILES)

    def test_only_the_changed_file_differs(self) -> None:
        before = file_digests(FILES)
        after = file_digests(FILES | {"scripts/run.py": "print('bye')\n"})
        assert before["SKILL.md"] == after["SKILL.md"]
        assert before["scripts/run.py"] != after["scripts/run.py"]

    def test_they_can_be_omitted(self) -> None:
        assert integrity_of(FILES, per_file=False).files == {}


class TestDriftSeparatesTheFourStates:
    """The distinction the prior art could not express.

    Recording origin and integrity separately is what makes four answers
    possible instead of two, and only one of the four needs a person.
    """

    def test_nothing_moved(self) -> None:
        assert a_record().drift() is Drift.NONE

    def test_only_the_local_copy_moved(self) -> None:
        assert a_record(modified=True).drift() is Drift.LOCAL

    def test_only_upstream_moved(self) -> None:
        record = a_record(upstream=Upstream(status=UpstreamStatus.AHEAD))
        assert record.drift() is Drift.UPSTREAM

    def test_both_moved_and_that_is_the_one_needing_a_person(self) -> None:
        record = a_record(modified=True, upstream=Upstream(status=UpstreamStatus.AHEAD))
        assert record.drift() is Drift.BOTH

    def test_an_unchecked_upstream_is_not_drift(self) -> None:
        """Offline is a normal state, not a finding.

        A stored "unavailable" wearing a `detected_at` looks authoritative and
        is not.
        """
        record = a_record(upstream=Upstream(status=UpstreamStatus.UNKNOWN))
        assert record.drift() is Drift.NONE


class TestTheRecordDefaultsHonestly:
    def test_upstream_starts_unknown_rather_than_current(self) -> None:
        """Never claim a check that has not happened."""
        assert a_record().upstream.status is UpstreamStatus.UNKNOWN

    def test_integrity_may_carry_no_per_file_map(self) -> None:
        record = a_record(integrity=Integrity(tree_digest="abc"))
        assert record.integrity.files == {}
