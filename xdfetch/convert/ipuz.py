"""ipuz crosswords (http://ipuz.org, v1 and v2), as many outlets export them from Crossword
Compiler and other tools.

    {"version": "http://ipuz.org/v2", "kind": ["http://ipuz.org/crossword#1"],
     "title": ..., "author": ..., "copyright": "© 2026", "intro": ..., "notes": ...,
     "dimensions": {"width": 5, "height": 5}, "block": "#", "empty": 0,
     "puzzle": [[1, 0, "#", {"cell": 4, "style": {"shapebg": "circle"}}, ...], ...],
     "solution": [["A", "B", "#", {"value": "C"}, ...], ...],
     "clues": {"Across": [{"number": 1, "clue": "<I>Italic</I> clue"}, [2, "or a pair"]], "Down": [...]}}

Some sites serve it as JSONP, wrapped in `ipuz(...)`; the wrapper (and a byte-order mark)
is taken off first.

Circles (style shapebg circle) are lowercase letters with Special: circle; a bar
(style barred) is listed as tricky; a square with more than one letter is a rebus square.
A style can be given inline or by name from the file's "styles". An omitted square (null in
both puzzle and solution, as for letters printed beside the grid) is `_`. Clues can be
{"number", "clue"} objects, [number, clue] pairs or "number clue" strings, under "Across"
and "Down" or "Across:<label>"-style keys. Clue text with <i>/<b> becomes xd markup. The
title, author and notes are the file's; the editor is the file's "editor", else an intro
that says "Edited by ..."; any other intro goes in the notes ahead of the file's notes; the
copyright is the file's line unless it is a bare "©".

An outlet whose files need more than this (a title kept in a record beside the file, say)
can supply it from a converter plugin that calls convert() here and adjusts the result:
see xdfetch.convert.
"""

from __future__ import annotations

import html
import json
import re

from ..xd import BLOCK, REBUS_KEYS, VOID, Clue, Puzzle, clue_text, number_grid, plain
from . import Context, ConvertError


def unwrap(data: bytes) -> bytes:
    """The JSON, without a byte-order mark or a JSONP `ipuz(...)` wrapper around it."""
    data = data.lstrip(b"\xef\xbb\xbf").strip()
    if data.startswith(b"ipuz(") and data.endswith(b")"):
        data = data[5:-1].strip()
    return data


def sniff(data: bytes) -> bool:
    # a big puzzle's file can put its grid first, so "ipuz.org" can be deep in the file
    data = unwrap(data)
    return data.startswith(b"{") and b"ipuz.org" in data and b'"puzzle"' in data and b'"solution"' in data


def cell_number(cell) -> int | None:
    if isinstance(cell, dict):
        cell = cell.get("cell")
    if isinstance(cell, str) and cell.isdigit():  # some files write labels as strings
        cell = int(cell)
    return cell if isinstance(cell, int) and not isinstance(cell, bool) and cell > 0 else None


def cell_style(cell, styles: dict) -> dict:
    style = (cell.get("style") or {}) if isinstance(cell, dict) else {}
    if isinstance(style, str):  # a named style from the file's "styles"
        style = styles.get(style) or {}
    return style if isinstance(style, dict) else {}


def clue_groups(clues: dict) -> dict[str, list]:
    """The Across and Down lists, under "Across" or "Across:<label>"-style keys."""
    out: dict[str, list] = {"A": [], "D": []}
    for key, items in clues.items():
        direction = {"across": "A", "down": "D"}.get(key.split(":")[0].strip().lower())
        if direction and isinstance(items, list):
            out[direction] += items
    return out


def clue_parts(item) -> tuple[object, object] | None:
    """(number, clue) from a clue object, a [number, clue] pair or a "number clue" string."""
    if isinstance(item, dict):
        return item.get("number"), item.get("clue", "")
    if isinstance(item, list) and len(item) >= 2:
        return item[0], item[1]
    if isinstance(item, str):
        m = re.match(r"\s*(\d+)\.?\s+(.+)", item)
        if m:
            return m.group(1), m.group(2)
    return None


def cell_value(cell) -> str | None:
    if isinstance(cell, dict):
        cell = cell.get("value")
    return cell if isinstance(cell, str) else None


