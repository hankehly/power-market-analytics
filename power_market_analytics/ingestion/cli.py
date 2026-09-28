"""The argparse plumbing the source scripts share.

Each ``scripts/<source>.py`` is one command with ``download`` and ``load`` verbs and a
subcommand per dataset (``docs/superpowers/specs/2026-09-28-source-commands-design.md``).
The bespoke parts — a download's flags and body — live in the script. What every dataset
repeats lives here: how a subcommand is registered, the three arguments every load takes,
and the run they drive.
"""

from __future__ import annotations

import argparse
import inspect
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.loader import CsvLoader, CsvTableSchema

#: A subcommand's body: ``main`` calls it with the parsed arguments.
Handler = Callable[[argparse.Namespace], None]


def add_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    run: Handler,
    *,
    help: str,
) -> argparse.ArgumentParser:
    """Register a dataset subcommand whose ``--help`` description is its handler's docstring.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction
        The ``download`` or ``load`` verb's subparsers.
    name : str
        The dataset name — the directory under ``data/<source>/``.
    run : callable
        The handler. Its docstring, dedented, is the subcommand's description, kept
        paragraph by paragraph (``RawDescriptionHelpFormatter``).
    help : str
        The one-line summary the verb's ``--help`` lists.

    Returns
    -------
    argparse.ArgumentParser
        The subcommand's parser, for its own arguments.
    """
    parser = subparsers.add_parser(
        name,
        help=help,
        description=inspect.cleandoc(run.__doc__ or ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.set_defaults(run=run)
    return parser


def add_load_arguments(
    parser: argparse.ArgumentParser,
    *,
    schema: Path,
    data: Path,
    table: str,
    data_help: str = "A file, a directory of files or a glob pattern to load.",
) -> None:
    """Add ``--schema``, ``--data`` and ``--table``, the three arguments every load takes.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The load subcommand's parser.
    schema : pathlib.Path
        Default contract, ``conf/schemas/<table>.yaml``.
    data : pathlib.Path
        Default input: the downloader's output directory, a file or a glob pattern.
    table : str
        Default destination, ``pma_raw.<table>``.
    data_help : str, optional
        The ``--data`` help, when the dataset has more to say than the default.
    """
    parser.add_argument(
        "--schema", type=Path, default=schema, help="Path to the YAML schema definition."
    )
    parser.add_argument("--data", type=Path, default=data, help=data_help)
    parser.add_argument("--table", default=table, help="Destination table (database.table).")


def load_from_args(args: argparse.Namespace, loader_cls: type[CsvLoader]) -> int:
    """Read the contract, run the loader and log the row count.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments carrying ``schema``, ``data`` and ``table``
        (:func:`add_load_arguments`).
    loader_cls : type
        The loader class, built as ``loader_cls(schema=…, filepath=args.data, table=args.table)``.
        Looked up by the caller at call time, so a test can swap it in the script's namespace.

    Returns
    -------
    int
        The number of rows written.

    Raises
    ------
    FileNotFoundError
        If the contract file does not exist — before any loader is built.
    """
    schema = CsvTableSchema.from_yaml(args.schema)
    loader = loader_cls(schema=schema, filepath=args.data, table=args.table)
    n_rows = loader.load()
    logger.info("Loaded {} rows into {}", n_rows, args.table)
    return n_rows
