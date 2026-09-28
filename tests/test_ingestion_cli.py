"""The argparse plumbing the source scripts share (``power_market_analytics.ingestion.cli``)."""

from __future__ import annotations

import argparse

import pytest
from loguru import logger

from power_market_analytics.ingestion.cli import (
    add_load_arguments,
    add_subcommand,
    load_from_args,
)
from power_market_analytics.ingestion.loader import CsvTableSchema
from tests.support import REPO_ROOT


class RecordingLoader:
    """A loader stand-in: records its constructor kwargs and whether ``load()`` ran."""

    built: list[dict] = []

    def __init__(self, schema, filepath, table):
        self.record = {"schema": schema, "filepath": filepath, "table": table, "loaded": False}
        type(self).built.append(self.record)

    def load(self) -> int:
        self.record["loaded"] = True
        return 7


@pytest.fixture(autouse=True)
def reset_recording_loader():
    RecordingLoader.built = []
    yield
    RecordingLoader.built = []


DEFAULTS = {
    "schema": REPO_ROOT / "conf/schemas/jepx_spot.yaml",
    "data": REPO_ROOT / "data/jepx/spot",
    "table": "pma_raw.jepx_spot",
}


def load_parser(**overrides) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="t")
    add_load_arguments(parser, **{**DEFAULTS, **overrides})
    return parser


class TestAddLoadArguments:
    def test_defaults_are_the_datasets(self):
        args = load_parser().parse_args([])

        assert (args.schema, args.data, args.table) == (
            DEFAULTS["schema"],
            DEFAULTS["data"],
            DEFAULTS["table"],
        )

    def test_overrides_are_paths_and_a_string(self, tmp_path):
        args = load_parser().parse_args(
            [
                "--schema",
                str(tmp_path / "s.yaml"),
                "--data",
                str(tmp_path / "x.csv"),
                "--table",
                "db.t",
            ]
        )

        assert args.schema == tmp_path / "s.yaml"
        assert args.data == tmp_path / "x.csv"
        assert args.table == "db.t"

    def test_the_data_help_is_the_datasets_when_given(self, capsys):
        parser = load_parser(data_help="The manifest is read two levels up.")

        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["-h"])

        assert exc.value.code == 0
        assert "The manifest is read two levels up." in capsys.readouterr().out

    def test_the_data_help_has_a_default(self, capsys):
        with pytest.raises(SystemExit):
            load_parser().parse_args(["-h"])

        assert "A file, a directory of files or a glob pattern to load." in capsys.readouterr().out


class TestLoadFromArgs:
    def test_reads_the_contract_runs_the_loader_and_returns_the_count(self):
        args = load_parser().parse_args([])

        assert load_from_args(args, RecordingLoader) == 7

        (built,) = RecordingLoader.built
        assert isinstance(built["schema"], CsvTableSchema)
        assert built["schema"].grain == ["trade_date", "time_code"]
        assert built["filepath"] == DEFAULTS["data"]
        assert built["table"] == "pma_raw.jepx_spot"
        assert built["loaded"] is True

    def test_a_missing_contract_fails_before_the_loader_is_built(self, tmp_path):
        args = load_parser().parse_args(["--schema", str(tmp_path / "nope.yaml")])

        with pytest.raises(FileNotFoundError):
            load_from_args(args, RecordingLoader)

        assert RecordingLoader.built == []

    def test_logs_the_row_count_and_the_table(self):
        args = load_parser().parse_args(["--table", "db.t"])
        messages: list[str] = []
        sink = logger.add(lambda m: messages.append(m.record["message"]), level="INFO")
        try:
            load_from_args(args, RecordingLoader)
        finally:
            logger.remove(sink)

        assert "Loaded 7 rows into db.t" in messages


class TestAddSubcommand:
    @staticmethod
    def parser_with(run, help="A thing"):
        parser = argparse.ArgumentParser(prog="t")
        subparsers = parser.add_subparsers(dest="dataset", required=True)
        sub = add_subcommand(subparsers, "thing", run, help=help)
        return parser, sub

    def test_run_is_the_handler_and_the_parser_is_returned(self):
        calls = []

        def run(args):
            """Do the thing."""
            calls.append(args)

        parser, sub = self.parser_with(run)
        sub.add_argument("--n", type=int, default=1)

        args = parser.parse_args(["thing", "--n", "3"])
        args.run(args)

        assert calls == [args]
        assert args.n == 3

    def test_description_is_the_handlers_docstring_dedented(self, capsys):
        def run(args):
            """First line.

            Second paragraph, kept apart from the first.
            """

        parser, _sub = self.parser_with(run)

        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["thing", "-h"])

        out = capsys.readouterr().out
        assert exc.value.code == 0
        assert "First line.\n\nSecond paragraph, kept apart from the first." in out

    def test_the_help_is_what_the_verb_lists(self, capsys):
        def run(args):
            """Do the thing."""

        parser, _sub = self.parser_with(run, help="One line about the thing")

        with pytest.raises(SystemExit):
            parser.parse_args(["-h"])

        assert "One line about the thing" in capsys.readouterr().out

    def test_a_handler_without_a_docstring_gets_an_empty_description(self, capsys):
        def run(args):
            pass

        parser, _sub = self.parser_with(run)

        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["thing", "-h"])

        assert exc.value.code == 0
        assert "usage: t thing [-h]" in capsys.readouterr().out
