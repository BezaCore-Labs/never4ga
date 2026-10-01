"""What `work get` and `work search` report about a work item.

`details/openproject-adapter.md` section 8 lists twenty-three normalized
fields. The adapter produces all of them, putting the ones with no home on
:class:`WorkItem` into ``extra``, and the reporting layer must carry them all.

The description matters most. Without it a consumer sees a ticket's title,
status and assignee and nothing of what the ticket *says*, so a backlog can be
listed but not read.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from never4ga.domain.identity import ExternalId
from never4ga.ports.work_management import WorkItem
from never4ga.services.work_reading import _item

REF = ExternalId(provider="openproject", value="941")

#: Everything `details/openproject-adapter.md` section 8 names. `connection`
#: and `project_ref` are deliberately absent from the item: the response
#: envelope already carries them, and repeating them per item would be a second
#: copy of a fact.
SECTION_8_FIELDS = (
    "provider",
    "ref",
    "display_id",
    "title",
    "description",
    "description_excerpt",
    "type",
    "status",
    "priority",
    "assignee",
    "responsible",
    "created_at",
    "updated_at",
    "start_date",
    "due_date",
    "percent_complete",
    "parent",
    "relations",
    "milestone",
    "sprint",
    "url",
    "provider_version",
    "extensions",
)


def full_item() -> WorkItem:
    return WorkItem(
        ref=REF,
        title="work CLI: three reporting defects found in use",
        status="New",
        assignee="Alex Example",
        priority="Normal",
        milestone="v0.2",
        updated_at=datetime(2026, 8, 30, 7, 7, 8, tzinfo=UTC),
        url="https://openproject.example/work_packages/941",
        relations=(ExternalId(provider="openproject", value="843"),),
        extra={
            "connection": "work_openproject",
            "project_ref": "never4ga",
            "project": "Never4gA",
            "display_id": "941",
            "description": (
                "Three defects found while using the verb.\n\n"
                "At length, over several paragraphs, as a ticket is written."
            ),
            "description_excerpt": "Three defects found while using the verb.",
            "type": "Task",
            "responsible": "Alex Example",
            "category": "Maintenance",
            "sprint": "Backlog",
            "created_at": datetime(2026, 8, 30, 7, 0, tzinfo=UTC),
            "start_date": date(2026, 8, 30),
            "due_date": date(2026, 9, 30),
            "percent_complete": 0,
            "parent": ExternalId(provider="openproject", value="843"),
            "relation_kinds": {"843": "parent"},
            "provider_version": "13.4.1",
            "extensions": {"customField7": "something"},
        },
    )


class TestTheReportedItemMatchesTheSpecification:
    def test_every_normalized_field_is_reported(self) -> None:
        payload = _item(full_item())
        missing = [name for name in SECTION_8_FIELDS if name not in payload]
        assert missing == []

    def test_the_description_reaches_the_caller(self) -> None:
        payload = _item(full_item())
        assert payload["description_excerpt"] == "Three defects found while using the verb."

    def test_dates_and_times_are_reported_as_strings(self) -> None:
        """The payload is rendered as JSON; a `date` is not serialisable."""
        payload = _item(full_item())
        assert payload["created_at"] == "2026-08-30T07:00:00+00:00"
        assert payload["updated_at"] == "2026-08-30T07:07:08+00:00"
        assert payload["start_date"] == "2026-08-30"
        assert payload["due_date"] == "2026-09-30"

    def test_an_external_id_is_reported_as_its_value(self) -> None:
        payload = _item(full_item())
        assert payload["parent"] == "843"
        assert payload["relations"] == ["843"]

    def test_the_whole_payload_is_json_serialisable(self) -> None:
        import json

        json.dumps(_item(full_item()))

    def test_the_item_names_the_project_it_is_in(self) -> None:
        # `project_ref` on the envelope is the project that was asked. An item
        # read by id from another project is in a different one, so each item
        # names its own.
        assert _item(full_item())["project"] == "Never4gA"

    def test_relation_kinds_say_how_each_relation_relates(self) -> None:
        payload = _item(full_item())
        assert payload["relation_kinds"] == {"843": "parent"}


class TestAnItemMissingMostOfIt:
    """`Unavailable fields are null/omitted` -- section 8's last line."""

    def test_a_sparse_item_reports_nulls_rather_than_raising(self) -> None:
        payload = _item(WorkItem(ref=REF, title="Bare"))
        assert payload["title"] == "Bare"
        assert payload["description_excerpt"] is None
        assert payload["parent"] is None
        assert payload["relations"] == []

    def test_a_sparse_item_still_names_every_field(self) -> None:
        payload = _item(WorkItem(ref=REF, title="Bare"))
        missing = [name for name in SECTION_8_FIELDS if name not in payload]
        assert missing == []

    def test_the_envelopes_two_fields_are_not_repeated_per_item(self) -> None:
        payload = _item(full_item())
        assert "connection" not in payload
        assert "project_ref" not in payload


