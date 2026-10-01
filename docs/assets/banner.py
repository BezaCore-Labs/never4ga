"""Build `banner.svg`, the README banner, from the brand's own sources.

Never4gA has a light brand: BezaCore Labs' colours and type, the wordmark in
Archivo SemiBold with the 4 in the brand's yellow signal, and BezaCore Labs'
own endorsement lockup. Everything is outlines, not live text, because GitHub
renders an SVG in an `<img>` without web fonts and would fall back to whatever
the reader has installed.

It is run by hand when the banner changes, not by the build, and needs
`fontTools`, which is not a dependency of Never4gA:

    python docs/assets/banner.py --masters <brand/masters> --inter <Inter.ttf>

`--masters` is BezaCore Labs' brand masters directory, which supplies Archivo
and the endorsement lockup; `--inter` is Inter's variable font. Both are SIL
Open Font License 1.1.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import Any

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

HERE = pathlib.Path(__file__).parent

# BezaCore Labs tokens, on dark. Nothing here is a Never4gA colour of its own.
PAGE = "#081221"
INK = "#EEF2F7"
PRIMARY = "#B0B7FF"
SIGNAL = "#F7D710"

WORD = "Never4gA"
SIGNAL_AT = WORD.index("4")
TAGLINE = ("The right context for every AI session,", "assembled without spending a token.")
WIDTH, HEIGHT = 1280, 320


def instance(path: pathlib.Path, **axes: float) -> TTFont:
    return instantiateVariableFont(TTFont(path), axes, inplace=True)


def pair_kerning(font: TTFont) -> tuple[dict[tuple[str, str], int], list[Any]]:
    """GPOS pair adjustments: single pairs, and the class tables to look up."""
    pairs: dict[tuple[str, str], int] = {}
    classes: list[Any] = []
    if "GPOS" not in font:
        return pairs, classes
    for lookup in font["GPOS"].table.LookupList.Lookup:
        subtables = lookup.SubTable
        if lookup.LookupType == 9:
            subtables = [extension.ExtSubTable for extension in subtables]
        for sub in subtables:
            if getattr(sub, "LookupType", 2) != 2:
                continue
            if sub.Format == 1:
                for first, pairset in zip(sub.Coverage.glyphs, sub.PairSet, strict=False):
                    for record in pairset.PairValueRecord:
                        value = getattr(record.Value1, "XAdvance", 0) or 0
                        if value:
                            pairs.setdefault((first, record.SecondGlyph), value)
            elif sub.Format == 2:
                classes.append(sub)
    return pairs, classes


def kern(pairs: dict[tuple[str, str], int], classes: list[Any], left: str, right: str) -> int:
    if (left, right) in pairs:
        return pairs[(left, right)]
    for sub in classes:
        if left not in sub.Coverage.glyphs:
            continue
        first = sub.ClassDef1.classDefs.get(left, 0)
        second = sub.ClassDef2.classDefs.get(right, 0)
        value = getattr(sub.Class1Record[first].Class2Record[second].Value1, "XAdvance", 0) or 0
        if value:
            return int(value)
    return 0


def outline(
    font: TTFont,
    text: str,
    *,
    x: float,
    baseline: float,
    size: float,
    fills: list[str],
    tracking: float = 0,
) -> str:
    """`text` as one path per glyph, `size` px to the em, kerned by the font's own table."""
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    pairs, classes = pair_kerning(font)
    upem = font["head"].unitsPerEm
    names = [cmap[ord(character)] for character in text]
    paths, advance = [], 0.0
    for index, name in enumerate(names):
        pen = SVGPathPen(glyphs)
        glyphs[name].draw(pen)
        if pen.getCommands():
            paths.append(
                f'<path fill="{fills[index]}" transform="translate({advance:.1f} 0)" '
                f'd="{pen.getCommands()}"/>'
            )
        advance += glyphs[name].width + tracking * upem / 1000
        if index + 1 < len(names):
            advance += kern(pairs, classes, name, names[index + 1])
    scale = size / upem
    return (
        f'<g transform="translate({x:.1f} {baseline:.1f}) scale({scale:.5f}) scale(1 -1)">'
        + "".join(paths)
        + "</g>"
    )


def endorsement(masters: pathlib.Path, *, x: float, y: float, width: float) -> str:
    """BezaCore Labs' endorsement lockup, on-dark, from its own build script."""
    sys.path.insert(0, str(masters))
    import build  # type: ignore[import-not-found]

    master = build.endorsement(build.VARIANTS["on-dark"])
    box = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', master)
    assert box is not None
    inner = master[master.index(">") + 1 : master.rindex("</svg>")]
    scale = width / float(box[1])
    return f'<g transform="translate({x:.1f} {y:.1f}) scale({scale:.5f})">{inner}</g>'


def banner(masters: pathlib.Path, inter: pathlib.Path) -> str:
    archivo = instance(masters / "fonts" / "Archivo.ttf", wght=600, wdth=100)
    text = instance(inter, wght=400, opsz=24)
    word = [SIGNAL if index == SIGNAL_AT else INK for index in range(len(WORD))]
    parts = [
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{PAGE}"/>',
        outline(archivo, WORD, x=96, baseline=158, size=104, fills=word, tracking=-12),
        *(
            outline(text, line, x=98, baseline=232 + row * 38, size=28, fills=[PRIMARY] * len(line))
            for row, line in enumerate(TAGLINE)
        ),
        endorsement(masters, x=WIDTH - 96 - 250, y=HEIGHT - 96, width=250),
    ]
    label = f"Never4gA, by BezaCore Labs: {' '.join(TAGLINE)}"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{label}">'
        + "".join(parts)
        + "</svg>\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--masters", type=pathlib.Path, required=True)
    parser.add_argument("--inter", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, default=HERE / "banner.svg")
    arguments = parser.parse_args()
    arguments.out.write_text(banner(arguments.masters, arguments.inter))
    print(f"wrote {arguments.out}")


if __name__ == "__main__":
    main()
