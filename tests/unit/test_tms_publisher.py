"""Pruebas de la regla de disponibilidad y de la selección del publicador TMS."""

from __future__ import annotations

from datetime import date

import pytest

from nexora.generators.config import LATE_ARRIVAL_LAG_DAYS
from nexora.generators.defects import inject_defects
from nexora.generators.shipments import generate_shipments
from nexora.generators.tms_publisher import (
    TMS_SHIPMENTS,
    TMS_TABLES,
    available_date,
    build_parser,
    group_available_dates,
    select_shipments,
)


def _late_rows(rows: tuple[dict[str, str], ...]) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if available_date(row["shipment_id"], date.fromisoformat(row["pickup_date"]))
        != date.fromisoformat(row["pickup_date"])
    ]


def test_available_date_is_deterministic() -> None:
    pickup = date(2026, 11, 1)
    first = [available_date(f"SHP-{i:06d}", pickup) for i in range(1, 501)]
    second = [available_date(f"SHP-{i:06d}", pickup) for i in range(1, 501)]
    assert first == second


def test_available_date_lag_within_range() -> None:
    pickup = date(2026, 11, 1)
    min_lag, max_lag = LATE_ARRIVAL_LAG_DAYS
    lags = {(available_date(f"SHP-{i:06d}", pickup) - pickup).days for i in range(1, 5001)}
    assert 0 in lags
    assert lags - {0}
    assert all(lag == 0 or min_lag <= lag <= max_lag for lag in lags)


def test_late_share_between_one_and_three_percent() -> None:
    rows = generate_shipments(2000, 42)
    share = len(_late_rows(rows)) / len(rows)
    assert 0.01 <= share <= 0.03


def test_copies_of_a_shipment_share_the_date() -> None:
    rows = inject_defects(generate_shipments(500, 42, 1), 42).rows
    ids = [row["shipment_id"] for row in rows]
    copied = {shipment_id for shipment_id in ids if ids.count(shipment_id) > 1}
    assert copied
    dates = group_available_dates(rows)
    for row in rows:
        if row["shipment_id"] in copied:
            own = available_date(row["shipment_id"], date.fromisoformat(row["pickup_date"]))
            assert own == dates[row["shipment_id"]]


def test_select_shipments_keeps_groups_together() -> None:
    rows = inject_defects(generate_shipments(500, 42, 1), 42).rows
    until = date(2026, 11, 7)
    selected = select_shipments(rows, until)
    selected_ids = {row["shipment_id"] for row in selected}
    for row in rows:
        assert (row["shipment_id"] in selected_ids) == (
            group_available_dates([row])[row["shipment_id"]] <= until
        )
    assert sum(row["shipment_id"] in selected_ids for row in rows) == len(selected)
    assert 0 < len(selected) < len(rows)


def test_select_shipments_without_until_keeps_all_rows() -> None:
    rows = generate_shipments(50, 42)
    assert select_shipments(rows) == list(rows)


def test_late_rows_wait_for_their_date() -> None:
    rows = generate_shipments(2000, 42)
    late = _late_rows(rows)
    assert late
    row = late[0]
    pickup = date.fromisoformat(row["pickup_date"])
    ready = available_date(row["shipment_id"], pickup)
    assert row not in select_shipments(rows, ready - date.resolution)
    assert row in select_shipments(rows, ready)


def test_tables_and_ddl() -> None:
    assert [table.name for table in TMS_TABLES] == [
        "carriers",
        "routes",
        "service_types",
        "shipments",
    ]
    ddl = TMS_SHIPMENTS.create_statement().as_string(None)
    assert '"tms"."shipments"' in ddl
    assert "tms_row_id bigint generated always as identity primary key" in ddl
    assert "updated_at timestamptz not null default clock_timestamp()" in ddl
    assert "unique" not in ddl
    carriers = TMS_TABLES[0].create_statement("it_x_tms").as_string(None)
    assert '"it_x_tms"."carriers"' in carriers
    assert 'unique ("carrier_id")' in carriers
    assert TMS_SHIPMENTS.lock_key() == "tms.shipments"


def test_parser_reads_until_as_date() -> None:
    args = build_parser().parse_args(["--input", "data/demo", "--until", "2026-11-03"])
    assert args.until == date(2026, 11, 3)
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--input", "data/demo", "--until", "03/11/2026"])
