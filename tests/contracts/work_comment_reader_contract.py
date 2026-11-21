"""WorkCommentReader contract.

Specification:
- core/03 section 25 -- `comment` is a tracker capability, checked rather than
  assumed.
- details/openproject-adapter.md section 12 -- `comments` is one of the
  capabilities an adapter reports.
- details/external-work-management-architecture.md -- comments are the
  tracker's operational state, read from it rather than copied into the vault.

A ticket's decisions often live in its comments rather than its description,
so a reader that returned the fields and not the comments would hand a caller
the question and not the answer.
"""

from __future__ import annotations

import pytest

from never4ga.domain.capabilities import WorkManagementCapability
from never4ga.domain.identity import ExternalId
from never4ga.errors import WorkItemNotFoundError
from never4ga.ports.work_management import WorkComment, WorkCommentReader


class WorkCommentReaderContract:
    @pytest.fixture
    def reader(self) -> WorkCommentReader:
        raise NotImplementedError("supply a WorkCommentReader fixture")

    @pytest.fixture
    def commented(self, reader: WorkCommentReader) -> tuple[ExternalId, str]:
        """An item with at least one comment, and what its newest comment says."""
        raise NotImplementedError("supply an item that carries a comment")

    @pytest.fixture
    def unknown(self, reader: WorkCommentReader) -> ExternalId:
        return ExternalId(provider=reader.provider_id, value="does-not-exist")

    def test_is_a_comment_reader(self, reader: WorkCommentReader) -> None:
        assert isinstance(reader, WorkCommentReader)

    def test_declares_the_capability(self, reader: WorkCommentReader) -> None:
        assert WorkManagementCapability.READ_COMMENTS in reader.capabilities

    def test_reads_the_comments_of_an_item(
        self, reader: WorkCommentReader, commented: tuple[ExternalId, str]
    ) -> None:
        ref, newest = commented
        found = reader.get_work_item_comments(ref)
        assert found
        assert all(isinstance(one, WorkComment) for one in found)
        assert found[-1].body == newest

    def test_comments_are_oldest_first(
        self, reader: WorkCommentReader, commented: tuple[ExternalId, str]
    ) -> None:
        found = reader.get_work_item_comments(commented[0])
        stamps = [one.created_at for one in found if one.created_at is not None]
        assert stamps == sorted(stamps)

    def test_every_comment_has_a_body(
        self, reader: WorkCommentReader, commented: tuple[ExternalId, str]
    ) -> None:
        # An activity that only changed fields is not a comment, and a blank
        # entry would read as somebody having said nothing.
        assert all(one.body.strip() for one in reader.get_work_item_comments(commented[0]))

    def test_a_comment_is_named_by_the_provider(
        self, reader: WorkCommentReader, commented: tuple[ExternalId, str]
    ) -> None:
        # core/06 section 3: a provider's id is never canonical identity. It
        # is what a later write names to amend this comment.
        for one in reader.get_work_item_comments(commented[0]):
            assert isinstance(one.ref, ExternalId)
            assert one.ref.provider == reader.provider_id

    def test_an_unknown_item_is_not_found(
        self, reader: WorkCommentReader, unknown: ExternalId
    ) -> None:
        # Not an empty list: "no comments" and "no such ticket" send a caller
        # to different places.
        with pytest.raises(WorkItemNotFoundError):
            reader.get_work_item_comments(unknown)
