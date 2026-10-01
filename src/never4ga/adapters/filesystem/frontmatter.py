"""Markdown + YAML frontmatter codec.

A canonical Never4gA document is a YAML mapping between ``---`` fences followed
by a Markdown body (core/02 section 5.1). This module is the single place that
knows YAML.

Two properties matter more than convenience, because Never4gA is never the only
writer of these files:

**Nothing is silently retyped.** core/02 section 7.4 warns that unquoted
timestamps are parser-dependent. ruamel's round-trip loader resolves a bare
``2026-08-22T19:00:00Z`` into a ``TimeStamp`` and re-emits it without the ``Z``,
so the timestamp constructor is disabled here and such scalars stay strings.
YAML 1.2 semantics also keep ``no``, ``on`` and ``off`` as strings rather than
booleans.

**Nothing is gratuitously reformatted.** Round-trip mode preserves key order,
comments, blank lines and quoting style, so re-rendering a document Never4gA has
not modified reproduces it byte for byte.

The one deliberate exception is that a plain scalar a YAML 1.1 reader would
retype -- a bare timestamp, ``no``, ``null`` -- is double-quoted on the way out.
That is core/02 section 7.4's "writers SHOULD quote timestamps" applied to
everything with the same hazard, and it only affects files being rewritten
anyway. Sequence indentation is normalised to two spaces for the same reason:
ruamel emits a configured indent rather than the one it read.
"""

from __future__ import annotations

import copy
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, ScalarString

from never4ga.errors import Never4gaError

__all__ = [
    "FRONTMATTER_FENCE",
    "FrontmatterError",
    "ParsedMarkdown",
    "parse_document",
    "render_document",
]

FRONTMATTER_FENCE: Final = "---"

_TIMESTAMP_TAG: Final = "tag:yaml.org,2002:timestamp"

#: Words a YAML 1.1 reader turns into booleans. YAML 1.2 -- and therefore this
#: codec -- reads them as strings, so writing them bare would mean two readers
#: disagree about the same file.
_YAML_11_BOOLEANS: Final = frozenset(
    {
        *("y", "Y", "yes", "Yes", "YES"),
        *("n", "N", "no", "No", "NO"),
        *("on", "On", "ON", "off", "Off", "OFF"),
        *("true", "True", "TRUE", "false", "False", "FALSE"),
    }
)

#: Likewise for null.
_YAML_11_NULLS: Final = frozenset({"~", "null", "Null", "NULL"})

#: A YAML 1.1 date or timestamp: `2026-08-22`, `2026-08-22T19:00:00Z`, and the
#: space-separated spelling.
_YAML_11_TIMESTAMP: Final = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}(?:[Tt ].*)?$")

#: YAML 1.1 sexagesimal integers (`1:30` is 90) and underscored numbers
#: (`1_000`). Both are plain strings under YAML 1.2.
_YAML_11_NUMERIC: Final = re.compile(r"^[-+]?(?:[0-9][0-9_]*(?::[0-5]?[0-9])+|[0-9]+_[0-9_]*)$")


class FrontmatterError(Never4gaError, ValueError):
    """A document's frontmatter block could not be read."""


@dataclass(frozen=True, slots=True)
class ParsedMarkdown:
    """A document split into its frontmatter mapping and Markdown body.

    ``frontmatter`` is ``None`` when the document has no frontmatter block at
    all, which is different from an empty block: the first is plain Markdown,
    the second is a document that declares empty frontmatter. Reserved OKF
    navigation files rely on that distinction (core/02 section 3.2).
    """

    frontmatter: Mapping[str, Any] | None
    body: str
    line_ending: str = "\n"
    #: How many file lines precede the body. A retrieval result reports "source
    #: lines" (`details/retrieval-context-memory.md` section 8), and a line number
    #: that does not match the file someone opens is worse than none at all.
    body_offset: int = 0


def _codec() -> YAML:
    """A round-trip YAML configured for canonical Never4gA documents.

    A fresh instance per call: a ``YAML`` object carries loader/emitter state and
    is not safe to share.
    """
    yaml = YAML(typ="rt")
    # No explicit `version`: ruamel already applies YAML 1.2 scalar resolution,
    # and setting it would emit a `%YAML 1.2` directive into every file.
    yaml.preserve_quotes = True
    yaml.width = 4096  # never fold a long value onto a second line
    yaml.indent(mapping=2, sequence=4, offset=2)
    # Keep timestamps as the strings the author wrote (core/02 section 7.4).
    yaml.constructor.add_constructor(_TIMESTAMP_TAG, lambda loader, node: node.value)
    return yaml


