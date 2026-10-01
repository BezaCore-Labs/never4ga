"""A task description becomes terms, and stops there (core/04 section 16).

    The full raw user prompt SHOULD NOT be persisted merely because it was used
    for retrieval.

So a task is a *short description*, it turns into words to search for, and what
travels onward is that a task was given -- never what it said. These tests hold
the transformation; the ones that hold the not-said half live with the CLI and
the API, because that is where a pack could leak it.
"""

from __future__ import annotations

from never4ga.domain.context import terms_from_task


class TestTermsFromATask:
    def test_words_become_terms(self) -> None:
        assert terms_from_task("Implement member search") == ("Implement", "member", "search")

    def test_punctuation_is_not_a_term(self) -> None:
        assert terms_from_task("Fix the CRLF bug (again)") == (
            "Fix",
            "the",
            "CRLF",
            "bug",
            "again",
        )

    def test_a_single_character_is_dropped(self) -> None:
        # It matches everything and means nothing.
        assert "a" not in terms_from_task("a real one")

    def test_a_repeated_word_appears_once(self) -> None:
        assert terms_from_task("search the search index") == ("search", "the", "index")

    def test_order_is_the_order_it_was_written(self) -> None:
        assert terms_from_task("beta alpha") == ("beta", "alpha")

    def test_nothing_in_is_nothing_out(self) -> None:
        assert terms_from_task("") == ()

    def test_no_stopword_list_is_applied(self) -> None:
        # Terms are OR-ed and ranked by BM25, so a common word costs a low-value
        # match rather than a wrong result. Deciding which words are worthless
        # is a vocabulary decision, and this is the wrong place to make one.
        assert "the" in terms_from_task("the parser")

    def test_it_is_deterministic(self) -> None:
        task = "Implement member search across the directory"
        assert terms_from_task(task) == terms_from_task(task)
