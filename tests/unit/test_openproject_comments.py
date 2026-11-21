"""Reading a work package's comments from OpenProject.

A comment is an activity carrying text: `GET /work_packages/{id}/activities`
lists every journal entry, and most of them only record a field changing. The
author arrives as a link to a user with no title, so the name is a second read,
made once per author.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
import pytest

from never4ga.adapters.openproject import OpenProjectProvider, OpenProjectWriter
from never4ga.domain.capabilities import WorkManagementCapability
from never4ga.domain.identity import ExternalId
from never4ga.errors import ProviderUnavailableError
from never4ga.ports.work_management import ProposedMutation
from tests.openproject_fixtures import (
    BASE_URL,
    PROJECT_REF,
    fixture_transport,
    offline_transport,
    writable_transport,
)

KNOWN = ExternalId(provider="openproject", value="838")


def build(transport: httpx.BaseTransport) -> OpenProjectProvider:
    return OpenProjectProvider(
        base_url=BASE_URL,
        token="not-a-real-token",
        connection="work_openproject",
        project_ref=PROJECT_REF,
        transport=transport,
    )


class Recording(httpx.BaseTransport):
    """A transport that remembers the paths it was asked for."""

    def __init__(self, inner: httpx.BaseTransport, *, refuse: str | None = None) -> None:
        self._inner = inner
        self._refuse = refuse
        self.paths: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        self.paths.append(path)
        if self._refuse is not None and path.startswith(self._refuse):
            return httpx.Response(403, json={"message": "You are not authorized."})
        return self._inner.handle_request(request)


class TestTheCapturedActivities:
    def test_only_an_activity_with_text_is_a_comment(self) -> None:
        # Three activities: a creation and a type change that carry an empty
        # `comment.raw`, and one that says something.
        found = build(fixture_transport()).get_work_item_comments(KNOWN)
        assert [one.ref.value for one in found] == ["1829"]

    def test_the_comment_is_its_raw_markdown(self) -> None:
        (comment,) = build(fixture_transport()).get_work_item_comments(KNOWN)
        assert comment.body.startswith("Closed on completion.")
        assert "<p" not in comment.body

    def test_the_author_is_named(self) -> None:
        (comment,) = build(fixture_transport()).get_work_item_comments(KNOWN)
        assert comment.author == "Ada Example"

    def test_the_time_is_parsed(self) -> None:
        (comment,) = build(fixture_transport()).get_work_item_comments(KNOWN)
        assert comment.created_at == datetime(2026, 8, 26, 12, 55, 38, 146000, tzinfo=UTC)

    def test_the_capability_is_discovered_from_the_work_package_links(self) -> None:
        provider = build(fixture_transport())
        assert WorkManagementCapability.READ_COMMENTS in provider.capabilities


class TestTheAuthorLookup:
    def test_each_author_is_looked_up_once(self) -> None:
        recording = Recording(fixture_transport())
        provider = build(recording)
        provider.get_work_item_comments(KNOWN)
        provider.get_work_item_comments(KNOWN)
        assert recording.paths.count("/api/v3/users/5") == 1

    def test_a_refused_lookup_costs_the_name_and_nothing_else(self) -> None:
        # A token may read a ticket and not the user directory. The comment is
        # what was asked for; the name is a label on it.
        recording = Recording(fixture_transport(), refuse="/api/v3/users/")
        (comment,) = build(recording).get_work_item_comments(KNOWN)
        assert comment.author is None
        assert comment.body.startswith("Closed on completion.")


class TestFailure:
    def test_an_unreachable_tracker_raises_rather_than_answering_empty(self) -> None:
        with pytest.raises(ProviderUnavailableError):
            build(offline_transport()).get_work_item_comments(KNOWN)

    def test_another_trackers_id_is_not_asked_about(self) -> None:
        from never4ga.errors import WorkItemNotFoundError

        recording = Recording(fixture_transport())
        with pytest.raises(WorkItemNotFoundError):
            build(recording).get_work_item_comments(ExternalId(provider="jira", value="838"))
        assert not any(path.endswith("/activities") for path in recording.paths)


class TestWhatWasWrittenCanBeRead:
    def test_a_posted_comment_is_read_back_last(self) -> None:
        writer = OpenProjectWriter(
            base_url=BASE_URL,
            token="not-a-real-token",
            connection="work_openproject",
            project_ref=PROJECT_REF,
            transport=writable_transport(),
        )
        made = writer.apply(ProposedMutation.comment(KNOWN, "Decided: keep it on one page."))
        found = writer.get_work_item_comments(KNOWN)
        assert found[-1].body == "Decided: keep it on one page."
        assert found[-1].ref == made.activity
