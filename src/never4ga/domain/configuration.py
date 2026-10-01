"""What machine-local configuration says that Never4gA has an opinion about.

A value rather than a setting: :mod:`never4ga.config` reads the file and only
a composition root may do that, but `doctor` has to be able to *report* on
what was found there. So the fact travels as a domain value, handed in as an
argument the way every other injected finding is, and no service ever learns
that a config file exists.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RetiredSetting"]


@dataclass(frozen=True, slots=True)
class RetiredSetting:
    """A configuration key that no longer does anything, and why.

    Dead configuration that *looks* live is worse than none: a reader trusts
    it and diagnoses the wrong thing. Never4gA cannot delete
    somebody's file -- core/05 section 9 says it reads and never writes -- so
    the least it can do is say the key is answering for nothing.
    """

    #: The top-level key or table name as it appears in the file.
    key: str
    #: Why it no longer does anything, naming what retired it.
    reason: str
