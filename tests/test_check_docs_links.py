"""Tests for scripts/check_docs_links.py."""

from __future__ import annotations

import subprocess

import pytest

from tests.support import import_script

check_docs_links = import_script("check_docs_links")


def git_repo(root):
    """Make ``root`` a git repo with every file in it tracked."""
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


def write(root, relative, text=""):
    """Write ``text`` to ``root / relative``, creating parents."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestLinkTargets:
    def test_it_reads_inline_and_image_links(self):
        text = "see [a](one.md) and ![alt](assets/two.png)"
        assert check_docs_links.link_targets(text) == ["one.md", "assets/two.png"]

    def test_it_stops_a_target_at_a_markdown_title(self):
        assert check_docs_links.link_targets('[a](one.md "Title")') == ["one.md"]

    def test_it_finds_nothing_in_prose(self):
        assert check_docs_links.link_targets("no links here") == []


class TestIsCheckable:
    @pytest.mark.parametrize(
        "target",
        [
            "https://example.invalid/x",
            "http://example.invalid/x",
            "mailto:someone@example.invalid",
            "tel:+81000000000",
            "//example.invalid/x",
            "#a-heading",
        ],
    )
    def test_it_skips_targets_outside_the_repo(self, target):
        assert check_docs_links.is_checkable(target) is False

    @pytest.mark.parametrize("target", ["assets/O-001-<slug>.png", "assets/R-XXX-E-001.png"])
    def test_it_skips_placeholder_targets(self, target):
        assert check_docs_links.is_checkable(target) is False

    def test_it_checks_a_plain_relative_path(self):
        assert check_docs_links.is_checkable("research/demand/observations.md") is True


class TestResolves:
    def test_it_accepts_a_path_relative_to_the_page(self, tmp_path):
        page = write(tmp_path, "docs/research/index.md")
        write(tmp_path, "docs/research/assets/plot.png")
        assert check_docs_links.resolves("assets/plot.png", page, tmp_path) is True

    def test_it_accepts_a_path_relative_to_the_docs_root(self, tmp_path):
        page = write(tmp_path, "docs/research/index.md")
        write(tmp_path, "docs/research/demand/observations.md")
        target = "research/demand/observations.md"
        assert check_docs_links.resolves(target, page, tmp_path) is True

    def test_it_accepts_a_path_relative_to_the_repo_root(self, tmp_path):
        page = write(tmp_path, "README.md")
        write(tmp_path, "docs/guide.md")
        assert check_docs_links.resolves("docs/guide.md", page, tmp_path) is True

    def test_it_ignores_the_anchor(self, tmp_path):
        page = write(tmp_path, "docs/a.md")
        write(tmp_path, "docs/b.md")
        assert check_docs_links.resolves("b.md#some-heading", page, tmp_path) is True

    def test_a_bare_anchor_on_the_same_page_resolves(self, tmp_path):
        page = write(tmp_path, "docs/a.md")
        assert check_docs_links.resolves("#heading", page, tmp_path) is True

    def test_it_rejects_a_path_that_exists_nowhere(self, tmp_path):
        page = write(tmp_path, "docs/a.md")
        assert check_docs_links.resolves("gone.md", page, tmp_path) is False


class TestTrackedMarkdownFiles:
    def test_it_lists_tracked_markdown_only(self, tmp_path):
        write(tmp_path, "docs/a.md")
        write(tmp_path, "docs/b.txt")
        git_repo(tmp_path)
        write(tmp_path, "docs/untracked.md")
        found = {p.name for p in check_docs_links.tracked_markdown_files(tmp_path)}
        assert found == {"a.md"}


class TestBrokenLinks:
    def test_it_reports_only_the_unresolvable_target(self, tmp_path):
        write(
            tmp_path,
            "docs/a.md",
            "[ok](b.md) [gone](missing.md) [ext](https://example.invalid) [ph](assets/R-XXX.png)",
        )
        write(tmp_path, "docs/b.md")
        git_repo(tmp_path)
        assert check_docs_links.broken_links(tmp_path) == [(tmp_path / "docs/a.md", "missing.md")]

    def test_it_finds_nothing_when_every_link_resolves(self, tmp_path):
        write(tmp_path, "docs/a.md", "[ok](b.md)")
        write(tmp_path, "docs/b.md")
        git_repo(tmp_path)
        assert check_docs_links.broken_links(tmp_path) == []


class TestMain:
    def test_it_exits_0_and_says_so_when_clean(self, tmp_path, capsys):
        write(tmp_path, "docs/a.md", "[ok](b.md)")
        write(tmp_path, "docs/b.md")
        git_repo(tmp_path)
        assert check_docs_links.main(["--root", str(tmp_path)]) == 0
        assert "every relative Markdown link resolves" in capsys.readouterr().out

    def test_it_exits_1_and_names_each_broken_link(self, tmp_path, capsys):
        write(tmp_path, "docs/a.md", "[gone](missing.md)")
        git_repo(tmp_path)
        assert check_docs_links.main(["--root", str(tmp_path)]) == 1
        captured = capsys.readouterr()
        assert "docs/a.md: missing.md" in captured.out
        assert "1 broken link(s)" in captured.err

    def test_it_defaults_to_this_repo_and_passes(self, capsys):
        assert check_docs_links.main([]) == 0
        assert "every relative Markdown link resolves" in capsys.readouterr().out
