"""Generated navigation: what an `index.md` should say about its directory.

`core/01` §4 reserves `index.md` for OKF progressive disclosure and §6 keeps
concept frontmatter out of it, which together are what make it derivable: it
carries no identity, no relations and no judgement.

A hand-kept index drifts: it names directories that do not exist, misses ones
that do, and links to folders, which Obsidian does nothing useful with. A
generated one lists what the directory actually holds and links to files.
"""

from __future__ import annotations

from typing import Any

from never4ga.adapters.fakes import InMemoryVaultFileStore
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.services.navigation import (
    BEGIN,
    END,
    NavigationEntry,
    block_for,
    current_block,
    entries_for,
    lineage_of,
    matches,
    missing_indexes,
    outdated,
    refresh,
    spliced,
)


def doc(path: str, title: str | None = None, **frontmatter: Any) -> StoredDocument:
    fields: dict[str, Any] = {"type": "knowledge", "schema": "never4ga/0.1", **frontmatter}
    if title is not None:
        fields["title"] = title
    return StoredDocument(
        concept_id=ConceptId.new(),
        path=VaultPath.parse(path),
        frontmatter=fields,
        body="",
    )


def entries(
    directory: str | None,
    *documents: StoredDocument,
    indexed: list[str] | None = None,
) -> list[NavigationEntry]:
    return entries_for(
        None if directory is None else VaultPath.parse(directory),
        documents,
        indexed_directories=[VaultPath.parse(p) for p in (indexed or [])],
    )


class TestWhatIsListed:
    def test_a_document_is_listed_by_its_title(self) -> None:
        (entry,) = entries("30_Knowledge/Notes", doc("30_Knowledge/Notes/a.md", "Hybrid Retrieval"))
        assert (entry.target, entry.title) == ("a.md", "Hybrid Retrieval")

    def test_a_document_links_to_the_file_rather_than_the_folder(self) -> None:
        # Obsidian does nothing useful with a folder link, and a
        # non-Obsidian reader cannot follow one at all.
        (entry,) = entries("30_Knowledge/Notes", doc("30_Knowledge/Notes/a.md", "A"))
        assert entry.target.endswith(".md")

    def test_documents_are_sorted_by_title(self) -> None:
        found = entries(
            "30_Knowledge/Notes",
            doc("30_Knowledge/Notes/z.md", "Alpha"),
            doc("30_Knowledge/Notes/a.md", "Zulu"),
        )
        assert [e.title for e in found] == ["Alpha", "Zulu"]

    def test_a_document_with_no_title_falls_back_to_its_filename(self) -> None:
        (entry,) = entries("30_Knowledge/Notes", doc("30_Knowledge/Notes/hybrid-retrieval.md"))
        assert entry.title == "Hybrid Retrieval"

    def test_the_vault_root_is_addressable(self) -> None:
        found = entries(None, doc("30_Knowledge/Notes/a.md", "A"), doc("home.md", "Home"))
        assert [e.title for e in found] == ["Home", "30 Knowledge"]


class TestWhatIsNotListed:
    def test_a_directory_with_nothing_in_it_is_omitted(self) -> None:
        """Only directories that hold something are listed.

        A scaffold lists every directory the profile *might* have. A generated
        index lists the ones that hold something, so a link is never a promise
        the vault does not keep.
        """
        found = entries("10_Workspaces/Acme", doc("10_Workspaces/Acme/Logs/a.md", "A"))
        assert [e.title for e in found] == ["Logs"]

    def test_the_index_does_not_list_itself(self) -> None:
        found = entries(
            "30_Knowledge",
            doc("30_Knowledge/index.md", "Knowledge"),
            doc("30_Knowledge/a.md", "A"),
        )
        assert [e.title for e in found] == ["A"]

    def test_reserved_history_is_not_content_either(self) -> None:
        # `core/01` §5: `log.md` is reserved directory history.
        found = entries("30_Knowledge", doc("30_Knowledge/log.md", "Log"))
        assert found == []

    def test_a_document_deeper_down_is_the_subdirectorys_business(self) -> None:
        found = entries("30_Knowledge", doc("30_Knowledge/Notes/deep/a.md", "A"))
        assert [e.title for e in found] == ["Notes"]


