#!/usr/bin/env python3
"""Documentation checks. Run from the repository root:

    python tools/check_docs.py

Seven checks, all of which must pass before documentation is pushed:

1. **Dashes.** No em dash, en dash or prose double hyphen anywhere in the
   Markdown. Horizontal rules, table separators, command-line flags and code
   blocks are exempt.
2. **Links.** Every relative link and image target exists.
3. **Anchors.** Every ``#fragment`` matches a real heading or an explicit
   ``<a id=...>`` on the target page, using GitHub's own slug rules.
4. **Citations.** Every ``[[N]](.../references.md#ref-N)`` has a matching
   anchor, the numbering has no gaps, and nothing is defined but never cited.
5. **Pipes in table math.** No ``|`` or ``\\|`` inside ``$...$`` on a line that
   starts with ``|``. A bare pipe splits the table cell, and ``\\|`` is the
   double-bar norm in MathJax, so absolute values must be written
   ``\\lvert x \\rvert``.
6. **Inline math spacing.** No whitespace immediately inside a ``$`` delimiter,
   because GitHub then refuses to render the span as math.
7. **Display math.** ``$$`` stands alone on its own line, above and below the
   equation, which is the repository convention.

Exit status is 0 when everything passes and 1 otherwise, so it can be wired
into CI.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HTML_REF_RE = re.compile(r'(?:src|href)="([^"]+)"')
HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
# One inline span: a lone $ (not $$, not \$) up to the next lone $. May run
# on to the next line of the same paragraph, as GitHub allows.
INLINE_MATH_RE = re.compile(r"(?<![\\$])\$(?!\$)(.+?)(?<![\\$])\$(?!\$)", re.DOTALL)
EXTERNAL = ("http://", "https://", "mailto:", "../../actions", "../../star",
            "../../commits")


def markdown_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "assets" / "README.md"]
    files += sorted(ROOT.glob("docs/**/*.md"))
    return [f for f in files if f.exists()]


def github_slug(heading: str) -> str:
    """GitHub's anchor slug: lowercase, drop punctuation, keep unicode letters."""
    text = heading.strip().lower()
    kept = [ch for ch in text
            if unicodedata.category(ch)[0] in "LN" or ch in " -_"]
    return "".join(kept).strip().replace(" ", "-")


