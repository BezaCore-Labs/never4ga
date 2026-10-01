"""A focused Markdown scanner.

Chunking needs ATX headings, heading hierarchy, line ranges and fenced-code
awareness; link extraction needs inline and reference-style links. That is a
scanner, not a CommonMark implementation, so it needs no Markdown parser
dependency.

It reads more syntax than Never4gA writes, on purpose. core/02 section 31: "be
liberal in what you can read, strict and intentional in what you write."
Never4gA emits ATX headings and standard Markdown links; it reads setext
headings and wikilinks too, because a vault that arrives from somewhere else
uses whatever its author used, and Obsidian writes wikilinks by default.

The cost of not reading a syntax is not a missing feature. A vault whose links
are all invisible gets a `doctor` that reports zero broken links -- a confident
wrong answer, which is worse than no answer at all.

What it deliberately does not do:

- **Nested brackets** inside link text. A label containing an unescaped ``]`` is
  left alone rather than half-parsed.
- **Obsidian's link-resolution heuristics.** A wikilink names its target; this
  records the name. Turning a name into a document needs the whole vault and is
  the indexing service's job, where ambiguity becomes a finding rather than a
  guess.

Being a scanner, it errs toward *not* finding things. A heading it misses costs
a chunk boundary; a heading it invents inside a code block corrupts one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from never4ga.domain.chunk import LineRange

__all__ = [
    "Heading",
    "MarkdownLink",
    "Section",
    "scan_headings",
    "scan_links",
    "scan_sections",
]

#: ATX heading: up to three leading spaces, one to six hashes, then either the
#: end of the line or a space. ``#hashtag`` is a tag, not a heading, and four
#: leading spaces is an indented code block.
_HEADING: Final = re.compile(r"^ {0,3}(?P<hashes>#{1,6})(?:[ \t]+(?P<text>.*?))?[ \t]*$")

#: A setext underline: a run of one character, all ``=`` or all ``-``. What it
#: underlines has to be a paragraph line, which :func:`_is_paragraph` decides.
_UNDERLINE: Final = re.compile(r"^ {0,3}(?P<rule>=+|-+)[ \t]*$")

#: Lines that are structure rather than paragraph text, so a ``---`` beneath one
#: is a thematic break or a table rule and not a heading.
_NOT_PARAGRAPH: Final = re.compile(r"^ {0,3}(?:[-*+>|]|\d+[.)])(?:\s|$)")

#: ``[[Target|display]]``, ``[[Target#Heading]]``, ``![[Embed]]``. The leading
#: ``!`` is captured rather than excluded: an embedded *note* is a connection
#: Obsidian counts, and a transclusion of something missing is worth reporting.
_WIKILINK: Final = re.compile(r"(?P<embed>!)?\[\[(?P<body>[^\[\]\n]+)\]\]")

#: An embed target that names a file of some other kind. Same rule as a Markdown
#: image: assets are not concepts (details/data-indexing-maintenance.md 4).
_ASSET: Final = re.compile(r"\.(?!md$)[A-Za-z0-9]+$")

#: An opening or closing code fence: three or more backticks or tildes.
_FENCE: Final = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})\s*(?P<info>.*)$")

#: A trailing closing sequence, which is decoration rather than text.
_CLOSING_HASHES: Final = re.compile(r"[ \t]+#+[ \t]*$")

#: ``[text](target "optional title")``, not preceded by ``!``.
_INLINE_LINK: Final = re.compile(
    r"(?<!!)\[(?P<text>[^\]\n]*)\]\((?P<destination>[^()\s]*)(?:\s+[\"'][^\"']*[\"'])?\)"
)

#: ``[text][label]`` and the collapsed ``[label][]``.
_REFERENCE_LINK: Final = re.compile(r"(?<!!)\[(?P<text>[^\]\n]*)\]\[(?P<label>[^\]\n]*)\]")

#: ``[label]: destination`` on its own line.
_DEFINITION: Final = re.compile(
    r"^ {0,3}\[(?P<label>[^\]\n]+)\]:\s*(?P<destination>\S+)(?:\s+[\"'][^\"']*[\"'])?\s*$"
)

#: An inline code span. Whatever is inside is not markup.
_CODE_SPAN: Final = re.compile(r"`+[^`]*`+")

#: A destination with a scheme, or a protocol-relative one: not a vault path.
_EXTERNAL: Final = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//)")


@dataclass(frozen=True, slots=True)
class Heading:
    """One ATX heading and where it sits in the hierarchy."""

    level: int
    text: str
    line: int
    path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Section:
    """A heading and the lines beneath it, up to the next heading.

    A section runs to the next heading of *any* level, so a parent holds its own
    prose and each child is a section in its own right. ``heading_path`` keeps
    the hierarchy that flat structure would otherwise lose.
    """

    heading_path: tuple[str, ...]
    line_range: LineRange
    text: str


@dataclass(frozen=True, slots=True)
class MarkdownLink:
    """One link, as written.

    ``target`` is a *path* for a standard Markdown link and a *name or path* for
    a wikilink -- Obsidian resolves a bare name against the whole vault. Which
    of the two it is matters to whoever resolves it, so ``is_wikilink`` says.
    """

    text: str
    target: str
    anchor: str | None
    line: int
    is_wikilink: bool = False

    @property
    def is_external(self) -> bool:
        """True when the destination names a scheme rather than a vault path."""
        return not self.is_wikilink and bool(_EXTERNAL.match(self.target))


def scan_headings(body: str) -> tuple[Heading, ...]:
    """Every ATX and setext heading outside a code fence, with its hierarchy."""
    headings: list[Heading] = []
    ancestors: list[Heading] = []
    for level, text, number in _heading_lines(body):
        while ancestors and ancestors[-1].level >= level:
            ancestors.pop()
        heading = Heading(
            level=level,
            text=text,
            line=number,
            path=(*(ancestor.text for ancestor in ancestors), text),
        )
        ancestors.append(heading)
        headings.append(heading)
    return tuple(headings)


def _heading_lines(body: str) -> list[tuple[int, str, int]]:
    """Each heading as ``(level, text, line)``, in document order.

    A setext heading is recognised by the line *after* it, so the scan looks
    ahead by one and consumes the underline. CommonMark resolves the ``---``
    ambiguity in favour of the heading when a paragraph precedes it, which is
    also what a reader sees.
    """
    lines = _uncoded_lines(body)
    found: list[tuple[int, str, int]] = []
    consumed = -1
    for position, (number, line) in enumerate(lines):
        if position == consumed:
            continue

        atx = _HEADING.match(line)
        if atx is not None:
            text = _CLOSING_HASHES.sub("", atx.group("text") or "").strip()
            found.append((len(atx.group("hashes")), text, number))
            continue

        following = lines[position + 1] if position + 1 < len(lines) else None
        underline = _UNDERLINE.match(following[1]) if following is not None else None
        if underline is not None and following is not None and _is_paragraph(line):
            # The underline is decoration; the line above is the heading, and is
            # where the section starts.
            consumed = position + 1
            found.append((1 if underline.group("rule")[0] == "=" else 2, line.strip(), number))
    return found


def _is_paragraph(line: str) -> bool:
    """Whether this line is ordinary prose that an underline could turn into a heading.

    A blank line has nothing to underline. A list item, a quote, a table row or
    an ATX heading is structure of its own, and a rule beneath one is a thematic
    break rather than a promotion.
    """
    return bool(
        line.strip()
        and not _NOT_PARAGRAPH.match(line)
        and not _HEADING.match(line)
        and not _UNDERLINE.match(line)
    )


def scan_sections(body: str) -> tuple[Section, ...]:
    """The document split at its headings, in document order.

    Content before the first heading is a section with an empty heading path:
    frontmatter-adjacent prose is still worth indexing, and dropping it would
    lose the opening paragraph of every note written lead-first.
    """
    lines = body.splitlines()
    if not any(line.strip() for line in lines):
        return ()

    headings = scan_headings(body)
    starts = [heading.line for heading in headings]
    boundaries: list[tuple[int, tuple[str, ...]]] = []
    if not starts or starts[0] > 1:
        boundaries.append((0, ()))
    boundaries.extend((heading.line, heading.path) for heading in headings)

    sections: list[Section] = []
    for position, (start, heading_path) in enumerate(boundaries):
        first = max(start, 1)
        last = boundaries[position + 1][0] - 1 if position + 1 < len(boundaries) else len(lines)
        span = lines[first - 1 : last]
        if not any(line.strip() for line in span):
            continue
        # Trailing blank lines belong to the gap between sections, not to it.
        while span and not span[-1].strip():
            span.pop()
            last -= 1
        sections.append(
            Section(
                heading_path=heading_path,
                line_range=LineRange(start=first, end=max(last, first)),
                text="\n".join(span),
            )
        )
    return tuple(sections)


def scan_links(body: str) -> tuple[MarkdownLink, ...]:
    """Every inline and reference-style link outside code.

    Images are excluded: they address assets rather than concepts, and a broken-
    link finding about a PNG is noise (details/data-indexing-maintenance.md
    section 4).
    """
    definitions = _link_definitions(body)
    links: list[MarkdownLink] = []
    for number, line in _uncoded_lines(body):
        if _DEFINITION.match(line):
            continue
        masked = _CODE_SPAN.sub(lambda match: " " * len(match.group(0)), line)
        wikilinks = [_wikilink(match, number) for match in _WIKILINK.finditer(masked)]
        links.extend(link for link in wikilinks if link is not None)

        # A wikilink is `[[...]]`, which the Markdown patterns would also read as
        # a bracketed label. Blank them before looking for the other two forms.
        masked = _WIKILINK.sub(lambda match: " " * len(match.group(0)), masked)
        for match in _INLINE_LINK.finditer(masked):
            links.append(_link(match.group("text"), match.group("destination"), number))
        for match in _REFERENCE_LINK.finditer(masked):
            text = match.group("text")
            label = match.group("label").strip() or text
            destination = definitions.get(label.casefold())
            if destination is not None:
                links.append(_link(text, destination, number))
    return tuple(links)


def _wikilink(match: re.Match[str], line: int) -> MarkdownLink | None:
    """One ``[[target|display]]``, or ``None`` if it addresses an asset.

    The target is left as written. Obsidian resolves a bare name against the
    whole vault by its own rules; reproducing that guess here would put a
    heuristic inside a scanner, so the name travels on and the indexing service
    resolves it where the whole vault is in view.
    """
    body = match.group("body")
    target, _, display = body.partition("|")
    target, _, anchor = target.partition("#")
    target = target.strip()
    if match.group("embed") and _ASSET.search(target):
        return None
    if not target and not anchor.strip():
        return None
    return MarkdownLink(
        text=display.strip() or target,
        target=target.removesuffix(".md"),
        anchor=anchor.strip() or None,
        line=line,
        is_wikilink=True,
    )


def _link(text: str, destination: str, line: int) -> MarkdownLink:
    target, _, anchor = destination.partition("#")
    return MarkdownLink(text=text.strip(), target=target, anchor=anchor or None, line=line)


def _link_definitions(body: str) -> dict[str, str]:
    definitions: dict[str, str] = {}
    for _, line in _uncoded_lines(body):
        match = _DEFINITION.match(line)
        if match is not None:
            definitions.setdefault(
                match.group("label").strip().casefold(), match.group("destination")
            )
    return definitions


def _uncoded_lines(body: str) -> list[tuple[int, str]]:
    """Every line outside a fenced code block, numbered from one.

    A fence closes only on the same character, at the same length or longer. An
    unclosed fence runs to the end of the document, which is what a reader sees.
    """
    outside: list[tuple[int, str]] = []
    fence: str | None = None
    for number, line in enumerate(body.splitlines(), start=1):
        match = _FENCE.match(line)
        if fence is None:
            if match is not None:
                fence = match.group("fence")
                continue
            outside.append((number, line))
        elif (
            match is not None
            and match.group("fence")[0] == fence[0]
            and len(match.group("fence")) >= len(fence)
            and not match.group("info")
        ):
            fence = None
    return outside