class TestSubdirectories:
    def test_one_with_an_index_links_to_it(self) -> None:
        (entry,) = entries(
            "30_Knowledge",
            doc("30_Knowledge/Notes/a.md", "A"),
            indexed=["30_Knowledge/Notes"],
        )
        assert entry.target == "Notes/index.md"

    def test_one_without_an_index_links_to_the_directory(self) -> None:
        (entry,) = entries("30_Knowledge", doc("30_Knowledge/Notes/a.md", "A"))
        assert entry.target == "Notes/"

    def test_its_name_becomes_a_title(self) -> None:
        (entry,) = entries("10_Workspaces", doc("10_Workspaces/Harborview-Club/a.md", "A"))
        assert entry.title == "Harborview Club"

    def test_directories_come_after_documents(self) -> None:
        found = entries(
            "10_Workspaces/Acme",
            doc("10_Workspaces/Acme/workspace.md", "Acme"),
            doc("10_Workspaces/Acme/Logs/a.md", "A"),
        )
        assert [e.is_directory for e in found] == [False, True]


class TestTheBlock:
    def test_it_is_delimited(self) -> None:
        rendered = block_for(entries("30_Knowledge", doc("30_Knowledge/a.md", "A")))
        assert rendered.startswith(BEGIN) and rendered.endswith(END)

    def test_sections_and_documents_are_separate_lists(self) -> None:
        rendered = block_for(
            entries(
                "10_Workspaces/Acme",
                doc("10_Workspaces/Acme/workspace.md", "Acme"),
                doc("10_Workspaces/Acme/Logs/a.md", "A"),
            )
        )
        assert "## Sections" in rendered and "## Documents" in rendered

    def test_an_absent_section_is_omitted_rather_than_empty(self) -> None:
        rendered = block_for(entries("30_Knowledge", doc("30_Knowledge/a.md", "A")))
        assert "## Sections" not in rendered

    def test_an_empty_directory_says_so(self) -> None:
        # Silence would read as a bug in the generator rather than a fact about
        # the vault.
        assert "_Nothing here yet._" in block_for([])

    def test_it_is_plain_markdown(self) -> None:
        rendered = block_for(entries("30_Knowledge", doc("30_Knowledge/a.md", "A")))
        assert "[A](a.md)" in rendered


class TestSplicing:
    """Everything outside the markers is a person's, and stays untouched."""

    def test_prose_above_the_block_survives(self) -> None:
        existing = "# Knowledge\n\nNotes are classified with type, never a folder tree.\n"
        result = spliced(existing, block_for([]))
        assert "Notes are classified with type, never a folder tree." in result

    def test_the_block_is_appended_when_there_is_none(self) -> None:
        result = spliced("# Knowledge\n", block_for([]))
        assert result.startswith("# Knowledge") and BEGIN in result

    def test_a_second_pass_replaces_rather_than_appends(self) -> None:
        first = spliced("# Knowledge\n", block_for([]))
        second = spliced(first, block_for(entries("30_K", doc("30_K/a.md", "A"))))
        assert second.count(BEGIN) == 1
        assert "_Nothing here yet._" not in second

    def test_prose_below_the_block_survives_too(self) -> None:
        existing = spliced("# Knowledge\n", block_for([])) + "\nA closing thought.\n"
        result = spliced(existing, block_for(entries("30_K", doc("30_K/a.md", "A"))))
        assert result.endswith("A closing thought.\n")
        assert "[A](a.md)" in result

    def test_an_empty_file_becomes_just_the_block(self) -> None:
        assert spliced("", block_for([])).strip().startswith(BEGIN)


