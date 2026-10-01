"""What must not enter the vault from somewhere else.

Invisible characters that hide instructions from a reader, and personal paths
that should never be baked into shared material.
"""

from __future__ import annotations

from never4ga.domain.text_hazards import (
    PLACEHOLDER_USERNAMES,
    find_invisible_characters,
    find_personal_paths,
    scan_text,
)

#: "ignore", spelled in Unicode tag characters. Rendered, the line below reads
#: "Run the build now." -- the word between them is invisible in every editor,
#: diff and terminal, and is exactly what the model reads.
SMUGGLED = "Run the build \U000e0069\U000e0067\U000e006e\U000e006f\U000e0072\U000e0065 now."


class TestInvisibleCharacters:
    def test_the_tag_block_is_caught(self) -> None:
        found = list(find_invisible_characters(SMUGGLED))
        assert [hazard.kind for hazard in found] == ["tag_character"] * 6

    def test_it_says_where(self) -> None:
        """A character nobody can see is only actionable with a position."""
        hazard = next(iter(find_invisible_characters(SMUGGLED)))
        assert hazard.line == 1 and hazard.column > 0

    def test_it_names_the_code_point(self) -> None:
        hazard = next(iter(find_invisible_characters(SMUGGLED)))
        assert "U+E0069" in hazard.detail

    def test_a_bidi_override_is_caught(self) -> None:
        """The Trojan Source class."""
        found = list(find_invisible_characters("if (admin) \u202e{ }"))
        assert [hazard.kind for hazard in found] == ["bidi_override"]

    def test_a_zero_width_space_is_caught(self) -> None:
        assert [h.kind for h in find_invisible_characters("ad\u200bmin")] == ["zero_width"]

    def test_ordinary_text_is_clean(self) -> None:
        assert not list(find_invisible_characters("# A Skill\n\nRun `make`.\n"))

    def test_emoji_are_not_a_hazard(self) -> None:
        """A house-style question, not a security one. Not this check's business."""
        assert not list(find_invisible_characters("Ship it \U0001f680"))

    def test_accented_and_non_latin_text_is_clean(self) -> None:
        """The check must not punish a language for not being English."""
        assert not list(find_invisible_characters("café, naïve, 日本語, Ελληνικά, עברית"))


class TestPersonalPaths:
    def test_a_real_home_directory_is_caught(self) -> None:
        found = list(find_personal_paths("see /home/alice/notes"))
        assert [hazard.kind for hazard in found] == ["personal_path_posix"]

    def test_a_placeholder_is_allowed(self) -> None:
        """`/home/username/...` in a template is how it should be written."""
        assert not list(find_personal_paths("see /home/username/notes"))

    def test_every_placeholder_passes(self) -> None:
        for name in PLACEHOLDER_USERNAMES:
            assert not list(find_personal_paths(f"/home/{name}/x")), name

    def test_macos_and_windows_are_caught_too(self) -> None:
        kinds = {h.kind for h in find_personal_paths("/Users/carol and C:\\Users\\Bob")}
        assert kinds == {"personal_path_macos", "personal_path_windows"}

    def test_a_forbidden_literal_is_caught_even_without_a_username(self) -> None:
        """A path with no user component that must still never be baked in."""
        found = list(
            find_personal_paths(
                "cd /srv/vault/private", forbidden=frozenset({"/srv/vault/private"})
            )
        )
        assert [hazard.kind for hazard in found] == ["forbidden_path"]

    def test_a_relative_path_is_not_a_hazard(self) -> None:
        assert not list(find_personal_paths("see ./scripts/run.py and ~/.config"))


class TestScanningBoth:
    def test_findings_come_back_in_reading_order(self) -> None:
        text = "line one\n/home/alice/x\nplain\nad\u200bmin\n"
        assert [hazard.line for hazard in scan_text(text)] == [2, 4]

    def test_clean_text_returns_nothing(self) -> None:
        assert scan_text("# Fine\n\nNothing to see.\n") == ()

    def test_a_file_can_carry_both_kinds(self) -> None:
        text = f"{SMUGGLED}\n/home/alice/x\n"
        kinds = {hazard.kind for hazard in scan_text(text)}
        assert kinds == {"tag_character", "personal_path_posix"}
