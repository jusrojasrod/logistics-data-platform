"""Watermark of the batch extraction from the ``tms`` schema.

The extractor reads each ``tms.<table>`` by ``updated_at``. The watermark of a
table is the largest ``updated_at`` already copied to ``raw``. One extraction
covers the window ``(previous, max_ts]``: the lower bound (the previous
watermark) is excluded and the upper bound (the current ``max(updated_at)``) is
included, so a row whose ``updated_at`` ties the upper bound is extracted now
and never again. Without a previous watermark the window has no lower bound.

:func:`compute_window` and :func:`in_window` are pure functions. This module
also builds the SQL of the table ``meta.batch_watermarks`` (one row per source
table): its DDL, the read of one watermark and the upsert that advances it.
The caller runs them inside its own transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg import sql

META_SCHEMA: str = "meta"
WATERMARK_TABLE: str = "batch_watermarks"


@dataclass(frozen=True)
class Window:
    """Extraction window ``(lower, upper]``; ``lower`` is ``None`` on the first run."""

    lower: datetime | None
    upper: datetime


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} debe tener zona horaria: {value!r}")


def compute_window(previous: datetime | None, max_ts: datetime | None) -> Window | None:
    """Return the window ``(previous, max_ts]`` or ``None`` if there are no new rows.

    ``previous`` is the stored watermark (``None`` if the table was never
    extracted) and ``max_ts`` the current ``max(updated_at)`` of the source
    table (``None`` if it is empty). There are no new rows when the table is
    empty or ``max_ts`` equals the watermark. A ``max_ts`` older than the
    watermark means the source lost rows or its clock went back, and raises
    :class:`ValueError`. Both timestamps must be timezone-aware.
    """
    if max_ts is None:
        return None
    _require_aware("max_ts", max_ts)
    if previous is None:
        return Window(lower=None, upper=max_ts)
    _require_aware("previous", previous)
    if max_ts < previous:
        raise ValueError(
            f"max(updated_at) {max_ts.isoformat()} es anterior al watermark "
            f"{previous.isoformat()}"
        )
    if max_ts == previous:
        return None
    return Window(lower=previous, upper=max_ts)


def in_window(ts: datetime, window: Window) -> bool:
    """Return whether ``ts`` falls in ``window``: lower bound excluded, upper included."""
    _require_aware("ts", ts)
    return (window.lower is None or ts > window.lower) and ts <= window.upper


def watermark_identifier(schema: str = META_SCHEMA) -> sql.Identifier:
    """Return the qualified identifier of the watermark table."""
    return sql.Identifier(schema, WATERMARK_TABLE)


def create_statement(schema: str = META_SCHEMA) -> sql.Composed:
    """Return the ``CREATE TABLE`` of the watermark table."""
    return sql.SQL(
        "create table if not exists {table} (\n"
        "    source_table text primary key,\n"
        "    watermark_ts timestamptz not null,\n"
        "    run_id text not null,\n"
        "    set_at timestamptz not null\n"
        ")"
    ).format(table=watermark_identifier(schema))


def select_statement(schema: str = META_SCHEMA) -> sql.Composed:
    """Return the query of the watermark of one source table (parameter: its name)."""
    return sql.SQL("select watermark_ts from {table} where source_table = %s").format(
        table=watermark_identifier(schema)
    )


def upsert_statement(schema: str = META_SCHEMA) -> sql.Composed:
    """Return the upsert that sets the watermark of one source table.

    Parameters, in order: ``source_table``, ``watermark_ts``, ``run_id`` and
    ``set_at``.
    """
    return sql.SQL(
        "insert into {table} (source_table, watermark_ts, run_id, set_at) "
        "values (%s, %s, %s, %s) "
        "on conflict (source_table) do update set "
        "watermark_ts = excluded.watermark_ts, "
        "run_id = excluded.run_id, "
        "set_at = excluded.set_at"
    ).format(table=watermark_identifier(schema))