class TestDetectingDrift:
    def test_a_file_carrying_the_block_matches(self) -> None:
        block = block_for(entries("30_K", doc("30_K/a.md", "A")))
        assert matches(spliced("# K\n", block), block)

    def test_a_file_with_no_block_does_not(self) -> None:
        assert not matches("# K\n", block_for([]))

    def test_a_changed_directory_does_not(self) -> None:
        before = block_for(entries("30_K", doc("30_K/a.md", "A")))
        after = block_for(entries("30_K", doc("30_K/a.md", "A"), doc("30_K/b.md", "B")))
        assert not matches(spliced("# K\n", before), after)

    def test_an_unterminated_block_is_not_a_block(self) -> None:
        """Otherwise the next write swallows the rest of the file.

        A truncated or hand-mangled marker means Never4gA does not know where
        its region ends, and the safe reading is that it has none.
        """
        assert current_block(f"# K\n{BEGIN}\n- [A](a.md)\n") is None

    def test_and_such_a_file_is_repaired_rather_than_doubled(self) -> None:
        # The stray marker goes with it. Leaving it would give the file two
        # opening markers, and the next read would treat the span between the
        # old one and the new close as the block.
        result = spliced(f"# K\n{BEGIN}\n- [A](a.md)\n", block_for([]))
        assert result.count(BEGIN) == 1
        assert result.count(END) == 1
        assert result.startswith("# K")


class TestAbsorbingTheOldList:
    """The stale scaffold is navigation, and the block now owns navigation.

    Splicing a correct block above a wrong one leaves two sets of navigation in
    one file: two copies of one fact, both visible and disagreeing.
    """

    SCAFFOLD = (
        "# Knowledge\n"
        "\n"
        "- [Notes](Notes/) - Durable reusable knowledge. Semantically flat.\n"
        "- [Maps](Maps/) - Curated navigation and synthesis.\n"
        "- [Assets](Assets/) - Supporting binaries.\n"
        "\n"
        "Notes are classified with type, domains, tags, links and typed relations,\n"
        "never with a topical folder tree.\n"
    )

    def test_the_old_link_list_is_taken_over(self) -> None:
        result = spliced(self.SCAFFOLD, block_for([]))
        assert "- [Maps](Maps/)" not in result

    def test_the_orientation_prose_is_not(self) -> None:
        result = spliced(self.SCAFFOLD, block_for([]))
        assert "never with a topical folder tree." in result

    def test_and_neither_is_the_heading(self) -> None:
        assert spliced(self.SCAFFOLD, block_for([])).startswith("# Knowledge")

    def test_what_the_bullets_said_survives_in_the_block(self) -> None:
        """The descriptions were worth keeping, and they are derivable.

        A directory's role is fixed by its name -- `Logs/` is activity history
        in every workspace there has ever been -- so the generator carries them
        rather than a person having to keep a list true.
        """
        rendered = block_for(entries("30_Knowledge", doc("30_Knowledge/Notes/a.md", "A")))
        assert "Durable reusable knowledge. Semantically flat." in rendered

    def test_a_second_splice_leaves_the_prose_alone(self) -> None:
        once = spliced(self.SCAFFOLD, block_for([]))
        twice = spliced(once, block_for(entries("30_K", doc("30_K/a.md", "A"))))
        assert "never with a topical folder tree." in twice
        assert twice.count(BEGIN) == 1


class TestWhatIsDeliberatelyNotGenerated:
    def test_a_directory_holding_only_subdirectories_is_still_indexed(self) -> None:
        """`30_Knowledge/` holds no concepts; its `Notes/` does.

        Keying on each concept's immediate parent would skip every directory
        that is nothing but subdirectories, which is most of the roots.
        """
        found = entries(
            "30_Knowledge",
            doc("30_Knowledge/Notes/a.md", "A"),
            indexed=["30_Knowledge/Notes"],
        )
        assert [e.target for e in found] == ["Notes/index.md"]

    def test_a_directory_of_binaries_is_absent(self) -> None:
        """Stated rather than found later.

        `Assets/` holds binaries, which are legitimate vault content and never
        become concepts, so nothing here can see them. Walking the filesystem
        for every directory on every diagnosis would cost far more than one
        link is worth.
        """
        found = entries("10_Workspaces/Acme", doc("10_Workspaces/Acme/workspace.md", "Acme"))
        assert [e.title for e in found] == ["Acme"]


