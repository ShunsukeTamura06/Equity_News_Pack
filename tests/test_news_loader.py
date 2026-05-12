"""news_loader モジュールのテスト。

SQL ビルドは純粋ロジックなので網羅的に、PostgreSQL 接続はモックで疑似テストする。
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import polars as pl
import psycopg
import pytest

from src.news_loader import (
    NEWS_OUTPUT_COLUMNS,
    build_news_query,
    deduplicate_by_clean_headline,
    query_news,
)
from src.settings import DBConfig


def _render_sql(composed) -> str:
    """``psycopg.sql.Composed`` を文字列に変換する（接続不要）。"""
    return composed.as_string(None)


@pytest.fixture
def jp_db() -> DBConfig:
    return DBConfig(
        host="db.example", port=5432, database="postgres",
        user="u", password="p", news_table="news_all",
    )


# ============================================================================
# build_news_query ―― SQL 構築の純粋ロジック
# ============================================================================


def test_build_news_query_basic_clauses():
    composed, params = build_news_query(
        start_date="2026-01-01",
        end_date="2026-01-07",
        language="ja",
        include_topics=["JPS", "KABU"],
    )
    sql = _render_sql(composed)
    # 期間・言語・トピックの条件が SQL に含まれる
    assert "story_date_time_utc9" in sql
    assert "language" in sql.lower()
    assert "topics" in sql.lower()
    # パラメータ化されている（SQL インジェクション防止）
    assert params["start"].startswith("2026-01-01")
    assert params["end"].startswith("2026-01-08")  # end_date は inclusive のため翌日 0 時未満
    assert params["language"] == "ja"
    assert params["include_topics"] == ["JPS", "KABU"]


def test_build_news_query_with_exclude_topics():
    composed, params = build_news_query(
        start_date="2026-01-01",
        end_date="2026-01-07",
        language="en",
        include_topics=["USS"],
        exclude_topics=["HEADS"],
    )
    sql = _render_sql(composed)
    assert "NOT" in sql
    assert params["exclude_topics"] == ["HEADS"]


def test_build_news_query_without_exclude_omits_clause():
    composed, _ = build_news_query(
        start_date="2026-01-01",
        end_date="2026-01-07",
        language="ja",
        include_topics=["JPS"],
    )
    sql = _render_sql(composed)
    assert "exclude_topics" not in sql


def test_build_news_query_date_validation():
    with pytest.raises(ValueError, match="日付の形式"):
        build_news_query("2026/01/01", "2026-01-07", "ja", ["JPS"])
    with pytest.raises(ValueError, match="終了日"):
        build_news_query("2026-01-07", "2026-01-01", "ja", ["JPS"])


def test_build_news_query_includes_partition_pruning():
    """パーティション列 (year_utc9 等) を使った WHERE 句で性能改善する。"""
    composed, _ = build_news_query("2026-01-01", "2026-01-07", "ja", ["JPS"])
    sql = _render_sql(composed)
    assert "year_utc9" in sql or "month_utc9" in sql or "day_utc9" in sql


def test_build_news_query_min_body_length():
    composed, params = build_news_query(
        "2026-01-01", "2026-01-07", "ja", ["JPS"], min_body_length=100
    )
    sql = _render_sql(composed)
    assert "story_body" in sql
    assert params["min_body_length"] == 100


def test_build_news_query_uses_table_name_safely():
    """テーブル名は ``Identifier`` 経由で SQL に埋め込まれる（SQL インジェクション防止）。"""
    composed, _ = build_news_query(
        "2026-01-01", "2026-01-07", "ja", ["JPS"], table_name="my_schema.my_news",
    )
    sql = _render_sql(composed)
    # ダブルクォートで安全にエスケープされている
    assert '"my_schema"."my_news"' in sql


# ============================================================================
# query_news ―― psycopg をモックして疑似実行
# ============================================================================


def test_query_news_returns_polars_with_normalized_columns(mocker, jp_db):
    """DB の列名 (unique_story_index 等) を内部スキーマ (SUID 等) にリネームすること。"""
    mock_cursor = MagicMock()
    mock_cursor.description = [
        ("SUID",), ("Headline",), ("Body",),
        ("CaptureTime",), ("related_rics",), ("attribution",),
    ]
    mock_cursor.fetchall.return_value = [
        ("S1", "h1", "b1", datetime(2026, 1, 5, 10, 0), "7203.T 6758.T", "RTRS"),
        ("S2", "h2", "b2", datetime(2026, 1, 5, 11, 0), "9984.T", "RTRS"),
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_connect = mocker.patch("src.news_loader.psycopg.connect")
    mock_connect.return_value.__enter__.return_value = mock_conn

    df = query_news(jp_db, "2026-01-05", "2026-01-05", "ja", ["JPS"])

    # 接続文字列が DB 設定から組み立てられている
    mock_connect.assert_called_once_with(jp_db.to_conninfo())
    # 出力 DataFrame の構造
    assert df.height == 2
    assert df.columns == NEWS_OUTPUT_COLUMNS
    assert df["SUID"].to_list() == ["S1", "S2"]


def test_query_news_returns_empty_dataframe_when_no_rows(mocker, jp_db):
    mock_cursor = MagicMock()
    mock_cursor.description = [
        ("SUID",), ("Headline",), ("Body",),
        ("CaptureTime",), ("related_rics",), ("attribution",),
    ]
    mock_cursor.fetchall.return_value = []
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_connect = mocker.patch("src.news_loader.psycopg.connect")
    mock_connect.return_value.__enter__.return_value = mock_conn

    df = query_news(jp_db, "2026-01-05", "2026-01-05", "ja", ["JPS"])
    assert df.is_empty()
    assert df.columns == NEWS_OUTPUT_COLUMNS


# ============================================================================
# deduplicate_by_clean_headline
# ============================================================================


def test_deduplicate_by_clean_headline_keeps_first_by_capture_time():
    """``"プロバイダ: 本文"`` 形式から本文だけ取り出し、最古を残す。"""
    df = pl.DataFrame(
        {
            "SUID": ["A", "B", "C"],
            "Headline": [
                "RTRS: 速報　日銀、政策金利を据え置き",
                "RTRS: 速報　日銀、政策金利を据え置き",  # A と内容同じ -> 後発 B は除外
                "RTRS: 別の見出し",
            ],
            "CaptureTime": [
                datetime(2026, 1, 5, 10, 0),
                datetime(2026, 1, 5, 11, 0),
                datetime(2026, 1, 5, 12, 0),
            ],
        }
    )
    out = deduplicate_by_clean_headline(df).sort("SUID")
    assert out["SUID"].to_list() == ["A", "C"]


def test_deduplicate_by_clean_headline_handles_no_colon():
    """``:`` を含まない見出しはそのまま比較対象になる。"""
    df = pl.DataFrame(
        {
            "SUID": ["A", "B"],
            "Headline": ["plain headline", "plain headline"],
            "CaptureTime": [datetime(2026, 1, 5, 10, 0), datetime(2026, 1, 5, 11, 0)],
        }
    )
    out = deduplicate_by_clean_headline(df)
    assert out.height == 1
    assert out["SUID"].item() == "A"
