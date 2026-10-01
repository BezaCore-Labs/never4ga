"""A session's own workspace is required reading (core/04 section 17, core/07 section 10).

The workspace page, its parents' pages, every live context document in its
`Context/`, its newest activity log that is not a roll-up, and the standards
that bind every session (core/02 section 21.15). Required reading
is never budgeted away and never cut: it goes inline while the response stays
under the transport limit, and anything that does not fit is listed with its
path on disk to be read in full. Its total is reported against a ceiling in
every startup pack and by `doctor`, and neither cuts anything.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from never4ga.cli import EXIT_OK, main
from never4ga.context.budget import REQUIRED_READING_CEILING, TRANSPORT_LIMIT
from never4ga.mcp.toolbox import Toolbox

Run = Callable[..., tuple[int, str]]

SMALL = "Where Things Stand"
LARGE = "Everything Ever Asked"
LARGE_SIZE = 30_000


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "Projects" / "client-repo"
    (root / ".git").mkdir(parents=True)
    return root


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str, as_json: bool = True) -> tuple[int, str]:
        flags = ["--json"] if as_json else []
        code = main(["--vault", str(vault), *flags, *arguments])
        return code, capsys.readouterr().out

    return invoke


def create(run: Run, vault: Path, *arguments: str, body: str | None = None) -> Path:
    code, out = run("concept", "create", *arguments)
    assert code == EXIT_OK, out
    path = vault / str(json.loads(out)["path"])
    if body is not None:
        path.write_text(path.read_text(encoding="utf-8") + body, encoding="utf-8")
    return path


@pytest.fixture
def workspace(run: Run, vault: Path, repository: Path) -> str:
    run("init")
    _, out = run("workspace", "create", "Client Workspace", "--type", "product")
    workspace_id = str(json.loads(out)["id"])
    run("workspace", "map", workspace_id, "--repo", str(repository))
    create(run, vault, "context", SMALL, "--workspace", workspace_id, body="\nsmall body\n")
    create(run, vault, "context", LARGE, "--workspace", workspace_id, body="\n" + "q" * LARGE_SIZE)
    create(
        run,
        vault,
        "activity_log",
        "The Last Handoff",
        "--workspace",
        workspace_id,
        "--field",
        "occurred_at=2026-09-26T10:00:00Z",
        body="\nwhat the last session left\n",
    )
    create(
        run,
        vault,
        "activity_log",
        "History Roll-up",
        "--workspace",
        workspace_id,
        "--field",
        "occurred_at=2026-09-27T10:00:00Z",
        "--field",
        "covers=2026-08-01/2026-09-27",
        body="\nthe whole history\n",
    )
    create(run, vault, "decision", "A Settled Thing", "--workspace", workspace_id)
    run("index")
    return workspace_id


def startup(run: Run, repository: Path, *extra: str) -> dict[str, Any]:
    code, out = run("context", "startup", "--path", str(repository), *extra)
    assert code == EXIT_OK, out
    payload: dict[str, Any] = json.loads(out)
    return payload


def by_title(pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["title"]: item for item in pack["items"]}


class TestWhatIsRequired:
    def test_the_workspace_and_its_context_documents(
        self, run: Run, repository: Path, workspace: str
    ) -> None:
        items = by_title(startup(run, repository))
        assert items["Client Workspace"]["required"] is True
        assert items[SMALL]["required"] is True
        assert items[LARGE]["required"] is True

    def test_the_newest_log_that_is_not_a_roll_up(
        self, run: Run, repository: Path, workspace: str
    ) -> None:
        items = by_title(startup(run, repository))
        assert items["The Last Handoff"]["required"] is True
        assert items["History Roll-up"]["required"] is False

    def test_a_decision_is_not(self, run: Run, repository: Path, workspace: str) -> None:
        assert by_title(startup(run, repository))["A Settled Thing"]["required"] is False

    def test_a_focused_pack_marks_nothing(self, run: Run, repository: Path, workspace: str) -> None:
        code, out = run("context", "focus", "small", "--path", str(repository))
        assert code == EXIT_OK, out
        assert all(item["required"] is False for item in json.loads(out)["items"])


class TestNothingRequiredIsCut:
    def test_a_required_document_too_large_to_go_inline_is_listed_by_path(
        self, run: Run, vault: Path, repository: Path, workspace: str
    ) -> None:
        large = by_title(startup(run, repository))[LARGE]
        assert large["body"] is None
        assert large["required"] is True
        assert large["size"] >= LARGE_SIZE
        assert Path(large["disk_path"]) == vault / large["path"]
        assert Path(large["disk_path"]).is_file()

    def test_required_reading_is_never_dropped_however_small_the_budget(
        self, run: Run, repository: Path, workspace: str
    ) -> None:
        items = by_title(startup(run, repository, "--max-items", "1", "--max-characters", "1"))
        for title in ("Client Workspace", SMALL, LARGE, "The Last Handoff"):
            assert items[title]["required"] is True

    def test_a_small_required_body_goes_inline(
        self, run: Run, repository: Path, workspace: str
    ) -> None:
        assert "small body" in by_title(startup(run, repository))[SMALL]["body"]


class TestTheTotalIsWatched:
    def test_every_startup_reports_it_against_the_ceiling(
        self, run: Run, repository: Path, workspace: str
    ) -> None:
        pack = startup(run, repository)
        required = [item for item in pack["items"] if item["required"]]
        assert pack["usage"]["required_characters"] == sum(item["size"] for item in required)
        assert pack["usage"]["required_ceiling"] == REQUIRED_READING_CEILING

    def test_the_human_summary_states_it(self, run: Run, repository: Path, workspace: str) -> None:
        _, out = run("context", "startup", "--path", str(repository), as_json=False)
        (line,) = [line for line in out.splitlines() if "required reading:" in line]
        assert f"of {REQUIRED_READING_CEILING:,}" in line

    def test_the_human_summary_says_where_to_read_what_did_not_fit(
        self, run: Run, vault: Path, repository: Path, workspace: str
    ) -> None:
        _, out = run("context", "startup", "--path", str(repository), as_json=False)
        (line,) = [
            line for line in out.splitlines() if line.startswith("  context ") and LARGE in line
        ]
        assert "read in full" in line
        assert str(vault) in line

    def test_doctor_agrees_with_the_pack(
        self, run: Run, repository: Path, workspace: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # One definition of required reading, measured two ways: a ceiling
        # just below the pack's own total must make doctor report it.
        total = startup(run, repository)["usage"]["required_characters"]
        monkeypatch.setattr("never4ga.services.doctor.REQUIRED_READING_CEILING", total - 1)
        _, out = run("doctor")
        codes = [finding["code"] for finding in json.loads(out)["findings"]]
        assert codes.count("required_reading_too_large") == 1

        monkeypatch.setattr("never4ga.services.doctor.REQUIRED_READING_CEILING", total)
        _, out = run("doctor")
        codes = [finding["code"] for finding in json.loads(out)["findings"]]
        assert "required_reading_too_large" not in codes

    def test_covers_is_a_registered_field(self, run: Run, repository: Path, workspace: str) -> None:
        _, out = run("doctor")
        messages = [finding["message"] for finding in json.loads(out)["findings"]]
        assert not any("'covers'" in message for message in messages)


VAULT_RULE = "Commits Name Their Author"
ON_DEMAND_RULE = "How a Brand Is Built"
LOCAL_RULE = "Monthly Reconciliation"
LOCAL_DESCRIPTION = "The routine that keeps the books reconciled each month."
LOCAL_MUST_READ = "Every Release Is Staged First"
EXCEPTION = "This Workspace Commits Under Its Own Name"


def set_frontmatter(path: Path, *lines: str) -> None:
    """Add `lines` to a document's frontmatter, typed as YAML rather than as `--field` text."""
    text = path.read_text(encoding="utf-8")
    head, separator, rest = text.partition("\n---\n")
    path.write_text(head + "\n" + "\n".join(lines) + separator + rest, encoding="utf-8")