class TestEveryDirectoryGetsAnIndex:
    """A section link that resolves to nothing is not navigation.

    A section link points at `Name/index.md` when that file exists, and at
    `Name/` otherwise. Obsidian resolves neither a missing file nor a bare
    folder, so every directory holding concepts gets an `index.md` for the
    link to reach.

    Missing indexes are still not reported once per directory: `doctor` says
    it once, with a count.
    """

    def test_every_ancestor_holding_concepts_is_named(self) -> None:
        files = InMemoryVaultFileStore()
        concepts = [doc("10_Workspaces/Demo/Decisions/adr-0001.md", "ADR-0001")]
        assert sorted(str(p) for p in missing_indexes(files, concepts)) == [
            "10_Workspaces",
            "10_Workspaces/Demo",
            "10_Workspaces/Demo/Decisions",
        ]

    def test_a_directory_that_already_has_one_is_not_named(self) -> None:
        files = InMemoryVaultFileStore()
        files.write_text(VaultPath.parse("10_Workspaces/Demo/Decisions/index.md"), "# Decisions\n")
        concepts = [doc("10_Workspaces/Demo/Decisions/adr-0001.md", "ADR-0001")]
        assert "10_Workspaces/Demo/Decisions" not in {
            str(p) for p in missing_indexes(files, concepts)
        }

    def test_refresh_creates_them(self) -> None:
        files = InMemoryVaultFileStore()
        concepts = [doc("10_Workspaces/Demo/Decisions/adr-0001.md", "ADR-0001")]
        refresh(files, concepts)
        created = files.read_text(VaultPath.parse("10_Workspaces/Demo/Decisions/index.md"))
        assert created is not None
        assert BEGIN in created
        assert "[ADR-0001](adr-0001.md)" in created

    def test_a_created_index_carries_a_heading_of_its_own(self) -> None:
        # A file that opens with an HTML comment reads as machinery. The
        # directory's name is the one thing about it that is certainly true.
        files = InMemoryVaultFileStore()
        refresh(files, [doc("10_Workspaces/Demo/Decisions/adr-0001.md", "ADR-0001")])
        created = files.read_text(VaultPath.parse("10_Workspaces/Demo/Decisions/index.md")) or ""
        assert created.startswith("# Decisions\n")

    def test_the_section_link_resolves_on_the_first_pass(self) -> None:
        # A pass that wrote `Decisions/` and needed a second pass to write
        # `Decisions/index.md` would leave the vault correct only after running
        # repair twice.
        files = InMemoryVaultFileStore()
        refresh(files, [doc("10_Workspaces/Demo/Decisions/adr-0001.md", "ADR-0001")])
        workspace_index = files.read_text(VaultPath.parse("10_Workspaces/Demo/index.md")) or ""
        assert "(Decisions/index.md)" in workspace_index
        assert "(Decisions/)" not in workspace_index

    def test_refresh_is_settled_the_second_time(self) -> None:
        files = InMemoryVaultFileStore()
        concepts = [doc("10_Workspaces/Demo/Decisions/adr-0001.md", "ADR-0001")]
        refresh(files, concepts)
        assert refresh(files, concepts) == 0


class TestAFolderThatWasEmptied:
    """An index stays honest when everything it listed moves away.

    A folder's documents can all leave it: moved to where their type now
    lives, or adopted out of it. Its `index.md` stays behind, and it must stop
    linking to files that are gone. A hand-written index carries no generated
    block, and is left alone.
    """

    RUNBOOKS = "10_Workspaces/Demo/Runbooks"

    def emptied(self) -> tuple[InMemoryVaultFileStore, list[StoredDocument]]:
        files = InMemoryVaultFileStore()
        before = [doc(f"{self.RUNBOOKS}/phase-1.md", "Phase 1"), doc("10_Workspaces/Demo/x.md")]
        refresh(files, before)
        return files, [doc("10_Workspaces/Demo/Walkthroughs/phase-1.md", "Phase 1"), before[1]]

    def test_doctor_sees_it(self) -> None:
        files, after = self.emptied()
        assert VaultPath.parse(f"{self.RUNBOOKS}/index.md") in outdated(files, after)

    def test_refresh_says_it_is_empty(self) -> None:
        files, after = self.emptied()
        refresh(files, after)
        text = files.read_text(VaultPath.parse(f"{self.RUNBOOKS}/index.md")) or ""
        assert "phase-1.md" not in text
        assert "_Nothing here yet._" in text
        assert refresh(files, after) == 0

    def test_a_hand_written_index_is_left_alone(self) -> None:
        files = InMemoryVaultFileStore()
        hand = VaultPath.parse("10_Workspaces/Demo/Notes/index.md")
        files.write_text(hand, "# Notes\n\n- [Old](old.md)\n")
        refresh(files, [doc("10_Workspaces/Demo/x.md")])
        assert files.read_text(hand) == "# Notes\n\n- [Old](old.md)\n"