def anchors_of(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    found = {github_slug(m.group(1)) for m in
             (HEADING_RE.match(line) for line in text.splitlines()) if m}
    found |= set(re.findall(r'<a\s+id="([^"]+)"', text))
    found |= set(re.findall(r'<a\s+name="([^"]+)"', text))
    return found


def strip_code_blocks(text: str) -> list[tuple[int, str]]:
    """Return (line number, line) for lines outside fenced code blocks."""
    out, in_fence = [], False
    for n, line in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append((n, line))
    return out


def prose_lines(text: str) -> list[tuple[int, str]]:
    """Lines outside fenced code and outside ``$$`` display blocks, with inline
    code spans blanked so a backtick-quoted ``$`` cannot open a math span."""
    out, in_display = [], False
    for n, line in strip_code_blocks(text):
        if line.strip() == "$$":
            in_display = not in_display
            continue
        if in_display:
            continue
        out.append((n, CODE_SPAN_RE.sub(lambda m: " " * len(m.group(0)), line)))
    return out


def inline_math_spans(text: str) -> list[tuple[int, str]]:
    """Every ``$...$`` span in the prose as (line number, body), pairing the
    delimiters paragraph by paragraph so a span that continues on the next line
    is read as one span and a stray ``$`` cannot poison the whole file."""
    spans: list[tuple[int, str]] = []
    paragraph: list[tuple[int, str]] = []

    def flush() -> None:
        if not paragraph:
            return
        joined = "\n".join(line for _, line in paragraph)
        for m in INLINE_MATH_RE.finditer(joined):
            index = joined.count("\n", 0, m.start())
            spans.append((paragraph[index][0], m.group(1)))
        paragraph.clear()

    for n, line in prose_lines(text):
        if line.strip():
            paragraph.append((n, line))
        else:
            flush()
    flush()
    return spans


def check_dashes(files: list[Path]) -> list[str]:
    problems = []
    for f in files:
        for n, line in strip_code_blocks(f.read_text(encoding="utf-8")):
            if "—" in line or "–" in line:
                problems.append(f"{f.relative_to(ROOT)}:{n}: em or en dash: {line.strip()[:78]}")
            if line.strip() == "---" or re.match(r"^\s*\|[\s|:-]+\|\s*$", line):
                continue                      # horizontal rule, table separator
            for m in re.finditer(r"--", line):
                tail = line[m.end():m.end() + 1]
                if tail.isalpha():
                    continue                  # a command-line flag
                if line[:m.start()].rstrip().endswith("`"):
                    continue                  # inside an inline code span
                problems.append(
                    f"{f.relative_to(ROOT)}:{n}: prose double hyphen: {line.strip()[:78]}")
    return problems


def check_links(files: list[Path]) -> tuple[list[str], int]:
    problems, counted = [], 0
    cache: dict[Path, set[str]] = {}
    for f in files:
        text = f.read_text(encoding="utf-8")
        for m in list(LINK_RE.finditer(text)) + list(HTML_REF_RE.finditer(text)):
            target = m.group(1)
            if target.startswith(EXTERNAL):
                continue
            path, _, anchor = target.partition("#")
            if not path and not anchor:
                continue
            page = (f.parent / path).resolve() if path else f
            counted += 1
            if not page.exists():
                problems.append(f"{f.relative_to(ROOT)} -> {target}  (missing file)")
                continue
            if anchor and page.suffix == ".md":
                cache.setdefault(page, anchors_of(page))
                if anchor not in cache[page]:
                    problems.append(f"{f.relative_to(ROOT)} -> {target}  (missing anchor)")
    return problems, counted


def check_citations() -> tuple[list[str], int]:
    refs = ROOT / "docs" / "references.md"
    if not refs.exists():
        return ["docs/references.md is missing"], 0
    defined = {int(n) for n in re.findall(r'<a id="ref-(\d+)"',
                                          refs.read_text(encoding="utf-8"))}
    cited: set[int] = set()
    for f in ROOT.glob("docs/**/*.md"):
        cited |= {int(n) for n in
                  re.findall(r"#ref-(\d+)\)", f.read_text(encoding="utf-8"))}
    problems = []
    gaps = sorted(set(range(1, max(defined) + 1)) - defined) if defined else []
    if gaps:
        problems.append(f"gaps in the reference numbering: {gaps}")
    orphan_citations = sorted(cited - defined)
    if orphan_citations:
        problems.append(f"cited without an anchor: {orphan_citations}")
    unused = sorted(defined - cited)
    if unused:
        problems.append(f"defined but never cited: {unused}")
    return problems, len(defined)


def check_table_math(files: list[Path]) -> tuple[list[str], int]:
    """No ``|`` or ``\\|`` inside ``$...$`` on a table row."""
    problems, counted = [], 0
    for f in files:
        for n, line in prose_lines(f.read_text(encoding="utf-8")):
            if not line.lstrip().startswith("|"):
                continue
            for m in INLINE_MATH_RE.finditer(line):
                counted += 1
                if "|" in m.group(1):
                    problems.append(
                        f"{f.relative_to(ROOT)}:{n}: pipe inside table math, "
                        f"use \\lvert and \\rvert: {m.group(0)[:60]}")
    return problems, counted


def check_math_spacing(files: list[Path]) -> tuple[list[str], int]:
    """No whitespace immediately inside a ``$`` delimiter."""
    problems, counted = [], 0
    for f in files:
        for n, body in inline_math_spans(f.read_text(encoding="utf-8")):
            counted += 1
            if body != body.strip():
                problems.append(
                    f"{f.relative_to(ROOT)}:{n}: whitespace inside $ delimiters: "
                    f"${body[:50]}$")
    return problems, counted


def check_display_math(files: list[Path]) -> tuple[list[str], int]:
    """``$$`` stands alone on its line."""
    problems, delimiters = [], 0
    for f in files:
        for n, line in strip_code_blocks(f.read_text(encoding="utf-8")):
            line = CODE_SPAN_RE.sub(lambda m: " " * len(m.group(0)), line)
            if "$$" not in line:
                continue
            if line.strip() == "$$":
                delimiters += 1
            else:
                problems.append(
                    f"{f.relative_to(ROOT)}:{n}: $$ must stand alone on its line: "
                    f"{line.strip()[:60]}")
    return problems, delimiters // 2


def main() -> int:
    files = markdown_files()
    failed = False

    dash_problems = check_dashes(files)
    print(f"dashes      {len(files)} files"
          f"{'' if not dash_problems else f', {len(dash_problems)} problems'}")
    for p in dash_problems[:20]:
        print(f"            {p}")
    failed |= bool(dash_problems)

    link_problems, n_links = check_links(files)
    print(f"links       {n_links} checked"
          f"{'' if not link_problems else f', {len(link_problems)} broken'}")
    for p in link_problems[:20]:
        print(f"            {p}")
    failed |= bool(link_problems)

    cite_problems, n_refs = check_citations()
    print(f"citations   {n_refs} references"
          f"{'' if not cite_problems else f', {len(cite_problems)} problems'}")
    for p in cite_problems:
        print(f"            {p}")
    failed |= bool(cite_problems)

    for label, (problems, count), unit in (
            ("table math ", check_table_math(files), "spans on table rows"),
            ("inline math", check_math_spacing(files), "spans"),
            ("display    ", check_display_math(files), "blocks")):
        print(f"{label} {count} {unit}"
              f"{'' if not problems else f', {len(problems)} problems'}")
        for p in problems[:20]:
            print(f"            {p}")
        failed |= bool(problems)

    print("FAIL" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