@pytest.fixture
def standards(run: Run, vault: Path, workspace: str) -> dict[str, Path]:
    operations = "10_Workspaces/Client-Workspace/Operations"
    made = {
        VAULT_RULE: create(run, vault, "standard", VAULT_RULE, body="\nname the author\n"),
        ON_DEMAND_RULE: create(run, vault, "standard", ON_DEMAND_RULE, body="\nseven gates\n"),
        LOCAL_RULE: create(
            run,
            vault,
            "standard",
            LOCAL_RULE,
            "--workspace",
            workspace,
            "--in",
            operations,
            "--description",
            LOCAL_DESCRIPTION,
            body="\nreconcile\n",
        ),
        LOCAL_MUST_READ: create(
            run,
            vault,
            "standard",
            LOCAL_MUST_READ,
            "--workspace",
            workspace,
            "--in",
            operations,
            "--field",
            "required_reading=true",
            body="\nstage first\n",
        ),
        EXCEPTION: create(
            run,
            vault,
            "standard",
            EXCEPTION,
            "--workspace",
            workspace,
            "--in",
            operations,
            body="\nour own name\n",
        ),
    }
    set_frontmatter(made[ON_DEMAND_RULE], "required_reading: false")
    (vault_rule_id,) = [
        line.removeprefix("id: ")
        for line in made[VAULT_RULE].read_text(encoding="utf-8").splitlines()
        if line.startswith("id: ")
    ]
    set_frontmatter(
        made[EXCEPTION], "relations:", "  - type: excepts", f"    target: {vault_rule_id}"
    )
    run("index")
    return made