def _detect_line_ending(text: str) -> str:
    index = text.find("\n")
    if index > 0 and text[index - 1] == "\r":
        return "\r\n"
    return "\n"


def parse_document(text: str) -> ParsedMarkdown:
    """Split a Markdown document into frontmatter and body.

    Raises :class:`FrontmatterError` if a frontmatter block is present but is
    not readable YAML, or is not a mapping. A document with no block is not an
    error -- plain Markdown is legitimate vault content.
    """
    line_ending = _detect_line_ending(text)
    normalised = text.replace("\r\n", "\n")

    if not normalised.startswith(f"{FRONTMATTER_FENCE}\n"):
        return ParsedMarkdown(None, normalised, line_ending)

    lines = normalised.split("\n")
    closing = next(
        (i for i in range(1, len(lines)) if lines[i] == FRONTMATTER_FENCE),
        None,
    )
    if closing is None:
        # An opening fence with no closing one is a horizontal rule, not
        # frontmatter. Be liberal in what you read (core/02 section 31).
        return ParsedMarkdown(None, normalised, line_ending)

    yaml_source = "\n".join(lines[1:closing])
    body = "\n".join(lines[closing + 1 :])

    try:
        loaded = _codec().load(yaml_source) if yaml_source.strip() else None
    except YAMLError as error:
        raise FrontmatterError(f"frontmatter is not readable YAML: {error}") from error

    if loaded is None:
        loaded = {}
    if not isinstance(loaded, Mapping):
        raise FrontmatterError(f"frontmatter must be a YAML mapping, got {type(loaded).__name__}")
    return ParsedMarkdown(loaded, body, line_ending, body_offset=closing + 1)


def _needs_explicit_quotes(value: str) -> bool:
    """Would some other YAML reader turn this bare scalar into a non-string?"""
    return (
        value in _YAML_11_BOOLEANS
        or value in _YAML_11_NULLS
        or bool(_YAML_11_TIMESTAMP.match(value))
        or bool(_YAML_11_NUMERIC.match(value))
    )


def _quote_ambiguous_scalars(node: Any) -> Any:
    """Double-quote plain strings that a YAML 1.1 reader would retype.

    Scalars that already carry an explicit style are left alone, so a value the
    author single-quoted stays single-quoted. Mutates ``node`` in place, which is
    safe because :func:`render_document` works on a copy; mutating rather than
    rebuilding is what keeps a ``CommentedMap``'s comments attached.
    """
    if isinstance(node, ScalarString):
        return node
    if isinstance(node, str):
        return DoubleQuotedScalarString(node) if _needs_explicit_quotes(node) else node
    if isinstance(node, dict):
        for key, value in node.items():
            node[key] = _quote_ambiguous_scalars(value)
        return node
    if isinstance(node, list):
        for index, value in enumerate(node):
            node[index] = _quote_ambiguous_scalars(value)
        return node
    return node


def render_document(
    frontmatter: Mapping[str, Any] | None,
    body: str,
    line_ending: str = "\n",
) -> str:
    """Render frontmatter and body back into a Markdown document.

    ``frontmatter=None`` writes the body alone. An empty mapping writes an empty
    fenced block, preserving the distinction :class:`ParsedMarkdown` draws.
    """
    if frontmatter is None:
        rendered = body
    elif not frontmatter:
        rendered = f"{FRONTMATTER_FENCE}\n{FRONTMATTER_FENCE}\n{body}"
    else:
        # deepcopy keeps a CommentedMap's comments and key order while leaving
        # the caller's mapping untouched. A mappingproxy cannot be deep-copied,
        # so unwrap it first -- `.copy()` delegates to the mapping underneath and
        # keeps its type.
        source = frontmatter.copy() if isinstance(frontmatter, MappingProxyType) else frontmatter
        data = _quote_ambiguous_scalars(copy.deepcopy(source))
        buffer = io.StringIO()
        _codec().dump(data, buffer)
        rendered = f"{FRONTMATTER_FENCE}\n{buffer.getvalue()}{FRONTMATTER_FENCE}\n{body}"

    if line_ending != "\n":
        rendered = rendered.replace("\n", line_ending)
    return rendered
