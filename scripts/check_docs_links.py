"""Check that every relative Markdown link in the repo points at a file that exists.

The docs are a docsify site, so a link is written one of three ways and any
of them is correct: relative to the page (``assets/R-001-mae.png``), relative
to the site root at ``docs/`` (``research/spot_price/observations.md``), or
relative to the repo root (``docs/Kimball-Dimensional-Modeling-Techniques.md``
from the top-level README). A target is broken only when it resolves under
none of the three.

Only the path is checked, never the ``#anchor`` after it — an anchor check
needs a heading-slug model per renderer, and the failure this guards against
is a file moved or renamed by a restructure, which is a path.

Link discovery is a scan rather than one regular expression, because a regex
that "mostly" finds links fails in both directions: it skips forms it does not
know (a broken link then passes CI) and it matches link-shaped text inside
code samples (valid docs then fail CI). The scan therefore strips code first
and handles the destination forms CommonMark allows:

* fenced code blocks and inline code spans are removed before scanning;
* inline links ``[text](dest)``, with ``<dest>`` and balanced parentheses;
* reference links ``[text][label]`` / ``[label][]`` against their definitions.

Targets carrying the repo's own placeholder markers are skipped: ``<`` for an
inline ``<slug>``, and ``XXX`` for the ``O-XXX`` / ``R-XXX`` research IDs that
CLAUDE.md defines. Both appear in docs that show the naming convention rather
than link to a real file.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIRNAME = "docs"
#: A fence opens and closes with three or more backticks or tildes.
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
#: Inline code: one or more backticks, the shortest run closing it.
CODE_SPAN = re.compile(r"(`+)(?:.*?)\1", re.S)
#: ``[label]: destination`` at the start of any line, the optional title ignored.
REFERENCE_DEFINITION = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*(\S+)", re.M)
#: ``[text][label]`` and the collapsed ``[label][]``.
REFERENCE_USE = re.compile(r"\[([^\]]*)\]\[([^\]]*)\]")
#: Any RFC 3986 scheme, plus protocol-relative and bare-anchor targets.
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
#: Markers the repo uses for a name to be filled in, never a real path.
PLACEHOLDER_MARKERS = ("<", "XXX")


def tracked_markdown_files(root: Path) -> list[Path]:
    """List the git-tracked Markdown files under ``root``.

    Enumerating from git rather than globbing keeps generated and ignored
    trees (``dbt/target/``, ``.venv/``) out without a skip list.

    Parameters
    ----------
    root : Path
        Repository root to enumerate.

    Returns
    -------
    list of Path
        Absolute paths, in git's order.
    """
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "*.md"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [root / name for name in out.split("\0") if name]


def strip_code(text: str) -> str:
    """Blank out fenced code blocks and inline code spans.

    Link-shaped text inside a code sample is documentation about a link, not
    a link. Lines are kept (blanked) rather than dropped so nothing outside
    the code shifts.

    Parameters
    ----------
    text : str
        Markdown source.

    Returns
    -------
    str
        The source with code content removed.
    """
    lines: list[str] = []
    closing: str | None = None
    for line in text.splitlines():
        if closing is None:
            if match := FENCE.match(line):
                closing = match.group(1)[0] * 3
                lines.append("")
                continue
            lines.append(line)
        else:
            if line.strip().startswith(closing):
                closing = None
            lines.append("")
    return CODE_SPAN.sub("", "\n".join(lines))


def _destination(text: str, start: int) -> tuple[str, int] | None:
    """Read the destination of an inline link whose ``(`` is at ``start``.

    Parameters
    ----------
    text : str
        Markdown source with code already stripped.
    start : int
        Index of the opening parenthesis.

    Returns
    -------
    tuple of (str, int), or None
        The destination and the index just past the closing parenthesis;
        None when the parentheses never balance.
    """
    i = start + 1
    if i < len(text) and text[i] == "<":
        end = text.find(">", i)
        close = text.find(")", end) if end != -1 else -1
        if end == -1 or close == -1:
            return None
        return text[i + 1 : end], close + 1

    depth = 1
    out: list[str] = []
    while i < len(text):
        char = text[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                # A title after whitespace is not part of the destination.
                return "".join(out).split(None, 1)[0] if out else "", i + 1
        elif char == "\n":
            return None
        out.append(char)
        i += 1
    return None


def link_targets(text: str) -> list[str]:
    """Extract every link destination from Markdown source.

    Inline links, angle-bracketed and parenthesised destinations, and
    reference links resolved against their definitions. Code is stripped
    first, so samples do not contribute.

    Parameters
    ----------
    text : str
        Markdown source.

    Returns
    -------
    list of str
        Destinations, in the order found; reference uses last.
    """
    body = strip_code(text)
    definitions = {label.lower(): dest for label, dest in REFERENCE_DEFINITION.findall(body)}

    targets: list[str] = []
    i = 0
    while (open_bracket := body.find("[", i)) != -1:
        close_bracket = body.find("]", open_bracket)
        if close_bracket == -1:
            break
        if close_bracket + 1 < len(body) and body[close_bracket + 1] == "(":
            if found := _destination(body, close_bracket + 1):
                destination, end = found
                if destination:
                    targets.append(destination)
                i = end
                continue
        i = close_bracket + 1

    for text_part, label in REFERENCE_USE.findall(body):
        key = (label or text_part).lower()
        if key in definitions:
            targets.append(definitions[key])
    return targets


def is_checkable(target: str) -> bool:
    """Say whether a target names a repo file this check can resolve.

    Parameters
    ----------
    target : str
        A link target as written.

    Returns
    -------
    bool
        False for any URI scheme, protocol-relative URLs, bare anchors and
        placeholders.
    """
    if target.startswith(("#", "//")) or SCHEME.match(target):
        return False
    return not any(marker in target for marker in PLACEHOLDER_MARKERS)


def resolves(target: str, page: Path, root: Path) -> bool:
    """Say whether a target exists under any of the three link conventions.

    Parameters
    ----------
    target : str
        A link target as written, anchor included.
    page : Path
        The Markdown file the link is written in.
    root : Path
        Repository root.

    Returns
    -------
    bool
        True when the path exists relative to the page, the docs root or the
        repo root.
    """
    path = target.split("#", 1)[0]
    if not path:
        return True
    candidates = (page.parent / path, root / DOCS_DIRNAME / path, root / path)
    return any(candidate.exists() for candidate in candidates)


def broken_links(root: Path) -> list[tuple[Path, str]]:
    """Find every relative Markdown link in the repo that resolves to nothing.

    Parameters
    ----------
    root : Path
        Repository root.

    Returns
    -------
    list of (Path, str)
        The page and the target, for each broken link.
    """
    broken: list[tuple[Path, str]] = []
    for page in tracked_markdown_files(root):
        text = page.read_text(encoding="utf-8", errors="replace")
        for target in link_targets(text):
            if is_checkable(target) and not resolves(target, page, root):
                broken.append((page, target))
    return broken


def main(argv: list[str] | None = None) -> int:
    """Report broken relative Markdown links.

    Parameters
    ----------
    argv : list of str, optional
        Command-line arguments; ``sys.argv[1:]`` when omitted.

    Returns
    -------
    int
        0 when every link resolves, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root", type=Path, default=REPO_ROOT, help="repository root to check (default: this repo)"
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    broken = broken_links(root)
    for page, target in broken:
        print(f"{page.relative_to(root)}: {target}")
    if broken:
        print(f"{len(broken)} broken link(s)", file=sys.stderr)
        return 1
    print("every relative Markdown link resolves")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
