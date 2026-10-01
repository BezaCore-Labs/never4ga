"""Turning one canonical document into the material the indexes hold.

details/data-indexing-maintenance.md section 12 is the pipeline this feeds, and
its rule for bad input: "Invalid concept documents are reported rather than
silently skipped. Where safe, useful text may still be indexed with a validation
warning."
"""

from __future__ import annotations

from typing import Any

from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.indexing.projection import project
from never4ga.ports.text_index import FIELD_KEYWORDS, FIELD_TITLE

WORKSPACE = ConceptId.new()


def document(
    body: str = "# One\nbody\n",
    *,
    path: str = "30_Knowledge/Notes/thing.md",
    **frontmatter: Any,
) -> StoredDocument:
    concept_id = frontmatter.pop("concept_id", None) or ConceptId.new()
    base: dict[str, Any] = {
        "type": "knowledge",
        "id": str(concept_id),
        "schema": "never4ga/0.1",
        "title": "Thing",
        "created_at": "2026-08-23T12:00:00-04:00",
    }
    base.update(frontmatter)
    return StoredDocument(
        concept_id=concept_id,
        path=VaultPath.parse(path),
        frontmatter=base,
        body=body,
    )


class TestMetadataRecord:
    def test_the_modelled_fields_are_projected(self) -> None:
        record = project(
            document(
                tags=["architecture", "python"],
                domains=["software"],
                lifecycle="current",
                status="stable",
                authority="authoritative",
                workspace=str(WORKSPACE),
            )
        ).record
        assert record.concept_type == "knowledge"
        assert record.title == "Thing"
        assert record.tags == ("architecture", "python")
        assert record.domains == ("software",)
        assert (record.lifecycle, record.status, record.authority) == (
            "current",
            "stable",
            "authoritative",
        )
        assert record.workspace_id == WORKSPACE

    def test_everything_else_is_preserved_rather_than_dropped(self) -> None:
        # core/00 #15: unknown extension fields must survive.
        record = project(document(description="A thing", x_future="value")).record
        assert record.extra["description"] == "A thing"
        assert record.extra["x_future"] == "value"

    def test_the_fields_that_became_columns_are_not_repeated_in_extra(self) -> None:
        record = project(document(tags=["a"])).record
        assert not {"type", "title", "tags", "id"} & set(record.extra)

    def test_a_malformed_workspace_reference_is_reported_not_guessed(self) -> None:
        projection = project(document(workspace="not-a-uuid"))
        assert projection.record.workspace_id is None
        assert [issue.code for issue in projection.issues] == ["invalid_workspace_reference"]

    def test_a_life_areas_own_document_is_scoped_to_the_area(self) -> None:
        # An area holds its own documents (core/01 section 7) and names itself
        # with `area` (core/02 section 16.3). Reading `workspace` alone would
        # leave a decision under 20_Life/<Area>/ with no scope owner, and
        # nothing scoped to a course under that area could reach it.
        area = ConceptId.new()
        record = project(
            document(
                path="20_Life/Education/Decisions/one-workspace.md",
                type="decision",
                area=str(area),
            )
        ).record
        assert record.workspace_id == area

    def test_a_workspace_wins_over_an_area(self) -> None:
        # A course workspace's documents sit inside the area and belong to the
        # course. The narrower owner is the one scope should answer to.
        record = project(document(workspace=str(WORKSPACE), area=str(ConceptId.new()))).record
        assert record.workspace_id == WORKSPACE

    def test_the_area_is_still_kept_where_it_was(self) -> None:
        area = ConceptId.new()
        record = project(document(area=str(area))).record
        assert record.extra["area"] == str(area)

    def test_a_malformed_area_reference_is_reported_under_its_own_name(self) -> None:
        projection = project(document(area="not-a-uuid"))
        assert projection.record.workspace_id is None
        assert [issue.code for issue in projection.issues] == ["invalid_area_reference"]

    def test_a_missing_title_falls_back_and_says_so(self) -> None:
        projection = project(document(title=None))
        assert projection.record.title == "thing"
        assert "missing_title" in {issue.code for issue in projection.issues}

    def test_a_scalar_tag_is_read_as_a_single_tag(self) -> None:
        # A hand-edited vault will contain `tags: architecture` sooner or later.
        assert project(document(tags="architecture")).record.tags == ("architecture",)


class TestChunks:
    def test_document_level_fields_reach_the_lexical_index(self) -> None:
        (chunk, *_) = project(
            document(tags=["architecture"], aliases=["The Thing"], description="A thing")
        ).chunks
        assert chunk.metadata[FIELD_TITLE] == "Thing"
        keywords = chunk.metadata[FIELD_KEYWORDS]
        assert "architecture" in keywords
        assert "The Thing" in keywords
        assert "A thing" in keywords

    def test_a_document_with_no_body_still_projects_a_record(self) -> None:
        projection = project(document(""))
        assert projection.chunks == ()
        assert projection.record.title == "Thing"