class TestStandardsAreRequiredReading:
    """core/07 section 10 and core/02 section 21.15."""

    def test_a_vault_wide_standard_is_required(
        self, run: Run, repository: Path, standards: dict[str, Path]
    ) -> None:
        assert by_title(startup(run, repository))[VAULT_RULE]["required"] is True

    def test_a_vault_wide_standard_can_be_read_on_demand(
        self, run: Run, repository: Path, standards: dict[str, Path]
    ) -> None:
        item = by_title(startup(run, repository))[ON_DEMAND_RULE]
        assert item["required"] is False

    def test_a_workspace_standard_is_listed_with_its_description(
        self, run: Run, repository: Path, standards: dict[str, Path]
    ) -> None:
        item = by_title(startup(run, repository))[LOCAL_RULE]
        assert item["required"] is False
        assert item["description"] == LOCAL_DESCRIPTION

    def test_a_workspace_standard_can_be_required(
        self, run: Run, repository: Path, standards: dict[str, Path]
    ) -> None:
        assert by_title(startup(run, repository))[LOCAL_MUST_READ]["required"] is True

    def test_an_exception_to_a_required_standard_is_required(
        self, run: Run, repository: Path, standards: dict[str, Path]
    ) -> None:
        assert by_title(startup(run, repository))[EXCEPTION]["required"] is True

    def test_a_listed_standard_says_what_it_governs(
        self, run: Run, repository: Path, standards: dict[str, Path]
    ) -> None:
        _, out = run(
            "context", "startup", "--path", str(repository), "--max-characters", "1", as_json=False
        )
        lines = out.splitlines()
        (at,) = [
            n
            for n, line in enumerate(lines)
            if line.startswith("  standard ") and LOCAL_RULE in line
        ]
        assert lines[at + 1].strip() == LOCAL_DESCRIPTION

    def test_a_full_room_does_not_cut_a_required_standard(
        self, run: Run, vault: Path, repository: Path, workspace: str, standards: dict[str, Path]
    ) -> None:
        # Required reading alone fills the room for bodies. The vault-wide
        # rule is then read from its file, never left as a budget casualty.
        for number in range(6):
            create(
                run,
                vault,
                "context",
                f"Context Document {number}",
                "--workspace",
                workspace,
                body="\n" + "c" * 2_500,
            )
        run("index")
        _, out = run("context", "startup", "--path", str(repository), as_json=False)
        (line,) = [
            line
            for line in out.splitlines()
            if line.startswith("  standard ") and VAULT_RULE in line
        ]
        assert "[required" in line
        assert "budget was spent" not in line

    def test_doctor_counts_them(
        self,
        run: Run,
        repository: Path,
        standards: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        total = startup(run, repository)["usage"]["required_characters"]
        monkeypatch.setattr("never4ga.services.doctor.REQUIRED_READING_CEILING", total - 1)
        _, out = run("doctor")
        codes = [finding["code"] for finding in json.loads(out)["findings"]]
        assert codes.count("required_reading_too_large") == 1

        monkeypatch.setattr("never4ga.services.doctor.REQUIRED_READING_CEILING", total)
        _, out = run("doctor")
        codes = [finding["code"] for finding in json.loads(out)["findings"]]
        assert "required_reading_too_large" not in codes


class TestTheResponseFitsItsTransport:
    def test_a_crowded_pack_stays_under_the_limit(
        self, run: Run, vault: Path, repository: Path, workspace: str
    ) -> None:
        # The index is part of the response: forty long-titled decisions and a
        # large required document must still leave the whole of it under the
        # limit, with what did not fit listed by path rather than cut.
        for number in range(40):
            create(
                run,
                vault,
                "decision",
                f"ADR-{number + 1:04d} — A Decision With a Title as Long as the Real Ones Get",
                "--workspace",
                workspace,
                body="\n" + "d" * 2_000,
            )
        run("index")
        _, out = run("context", "startup", "--path", str(repository), as_json=False)
        assert len(out) <= TRANSPORT_LIMIT
        assert "read in full" in out


class TestEveryAgentSurface:
    def test_the_toolbox_carries_required_and_the_path_on_disk(
        self, vault: Path, repository: Path, workspace: str
    ) -> None:
        pack = Toolbox(vault, local=True).context_startup({"cwd": str(repository)})
        (large,) = [item for item in pack["items"] if item["title"] == LARGE]
        assert large["required"] is True
        assert Path(large["disk_path"]).is_file()


class TestTheSkillsDescribeTheLiveCheck:
    def test_no_shipped_skill_names_the_retired_code(self) -> None:
        # `context_too_large` is retired. A Skill that still names it tells
        # agents doctor reports it, and the Skill is what an agent obeys.
        from never4ga.services.scaffold import SKILLS

        assert not [name for name, text in SKILLS.items() if "context_too_large" in text]

    def test_the_wrap_skill_names_the_one_that_replaced_it(self) -> None:
        from never4ga.services.scaffold import SKILLS

        assert "required_reading_too_large" in SKILLS["never4ga-wrap"]