class TestWhatAPersonSees:
    """`work get`'s text output. The `--json` half is the payload above."""

    def summary(self, **overrides: object) -> str:
        from never4ga.cli import _work_item_summary

        payload = _item(full_item())
        payload.update(overrides)
        return _work_item_summary(payload)

    def test_the_description_is_shown(self) -> None:
        assert "Three defects found while using the verb." in self.summary()

    def test_the_ticket_is_named_first(self) -> None:
        assert self.summary().splitlines()[0] == (
            "941  work CLI: three reporting defects found in use"
        )

    def test_fields_that_have_no_value_are_omitted_rather_than_shown_as_none(self) -> None:
        summary = self.summary(assignee=None, due_date=None, parent=None)
        assert "None" not in summary
        assert "assignee" not in summary
        assert "due" not in summary

    def test_a_ticket_with_no_description_still_renders(self) -> None:
        summary = self.summary(description=None, description_excerpt=None)
        assert "941" in summary
        assert summary.rstrip() == summary

    def test_a_long_description_is_wrapped_rather_than_one_long_line(self) -> None:
        summary = self.summary(description=" ".join(["word"] * 60))
        body = [line for line in summary.splitlines() if line.startswith("  word")]
        assert len(body) > 1
        assert all(len(line) <= 80 for line in summary.splitlines())


class TestABlankSearchTerm:
    """A blank search term is dropped before it reaches the tracker.

    OpenProject answers a blank search with `400: Search can't be blank`, which
    would surface as `tracker_unreachable` and send the caller to diagnose a
    healthy connection over their own empty string.
    """

    def test_a_blank_term_is_dropped(self) -> None:
        from never4ga.ports.work_management import WorkItemQuery

        assert WorkItemQuery(terms=("",)).terms == ()

    def test_a_whitespace_only_term_is_dropped(self) -> None:
        from never4ga.ports.work_management import WorkItemQuery

        assert WorkItemQuery(terms=("   ", "\t")).terms == ()

    def test_real_terms_survive_beside_a_blank_one(self) -> None:
        from never4ga.ports.work_management import WorkItemQuery

        assert WorkItemQuery(terms=("", "milestone")).terms == ("milestone",)

    def test_terms_are_stripped(self) -> None:
        from never4ga.ports.work_management import WorkItemQuery

        assert WorkItemQuery(terms=("  milestone  ",)).terms == ("milestone",)

    def test_a_search_for_nothing_is_a_search_with_no_terms(self) -> None:
        """A search with no terms returns the project's items."""
        from never4ga.ports.work_management import WorkItemQuery

        assert WorkItemQuery(terms=("",)) == WorkItemQuery()


class TestTheFullDescription:
    """`work get` fetches one item deliberately; an excerpt defeats the verb.

    Section 8's `description_excerpt` is what a *list* carries: a search of
    fifty items must not pull fifty full descriptions.
    """

    def test_get_carries_the_whole_description(self) -> None:
        payload = _item(full_item())
        assert payload["description"].endswith("as a ticket is written.")

    def test_a_listed_item_carries_the_excerpt_and_not_the_whole_thing(self) -> None:
        payload = _item(full_item(), listed=True)
        assert payload["description_excerpt"] == "Three defects found while using the verb."
        assert payload["description"] is None

    def test_the_summary_prefers_the_full_text(self) -> None:
        from never4ga.cli import _work_item_summary

        assert "as a ticket is written." in _work_item_summary(_item(full_item()))

    def test_the_summary_falls_back_to_the_excerpt(self) -> None:
        from never4ga.cli import _work_item_summary

        summary = _work_item_summary(_item(full_item(), listed=True))
        assert "Three defects found while using the verb." in summary

    def test_paragraphs_survive_the_rendering(self) -> None:
        from never4ga.cli import _work_item_summary

        summary = _work_item_summary(_item(full_item()))
        assert "\n\n" in summary.split("Three defects")[1]


class TestReadingPastTheCache:
    """`work get --refresh` reads past the cache.

    `CachingWorkManagementProvider.get_work_item` takes `refresh`, and every
    surface must be able to reach it. Otherwise a ticket whose description
    changed could only be re-read by deleting a cache row by hand.
    """

    def test_the_port_accepts_the_request(self) -> None:
        """Provider-neutral: a provider with no cache ignores it truthfully."""
        import inspect

        from never4ga.ports.work_management import WorkManagementProvider

        signature = inspect.signature(WorkManagementProvider.get_work_item)
        assert "refresh" in signature.parameters

    def test_every_implementation_accepts_it(self) -> None:
        import inspect

        from never4ga.adapters.fakes.work_management import FakeWorkManagementProvider
        from never4ga.adapters.openproject.provider import OpenProjectProvider
        from never4ga.services.trackers import CachingWorkManagementProvider

        for provider in (
            FakeWorkManagementProvider,
            OpenProjectProvider,
            CachingWorkManagementProvider,
        ):
            signature = inspect.signature(provider.get_work_item)
            assert "refresh" in signature.parameters, provider.__name__