class TestRelations:
    def test_typed_relations_become_edges(self) -> None:
        target = ConceptId.new()
        (edge,) = project(document(relations=[{"type": "depends_on", "target": str(target)}])).edges
        assert (edge.relation_type, edge.target) == ("depends_on", target)

    def test_an_unregistered_relation_type_is_preserved(self) -> None:
        # core/02 section 17.3: unknown relation types must survive.
        target = ConceptId.new()
        (edge,) = project(
            document(relations=[{"type": "x_invented_this", "target": str(target)}])
        ).edges
        assert edge.relation_type == "x_invented_this"

    def test_a_relation_with_an_unusable_target_is_reported(self) -> None:
        projection = project(document(relations=[{"type": "depends_on", "target": "nonsense"}]))
        assert projection.edges == ()
        assert "invalid_relation_target" in {issue.code for issue in projection.issues}

    def test_a_relation_missing_its_type_is_reported(self) -> None:
        projection = project(document(relations=[{"target": str(ConceptId.new())}]))
        assert projection.edges == ()
        assert "invalid_relation" in {issue.code for issue in projection.issues}

    def test_relations_that_are_not_a_list_are_reported(self) -> None:
        projection = project(document(relations="depends_on"))
        assert projection.edges == ()
        assert "invalid_relations" in {issue.code for issue in projection.issues}


class TestLinks:
    def test_a_link_resolves_against_the_source_directory(self) -> None:
        (link,) = project(document("see [other](other.md)\n")).links
        assert link.target_path == VaultPath.parse("30_Knowledge/Notes/other.md")

    def test_a_parent_relative_link_resolves(self) -> None:
        (link,) = project(document("see [map](../Maps/m.md)\n")).links
        assert link.target_path == VaultPath.parse("30_Knowledge/Maps/m.md")

    def test_a_link_escaping_the_vault_is_reported_not_resolved(self) -> None:
        projection = project(document("see [out](../../../../etc/passwd)\n"))
        assert projection.links[0].target_path is None
        assert "link_escapes_vault" in {issue.code for issue in projection.issues}

    def test_an_external_link_is_kept_but_not_resolved(self) -> None:
        (link,) = project(document("see [spec](https://example.invalid/x)\n")).links
        assert link.is_external
        assert link.target_path is None

    def test_a_directory_link_is_not_a_document_target(self) -> None:
        (link,) = project(document("see [notes](../Notes/)\n")).links
        assert link.target_path is None

    def test_an_anchor_only_link_points_at_the_document_itself(self) -> None:
        (link,) = project(document("see [above](#a-heading)\n")).links
        assert link.anchor == "a-heading"
        assert link.target_path == VaultPath.parse("30_Knowledge/Notes/thing.md")


class TestWikilinks:
    def test_a_bare_wikilink_carries_a_name_rather_than_a_path(self) -> None:
        # Obsidian resolves a bare name against the whole vault. That needs
        # every document in view, so the name travels to the indexing service.
        (link,) = project(document("see [[Other Note]]\n")).links
        assert link.target_name == "Other Note"
        assert link.target_path is None

    def test_a_path_style_wikilink_resolves_from_the_vault_root(self) -> None:
        # `[[folder/Note]]` is Obsidian's absolute-in-vault form, which is not
        # relative to the file the way a Markdown link is.
        (link,) = project(document("see [[30_Knowledge/Maps/m]]\n")).links
        assert link.target_path == VaultPath.parse("30_Knowledge/Maps/m.md")
        assert link.target_name is None

    def test_an_anchor_only_wikilink_points_at_this_document(self) -> None:
        (link,) = project(document("see [[#A Heading]]\n")).links
        assert link.target_path == VaultPath.parse("30_Knowledge/Notes/thing.md")
        assert link.anchor == "A Heading"

    def test_a_wikilink_is_never_external(self) -> None:
        (link,) = project(document("see [[Other Note]]\n")).links
        assert not link.is_external

    def test_a_markdown_link_carries_no_name(self) -> None:
        (link,) = project(document("see [other](other.md)\n")).links
        assert link.target_name is None

    def test_both_styles_survive_in_one_document(self) -> None:
        links = project(document("[[Wiki Note]] and [md](other.md)\n")).links
        assert [(link.target_name, link.target_path is not None) for link in links] == [
            ("Wiki Note", False),
            (None, True),
        ]


class TestProvenance:
    def test_the_projection_records_the_content_hash_it_was_built_from(self) -> None:
        source = document()
        assert project(source).content_hash == source.content_hash

    def test_projecting_twice_gives_the_same_result(self) -> None:
        source = document("# One\nbody\n\n## Two\nmore\n")
        assert [chunk.chunk.key for chunk in project(source).chunks] == [
            chunk.chunk.key for chunk in project(source).chunks
        ]