class TestARefreshCanBeScopedToOneLineage:
    """Creating one document may not rewrite navigation for the whole vault.

    A whole-vault :func:`refresh` on creation would let one new workspace
    rewrite navigation everywhere, carrying any unrelated drift along inside
    the request that created one file.

    A new document changes what its own directory navigates and what each of
    its ancestors navigates, and nothing else, so that is what a creation is
    allowed to write. The whole-vault pass stays for `init`, where the vault is
    new, and for `repair --apply`, where sweeping is the point.
    """

    def test_an_unrelated_drifted_index_is_left_alone(self) -> None:
        files = InMemoryVaultFileStore()
        concepts = [
            doc("10_Workspaces/Other/Notes/old.md", "Old"),
            doc("10_Workspaces/Demo/Decisions/adr-0001.md", "ADR-0001"),
        ]
        stale = VaultPath.parse("10_Workspaces/Other/Notes/index.md")
        files.write_text(stale, "# Notes\n\n- [Gone](gone.md)\n")

        refresh(
            files,
            concepts,
            within=lineage_of(VaultPath.parse("10_Workspaces/Demo/Decisions/adr-0001.md")),
        )

        assert files.read_text(stale) == "# Notes\n\n- [Gone](gone.md)\n"

    def test_the_new_directory_and_its_ancestors_are_written(self) -> None:
        files = InMemoryVaultFileStore()
        concepts = [doc("10_Workspaces/Demo/Decisions/adr-0001.md", "ADR-0001")]

        written = refresh(
            files,
            concepts,
            within=lineage_of(VaultPath.parse("10_Workspaces/Demo/Decisions/adr-0001.md")),
        )

        assert written == 3
        for directory in ("10_Workspaces", "10_Workspaces/Demo", "10_Workspaces/Demo/Decisions"):
            assert files.read_text(VaultPath.parse(f"{directory}/index.md")) is not None

    def test_a_link_to_a_directory_outside_the_scope_still_resolves(self) -> None:
        """The out-of-scope sibling keeps whatever link it can actually be reached by.

        A section link points at `Name/index.md` when that file exists and at
        `Name/` otherwise. A scoped pass does not create the
        ones outside its scope, so it may not claim they will be there -- which
        the whole-vault pass is entitled to assume, because it creates them all.
        """
        files = InMemoryVaultFileStore()
        files.write_text(VaultPath.parse("10_Workspaces/Has/index.md"), "# Has\n")
        concepts = [
            doc("10_Workspaces/Has/note.md", "Kept"),
            doc("10_Workspaces/Without/note.md", "Uncreated"),
            doc("10_Workspaces/Demo/workspace.md", "Demo"),
        ]

        refresh(
            files, concepts, within=lineage_of(VaultPath.parse("10_Workspaces/Demo/workspace.md"))
        )

        parent = files.read_text(VaultPath.parse("10_Workspaces/index.md")) or ""
        assert "(Has/index.md)" in parent
        assert "(Without/)" in parent
        assert "(Without/index.md)" not in parent

    def test_a_scoped_pass_is_settled_the_second_time(self) -> None:
        files = InMemoryVaultFileStore()
        concepts = [doc("10_Workspaces/Demo/Decisions/adr-0001.md", "ADR-0001")]
        scope = lineage_of(VaultPath.parse("10_Workspaces/Demo/Decisions/adr-0001.md"))

        refresh(files, concepts, within=scope)

        assert refresh(files, concepts, within=scope) == 0

    def test_the_whole_vault_pass_still_sweeps_when_no_scope_is_given(self) -> None:
        files = InMemoryVaultFileStore()
        concepts = [
            doc("10_Workspaces/Other/Notes/old.md", "Old"),
            doc("10_Workspaces/Demo/Decisions/adr-0001.md", "ADR-0001"),
        ]
        stale = VaultPath.parse("10_Workspaces/Other/Notes/index.md")
        files.write_text(stale, "# Notes\n\n- [Gone](gone.md)\n")

        refresh(files, concepts)

        assert "[Old](old.md)" in (files.read_text(stale) or "")
