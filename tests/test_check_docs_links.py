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


class TestStripCode:
    def test_it_blanks_a_fenced_block(self):
        text = "before\n```\n[a](gone.md)\n```\nafter"
        assert check_docs_links.strip_code(text) == "before\n\n\n\nafter"

    def test_it_blanks_a_tilde_fence(self):
        assert "gone.md" not in check_docs_links.strip_code("~~~\n[a](gone.md)\n~~~")

    def test_a_longer_fence_is_not_closed_by_a_shorter_run(self):
        text = "````\n[a](gone.md)\n```\nstill code\n````\nafter"
        assert "gone.md" not in check_docs_links.strip_code(text)

    def test_an_unclosed_fence_swallows_the_rest(self):
        assert "gone.md" not in check_docs_links.strip_code("```\n[a](gone.md)")

    def test_it_removes_inline_code_spans(self):
        assert check_docs_links.strip_code("see `[a](gone.md)` here") == "see  here"

    def test_it_leaves_ordinary_prose_alone(self):
        assert check_docs_links.strip_code("plain [a](b.md)") == "plain [a](b.md)"


class TestLinkTargets:
    def test_it_reads_inline_and_image_links(self):
        text = "see [a](one.md) and ![alt](assets/two.png)"
        assert check_docs_links.link_targets(text) == ["one.md", "assets/two.png"]

    def test_it_stops_a_target_at_a_markdown_title(self):
        assert check_docs_links.link_targets('[a](one.md "Title")') == ["one.md"]

    def test_it_reads_an_angle_bracketed_destination(self):
        assert check_docs_links.link_targets("[a](<some file.md>)") == ["some file.md"]

    def test_it_keeps_balanced_parentheses_in_a_destination(self):
        assert check_docs_links.link_targets("[a](notes(1).md)") == ["notes(1).md"]

    def test_it_resolves_a_reference_link(self):
        text = "see [the guide][g]\n\n[g]: guide.md"
        assert check_docs_links.link_targets(text) == ["guide.md"]

    def test_it_resolves_a_collapsed_reference_link(self):
        text = "see [guide][]\n\n[guide]: guide.md"
        assert check_docs_links.link_targets(text) == ["guide.md"]

    def test_a_reference_use_without_a_definition_yields_nothing(self):
        assert check_docs_links.link_targets("see [the guide][missing]") == []

    def test_it_ignores_links_inside_code(self):
        assert check_docs_links.link_targets("```\n[a](gone.md)\n```") == []

    def test_an_empty_destination_is_skipped(self):
        assert check_docs_links.link_targets("[a]()") == []

    def test_an_unbalanced_destination_is_skipped(self):
        assert check_docs_links.link_targets("[a](open.md") == []

    def test_a_destination_broken_across_lines_is_skipped(self):
        assert check_docs_links.link_targets("[a](one\n.md)") == []

    def test_an_unclosed_angle_destination_is_skipped(self):
        assert check_docs_links.link_targets("[a](<one.md") == []

    def test_an_angle_destination_without_a_closing_paren_is_skipped(self):
        assert check_docs_links.link_targets("[a](<one.md>") == []

    def test_an_unclosed_bracket_stops_the_scan(self):
        assert check_docs_links.link_targets("[a(one.md)") == []

    def test_a_bracket_that_is_not_a_link_is_skipped(self):
        assert check_docs_links.link_targets("array[0] and [a](b.md)") == ["b.md"]

    def test_it_finds_nothing_in_prose(self):
        assert check_docs_links.link_targets("no links here") == []


class TestIsCheckable:
    @pytest.mark.parametrize(
        "target",
        [
            "https://example.invalid/x",
            "HTTPS://example.invalid/x",
            "ftp://example.invalid/file",
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

    def test_a_windows_style_path_is_not_mistaken_for_a_scheme(self):
        assert check_docs_links.is_checkable("docs/a.md") is True


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

    def test_a_broken_reference_link_is_reported(self, tmp_path):
        write(tmp_path, "docs/a.md", "see [the guide][g]\n\n[g]: missing.md")
        git_repo(tmp_path)
        assert check_docs_links.broken_links(tmp_path) == [(tmp_path / "docs/a.md", "missing.md")]

    def test_a_link_inside_a_code_fence_is_not_reported(self, tmp_path):
        write(tmp_path, "docs/a.md", "```markdown\n[example](nowhere.md)\n```")
        git_repo(tmp_path)
        assert check_docs_links.broken_links(tmp_path) == []

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
