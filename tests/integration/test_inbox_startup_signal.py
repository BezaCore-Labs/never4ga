"""A captured thought reaches the next startup, through every surface.

The Inbox exists for one loop: capture now, file it at the next session start.
That needs the startup pack to say the pile is there. These tests run the loop
end to end: capture, start a session, see the signal; file the item, start
another, see silence.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from never4ga.cli import EXIT_OK, main
from never4ga.mcp.toolbox import Toolbox


class Result:
    def __init__(self, code: int, out: str) -> None:
        self.code = code
        self.out = out

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
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "Projects" / "client-repo"
    (root / ".git").mkdir(parents=True)
    return root


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str) -> Result:
        code = main(["--vault", str(vault), "--json", *arguments])
        captured = capsys.readouterr()
        return Result(code, captured.out)

    return invoke


@pytest.fixture
def mapped(run: Run, repository: Path) -> None:
    run("init")
    created = run("workspace", "create", "Client Workspace", "--type", "product")
    run("workspace", "map", str(created.json["id"]), "--repo", str(repository))
    run("index")


def startup_signals(run: Run, repository: Path) -> dict[str, Any]:
    payload = run("context", "startup", "--path", str(repository)).json
    return {signal["kind"]: signal for signal in payload["signals"]}


class TestTheLoop:
    def test_a_capture_is_in_the_next_startup(
        self, run: Run, repository: Path, mapped: None
    ) -> None:
        assert run("capture", "remember to look at the flaky reconcile timing").code == EXIT_OK
        signals = startup_signals(run, repository)
        assert signals["inbox.pending"]["value"] == 1
        items = signals["inbox.items"]["value"]
        assert len(items) == 1
        assert items[0].startswith("00_Inbox/")
        assert "flaky" in items[0]

    def test_a_filed_item_is_gone_from_the_next_one(
        self, run: Run, vault: Path, repository: Path, mapped: None
    ) -> None:
        run("capture", "a passing thought")
        signals = startup_signals(run, repository)
        (item,) = signals["inbox.items"]["value"]
        # Processing: the thought becomes a concept and the capture is filed
        # away -- here, the shortest version: it is simply removed.
        (vault / item).unlink()
        assert "inbox.pending" not in startup_signals(run, repository)

    def test_an_untouched_inbox_says_nothing(
        self, run: Run, repository: Path, mapped: None
    ) -> None:
        assert "inbox.pending" not in startup_signals(run, repository)

    def test_the_toolbox_sees_the_same_pile(
        self, run: Run, vault: Path, repository: Path, mapped: None
    ) -> None:
        # The third root builds its own assembly; parity is the test that
        # notices when one of the three forgets a lane.
        run("capture", "a thought for whoever starts next")
        pack = Toolbox(vault, local=True).context_startup({"cwd": str(repository)})
        by_kind = {signal["kind"]: signal for signal in pack["signals"]}
        assert by_kind["inbox.pending"]["value"] == 1


class TestThroughARunningService:
    """The daemon assembles its own providers; this holds that assembly."""

    def test_the_service_answers_with_the_pile(
        self, run: Run, vault: Path, repository: Path, mapped: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from never4ga.config import LocalConfig, ServiceEndpoint
        from never4ga.platform_paths import PlatformPaths
        from never4ga.service import LocalService, ServiceSettings

        run("capture", "a thought the daemon should hand back")
        service = LocalService(
            ServiceSettings(
                vault=vault,
                endpoint=ServiceEndpoint(port=0),
                quiet_period=0.2,
                reconcile_interval=3600.0,
            ),
            paths=PlatformPaths.resolve(),
        )
        service.start()
        # Spend the deferred full pass now, so it cannot reconcile
        # underneath the test later.
        service.runtime.tick()
        try:
            host, port = service.bound
            endpoint = ServiceEndpoint(host=host, port=port)
            monkeypatch.setattr(
                LocalConfig,
                "load",
                classmethod(lambda cls, path=None: LocalConfig(service=endpoint)),
            )
            signals = startup_signals(run, repository)
            assert signals["inbox.pending"]["value"] == 1
        finally:
            service.stop()
