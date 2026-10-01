"""`never4ga connection list|health|set-token`, and `context focus --ticket`.

Specification:
- core/03 section 16 -- the canonical half is in the vault, the secret half
  never is, and the two are joined by name.
- details/openproject-adapter.md section 12 -- capabilities come from the
  instance, not from a hardcoded list.
- details/security-configuration.md section 4 -- a token is never echoed,
  logged, or written where the vault can see it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from never4ga.cli import EXIT_FAILED, EXIT_OK, main
from never4ga.domain.connections import Connection
from never4ga.platform_paths import PlatformPaths
from never4ga.services.connections import secret_ref_for

Run = Callable[..., "Result"]

CONNECTION = "work_openproject"


class Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err

    @property
    def json(self) -> Any:
        return json.loads(self.out or self.err)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str, as_json: bool = False) -> Result:
        code = main(["--vault", str(vault), *(["--json"] if as_json else []), *arguments])
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return invoke


def define_connection(vault: Path, *, provider: str = "openproject", **extra: str) -> None:
    """Write the canonical half the way a person would: by saving a file."""
    from never4ga.domain.identity import ConceptId

    folder = vault / "50_System" / "Integrations"
    folder.mkdir(parents=True, exist_ok=True)
    fields = {
        "type": "integration",
        "id": str(ConceptId.new()),
        "schema": "never4ga/0.1",
        "title": "Work OpenProject",
        "created_at": "2026-08-26T09:00:00Z",
        "connection": CONNECTION,
        "provider": provider,
        "base_url": "https://openproject.invalid",
        "project_ref": "never4ga",
        **extra,
    }
    body = "\n".join(f"{key}: {json.dumps(value)}" for key, value in fields.items())
    (folder / f"{CONNECTION}.md").write_text(
        f"---\n{body}\n---\n\n# Work OpenProject\n\n## Credentials\n\nIn the secret store.\n",
        encoding="utf-8",
    )


def stored_token(name: str = CONNECTION, provider: str = "openproject") -> str | None:
    from never4ga.adapters.filesystem import LocalSecretFileStore

    return LocalSecretFileStore(PlatformPaths.resolve().secrets_file).get(
        secret_ref_for(Connection(name=name, provider=provider))
    )


class TestListing:
    def test_an_empty_vault_lists_nothing(self, run: Run) -> None:
        run("init")
        result = run("connection", "list", as_json=True)
        assert result.code == EXIT_OK
        assert result.json["connections"] == []

    def test_it_says_whether_this_machine_holds_the_token(self, run: Run, vault: Path) -> None:
        # Whether, never what. The boolean is safe in a log and in an agent's
        # context; the value it stands for is not.
        run("init")
        define_connection(vault)
        listed = run("connection", "list", as_json=True).json["connections"]
        assert listed[0]["connection"] == CONNECTION
        assert listed[0]["token_stored"] is False
        assert listed[0]["adapter"] is True

    def test_a_provider_with_no_adapter_is_reported_as_such(self, run: Run, vault: Path) -> None:
        run("init")
        define_connection(vault, provider="jira")
        listed = run("connection", "list", as_json=True).json["connections"]
        assert listed[0]["adapter"] is False

    def test_a_leaked_secret_is_a_finding_rather_than_a_repair(self, run: Run, vault: Path) -> None:
        # core/02: validation reports; it never rewrites what a person wrote.
        run("init")
        define_connection(vault, api_token="hunter2")
        payload = run("connection", "list", as_json=True).json
        assert [finding["code"] for finding in payload["findings"]] == [
            "connection.secret_in_vault"
        ]
        assert "hunter2" not in json.dumps(payload)


class TestSettingATokenSafely:
    def test_a_piped_token_is_stored_under_its_name(
        self, run: Run, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run("init")
        define_connection(vault)
        monkeypatch.setattr("sys.stdin", _Pipe("a-real-looking-token\n"))

        result = run("connection", "set-token", CONNECTION, as_json=True)
        assert result.code == EXIT_OK
        assert result.json["secret"] == f"never4ga.connection.{CONNECTION}"
        assert stored_token() == "a-real-looking-token"

    def test_the_token_is_never_printed(
        self, run: Run, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run("init")
        define_connection(vault)
        monkeypatch.setattr("sys.stdin", _Pipe("hunter2"))
        result = run("connection", "set-token", CONNECTION)
        assert "hunter2" not in result.out
        assert "hunter2" not in result.err

    def test_it_never_reaches_the_vault(
        self, run: Run, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # core/03 section 16, checked the blunt way: read every file.
        run("init")
        define_connection(vault)
        monkeypatch.setattr("sys.stdin", _Pipe("hunter2"))
        run("connection", "set-token", CONNECTION)
        for path in vault.rglob("*"):
            if path.is_file():
                assert "hunter2" not in path.read_text(encoding="utf-8", errors="ignore")

    def test_an_empty_token_stores_nothing(
        self, run: Run, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run("init")
        define_connection(vault)
        monkeypatch.setattr("sys.stdin", _Pipe("   \n"))
        assert run("connection", "set-token", CONNECTION).code == EXIT_FAILED
        assert stored_token() is None

    def test_an_undefined_connection_is_refused(self, run: Run) -> None:
        run("init")
        result = run("connection", "set-token", "nowhere", as_json=True)
        assert result.code == EXIT_FAILED
        assert result.json["error"]["code"] == "connection_undefined"


class TestHealth:
    def test_a_connection_with_no_token_says_so(self, run: Run, vault: Path) -> None:
        run("init")
        define_connection(vault)
        result = run("connection", "health", CONNECTION, as_json=True)
        assert result.code == EXIT_FAILED
        assert result.json["error"]["code"] == "connection_token_missing"
        assert CONNECTION in result.json["error"]["repair_hint"]

    def test_an_unreachable_instance_reports_rather_than_raises(
        self, run: Run, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run("init")
        define_connection(vault)
        monkeypatch.setattr("sys.stdin", _Pipe("token"))
        run("connection", "set-token", CONNECTION)

        result = run("connection", "health", CONNECTION, as_json=True)
        assert result.code == EXIT_FAILED
        assert result.json["available"] is False
        assert result.json["capabilities"] == []
        assert "token" not in result.json["detail"]

    def test_a_provider_without_an_adapter_is_refused(self, run: Run, vault: Path) -> None:
        run("init")
        define_connection(vault, provider="jira")
        result = run("connection", "health", CONNECTION, as_json=True)
        assert result.json["error"]["code"] in {
            "connection_token_missing",
            "connection_unsupported",
        }


class TestFocusOnATicket:
    def test_a_focused_pack_needs_terms_or_a_ticket(self, run: Run) -> None:
        run("init")
        result = run("context", "focus", as_json=True)
        assert result.code == EXIT_FAILED
        assert result.json["error"]["code"] == "nothing_to_focus_on"

    def test_a_ticket_alone_is_enough(self, run: Run, vault: Path, tmp_path: Path) -> None:
        # `--ticket` is accepted with or without terms.
        run("init")
        created = run("workspace", "create", "Example", "--type", "product", as_json=True)
        repository = tmp_path / "repo"
        (repository / ".git").mkdir(parents=True)
        run("workspace", "map", str(created.json["id"]), "--repo", str(repository))
        run("index")

        result = run("context", "focus", "--ticket", "838", "--cwd", str(repository), as_json=True)
        # No tracker is declared and no terms were given, so the pack is
        # empty -- but it assembles, which is what "with or without terms"
        # means. Refusing the arguments is what must not happen.
        assert result.code == EXIT_OK
        assert result.json["depth"] == "focused"


class _Pipe:
    """A stdin that is not a terminal, so the token is read rather than prompted."""

    def __init__(self, text: str) -> None:
        self._text = text

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return self._text
