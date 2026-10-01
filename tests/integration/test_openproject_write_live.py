"""The OpenProject writer against a real instance. Opt-in, and it changes things.

These are the only tests in the suite that write to an external system, and
they are separated from the read-only live tests so that running one cannot
mean running the other by accident.

**They write only to a scratch project.** The token authenticates as its
owner, so every test write is indistinguishable from the owner having made it.
Pointing these at a real project would fill its tracker with test noise
attributed to a person, in the one system where that record is the audit trail.

The project is **made for the run and destroyed with it**, so there is no
standing project to accumulate and none to configure. Running this file needs a
token that may create and delete projects, which is more than writing work
packages into an existing one.

```bash
NEVER4GA_LIVE_OPENPROJECT=https://openproject.example \\
NEVER4GA_LIVE_OPENPROJECT_TOKEN_FILE=~/.op-token \\
    .venv/bin/pytest tests/integration/test_openproject_write_live.py -m live_write
```

What these prove that the fixtures cannot is that the instance still *misbehaves
the way the fixtures assume*. Every hazard the write path is built around is an
absence or a status line rather than a body: "an unknown field is ignored" is a
thing that does not happen, and a captured response cannot record it.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from never4ga.adapters.openproject import OpenProjectWriter
from never4ga.adapters.openproject.api import API_ROOT, OpenProjectApi
from never4ga.domain.capabilities import WorkManagementCapability
from never4ga.domain.identity import ExternalId
from never4ga.errors import WriteConflictError, WriteRejectedError

pytestmark = [
    pytest.mark.live_write,
    pytest.mark.skipif(
        not os.environ.get("NEVER4GA_LIVE_OPENPROJECT"),
        reason="set NEVER4GA_LIVE_OPENPROJECT to write to a real instance",
    ),
]


def _token() -> str:
    token_file = os.environ.get("NEVER4GA_LIVE_OPENPROJECT_TOKEN_FILE")
    if not token_file:
        pytest.skip("set NEVER4GA_LIVE_OPENPROJECT_TOKEN_FILE to the token's path")
    return Path(token_file).expanduser().read_text(encoding="utf-8").strip()


@pytest.fixture(scope="module")
def scratch(request: pytest.FixtureRequest) -> Iterator[str]:
    """A project made for this run and destroyed with it.

    These tests must never write into a real project. A standing scratch
    project would also accumulate work packages from every run, because
    nothing deletes them. A project made for the run leaves nothing to clean
    up. Its identifier is generated here rather than configured, so a typo in
    an environment variable cannot point the suite at a real project.

    Creating a project needs more privilege than writing work packages into
    one, so a token that can only do the latter cannot run this file.

    On OpenProject 16.x, create answers 201, delete answers 204, and a project
    holding work packages is fully gone within ten seconds.
    """
    api = OpenProjectApi(
        base_url=os.environ["NEVER4GA_LIVE_OPENPROJECT"],
        token=_token(),
    )
    identifier = f"n4g-live-{os.getpid()}-{int(time.time())}"
    with closing(api):
        api.post(
            f"{API_ROOT}/projects",
            {"identifier": identifier, "name": f"Never4gA live write run {identifier}"},
        )
        try:
            yield identifier
        finally:
            # Deletion is a background job when the project holds anything, so
            # this returns before the project is gone. Nothing waits on it: the
            # next run makes its own project and never looks for this one.
            api._client.delete(f"{API_ROOT}/projects/{identifier}")


@pytest.fixture(scope="module")
def writer(scratch: str) -> Iterator[OpenProjectWriter]:
    built = OpenProjectWriter(
        base_url=os.environ["NEVER4GA_LIVE_OPENPROJECT"],
        token=_token(),
        connection="live",
        project_ref=scratch,
    )
    with closing(built):
        yield built


@pytest.fixture
def item(writer: OpenProjectWriter, scratch: str) -> ExternalId:
    """A work package of this test's own, made fresh so nothing shares state."""
    # Created through the raw API rather than the writer, so a test of the
    # update path does not depend on the creation path.
    api = writer._api
    types = api.collection(f"{API_ROOT}/types")
    task = next(one for one in types if one["name"] == "Task")
    made = api.post(
        f"{API_ROOT}/projects/{scratch}/work_packages",
        {
            "subject": "live write test",
            "_links": {"type": {"href": task["_links"]["self"]["href"]}},
        },
    )
    return ExternalId(provider="openproject", value=str(made["id"]))


def test_the_token_may_write_here(writer: OpenProjectWriter, item: ExternalId) -> None:
    """The other half of the reader's capability test.

    `capabilities` is the intersection of what the token may do with what the
    class can do. The reader reports no write on this same instance; a writer
    reports all three, and only a live run can show that the difference is the
    class rather than the instance.
    """
    found = writer.capabilities
    assert WorkManagementCapability.UPDATE_WORK_ITEM in found
    assert WorkManagementCapability.CREATE_WORK_ITEM in found
    assert WorkManagementCapability.COMMENT_WORK_ITEM in found


