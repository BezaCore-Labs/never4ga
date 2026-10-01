"""The Domain Registry in a real vault.

What matters end to end: `init` seeds it, curating it changes what validates
clean, losing it falls back to the bootstrap vocabulary, and re-running `init`
never overwrites what the user curated. The registry is theirs (core/02
section 9.1), and a re-init that reset it would throw away exactly the work
section 22 wants the vault to hold.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from never4ga.cli import main
from never4ga.layout import DOMAIN_REGISTRY


class Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err

    @property
    def json(self) -> Any:
        return json.loads(self.out)


Run = Callable[..., Result]


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str, as_json: bool = False) -> Result:
        argv = ["--vault", str(vault), *(["--json"] if as_json else []), *arguments]
        code = main(argv)
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return invoke


@pytest.fixture
def registry_file(vault: Path, run: Run) -> Path:
    run("init")
    return vault / str(DOMAIN_REGISTRY)


def note_with_domain(vault: Path, run: Run, domain: str) -> None:
    run("knowledge", "create", "A note", "--domain", domain)


def issues(run: Run) -> list[str]:
    report = run("validate", as_json=True)
    return [issue["code"] for document in report.json["documents"] for issue in document["issues"]]


class TestInitSeedsTheRegistry:
    def test_init_creates_the_registry_document(self, registry_file: Path) -> None:
        assert registry_file.is_file()

    def test_the_seed_is_the_bootstrap_vocabulary(self, registry_file: Path) -> None:
        text = registry_file.read_text()
        assert "registry: domains" in text
        assert "software_development" in text

    def test_a_fresh_vault_is_healthy_at_strict(self, run: Run, registry_file: Path) -> None:
        result = run("doctor", as_json=True)
        assert result.json["healthy"] is True, result.json
        # Zero findings, not merely zero errors: the registry document itself
        # must be clean at strict, or every fresh vault starts with noise.
        assert result.json["findings"] == [], result.json["findings"]

    def test_reinit_preserves_a_curated_registry(
        self, vault: Path, run: Run, registry_file: Path
    ) -> None:
        curated = registry_file.read_text().replace(
            "- software_development", "- software_development\n  - liturgy"
        )
        registry_file.write_text(curated)
        run("init")
        assert "liturgy" in registry_file.read_text()


class TestTheRegistryGoverns:
    def test_a_bootstrap_domain_validates_clean(
        self, vault: Path, run: Run, registry_file: Path
    ) -> None:
        note_with_domain(vault, run, "software_development")
        assert "unregistered_domain" not in issues(run)

    def test_an_unknown_domain_warns(self, vault: Path, run: Run, registry_file: Path) -> None:
        note_with_domain(vault, run, "cooking")
        assert "unregistered_domain" in issues(run)

    def test_registering_a_domain_clears_the_warning(
        self, vault: Path, run: Run, registry_file: Path
    ) -> None:
        # The whole point: curation is a Markdown edit, not a code change.
        note_with_domain(vault, run, "cooking")
        curated = registry_file.read_text().replace(
            "- software_development", "- software_development\n  - cooking"
        )
        registry_file.write_text(curated)
        assert "unregistered_domain" not in issues(run)

    def test_the_registry_is_authoritative_when_it_shrinks(
        self, vault: Path, run: Run, registry_file: Path
    ) -> None:
        note_with_domain(vault, run, "home_maintenance")
        pruned = registry_file.read_text().replace("  - home_maintenance\n", "")
        registry_file.write_text(pruned)
        assert "unregistered_domain" in issues(run)

    def test_a_lost_registry_falls_back_to_the_bootstrap_values(
        self, vault: Path, run: Run, registry_file: Path
    ) -> None:
        note_with_domain(vault, run, "software_development")
        registry_file.unlink()
        assert "unregistered_domain" not in issues(run)

    def test_a_damaged_registry_reports_and_falls_back(
        self, vault: Path, run: Run, registry_file: Path
    ) -> None:
        text = registry_file.read_text().replace(
            "values:\n  - software_development\n  - artificial_intelligence\n  - home_maintenance",
            "values: broken",
        )
        registry_file.write_text(text)
        note_with_domain(vault, run, "software_development")
        found = issues(run)
        assert "invalid_registry_values" in found
        assert "unregistered_domain" not in found

    def test_doctor_reads_the_registry_too(
        self, vault: Path, run: Run, registry_file: Path
    ) -> None:
        note_with_domain(vault, run, "cooking")
        curated = registry_file.read_text().replace(
            "- software_development", "- software_development\n  - cooking"
        )
        registry_file.write_text(curated)
        result = run("doctor", as_json=True)
        # `healthy` ignores warnings, so assert the finding itself is gone.
        codes = [finding["code"] for finding in result.json["findings"]]
        assert "unregistered_domain" not in codes, codes
