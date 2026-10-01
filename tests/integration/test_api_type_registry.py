"""GET /v1/schema/types: the type registry, published.

Templates are body-only, so nothing else tells a client which optional fields
a type has (details/api-cli-mcp-contract.md section 3). A surface that knows
the types, their optional fields and their lifecycle vocabularies can offer all
three instead of asking a person to remember them. That also prevents errors
such as `lifecycle: active` on a type whose legal values are `not_started`,
`in_progress`, `submitted`, `graded`.

The registry is a property of the schema version, not of a vault: the same
answer for every vault this build serves.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def types_by_name(client: TestClient) -> dict[str, dict[str, Any]]:
    response = client.get("/v1/schema/types")
    assert response.status_code == 200, response.text
    return {entry["name"]: entry for entry in response.json()["types"]}


class TestTheRegistryIsPublished:
    def test_every_registered_type_is_listed(self, client: TestClient) -> None:
        from never4ga.schema import TYPE_REGISTRY

        assert set(types_by_name(client)) == set(TYPE_REGISTRY)

    def test_it_answers_without_the_vault_being_indexed(self, client: TestClient) -> None:
        # The registry is a fact about the schema, not about this vault.
        assert client.get("/v1/schema/types").status_code == 200

    def test_it_requires_a_credential(self, anonymous: TestClient) -> None:
        assert anonymous.get("/v1/schema/types").status_code == 401


class TestWhatEachTypeSaysAboutItself:
    def test_a_types_lifecycle_vocabulary_is_published(self, client: TestClient) -> None:
        """The legal values, so a client can offer them instead of free text."""
        entry = types_by_name(client)["course_assignment"]
        assert entry["lifecycle_values"] == [
            "not_started",
            "in_progress",
            "submitted",
            "graded",
        ]

    def test_the_optional_fields_are_published_with_their_kinds(self, client: TestClient) -> None:
        entry = types_by_name(client)["course_assignment"]
        optional = {field["name"]: field["kind"] for field in entry["optional_fields"]}
        assert optional["unit"] == "integer"
        assert optional["due"] == "date"
        # Undeclared is published as such rather than guessed at.
        assert optional["grade"] is None

    def test_required_fields_are_published(self, client: TestClient) -> None:
        entry = types_by_name(client)["course_assignment"]
        assert "workspace" in entry["required_fields"]
        assert "lifecycle" in entry["required_fields"]

    def test_the_base_required_fields_are_published_once_for_everyone(
        self, client: TestClient
    ) -> None:
        # A client building a form needs these, and repeating them on all
        # twenty-four types would be publishing one fact twenty-four times.
        body = client.get("/v1/schema/types").json()
        assert body["required_base_fields"] == ["type", "id", "schema", "title", "created_at"]
        assert body["schema_version"] == "never4ga/0.1"

    def test_where_a_type_lives_is_published_in_words(self, client: TestClient) -> None:
        entry = types_by_name(client)["course_assignment"]
        assert any("Units" in location for location in entry["locations"])

    def test_whether_placement_binds_is_published(self, client: TestClient) -> None:
        by_name = types_by_name(client)
        assert by_name["course_assignment"]["location_is_binding"] is True
        # `resource` is core/02's "Typical location": advisory, and a client
        # offering it should not warn as though it were a rule.
        assert by_name["resource"]["location_is_binding"] is False

    def test_a_type_another_verb_owns_says_so(self, client: TestClient) -> None:
        """`concept create` refuses these, so a dropdown must not offer them."""
        by_name = types_by_name(client)
        assert by_name["workspace"]["creatable"] is False
        assert by_name["system_manifest"]["creatable"] is False
        assert by_name["course_assignment"]["creatable"] is True

    def test_the_recommended_authority_rides_along(self, client: TestClient) -> None:
        assert types_by_name(client)["standard"]["recommended_authority"] == "authoritative"
