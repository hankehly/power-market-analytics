"""Print a preset's features in feature order, each with its expression.

``just show-preset demand e221`` resolves the preset — its base chain, its adds
and drops — and prints one line per feature: its rank, the preset of the chain
that added it, whether the mart tags it categorical, its ``view:column``
reference and its expression, the name people read. ``--refs`` prints only the
references and ``--expressions`` only the expressions, one per line, for
pasting into a preset file or an issue. Host-side: it reads the preset files
and the generated views, never the warehouse.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

from power_market_analytics.features.presets import (
    PRESETS_DIR,
    Preset,
    categorical_columns,
    feature_column,
    feature_expressions,
    load_presets,
    preset_tasks,
)

#: The width the description wraps at: the repo's line length.
WIDTH = 100
#: The table's columns, in order.
COLUMNS = ("#", "origin", "categorical", "reference", "expression")


def chain_of(preset: Preset, presets: dict[str, Preset]) -> list[Preset]:
    """The preset, its base, the base's base, and so on up to the root.

    Parameters
    ----------
    preset : Preset
    presets : dict of str to Preset
        The task's presets by name, the chain's members among them.

    Returns
    -------
    list of Preset
    """
    chain = [preset]
    base = preset.base
    while base is not None:
        chain.append(presets[base])
        base = chain[-1].base
    return chain


def origins(chain: list[Preset]) -> dict[str, str]:
    """The preset of the chain that added each feature of its first member.

    Walking up from the leaf, a feature was added by the first preset whose
    base does not hold it; the root has no base, so it added what is left.

    Parameters
    ----------
    chain : list of Preset
        As ``chain_of`` returns it.

    Returns
    -------
    dict of str to str
        Feature reference → preset name, in feature order.
    """
    result: dict[str, str] = {}
    for ref in chain[0].features:
        i = 0
        while i + 1 < len(chain) and ref in chain[i + 1].features:
            i += 1
        result[ref] = chain[i].name
    return result


def rows(preset: Preset, chain: list[Preset]) -> list[tuple[str, ...]]:
    """One tuple per feature, the table's columns as strings."""
    added_by = origins(chain)
    categorical = set(categorical_columns(preset))
    expressions = feature_expressions(preset)
    return [
        (
            str(rank),
            added_by[ref],
            "yes" if feature_column(ref) in categorical else "",
            ref,
            expressions.get(feature_column(ref), feature_column(ref)),
        )
        for rank, ref in enumerate(preset.features, 1)
    ]


def table(lines: list[tuple[str, ...]]) -> list[str]:
    """The header and the rows, columns two spaces apart.

    The rank is right-aligned and the last column, the expression, unpadded,
    so the ragged edge is where the long names are.
    """
    widths = [max(len(line[i]) for line in (COLUMNS, *lines)) for i in range(len(COLUMNS) - 1)]

    def formatted(line: tuple[str, ...]) -> str:
        padded = [cell.ljust(width) for cell, width in zip(line[1:-1], widths[1:], strict=True)]
        return "  ".join([line[0].rjust(widths[0]), *padded, line[-1]])

    return [formatted(COLUMNS), *(formatted(line) for line in lines)]


def render(preset: Preset, presets: dict[str, Preset]) -> str:
    """The preset's header lines and its feature table, as printed."""
    chain = chain_of(preset, presets)
    n = len(preset.features)
    header = [
        f"{preset.task}/{preset.name}: {n} feature{'' if n == 1 else 's'}",
        f"chain: {' <- '.join(member.name for member in chain)}",
        textwrap.fill(f"description: {preset.description}", width=WIDTH, subsequent_indent="  "),
        "",
    ]
    return "\n".join([*header, *table(rows(preset, chain))])


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point.

    Parameters
    ----------
    argv : list of str, optional
        Arguments; ``sys.argv[1:]`` when omitted.

    Returns
    -------
    int
        0; an unknown task or preset is an argument error (exit 2).
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("task", help="the task: demand or spot_price")
    parser.add_argument("name", help="the preset's name, its file's stem")
    what = parser.add_mutually_exclusive_group()
    what.add_argument(
        "--refs", action="store_true", help="only the view:column references, one per line"
    )
    what.add_argument(
        "--expressions", action="store_true", help="only the expressions, one per line"
    )
    parser.add_argument(
        "--presets-dir",
        type=Path,
        default=PRESETS_DIR,
        help="the directory of preset files, one subdirectory per task (default: conf/presets)",
    )
    args = parser.parse_args(argv)
    tasks = preset_tasks(args.presets_dir)
    if args.task not in tasks:
        parser.error(f"unknown task {args.task!r}; the tasks are: {', '.join(tasks)}")
    presets = load_presets(args.task, args.presets_dir)
    if args.name not in presets:
        parser.error(
            f"{args.task} has no preset {args.name!r}; its presets are: {', '.join(presets)}"
        )
    preset = presets[args.name]
    if args.refs:
        print("\n".join(preset.features))
    elif args.expressions:
        expressions = feature_expressions(preset)
        print("\n".join(expressions.get(column, column) for column in preset.columns))
    else:
        print(render(preset, presets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