def convert(data: bytes, ctx: Context) -> Puzzle:
    try:
        d = json.loads(unwrap(data).decode("utf-8"))
        h, w = int(d["dimensions"]["height"]), int(d["dimensions"]["width"])
        puzzle, solution = d["puzzle"], d["solution"]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as e:
        raise ConvertError(f"not an ipuz crossword: {e}") from e
    block = d.get("block", "#")
    styles = d.get("styles") or {}
    groups = clue_groups(d.get("clues") or {})
    if len(puzzle) != h or len(solution) != h or any(len(r) != w for r in puzzle) or any(len(r) != w for r in solution):
        raise ConvertError(f"grid is not {w}x{h}")
    p = Puzzle()

    circled: set[tuple[int, int]] = set()
    for r in range(h):
        for c in range(w):
            style = cell_style(puzzle[r][c], styles)
            for k, v in style.items():
                if (k, v) == ("shapebg", "circle"):
                    circled.add((r, c))
                elif k == "barred":
                    p.tricky.append(f"bar {v} at row {r} col {c}")
                elif k in ("color", "colortext", "highlight"):
                    p.tricky.append(f"{k} {v} at row {r} col {c}")
    if circled:
        p.set("Special", "circle")

    rebus: dict[str, str] = {}
    plain_keys = iter(REBUS_KEYS)
    letters = iter("abcdefghijklmnopqrstuvwxyz")
    grid = []
    for r in range(h):
        row = ""
        for c in range(w):
            v = cell_value(solution[r][c])
            is_block = v == block or (cell_number(puzzle[r][c]) is None and puzzle[r][c] == block)
            if is_block or (v is None and puzzle[r][c] == block):
                row += BLOCK
            elif v is None and puzzle[r][c] is None:
                row += VOID
            elif not v:
                row += " "
                p.needs_review(f"empty square at row {r} col {c}")
            elif len(v) == 1 and v.isalpha() and v.isascii():
                row += v.lower() if (r, c) in circled else v.upper()
            else:
                value = v.upper() if v.isalpha() else v
                if value not in rebus:
                    try:
                        rebus[value] = next(letters) if (r, c) in circled else next(plain_keys)
                    except StopIteration:
                        raise ConvertError("more rebus values than keys") from None
                row += rebus[value]
        grid.append(row)
    p.grid = grid
    if rebus:
        p.set("Rebus", " ".join(f"{k}={v}" for v, k in rebus.items()))

    numbering = number_grid([row.replace(" ", "?") for row in grid])
    from ..xd import answer_at

    theirs = {(r, c): cell_number(puzzle[r][c]) for r in range(h) for c in range(w) if cell_number(puzzle[r][c])}
    ours = {pos: n for (_, n), pos in numbering.items()}
    off = sorted(pos for pos in set(theirs) | set(ours) if theirs.get(pos) != ours.get(pos))
    if off:
        p.needs_review(
            "the outlet's cell numbers differ from standard numbering at "
            + ", ".join(f"row {r} col {c}" for r, c in off[:6])
            + ("..." if len(off) > 6 else "")
        )

    for direction in ("A", "D"):
        for item in groups[direction]:
            parts = clue_parts(item)
            if parts is None:
                continue
            number, raw = parts
            try:
                number = int(str(number))
            except (TypeError, ValueError):
                p.needs_review(f"{direction} clue with no number: {str(raw)[:40]}")
                continue
            pos = numbering.get((direction, number))
            if pos is None:
                p.needs_review(f"{direction}{number}: clue has no slot in the grid")
                answer = ""
            else:
                from_grid = answer_at(grid, direction, *pos)
                answer = "".join(next((v for v, k in rebus.items() if k == ch), ch.upper()) for ch in from_grid)
            p.clues.append(Clue(direction, number, clue_text(html.unescape(str(raw))), answer))
    if not p.clues:
        raise ConvertError("no clues")
    for item in groups["A"] + groups["D"]:
        if isinstance(item, dict) and item.get("answer"):
            p.tricky.append("a clue carries its own answer key; the grid's is used")
            break

    # headers
    title = plain(str(d.get("title") or ""))
    author = plain(str(d.get("author") or ""))
    editor = plain(str(d.get("editor") or ""))
    intro = plain(str(d.get("intro") or ""))
    intro_note = ""
    if intro and not intro.isdigit():
        if re.search(r"edited by", intro, re.I):
            editor = editor or re.sub(r"^.*edited by\s*", "", intro, flags=re.I)
        else:
            intro_note = intro
    p.set("Title", title)
    p.set("Author", re.sub(r"\s+&\s+", " and ", author))
    p.set("Editor", editor)
    copyright_ = plain(str(d.get("copyright") or ""))
    if copyright_ and copyright_ != "©":
        p.set("Copyright", copyright_)
    if ctx.date:
        p.set("Date", ctx.date.isoformat())
    p.notes = "\n\n".join(n for n in (intro_note, plain(str(d.get("notes") or ""))) if n)
    return p
