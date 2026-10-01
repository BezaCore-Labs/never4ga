"""POST /v1/concepts and /v1/concepts/adopt.

Creating and adopting concepts over HTTP (details/api-cli-mcp-contract.md
section 3). Both endpoints wrap the same `ContentService` verbs the CLI calls;
neither carries logic of its own, per the three-thin-clients rule.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def hand_written(vault: Path, relative: str, text: str = "# By Hand\n\nprose\n") -> None:
    absolute = vault / relative
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text(text, encoding="utf-8")


class TestCreatingAConcept:
    def test_a_knowledge_note_is_created(self, client: TestClient, vault: Path) -> None:
        response = client.post(
            "/v1/concepts", json={"type": "knowledge", "title": "Loopback Authoring"}
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["path"] == "30_Knowledge/Notes/loopback-authoring.md"
        assert (vault / payload["path"]).is_file()

    def test_the_actor_is_recorded(self, client: TestClient, vault: Path) -> None:
        response = client.post(
            "/v1/concepts",
            json={
                "type": "knowledge",
                "title": "Loopback Authoring",
                "actor": "obsidian-companion/0.1",
            },
        )
        assert response.status_code == 201
        text = (vault / response.json()["path"]).read_text(encoding="utf-8")
        assert "obsidian-companion/0.1" in text

    def test_fields_and_description_ride_through(self, client: TestClient, vault: Path) -> None:
        response = client.post(
            "/v1/concepts",
            json={
                "type": "knowledge",
                "title": "Loopback Authoring",
                "description": "Written over HTTP.",
                "fields": {"tags": ["api"]},
            },
        )
        assert response.status_code == 201
        text = (vault / response.json()["path"]).read_text(encoding="utf-8")
        assert "Written over HTTP." in text
        assert "api" in text

    def test_a_refusal_is_a_422_naming_the_reason(self, client: TestClient) -> None:
        # A decision needs a workspace, and the service refuses to guess one.
        response = client.post("/v1/concepts", json={"type": "decision", "title": "Orphaned"})
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "concept_not_created"
        assert "workspace" in error["message"]

    def test_nothing_is_written_on_refusal(self, client: TestClient, vault: Path) -> None:
        before = sorted(path for path in vault.rglob("*.md"))
        response = client.post("/v1/concepts", json={"type": "decision", "title": "Orphaned"})
        assert response.status_code == 422
        assert sorted(path for path in vault.rglob("*.md")) == before

    def test_a_field_repeating_the_title_is_a_422_not_a_500(
        self, client: TestClient, vault: Path
    ) -> None:
        before = sorted(path for path in vault.rglob("*.md"))
        response = client.post(
            "/v1/concepts",
            json={"type": "knowledge", "title": "Loopback Authoring", "fields": {"title": "Other"}},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "concept_not_created"
        assert sorted(path for path in vault.rglob("*.md")) == before

    def test_a_field_naming_the_type_is_a_422(self, client: TestClient, vault: Path) -> None:
        # Otherwise the field would overwrite the type and write `type: garbage`.
        before = sorted(path for path in vault.rglob("*.md"))
        response = client.post(
            "/v1/concepts",
            json={"type": "knowledge", "title": "Owned", "fields": {"type": "garbage"}},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "concept_not_created"
        assert sorted(path for path in vault.rglob("*.md")) == before


class TestAdoptingOverHttp:
    def test_a_hand_written_note_is_adopted_in_place(self, client: TestClient, vault: Path) -> None:
        hand_written(vault, "30_Knowledge/Notes/by-hand.md")
        response = client.post("/v1/concepts/adopt", json={"path": "30_Knowledge/Notes/by-hand.md"})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["path"] == "30_Knowledge/Notes/by-hand.md"
        assert "adopted where it sits" in payload["placement"]
        text = (vault / "30_Knowledge/Notes/by-hand.md").read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert text.endswith("# By Hand\n\nprose\n")

    def test_a_title_field_names_the_adopted_concept(self, client: TestClient, vault: Path) -> None:
        hand_written(vault, "30_Knowledge/Notes/by-hand.md")
        response = client.post(
            "/v1/concepts/adopt",
            json={"path": "30_Knowledge/Notes/by-hand.md", "fields": {"title": "Named Over HTTP"}},
        )
        assert response.status_code == 200, response.text
        text = (vault / "30_Knowledge/Notes/by-hand.md").read_text(encoding="utf-8")
        assert "title: Named Over HTTP" in text
        assert text.endswith("# By Hand\n\nprose\n")

    def test_type_and_fields_ride_through(self, client: TestClient, vault: Path) -> None:
        hand_written(vault, "10_Workspaces/Never4gA/Strategy/positioning.md")
        response = client.post(
            "/v1/concepts/adopt",
            json={
                "path": "10_Workspaces/Never4gA/Strategy/positioning.md",
                "type": "standard",
            },
        )
        assert response.status_code == 200, response.text

    def test_a_refusal_is_a_422_naming_the_reason(self, client: TestClient, vault: Path) -> None:
        hand_written(vault, "10_Workspaces/Never4gA/Strategy/positioning.md")
        response = client.post(
            "/v1/concepts/adopt",
            json={"path": "10_Workspaces/Never4gA/Strategy/positioning.md"},
        )
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "concept_not_adopted"
        assert "resource" in error["message"]

    def test_the_candidates_arrive_as_data_a_client_can_offer(
        self, client: TestClient, vault: Path
    ) -> None:
        """A browser client has no flags, so it must not have to parse the
        sentence to build its own ask."""
        hand_written(vault, "10_Workspaces/Never4gA/Strategy/positioning.md")
        response = client.post(
            "/v1/concepts/adopt",
            json={"path": "10_Workspaces/Never4gA/Strategy/positioning.md"},
        )
        error = response.json()["error"]
        assert error["details"]["candidates"] == ["resource", "standard"]
        assert "--type" not in error["message"]
        assert "--type" not in (error.get("repair_hint") or "")

    def test_a_missing_file_is_a_422(self, client: TestClient) -> None:
        response = client.post("/v1/concepts/adopt", json={"path": "30_Knowledge/Notes/absent.md"})
        assert response.status_code == 422

    def test_a_malformed_path_is_a_400(self, client: TestClient) -> None:
        response = client.post("/v1/concepts/adopt", json={"path": "../outside.md"})
        assert response.status_code == 400


class TestAdoptionRefusesOwnedFields:
    def test_a_malformed_id_field_is_a_422_not_an_identity_error(
        self, client: TestClient, vault: Path
    ) -> None:
        hand_written(vault, "30_Knowledge/Notes/by-hand.md")
        before = (vault / "30_Knowledge/Notes/by-hand.md").read_text(encoding="utf-8")
        response = client.post(
            "/v1/concepts/adopt",
            json={"path": "30_Knowledge/Notes/by-hand.md", "fields": {"id": "garbage"}},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "concept_not_adopted"
        assert (vault / "30_Knowledge/Notes/by-hand.md").read_text(encoding="utf-8") == before


class TestNumberingADecision:
    """Decision numbering over HTTP: the allocation `concept create` does.

    The rule is core/02 section 21.11.
    """

    def test_a_decision_asked_to_be_numbered_is(self, client: TestClient) -> None:
        response = client.post(
            "/v1/concepts",
            json={
                "type": "decision",
                "title": "First Ruling",
                "in": "10_Workspaces/Never4gA/Decisions",
                "numbered": True,
            },
        )
        assert response.status_code == 201, response.text
        assert (
            response.json()["path"] == "10_Workspaces/Never4gA/Decisions/adr-0001_first-ruling.md"
        )

    def test_a_stated_number_that_is_not_the_next_is_a_422(self, client: TestClient) -> None:
        response = client.post(
            "/v1/concepts",
            json={
                "type": "decision",
                "title": "ADR-0033 — Out of Line",
                "in": "10_Workspaces/Never4gA/Decisions",
            },
        )
        assert response.status_code == 422
        assert "ADR-0001" in response.json()["error"]["message"]
