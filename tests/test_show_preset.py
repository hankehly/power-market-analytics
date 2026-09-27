"""show_preset: a preset's features in feature order, each with its origin and expression."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from power_market_analytics.features.presets import load_presets
from tests.support import import_script

script = import_script("show_preset")


def write_preset(directory: Path, name: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.yaml").write_text(textwrap.dedent(text))


@pytest.fixture
def chain(tmp_path: Path) -> Path:
    """three <- two <- one: the lag from one, the day type from two, the month re-added by three."""
    task = tmp_path / "demand"
    write_preset(
        task,
        "one",
        "description: The first.\nfeatures: [ftr_day_calendar:month, ftr_period_jepx:lag_1d_price]\n",
    )
    write_preset(
        task,
        "two",
        "description: The day type for the month.\nbase: one\nadd: [day_type]\ndrop: [month]\n",
    )
    write_preset(
        task, "three", "description: Month back.\nbase: two\nadd: [ftr_day_calendar:month]\n"
    )
    return tmp_path


class TestMain:
    def test_prints_the_chain_then_each_features_origin_mark_reference_and_expression(
        self, chain, capsys
    ):
        assert script.main(["demand", "three", "--presets-dir", str(chain)]) == 0
        assert capsys.readouterr().out.splitlines() == [
            "demand/three: 3 features",
            "chain: three <- two <- one",
            "description: Month back.",
            "",
            "#  origin  categorical  reference" + " " * 21 + "expression",
            "1  one" + " " * 18 + "ftr_period_jepx:lag_1d_price  LAG(area_price_jpy_kwh, 1d)",
            "2  two     yes" + " " * 10 + "ftr_day_calendar:day_type" + " " * 5 + "day_type",
            "3  three" + " " * 16 + "ftr_day_calendar:month" + " " * 8 + "month",
        ]

    def test_a_root_preset_is_its_own_chain(self, chain, capsys):
        assert script.main(["demand", "one", "--presets-dir", str(chain)]) == 0
        lines = capsys.readouterr().out.splitlines()
        assert lines[:2] == ["demand/one: 2 features", "chain: one"]
        assert [line.split()[1] for line in lines[5:]] == ["one", "one"]

    def test_refs_and_expressions_print_one_per_line(self, chain, capsys):
        assert script.main(["demand", "three", "--refs", "--presets-dir", str(chain)]) == 0
        assert capsys.readouterr().out.splitlines() == [
            "ftr_period_jepx:lag_1d_price",
            "ftr_day_calendar:day_type",
            "ftr_day_calendar:month",
        ]
        assert script.main(["demand", "three", "--expressions", "--presets-dir", str(chain)]) == 0
        assert capsys.readouterr().out.splitlines() == [
            "LAG(area_price_jpy_kwh, 1d)",
            "day_type",
            "month",
        ]

    def test_a_long_description_wraps_at_100_columns(self, tmp_path, capsys):
        write_preset(
            tmp_path / "demand",
            "one",
            "description: " + "word " * 40 + "\nfeatures: [ftr_day_calendar:month]\n",
        )
        assert script.main(["demand", "one", "--presets-dir", str(tmp_path)]) == 0
        lines = capsys.readouterr().out.splitlines()
        assert lines[2].startswith("description: word word")
        assert lines[3].startswith("  word") and lines[4].startswith("  word")
        assert all(len(line) <= 100 for line in lines[2:5])
        assert lines[5] == ""

    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            (["nope", "one"], "unknown task 'nope'; the tasks are: demand"),
            (["demand", "nope"], "demand has no preset 'nope'; its presets are: one, three, two"),
            (["demand", "one", "--refs", "--expressions"], "not allowed with argument"),
        ],
    )
    def test_rejects_an_unknown_task_or_preset_and_both_lists(self, chain, capsys, argv, message):
        with pytest.raises(SystemExit) as excinfo:
            script.main([*argv, "--presets-dir", str(chain)])
        assert excinfo.value.code == 2
        assert message in capsys.readouterr().err

    def test_the_repository_presets_render(self, capsys):
        features = load_presets("demand")["e212"].features
        assert script.main(["demand", "e212"]) == 0
        lines = capsys.readouterr().out.splitlines()
        assert lines[:2] == [f"demand/e212: {len(features)} features", "chain: e212"]
        rows = lines[-len(features) :]
        assert all(ref in row for ref, row in zip(features, rows, strict=True))
        assert rows[-1].startswith(str(len(features)))
