"""Pruebas de la ventana de extracción por watermark ``(anterior, max_ts]``.

Sin base de datos: solo las funciones puras y el texto SQL generado.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from nexora.ingestion.batch.watermark import (
    Window,
    compute_window,
    create_statement,
    in_window,
    select_statement,
    upsert_statement,
)

T0 = datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=5)
T2 = T0 + timedelta(minutes=10)


def test_empty_source_returns_none() -> None:
    assert compute_window(None, None) is None
    assert compute_window(T0, None) is None


def test_first_window_without_watermark_has_no_lower_bound() -> None:
    window = compute_window(None, T1)

    assert window == Window(lower=None, upper=T1)
    assert in_window(T0 - timedelta(days=365), window)
    assert in_window(T1, window)
    assert not in_window(T1 + timedelta(microseconds=1), window)


def test_no_new_rows_returns_none() -> None:
    assert compute_window(T1, T1) is None


def test_same_instant_in_other_offset_is_no_new_rows() -> None:
    bogota = timezone(timedelta(hours=-5))

    assert compute_window(T1, T1.astimezone(bogota)) is None


def test_window_goes_from_previous_watermark_to_max_ts() -> None:
    assert compute_window(T0, T2) == Window(lower=T0, upper=T2)


def test_tie_with_upper_bound_is_included() -> None:
    window = compute_window(T0, T2)

    assert window is not None
    assert in_window(T2, window)


def test_equal_to_previous_watermark_is_excluded() -> None:
    window = compute_window(T0, T2)

    assert window is not None
    assert not in_window(T0, window)
    assert in_window(T0 + timedelta(microseconds=1), window)


def test_rows_of_consecutive_windows_do_not_overlap() -> None:
    first = compute_window(None, T1)
    second = compute_window(T1, T2)
    stamps = [T0, T1, T1 + timedelta(microseconds=1), T2]

    assert first is not None and second is not None
    assert [in_window(ts, first) for ts in stamps] == [True, True, False, False]
    assert [in_window(ts, second) for ts in stamps] == [False, False, True, True]


def test_max_ts_older_than_watermark_fails() -> None:
    with pytest.raises(ValueError, match="anterior al watermark"):
        compute_window(T2, T1)


def test_naive_timestamps_fail() -> None:
    naive = datetime(2026, 11, 3, 12, 0, 0)

    with pytest.raises(ValueError, match="zona horaria"):
        compute_window(None, naive)
    with pytest.raises(ValueError, match="zona horaria"):
        compute_window(naive, T1)


def test_statements_use_the_given_schema() -> None:
    ddl = create_statement("it_ab12_meta").as_string(None)
    read = select_statement().as_string(None)
    upsert = upsert_statement("it_ab12_meta").as_string(None)

    assert '"it_ab12_meta"."batch_watermarks"' in ddl
    assert "source_table text primary key" in ddl
    assert '"meta"."batch_watermarks"' in read
    assert "on conflict (source_table) do update" in upsert
    assert upsert.count("%s") == 4
