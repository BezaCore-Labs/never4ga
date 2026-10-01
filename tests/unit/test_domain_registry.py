"""The Domain Registry as a vault document (core/02 sections 9.1, 21.17, 22).

core/02 section 22 sanctions hard-coding the bootstrap values and then says:
"Before the runtime is considered mature, the canonical registries SHOULD be
represented inside `50_System/Schemas/` so the system can describe its own
vocabulary." This is that representation for the domain vocabulary.

The shape: a `registry` concept at `50_System/Schemas/domain-registry.md` whose
frontmatter carries `registry: domains` and `values: [...]`. Section 21.17
leaves the machine-readable fields to be registered rather than naming them, so
these two are registered on the `registry` type in code.

The registry is authoritative when present. `never4ga init` seeds it with the
bootstrap values, and a vault that has lost it falls back to those same values
rather than treating every domain as unknown.
"""

from __future__ import annotations

from never4ga.domain.document import VaultPath
from never4ga.layout import DOMAIN_REGISTRY
from never4ga.schema import (
    DOMAIN_VALUES,
    ValidationLevel,
    domains_from_registry,
    validate_document,
)

NOTE = VaultPath.parse("30_Knowledge/Notes/some-note.md")


def registry_frontmatter(**overrides: object) -> dict[str, object]:
    frontmatter: dict[str, object] = {
        "type": "registry",
        "id": "01a02c26-f966-7190-a245-b795880e79d3",
        "schema": "never4ga/0.1",
        "title": "Domain Registry",
        "created_at": "2026-08-24T12:00:00+00:00",
        "registry": "domains",
        "values": ["technology", "faith", "finance"],
    }
    frontmatter.update(overrides)
    return frontmatter


def note_frontmatter(domains: list[str]) -> dict[str, object]:
    return {
        "type": "knowledge",
        "id": "01a02c26-f966-7190-a245-b795880e79d4",
        "schema": "never4ga/0.1",
        "title": "Some note",
        "created_at": "2026-08-24T12:00:00+00:00",
        "domains": domains,
    }


class TestExtraction:
    def test_a_domain_registry_yields_its_values(self) -> None:
        assert domains_from_registry(registry_frontmatter()) == (
            "technology",
            "faith",
            "finance",
        )

    def test_a_registry_for_another_vocabulary_yields_nothing(self) -> None:
        assert domains_from_registry(registry_frontmatter(registry="entity_types")) is None

    def test_a_non_registry_concept_yields_nothing(self) -> None:
        assert domains_from_registry(note_frontmatter(["technology"])) is None

    def test_malformed_values_yield_nothing_rather_than_a_partial_list(self) -> None:
        # Reading half a registry would validate against half a vocabulary; the
        # damage is reported by validation, and consumers fall back whole.
        assert domains_from_registry(registry_frontmatter(values="technology")) is None
        assert domains_from_registry(registry_frontmatter(values=["ok", 7])) is None

    def test_no_frontmatter_yields_nothing(self) -> None:
        assert domains_from_registry(None) is None


class TestValidationAgainstTheRegistry:
    def test_a_registered_domain_is_clean(self) -> None:
        report = validate_document(
            NOTE,
            note_frontmatter(["faith"]),
            level=ValidationLevel.STRICT,
            domains=("faith", "technology"),
        )
        assert not [i for i in report.issues if i.code == "unregistered_domain"]

    def test_an_unregistered_domain_still_warns(self) -> None:
        report = validate_document(
            NOTE,
            note_frontmatter(["cooking"]),
            level=ValidationLevel.STRICT,
            domains=("faith", "technology"),
        )
        assert [i for i in report.issues if i.code == "unregistered_domain"]

    def test_the_registry_is_authoritative_not_additive(self) -> None:
        # A curated registry that omits a bootstrap value means that value is
        # no longer registered. Registries SHOULD be authoritative (21.17).
        bootstrap_domain = DOMAIN_VALUES[0]
        report = validate_document(
            NOTE,
            note_frontmatter([bootstrap_domain]),
            level=ValidationLevel.STRICT,
            domains=("faith",),
        )
        assert [i for i in report.issues if i.code == "unregistered_domain"]

    def test_omitting_domains_falls_back_to_the_bootstrap_values(self) -> None:
        report = validate_document(
            NOTE, note_frontmatter([DOMAIN_VALUES[0]]), level=ValidationLevel.STRICT
        )
        assert not [i for i in report.issues if i.code == "unregistered_domain"]


class TestTheRegistryDocumentItself:
    def test_a_well_formed_registry_is_clean_at_strict(self) -> None:
        report = validate_document(
            DOMAIN_REGISTRY, registry_frontmatter(), level=ValidationLevel.STRICT
        )
        assert not report.issues, [i.message for i in report.issues]

    def test_values_that_are_not_a_list_are_an_error(self) -> None:
        report = validate_document(
            DOMAIN_REGISTRY,
            registry_frontmatter(values="technology"),
            level=ValidationLevel.STRICT,
        )
        assert [i for i in report.errors if i.code == "invalid_registry_values"]

    def test_a_non_string_value_is_an_error(self) -> None:
        report = validate_document(
            DOMAIN_REGISTRY,
            registry_frontmatter(values=["ok", 7]),
            level=ValidationLevel.STRICT,
        )
        assert [i for i in report.errors if i.code == "invalid_registry_values"]

    def test_the_registry_lives_in_schemas(self) -> None:
        assert str(DOMAIN_REGISTRY) == "50_System/Schemas/domain-registry.md"
