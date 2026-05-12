"""storage モジュールのテスト。

外部依存なしで完結するため、すべて実行可能。
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from src.storage import (
    read_parquet_local,
    serialize_list_columns_to_string,
    upsert_parquet_local,
    write_all_tsv,
)


@pytest.fixture
def sample_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "SUID": ["A1", "A2", "A3"],
            "BBG_Ticker": [["7203 JP Equity"], ["6758 JP Equity", "6502 JP Equity"], ["9984 JP Equity"]],
            "date": ["2026-01-05", "2026-01-05", "2026-01-06"],
            "Headline": ["h1", "h2", "h3"],
            "Summarized_Text": ["s1", "s2", "s3"],
            "Sector": ["Cyclical", "Tech", "Comms"],
            "Market_Sentiment": [1, 0, -1],
        }
    )


# ----------------------------------------------------------------------------
# read_parquet_local / upsert_parquet_local
# ----------------------------------------------------------------------------


def test_read_parquet_local_returns_none_when_missing(tmp_path: Path):
    assert read_parquet_local(tmp_path / "missing.parquet") is None


def test_upsert_creates_file_when_not_exists(tmp_path: Path, sample_df: pl.DataFrame):
    path = tmp_path / "out.parquet"
    merged = upsert_parquet_local(path, sample_df)
    assert path.exists()
    assert merged.height == sample_df.height
    assert read_parquet_local(path).height == sample_df.height


def test_upsert_dedups_by_key_keeping_last(tmp_path: Path, sample_df: pl.DataFrame):
    path = tmp_path / "out.parquet"
    # 1 回目
    upsert_parquet_local(path, sample_df)

    # 2 回目: A1 を上書き (Headline が変わる) + A4 を追加
    new_df = pl.DataFrame(
        {
            "SUID": ["A1", "A4"],
            "BBG_Ticker": [["7203 JP Equity"], ["6861 JP Equity"]],
            "date": ["2026-01-05", "2026-01-07"],
            "Headline": ["h1_updated", "h4"],
            "Summarized_Text": ["s1_updated", "s4"],
            "Sector": ["Cyclical", "Tech"],
            "Market_Sentiment": [1, 1],
        }
    )
    merged = upsert_parquet_local(path, new_df)

    assert merged.height == 4  # A1, A2, A3, A4
    a1 = merged.filter(pl.col("SUID") == "A1")
    assert a1["Headline"].item() == "h1_updated"


def test_upsert_creates_parent_directory(tmp_path: Path, sample_df: pl.DataFrame):
    path = tmp_path / "nested" / "subdir" / "out.parquet"
    upsert_parquet_local(path, sample_df)
    assert path.exists()


# ----------------------------------------------------------------------------
# serialize_list_columns_to_string
# ----------------------------------------------------------------------------


def test_serialize_list_column_joins_with_comma(sample_df: pl.DataFrame):
    out = serialize_list_columns_to_string(sample_df, ["BBG_Ticker"])
    assert out.schema["BBG_Ticker"] == pl.Utf8
    assert out["BBG_Ticker"].to_list() == [
        "7203 JP Equity",
        "6758 JP Equity,6502 JP Equity",
        "9984 JP Equity",
    ]


def test_serialize_list_column_noop_for_non_list_dtype(sample_df: pl.DataFrame):
    # SUID は Utf8 なので何もしない
    out = serialize_list_columns_to_string(sample_df, ["SUID"])
    assert out["SUID"].to_list() == sample_df["SUID"].to_list()


def test_serialize_list_column_handles_empty_list():
    df = pl.DataFrame({"BBG_Ticker": [[], ["X JP Equity"]]})
    out = serialize_list_columns_to_string(df, ["BBG_Ticker"])
    assert out["BBG_Ticker"].to_list() == ["", "X JP Equity"]


# ----------------------------------------------------------------------------
# write_all_tsv  ―  JP_ALL.csv / US_ALL.csv 形式
# ----------------------------------------------------------------------------


def test_write_all_tsv_format(tmp_path: Path, sample_df: pl.DataFrame):
    """JP_ALL.csv の参照仕様に従うこと。

    - TSV 区切り
    - 改行は CRLF
    - 先頭列は無名のインデックス（1, 2, 3, ...）
    - List 型カラムはカンマ結合済みで書き出す
    """
    path = tmp_path / "JP_All.csv"
    df_serialized = serialize_list_columns_to_string(sample_df, ["BBG_Ticker"])
    write_all_tsv(df_serialized, path)

    raw = path.read_bytes()
    assert raw.startswith(b"\tSUID\tBBG_Ticker\tdate\t"), "ヘッダ先頭は空セル + SUID"
    assert b"\r\n" in raw, "改行は CRLF"

    text = raw.decode("utf-8")
    lines = text.split("\r\n")
    assert lines[0] == "\tSUID\tBBG_Ticker\tdate\tHeadline\tSummarized_Text\tSector\tMarket_Sentiment"
    # 1 行目（データ）はインデックス 1 で始まる
    assert lines[1].startswith("1\tA1\t7203 JP Equity\t2026-01-05\t")
    # 2 行目はインデックス 2 で始まり、BBG_Ticker は comma-joined
    assert lines[2].startswith("2\tA2\t6758 JP Equity,6502 JP Equity\t")


def test_write_all_tsv_creates_parent_directory(tmp_path: Path, sample_df: pl.DataFrame):
    path = tmp_path / "nested" / "JP_All.csv"
    df_serialized = serialize_list_columns_to_string(sample_df, ["BBG_Ticker"])
    write_all_tsv(df_serialized, path)
    assert path.exists()


def test_write_all_tsv_empty_df(tmp_path: Path):
    """空 DataFrame でも空ファイルを生成する（参照の US_ALL.csv が 0 byte だったため）。"""
    df = pl.DataFrame(
        schema={"SUID": pl.Utf8, "BBG_Ticker": pl.Utf8, "Market_Sentiment": pl.Int64}
    )
    path = tmp_path / "empty.csv"
    write_all_tsv(df, path, write_header_when_empty=False)
    assert path.exists()
    assert path.stat().st_size == 0
