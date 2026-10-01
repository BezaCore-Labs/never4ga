"""The OpenProject adapter -- the first WorkManagementProvider.

`details/openproject-adapter.md` section 1 draws the boundary this package
exists to hold: **Never4gA core code must not depend on OpenProject-specific
concepts outside it.** Everything OpenProject-shaped -- API v3 paths, HAL
`_links`, `lockVersion`, the filter grammar, the vocabulary of statuses and
types -- stops here, and what leaves is a
:class:`~never4ga.ports.work_management.WorkItem`. A second provider (section
14 names Jira and Linear) is another package beside this one, not an edit to
anything above it.

The boundary is enforced rather than described:
`tests/architecture/test_layering.py` fails if any module outside this package
names OpenProject in code.

Reads and writes. :class:`OpenProjectProvider` reads and structurally cannot
write; :class:`OpenProjectWriter` extends it with the write half, gated by
`core/03` section 22's policy and verified against what the provider answers
rather than against a status line.
"""

from __future__ import annotations

from never4ga.adapters.openproject.api import OpenProjectApi
from never4ga.adapters.openproject.provider import OpenProjectProvider, ProjectSummary
from never4ga.adapters.openproject.writer import OpenProjectWriter

__all__ = [
    "OpenProjectApi",
    "OpenProjectProvider",
    "OpenProjectWriter",
    "ProjectSummary",
]
