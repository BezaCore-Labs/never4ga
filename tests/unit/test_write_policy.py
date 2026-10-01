"""The two gates a tracker write passes before anything is sent.

Specification:
- core/03 section 15 -- `mode` and `sync_policy` on the workspace declaration;
  `read_write` is the only policy that permits writing, and "for the first
  implementation, `read` SHOULD precede `read_write`".
- core/03 section 22 -- Never4gA MUST distinguish read operations,
  draft/proposed writes, and actual writes.

Two gates: the workspace policy, and `--apply` on the invocation. Without
`--apply` a verb proposes and sends nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from never4ga.domain.work_policy import (
    INGESTING_POLICIES,
    WRITING_POLICIES,
    WriteDisposition,
    WritePolicy,
)
from never4ga.errors import WriteNotPermittedError


def declaration(**overrides: Any) -> dict[str, Any]:
    declared: dict[str, Any] = {
        "mode": "external",
        "connection": "work_openproject",
        "provider": "openproject",
        "project_ref": "never4ga",
        "sync_policy": "read_write",
    }
    declared.update(overrides)
    return declared


class TestDisposition:
    def test_read_write_and_applying_applies(self) -> None:
        policy = WritePolicy(mode="external", sync_policy="read_write", applying=True)
        assert policy.disposition is WriteDisposition.APPLY

    def test_read_write_without_applying_proposes(self) -> None:
        # core/03 section 22's draft state. The gate is open; this invocation
        # did not ask to go through it.
        policy = WritePolicy(mode="external", sync_policy="read_write")
        assert policy.disposition is WriteDisposition.PROPOSE

    @pytest.mark.parametrize("sync_policy", ["read", "reference"])
    def test_a_policy_short_of_read_write_refuses(self, sync_policy: str) -> None:
        policy = WritePolicy(mode="external", sync_policy=sync_policy, applying=True)
        assert policy.disposition is WriteDisposition.REFUSE

    @pytest.mark.parametrize("mode", ["native", "none", ""])
    def test_no_external_tracker_refuses_whatever_the_policy_says(self, mode: str) -> None:
        # `native` means Never4gA's own Tasks/ is authoritative and `none` means
        # there is no work management at all. Neither has a tracker to write to,
        # so a `read_write` policy beside them is a contradiction rather than a
        # permission.
        policy = WritePolicy(mode=mode, sync_policy="read_write", applying=True)
        assert policy.disposition is WriteDisposition.REFUSE

    def test_an_unknown_policy_fails_closed(self) -> None:
        # A policy nobody recognises is not a policy that permits. Deliberate:
        # a typo in a vault file must not become write permission, and casing
        # is part of that -- `Read_Write` is not `read_write`.
        for value in ("readwrite", "Read_Write", "write", "yes"):
            policy = WritePolicy(mode="external", sync_policy=value, applying=True)
            assert policy.disposition is WriteDisposition.REFUSE

    def test_surrounding_whitespace_is_tolerated(self) -> None:
        policy = WritePolicy(mode=" external ", sync_policy=" read_write ", applying=True)
        assert policy.disposition is WriteDisposition.APPLY


class TestFromDeclaration:
    def test_reads_the_workspace_declaration(self) -> None:
        policy = WritePolicy.from_declaration(declaration(), applying=True)
        assert policy.disposition is WriteDisposition.APPLY

    def test_an_absent_declaration_refuses(self) -> None:
        assert WritePolicy.from_declaration(None, applying=True).disposition is (
            WriteDisposition.REFUSE
        )

    def test_an_absent_sync_policy_is_read_rather_than_permission(self) -> None:
        # The rule services/work_signals.py already applies to reads: core/03
        # section 15 lists `reference` first and calls `read` the safe default,
        # so an undeclared policy is never permission to do more.
        declared = declaration()
        del declared["sync_policy"]
        policy = WritePolicy.from_declaration(declared, applying=True)
        assert policy.sync_policy == "read"
        assert policy.disposition is WriteDisposition.REFUSE

    def test_a_declaration_that_is_not_a_mapping_refuses(self) -> None:
        assert WritePolicy.from_declaration("read_write", applying=True).disposition is (  # type: ignore[arg-type]
            WriteDisposition.REFUSE
        )


class TestRequirePermitted:
    def test_a_refusal_names_the_policy_and_what_was_refused(self) -> None:
        policy = WritePolicy(mode="external", sync_policy="read", applying=True)
        with pytest.raises(WriteNotPermittedError) as raised:
            policy.require_permitted("comment on work item 838")
        message = str(raised.value)
        assert "comment on work item 838" in message
        assert "read" in message
        assert "read_write" in message

    def test_proposing_is_permitted(self) -> None:
        # A proposal is not a write, so the gate does not stand in front of it.
        WritePolicy(mode="external", sync_policy="read_write").require_permitted("update 838")

    def test_applying_is_permitted(self) -> None:
        WritePolicy(mode="external", sync_policy="read_write", applying=True).require_permitted(
            "update 838"
        )

    def test_no_tracker_says_so_rather_than_blaming_the_policy(self) -> None:
        policy = WritePolicy(mode="native", sync_policy="read_write", applying=True)
        with pytest.raises(WriteNotPermittedError) as raised:
            policy.require_permitted("update 838")
        assert "native" in str(raised.value)


class TestVocabularies:
    def test_writing_is_a_subset_of_ingesting(self) -> None:
        # Anything permitted to write is permitted to read. The reverse is the
        # whole point of the two being different sets.
        assert WRITING_POLICIES < INGESTING_POLICIES

    def test_read_write_is_the_only_writing_policy(self) -> None:
        assert frozenset({"read_write"}) == WRITING_POLICIES


class TestValue:
    def test_is_frozen(self) -> None:
        policy = WritePolicy(mode="external", sync_policy="read_write")
        with pytest.raises(AttributeError):
            policy.applying = True  # type: ignore[misc]

    def test_defaults_refuse(self) -> None:
        # A policy nobody configured is not a policy that permits.
        assert WritePolicy().disposition is WriteDisposition.REFUSE
