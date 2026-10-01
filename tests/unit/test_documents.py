"""Canonical document and chunk identity.

Specification:
- core/00 #1, #15 -- Markdown is canonical; unknown extension data is preserved.
- core/01 -- vault-relative paths.
- core/06 section 4 -- stable chunk identity below the document level.
"""

from __future__ import annotations

import pytest

from never4ga.domain.chunk import ChunkIdentity, LineRange
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.errors import VaultPathError


class TestVaultPath:
    def test_normalises_to_posix_relative_form(self) -> None:
        assert str(VaultPath.parse("30_Knowledge/Notes/thing.md")) == "30_Knowledge/Notes/thing.md"

    def test_strips_leading_slash_and_dot_segments(self) -> None:
        assert str(VaultPath.parse("./30_Knowledge/./Notes/thing.md")) == (
            "30_Knowledge/Notes/thing.md"
        )

    def test_rejects_escaping_the_vault(self) -> None:
        for bad in ("../outside.md", "30_Knowledge/../../outside.md", "/etc/passwd"):
            with pytest.raises(VaultPathError):
                VaultPath.parse(bad)

    def test_rejects_empty(self) -> None:
        with pytest.raises(VaultPathError):
            VaultPath.parse("   ")

    def test_rejects_empty_through_the_constructor_too(self) -> None:
        # Slicing the parent directory of a top-level file would otherwise
        # produce a path with no segments, which no layout rule expects and
        # which fails later with an IndexError rather than a structured error.
        with pytest.raises(VaultPathError):
            VaultPath(())

    def test_exposes_segments_and_root(self) -> None:
        path = VaultPath.parse("10_Workspaces/never4ga/workspace.md")
        assert path.root == "10_Workspaces"
        assert path.segments == ("10_Workspaces", "never4ga", "workspace.md")
        assert path.name == "workspace.md"

    def test_is_hashable_and_value_compared(self) -> None:
        assert VaultPath.parse("a/b.md") == VaultPath.parse("a/b.md")
        assert len({VaultPath.parse("a/b.md"), VaultPath.parse("a/b.md")}) == 1


class TestStoredDocument:
    def _doc(self, **overrides: object) -> StoredDocument:
        defaults: dict[str, object] = {
            "concept_id": ConceptId.parse("0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31"),
            "path": VaultPath.parse("30_Knowledge/Notes/thing.md"),
            "frontmatter": {
                "type": "knowledge",
                "id": "0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31",
                "schema": "never4ga/0.1",
                "title": "Thing",
                "created_at": "2026-08-22T19:45:00Z",
            },
            "body": "# Thing\n\nBody text.\n",
        }
        defaults.update(overrides)
        return StoredDocument(**defaults)  # type: ignore[arg-type]

    def test_preserves_unknown_extension_fields(self) -> None:
        # core/00 #15: unknown extension fields must survive unchanged.
        doc = self._doc(
            frontmatter={
                "type": "knowledge",
                "id": "0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31",
                "schema": "never4ga/0.1",
                "title": "Thing",
                "created_at": "2026-08-22T19:45:00Z",
                "x_vendor_field": {"nested": [1, 2, 3]},
                "unknown_scalar": "keep me",
            }
        )
        assert doc.frontmatter["x_vendor_field"] == {"nested": [1, 2, 3]}
        assert doc.frontmatter["unknown_scalar"] == "keep me"

    def test_frontmatter_is_read_only(self) -> None:
        doc = self._doc()
        with pytest.raises(TypeError):
            doc.frontmatter["title"] = "mutated"  # type: ignore[index]

    def test_content_hash_is_deterministic_and_content_derived(self) -> None:
        assert self._doc().content_hash == self._doc().content_hash
        assert self._doc().content_hash != self._doc(body="different").content_hash

    def test_content_hash_ignores_location(self) -> None:
        # Identity and content are path-independent (core/02 section 5.1).
        moved = self._doc(path=VaultPath.parse("90_Archive/thing.md"))
        assert moved.content_hash == self._doc().content_hash

    def test_identity_survives_a_move(self) -> None:
        moved = self._doc(path=VaultPath.parse("90_Archive/thing.md"))
        assert moved.concept_id == self._doc().concept_id


class TestChunkIdentity:
    def _chunk(self, **overrides: object) -> ChunkIdentity:
        defaults: dict[str, object] = {
            "concept_id": ConceptId.parse("0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31"),
            "heading_path": ("Thing", "Details"),
            "ordinal": 0,
            "content_hash": "a" * 64,
            "policy_version": "chunk/0.1",
        }
        defaults.update(overrides)
        return ChunkIdentity(**defaults)  # type: ignore[arg-type]

    def test_key_is_deterministic(self) -> None:
        assert self._chunk().key == self._chunk().key

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("concept_id", ConceptId.new()),
            ("heading_path", ("Thing", "Other")),
            ("ordinal", 1),
            ("content_hash", "b" * 64),
            ("policy_version", "chunk/0.2"),
        ],
    )
    def test_every_identity_input_changes_the_key(self, field: str, value: object) -> None:
        # core/06 section 4 lists exactly these five inputs.
        assert self._chunk(**{field: value}).key != self._chunk().key

    def test_key_is_not_a_backend_identifier(self) -> None:
        key = self._chunk().key
        assert isinstance(key, str)
        assert not key.isdigit()

    def test_answers_the_provenance_question(self) -> None:
        # "This vector came from document X, heading Y, chunk-policy Z, hash H."
        chunk = self._chunk()
        assert chunk.concept_id == ConceptId.parse("0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31")
        assert chunk.heading_path == ("Thing", "Details")
        assert chunk.policy_version == "chunk/0.1"
        assert chunk.content_hash == "a" * 64

    def test_rejects_negative_ordinal(self) -> None:
        with pytest.raises(ValueError, match="ordinal"):
            self._chunk(ordinal=-1)


class TestLineRange:
    def test_reports_the_source_lines(self) -> None:
        span = LineRange(start=10, end=24)
        assert (span.start, span.end) == (10, 24)

    def test_rejects_inverted_or_non_positive_ranges(self) -> None:
        for start, end in ((24, 10), (0, 5), (-1, 3)):
            with pytest.raises(ValueError, match="line"):
                LineRange(start=start, end=end)
