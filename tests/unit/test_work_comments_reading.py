"""`work get` carries the item's comments.

A ticket's decisions often live in its comments rather than its description,
so a read that returned the fields alone would answer the question it was not
asked. The comments travel beside the item, not inside it: the normalised work
item is `details/openproject-adapter.md` section 8's, and a comment is its own
record with its own id.

Three surfaces render one service result (`core/05` section 13), so the service
is where this is tested, and the CLI's text rendering beside it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from never4ga.adapters.fakes import (
    FakeWorkManagementProvider,
    FakeWorkManagementWriter,
    InMemoryTrackerCache,
)
from never4ga.domain.identity import ExternalId
from never4ga.errors import CapabilityNotSupportedError, ProviderUnavailableError
from never4ga.ports.work_management import ProposedMutation, WorkComment, WorkItem
from never4ga.services.trackers import CachingWorkManagementProvider
from never4ga.services.work_reading import BoundReader
from tests.openproject_fixtures import TrackerReader

ITEM = WorkItem(ref=ExternalId(provider="fake-pm", value="431"), title="Pick a parser")


def bound(provider: object) -> BoundReader:
    template = TrackerReader().bind()
    return BoundReader(
        workspace=template.workspace,
        connection=template.connection,
        provider=provider,  # type: ignore[arg-type]
        project="never4ga",
    )


def get(provider: object, ref: str = "431") -> dict[str, Any]:
    reader = TrackerReader()
    result = reader.get(bound(provider), ref)
    assert isinstance(result, dict), result
    return result


class TestTheComments:
    def test_they_come_back_with_the_item(self) -> None:
        reader = TrackerReader()
        result = reader.get(reader.bind(), "838")
        assert isinstance(result, dict)
        (comment,) = result["comments"]
        assert comment["ref"] == "1829"
        assert comment["author"] == "Ada Example"
        assert comment["created_at"] == "2026-08-26T12:55:38.146000+00:00"
        assert comment["body"].startswith("Closed on completion.")

    def test_they_are_oldest_first(self) -> None:
        writer = FakeWorkManagementWriter([ITEM])
        for body in ("first", "second", "third"):
            writer.apply(ProposedMutation.comment(ITEM.ref, body))
        assert [one["body"] for one in get(writer)["comments"]] == ["first", "second", "third"]

    def test_an_item_with_none_has_an_empty_list(self) -> None:
        result = get(FakeWorkManagementWriter([ITEM]))
        assert result["comments"] == []
        assert "comments_unavailable" not in result

    def test_the_result_is_json(self) -> None:
        reader = TrackerReader()
        json.dumps(reader.get(reader.bind(), "838"))

    def test_they_are_not_part_of_the_item(self) -> None:
        reader = TrackerReader()
        result = reader.get(reader.bind(), "838")
        assert isinstance(result, dict)
        assert "comments" not in result["item"]


class TestWhenTheyCannotBeRead:
    """`null` and a reason, never `[]`: "nobody commented" is a claim."""

    def test_a_tracker_that_does_not_report_comments_says_so(self) -> None:
        result = get(FakeWorkManagementProvider([ITEM]))
        assert result["comments"] is None
        assert "does not report comments" in str(result["comments_unavailable"])

    def test_a_tracker_that_fails_mid_read_keeps_the_item(self) -> None:
        class Failing(FakeWorkManagementWriter):
            def get_work_item_comments(self, ref: ExternalId) -> Sequence[WorkComment]:
                raise ProviderUnavailableError("fake-pm stopped answering")

        result = get(Failing([ITEM]))
        assert result["item"]["title"] == "Pick a parser"
        assert result["comments"] is None
        assert "stopped answering" in str(result["comments_unavailable"])


class TestTheCache:
    def test_comments_are_never_served_from_it(self) -> None:
        # A cached ticket may be fifteen minutes old; a decision recorded in
        # the last minute must still be read.
        writer = FakeWorkManagementWriter([ITEM])
        cached = CachingWorkManagementProvider(
            writer, cache=InMemoryTrackerCache(), connection="fake"
        )
        assert cached.get_work_item_comments(ITEM.ref) == ()
        writer.apply(ProposedMutation.comment(ITEM.ref, "Decided."))
        assert [one.body for one in cached.get_work_item_comments(ITEM.ref)] == ["Decided."]

    def test_a_wrapped_reader_that_cannot_refuses(self) -> None:
        cached = CachingWorkManagementProvider(
            FakeWorkManagementProvider([ITEM]), cache=InMemoryTrackerCache(), connection="fake"
        )
        with pytest.raises(CapabilityNotSupportedError):
            cached.get_work_item_comments(ITEM.ref)


class TestWhatAPersonSees:
    def summary(self, comments: object, unavailable: str | None = None) -> str:
        from never4ga.cli import _work_get_summary

        result: dict[str, object] = {
            "item": {"ref": "431", "title": "Pick a parser", "description": "Which one."},
            "comments": comments,
        }
        if unavailable is not None:
            result["comments_unavailable"] = unavailable
        return _work_get_summary(result)

    def test_each_comment_shows_who_and_when(self) -> None:
        when = datetime(2026, 8, 26, 12, 55, tzinfo=UTC).isoformat()
        text = self.summary(
            [
                {
                    "ref": "a1",
                    "author": "Ada Example",
                    "created_at": when,
                    "body": "Decided: one pass.",
                }
            ]
        )
        assert "comments (1)" in text
        assert "Ada Example, 2026-08-26 12:55" in text
        assert "Decided: one pass." in text

    def test_the_item_still_comes_first(self) -> None:
        text = self.summary([])
        assert text.splitlines()[0] == "431  Pick a parser"

    def test_no_comments_says_nothing_about_them(self) -> None:
        assert "comments" not in self.summary([])

    def test_a_comment_without_an_author_or_time_still_renders(self) -> None:
        text = self.summary([{"ref": "a1", "author": None, "created_at": None, "body": "Hm."}])
        assert "None" not in text
        assert "Hm." in text

    def test_unreadable_comments_are_reported(self) -> None:
        text = self.summary(None, "fake-pm stopped answering")
        assert "comments not read: fake-pm stopped answering" in text