def test_a_proposal_reads_the_current_state_and_changes_nothing(
    writer: OpenProjectWriter, item: ExternalId
) -> None:
    proposal = writer.propose_update(item, {"title": "proposed, not applied"})
    assert proposal.lock_version is not None
    still = writer.get_work_item(item)
    assert still is not None
    assert still.title == "live write test"


def test_a_write_takes_and_is_verified_against_the_answer(
    writer: OpenProjectWriter, item: ExternalId
) -> None:
    result = writer.apply(writer.propose_update(item, {"title": "changed by the writer"}))
    assert result.item is not None
    assert result.item.title == "changed by the writer"
    # The PATCH response is the read-back. This asserts it agrees with an
    # independent GET, which is the reason there is no second request in the
    # write path.
    read_again = writer.get_work_item(item)
    assert read_again is not None
    assert read_again.title == result.item.title


def test_a_status_change_resolves_a_name_to_a_link(
    writer: OpenProjectWriter, item: ExternalId
) -> None:
    result = writer.apply(writer.propose_update(item, {"status": "Closed"}))
    assert result.item is not None
    assert result.item.status == "Closed"


def test_a_replayed_proposal_is_refused_rather_than_overwriting(
    writer: OpenProjectWriter, item: ExternalId
) -> None:
    proposal = writer.propose_update(item, {"title": "first"})
    writer.apply(proposal)
    with pytest.raises(WriteConflictError):
        writer.apply(proposal)


def test_an_unknown_field_name_is_refused_before_it_can_be_ignored(
    writer: OpenProjectWriter, item: ExternalId
) -> None:
    # The hazard, end to end: without the schema check this would be a
    # 200 that changed nothing, which no caller could tell from success.
    with pytest.raises(WriteRejectedError, match="thisFieldDoesNotExist"):
        writer.propose_update(item, {"thisFieldDoesNotExist": "x"})


def test_a_value_the_instance_refuses_is_caught_by_the_form(
    writer: OpenProjectWriter, item: ExternalId
) -> None:
    with pytest.raises(WriteRejectedError):
        writer.propose_update(item, {"title": ""})


def test_the_instance_still_ignores_an_unknown_field(
    writer: OpenProjectWriter, item: ExternalId
) -> None:
    """The assumption the whole write path rests on, asserted directly.

    If OpenProject ever starts rejecting unknown fields, this fails and the
    schema check becomes belt-and-braces rather than load-bearing. That is
    worth knowing, and no fixture can tell us.
    """
    api = writer._api
    before = api.get(f"{API_ROOT}/work_packages/{item.value}")
    answered = api.patch(
        f"{API_ROOT}/work_packages/{item.value}",
        {"lockVersion": before["lockVersion"], "thisFieldDoesNotExist": "ignored in silence"},
    )
    assert answered["lockVersion"] == before["lockVersion"], "a no-op write bumped the version"
    assert "thisFieldDoesNotExist" not in answered


def test_a_patch_answers_with_the_whole_resource(
    writer: OpenProjectWriter, item: ExternalId
) -> None:
    """The PATCH response carries the whole resource.

    The write path verifies against the PATCH response rather than a second GET
    because of this. If it stops holding, `divergences` starts reporting absent
    fields and the write path fails loudly rather than silently, but this is
    where the reason would show up first.
    """
    api = writer._api
    before = api.get(f"{API_ROOT}/work_packages/{item.value}")
    answered = api.patch(
        f"{API_ROOT}/work_packages/{item.value}",
        {"lockVersion": before["lockVersion"], "subject": "whole resource check"},
    )
    assert set(before) == set(answered)
    assert answered["_type"] == "WorkPackage"


def test_creating_a_work_item_end_to_end(writer: OpenProjectWriter, scratch: str) -> None:
    proposal = writer.propose_create(scratch, {"title": "created by the live suite"})
    result = writer.apply(proposal)
    assert result.item is not None
    assert result.item.title == "created by the live suite"
    # The instance defaults a type rather than refusing a creation without one.
    # If that changes, this is where it shows.
    assert result.item.extra.get("type")
    assert writer.get_work_item(result.item.ref) is not None


def test_the_creation_form_needs_no_version(writer: OpenProjectWriter, scratch: str) -> None:
    """The asymmetry between the two forms, asserted rather than assumed.

    The *update* form answers 409 without a fresh lockVersion. The *creation*
    form answers 200 for an empty body, because there is no version to be stale
    about yet. That is why `propose_create` makes one request where
    `propose_update` makes two.
    """
    form = writer._api.post(f"{API_ROOT}/projects/{scratch}/work_packages/form", {})
    assert form["_embedded"]["schema"]
    assert form["_embedded"]["payload"]["_links"]["type"]["title"]


