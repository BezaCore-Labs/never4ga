"""Never4gA -- Markdown-first, local-first personal knowledge and context system."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

#: The installed package's version. `pyproject.toml` is the one place it is
#: written; everything that reports a version reads it from here. A source
#: tree imported without being installed has no metadata to read.
try:
    __version__ = version("never4ga")
except PackageNotFoundError:
    __version__ = "0+unknown"
