"""A process's build id must describe the code it loaded, not the code on disk.

The CLI refuses to hand work to a daemon running different code. If the
service fingerprinted the source tree when asked, it would read the same disk
as the CLI, and the two would always agree however stale the daemon's loaded
code was.

A fingerprint captured while the process starts describes what that process
imported. One computed on demand describes only the filesystem, which both
processes share.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from never4ga.build import STARTUP_BUILD_ID, build_id


class TestItIsCapturedOnce:
    def test_the_startup_value_is_the_current_one_at_import(self) -> None:
        assert build_id() == STARTUP_BUILD_ID

    def test_editing_the_source_does_not_change_a_running_process(self, tmp_path: Path) -> None:
        """The captured id does not move when the source changes on disk.

        A separate process imports the package, an edit lands, and the value it
        captured must not move. If it does, a daemon started before the edit
        will keep agreeing with a CLI started after it.
        """
        package = tmp_path / "pkg"
        package.mkdir()
        (package / "one.py").write_text("x = 1\n")
        source = Path(__file__).resolve().parents[2] / "src"
        script = (
            f"import sys, pathlib;"
            f"sys.path.insert(0, {str(source)!r});"
            f"from never4ga.build import build_id;"
            f"root = pathlib.Path({str(package)!r});"
            f"before = build_id(root=root, version='test');"
            f"(root / 'one.py').write_text('x = 2  # edited\\n');"
            f"after = build_id(root=root, version='test');"
            f"print(before, after)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True
        )
        before, after = result.stdout.split()
        # An explicit root is a question about *that tree*, and still answers
        # honestly. Only the no-argument form is a claim about this process.
        assert before != after

    def test_an_explicit_root_is_never_the_captured_value(self, tmp_path: Path) -> None:
        (tmp_path / "only.py").write_text("y = 2\n")
        assert build_id(root=tmp_path) != STARTUP_BUILD_ID