def test_a_creation_the_instance_would_refuse_is_caught_first(
    writer: OpenProjectWriter, scratch: str
) -> None:
    with pytest.raises(WriteRejectedError):
        writer.propose_create(scratch, {"title": ""})


def test_a_created_item_can_be_given_a_parent(writer: OpenProjectWriter, scratch: str) -> None:
    """Hierarchy against the real instance, which the fixtures cannot prove.

    Without a parent, an item the adapter creates lands flat and the series it
    belongs to has to be rebuilt by hand. This checks that the link is accepted
    and comes back attached.
    """
    parent = writer.apply(writer.propose_create(scratch, {"title": "live suite parent"}))
    assert parent.item is not None
    child = writer.apply(
        writer.propose_create(
            scratch, {"title": "live suite child", "parent": parent.item.ref.value}
        )
    )
    assert child.item is not None
    # `extra["parent"]` is an ExternalId, not a bare id (normalise._reference).
    assert child.item.extra.get("parent") == parent.item.ref


def test_a_parent_can_be_detached_again(writer: OpenProjectWriter, scratch: str) -> None:
    """The other half of hierarchy. Attach-only would be write-once."""
    parent = writer.apply(writer.propose_create(scratch, {"title": "live suite detach parent"}))
    assert parent.item is not None
    child = writer.apply(
        writer.propose_create(
            scratch, {"title": "live suite detach child", "parent": parent.item.ref.value}
        )
    )
    assert child.item is not None and child.item.extra.get("parent") == parent.item.ref
    detached = writer.apply(writer.propose_update(child.item.ref, {"parent": ""}))
    assert detached.item is not None
    assert detached.item.extra.get("parent") is None


def test_a_parent_id_the_instance_does_not_have_is_refused(
    writer: OpenProjectWriter, scratch: str
) -> None:
    # Resolution reads the item, so a missing id fails before anything is sent.
    with pytest.raises(WriteRejectedError, match="parent"):
        writer.propose_create(scratch, {"title": "never created", "parent": "999999999"})


def test_a_comment_is_posted_and_comes_back_identified(
    writer: OpenProjectWriter, item: ExternalId
) -> None:
    result = writer.apply(writer.propose_comment(item, "posted by the live suite"))
    assert result.activity is not None
    assert result.item is None


def test_a_comment_can_be_replaced_rather_than_repeated(
    writer: OpenProjectWriter, item: ExternalId
) -> None:
    """Replacing a comment rather than adding another works on the real instance."""
    first = writer.apply(writer.propose_comment(item, "first account"))
    assert first.activity is not None
    second = writer.apply(writer.propose_comment(item, "corrected account", amends=first.activity))
    assert second.activity == first.activity
    reread = writer._api.get(f"{API_ROOT}/activities/{first.activity.value}")
    assert reread["comment"]["raw"] == "corrected account"


def test_editing_a_comment_still_refuses_the_object_that_created_it(
    writer: OpenProjectWriter, item: ExternalId
) -> None:
    """The asymmetry, asserted against the instance rather than assumed.

    If OpenProject ever accepts the formattable object here, this fails and the
    adapter's bare string can stop being a special case. Until then it is
    load-bearing, and a `400` reading "comment is invalid" is what a caller
    would otherwise be left to interpret.
    """
    made = writer.apply(writer.propose_comment(item, "original"))
    assert made.activity is not None
    with pytest.raises(WriteRejectedError, match="comment is invalid"):
        writer._api.patch(
            f"{API_ROOT}/activities/{made.activity.value}",
            {"comment": {"raw": "the object form"}},
        )


def test_a_comment_does_not_ride_inside_a_field_change(
    writer: OpenProjectWriter, item: ExternalId
) -> None:
    """A field change cannot carry a comment, asserted end to end.

    A `comment` key inside a PATCH answers 200 and is dropped in silence. The
    adapter refuses it outright, so a caller cannot reach the silence.
    """
    with pytest.raises(WriteRejectedError, match="its own request"):
        writer.propose_update(item, {"comment": "this would vanish"})


def test_everything_written_lives_in_the_scratch_project(
    writer: OpenProjectWriter, item: ExternalId, scratch: str
) -> None:
    """The guard that makes the rest of this file safe to run.

    A misconfigured `project_ref` would put test noise into a real project
    under a real person's name, and nothing else here would notice.
    """
    found = writer.get_work_item(item)
    assert found is not None
    assert found.extra["project_ref"] == scratch
    assert found.url is not None
