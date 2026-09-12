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

Links come from ``markdown-it-py``, the reference CommonMark parser, rather
than from a pattern of our own. Finding links looks like a job for a regular
expression and is not: a first attempt here missed reference links, angle-
bracketed and parenthesised destinations, and matched link-shaped text inside
code samples; the hand-written scanner that replaced it still got fence run
lengths and angle-bracketed reference definitions wrong. Both failure
directions are silent — a missed form lets a broken link through, a
mis-detected one fails CI on valid prose — so the parser earns its dependency.

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
from urllib.parse import unquote

from markdown_it import MarkdownIt
from markdown_it.token import Token

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIRNAME = "docs"
#: Any RFC 3986 scheme; protocol-relative and bare-anchor targets are separate.
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
#: Markers the repo uses for a name to be filled in, never a real path.
PLACEHOLDER_MARKERS = ("<", "XXX")
#: CommonMark, no docsify extensions: only the link syntax matters here.
PARSER = MarkdownIt("commonmark")


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


def _walk(tokens: list[Token]) -> list[Token]:
    """Flatten a token tree, inline children included.

    Parameters
    ----------
    tokens : list of Token
        Tokens from ``MarkdownIt.parse``.

    Returns
    -------
    list of Token
        Every token, parents before children.
    """
    out: list[Token] = []
    for token in tokens:
        out.append(token)
        if token.children:
            out.extend(_walk(token.children))
    return out


def link_targets(text: str) -> list[str]:
    """Extract every link and image destination from Markdown source.

    The parser resolves what the syntax means, so inline and reference links,
    angle-bracketed and parenthesised destinations, and code spans and fenced
    blocks are all handled by it rather than here.

    Parameters
    ----------
    text : str
        Markdown source.

    Returns
    -------
    list of str
        Destinations, in document order.
    """
    targets: list[str] = []
    for token in _walk(PARSER.parse(text)):
        attribute = {"link_open": "href", "image": "src"}.get(token.type)
        if attribute is None:
            continue
        # attrGet is typed str | int | float | None for attributes in general;
        # href and src are always strings when present.
        value = token.attrGet(attribute)
        if isinstance(value, str) and value:
            targets.append(value)
    return targets


def is_checkable(target: str) -> bool:
    """Say whether a target names a repo file this check can resolve.

    Parameters
    ----------
    target : str
        A link target as the parser reports it.

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
        A link target, anchor included.
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
    # The parser normalises destinations, so a space arrives as %20; the
    # filesystem wants it back.
    path = unquote(target.split("#", 1)[0])
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
