"""Normalising an OpenProject work package into a WorkItem.

Specification:
- details/openproject-adapter.md section 8 -- the normalized field set.
- core/03 section 17 -- identity is connection + project_ref + external_id.
- core/06 section 3 -- a provider id is never Never4gA canonical identity.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

import pytest

from never4ga.adapters.openproject.normalise import work_item
from never4ga.domain.identity import ConceptId, ExternalId
from never4ga.ports.work_management import WorkItem
from tests.openproject_fixtures import BASE_URL, load_fixture

CONNECTION = "work_openproject"
PROJECT = "never4ga"


def normalise(
    name: str = "work-package.json",
    *,
    payload: Mapping[str, Any] | None = None,
    provider_version: str | None = None,
    relations: Sequence[Mapping[str, Any]] = (),
) -> WorkItem:
    return work_item(
        payload=load_fixture(name) if payload is None else payload,
        connection=CONNECTION,
        project_ref=PROJECT,
        base_url=BASE_URL,
        provider_version=provider_version,
        relations=relations,
    )


def relation_elements() -> Sequence[Mapping[str, Any]]:
    payload: Any = load_fixture("relations_populated.json")
    elements: Sequence[Mapping[str, Any]] = payload["_embedded"]["elements"]
    return elements


class TestIdentity:
    def test_the_reference_is_external_never_canonical(self) -> None:
        assert isinstance(normalise().ref, ExternalId)
        assert not issubclass(ExternalId, ConceptId)

    def test_the_reference_is_namespaced_by_the_provider(self) -> None:
        item = normalise()
        assert item.ref.provider == "openproject"
        assert item.ref.value == "838"

    def test_the_connection_and_project_are_carried_with_the_item(self) -> None:
        # core/03 section 17: an external id alone does not identify anything.
        item = normalise()
        assert item.extra["connection"] == CONNECTION
        assert item.extra["project_ref"] == PROJECT

    def test_the_item_names_the_project_it_is_in(self) -> None:
        # `project_ref` is the project that was asked; an item fetched by id,
        # or listed from a parent, can be in another. Only the item says.
        assert normalise().extra["project"] == "Never4gA"


class TestFields:
    def test_the_title_is_the_subject(self) -> None:
        assert normalise().title.startswith("Milestone 6")

    def test_status_type_and_priority_come_from_link_titles(self) -> None:
        item = normalise()
        assert item.status == "New"
        assert item.extra["type"] == "Epic"
        assert item.priority == "Normal"

    def test_instants_are_timezone_aware(self) -> None:
        item = normalise()
        assert item.updated_at is not None
        assert item.updated_at.tzinfo is not None
        assert item.extra["created_at"].tzinfo is UTC

    def test_the_url_is_the_one_a_person_can_open(self) -> None:
        # section 5, "build UI URL": the API href is not a page.
        assert normalise().url == f"{BASE_URL}/work_packages/838"

    def test_an_unset_field_is_omitted_rather_than_guessed(self) -> None:
        # section 8: "unavailable fields are null/omitted". The captured item
        # has no assignee, no version and no parent.
        item = normalise()
        assert item.assignee is None
        assert item.milestone is None
        assert "parent" not in item.extra
        assert "due_date" not in item.extra

    def test_a_populated_item_carries_the_whole_section_8_field_set(self) -> None:
        item = normalise("work-package_populated.json")
        assert item.assignee == "Ada Example"
        assert item.milestone == "v0.1"
        assert item.extra["responsible"] == "Ada Example"
        assert item.extra["start_date"] == date(2026, 8, 25)
        assert item.extra["due_date"] == date(2026, 9, 15)
        assert item.extra["percent_complete"] == 40
        assert item.extra["category"] == "Documentation"
        assert item.extra["display_id"] == "838"

    def test_the_parent_is_an_external_reference(self) -> None:
        item = normalise("work-package_populated.json")
        assert item.extra["parent"] == ExternalId(provider="openproject", value="117")

    def test_the_description_is_excerpted_not_carried_whole(self) -> None:
        excerpt = normalise().extra["description_excerpt"]
        assert len(excerpt) <= 240
        assert "\n" not in excerpt

    def test_the_lock_version_is_kept_for_the_write_path_to_come(self) -> None:
        # section 9: the cache entry records it, and a write (section 6) needs
        # the version from a fresh read.
        assert normalise().extra["lock_version"] == 1

    def test_provider_version_is_recorded_when_known(self) -> None:
        assert normalise(provider_version="17.6.0").extra["provider_version"] == "17.6.0"

    def test_custom_fields_survive_as_extension_data(self) -> None:
        # section 7: provider-specific custom fields remain adapter extension
        # data rather than being flattened into the normalized field set.
        payload: Any = load_fixture("work-package.json")
        payload["customField7"] = {"raw": "escalated"}
        assert normalise(payload=payload).extra["extensions"] == {
            "customField7": {"raw": "escalated"}
        }

    def test_extra_is_read_only(self) -> None:
        item = normalise()
        with pytest.raises(TypeError):
            item.extra["type"] = "Bug"  # type: ignore[index]


class TestRelations:
    def test_relations_are_external_references(self) -> None:
        item = normalise(relations=relation_elements())
        assert item.relations == (
            ExternalId(provider="openproject", value="840"),
            ExternalId(provider="openproject", value="841"),
        )

    def test_a_relation_names_the_other_end_not_this_one(self) -> None:
        item = normalise(relations=relation_elements())
        assert ExternalId(provider="openproject", value="838") not in item.relations

    def test_relation_kinds_are_kept_as_extension_data(self) -> None:
        item = normalise(relations=relation_elements())
        assert item.extra["relation_kinds"] == {"840": "relates", "841": "blocks"}


class TestARelationIsNamedFromThisItemsEnd:
    """A directional relation says which way it points.

    OpenProject stores one relation per link and names it from its `from` end.
    Asked for "1191 precedes 1192", it stores `follows` from 1192 to 1191. The
    item at the `from` end reads `type`; the item at the `to` end reads the
    relation's own `reverseType`.
    """

    @staticmethod
    def measured() -> list[dict[str, Any]]:
        """Three `follows` relations in the shape OpenProject stores them."""

        def relation(number: int, source: str, target: str) -> dict[str, Any]:
            return {
                "_type": "Relation",
                "id": number,
                "name": "follows",
                "type": "follows",
                "reverseType": "precedes",
                "_links": {
                    "from": {"href": f"/api/v3/work_packages/{source}"},
                    "to": {"href": f"/api/v3/work_packages/{target}"},
                },
            }

        return [
            relation(17, "1193", "1192"),
            relation(16, "1192", "1191"),
            relation(15, "1192", "1189"),
        ]

    def _item(self, identifier: str, relations: Sequence[Mapping[str, Any]]) -> WorkItem:
        payload: Any = dict(load_fixture("work-package.json"))
        payload["id"] = int(identifier)
        payload["_links"] = {
            name: link for name, link in payload["_links"].items() if name != "parent"
        }
        return normalise(payload=payload, relations=relations)

    def test_the_item_at_the_to_end_reads_the_reverse_type(self) -> None:
        item = self._item("1192", self.measured())
        assert item.extra["relation_kinds"] == {
            "1193": "precedes",
            "1191": "follows",
            "1189": "follows",
        }

    def test_a_blocking_relation_reads_as_blocked_from_the_far_end(self) -> None:
        # The populated fixture's second relation is 838 blocks 841; read from
        # 841, it is blocked. (Its first, 838 relates 840, does not involve 841.)
        item = self._item("841", [relation_elements()[1]])
        assert item.extra["relation_kinds"] == {"838": "blocked"}

    def test_the_from_end_is_unchanged(self) -> None:
        item = normalise(relations=relation_elements())
        assert item.extra["relation_kinds"] == {"840": "relates", "841": "blocks"}

    def test_a_relation_without_a_reverse_type_falls_back_to_its_type(self) -> None:
        elements = self.measured()
        del elements[0]["reverseType"]
        item = self._item("1192", elements)
        assert item.extra["relation_kinds"]["1193"] == "follows"


class TestDates:
    def test_an_instant_with_an_offset_is_preserved(self) -> None:
        payload: Any = load_fixture("work-package.json")
        payload["updatedAt"] = "2026-08-25T15:30:00+02:00"
        assert normalise(payload=payload).updated_at == datetime.fromisoformat(
            "2026-08-25T15:30:00+02:00"
        )
