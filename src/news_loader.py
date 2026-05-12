"""ニュースデータの取得（PostgreSQL）。

旧版では BQuant 上の S3+Spark でニュース CSV を読み込んでいたが、AWS 移行後は
直接 PostgreSQL に問い合わせる構成に変更。返り値の Polars DataFrame は
旧スキーマ互換の列名（``SUID`` / ``Headline`` / ``Body`` / ``CaptureTime`` 等）に
正規化され、後段の :mod:`src.enrichment` がそのまま使える。

対象テーブル名は ``DBConfig.news_table`` で外出し（環境変数 ``DB_NEWS_TABLE``）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import psycopg
from psycopg import sql as pg_sql

from src.settings import DBConfig

NEWS_OUTPUT_COLUMNS: list[str] = [
    "SUID",
    "Headline",
    "Body",
    "CaptureTime",
    "related_rics",
    "attribution",
]
"""``query_news`` が返す DataFrame の列順。"""


_NEWS_QUERY_TEMPLATE = """
SELECT
    unique_story_index   AS "SUID",
    headline             AS "Headline",
    story_body           AS "Body",
    story_date_time_utc9 AS "CaptureTime",
    related_rics,
    attribution
FROM {table}
WHERE
    -- パーティション列で枝刈り（year/month/day_utc9）してから時刻で精密絞り込み
    (year_utc9, month_utc9, day_utc9) BETWEEN
        (%(start_year)s, %(start_month)s, %(start_day)s)
        AND (%(end_year)s, %(end_month)s, %(end_day)s)
    AND story_date_time_utc9 >= %(start)s
    AND story_date_time_utc9 <  %(end)s
    AND language = %(language)s
    AND length(story_body) > %(min_body_length)s
    AND string_to_array(topics, ' ') && %(include_topics)s
"""

_EXCLUDE_TOPICS_CLAUSE = (
    "    AND NOT (string_to_array(topics, ' ') && %(exclude_topics)s)\n"
)


def _qualified_identifier(name: str) -> pg_sql.Identifier:
    """``"schema.table"`` 形式の文字列を ``Identifier`` に安全に変換する。"""
    return pg_sql.Identifier(*name.split("."))


def build_news_query(
    start_date: str,
    end_date: str,
    language: str,
    include_topics: list[str],
    exclude_topics: list[str] | None = None,
    min_body_length: int = 50,
    table_name: str = "news_all",
) -> tuple[pg_sql.Composed, dict]:
    """ニュース取得用の SQL とパラメータを構築する。

    日付は ``[start_date 00:00, end_date+1 00:00)`` の半開区間として扱う
    （つまり ``end_date`` 当日分を含む）。

    Args:
        start_date: 取得開始日（``YYYY-MM-DD``）。
        end_date: 取得終了日（``YYYY-MM-DD``、含む）。
        language: ``"ja"`` / ``"en"`` 等。
        include_topics: 抽出対象トピック（OR 条件）。
        exclude_topics: 除外対象トピック（OR 条件）。空または ``None`` なら除外しない。
        min_body_length: 本文の最小文字数（これ以下は除外）。
        table_name: 対象テーブル名（``"<schema>.<table>"`` 可）。

    Returns:
        ``(sql, params)`` のタプル。``sql`` は ``psycopg.sql.Composed``、
        ``params`` は ``psycopg`` 互換の dict。

    Raises:
        ValueError: 日付形式が不正、または ``start_date > end_date`` の場合。
    """
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end_inclusive = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(
            "日付の形式が不正です。'YYYY-MM-DD' で指定してください。"
        ) from e
    if start > end_inclusive:
        raise ValueError("終了日が開始日より前に設定されています。")

    end_exclusive = end_inclusive + timedelta(days=1)

    params: dict = {
        "start": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end": end_exclusive.strftime("%Y-%m-%d %H:%M:%S"),
        "start_year": start.strftime("%Y"),
        "start_month": start.strftime("%m"),
        "start_day": start.strftime("%d"),
        "end_year": end_inclusive.strftime("%Y"),
        "end_month": end_inclusive.strftime("%m"),
        "end_day": end_inclusive.strftime("%d"),
        "language": language,
        "include_topics": list(include_topics),
        "min_body_length": min_body_length,
    }

    sql_text = _NEWS_QUERY_TEMPLATE
    if exclude_topics:
        sql_text += _EXCLUDE_TOPICS_CLAUSE
        params["exclude_topics"] = list(exclude_topics)

    sql = pg_sql.SQL(sql_text).format(table=_qualified_identifier(table_name))
    return sql, params


def query_news(
    db: DBConfig,
    start_date: str,
    end_date: str,
    language: str,
    include_topics: list[str],
    exclude_topics: list[str] | None = None,
    min_body_length: int = 50,
) -> pl.DataFrame:
    """指定条件でニュースを取得し、Polars DataFrame として返す。

    Args:
        db: PostgreSQL 接続設定（テーブル名は ``db.news_table`` を使用）。
        start_date: ``YYYY-MM-DD``。
        end_date: ``YYYY-MM-DD``（含む）。
        language: ``"ja"`` / ``"en"`` 等。
        include_topics: 抽出対象トピック（OR 条件）。
        exclude_topics: 除外対象トピック。
        min_body_length: 本文の最小文字数。

    Returns:
        :data:`NEWS_OUTPUT_COLUMNS` の列順を持つ DataFrame。
    """
    sql, params = build_news_query(
        start_date=start_date,
        end_date=end_date,
        language=language,
        include_topics=include_topics,
        exclude_topics=exclude_topics,
        min_body_length=min_body_length,
        table_name=db.news_table,
    )

    with psycopg.connect(db.to_conninfo()) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]

    if not rows:
        return pl.DataFrame(schema={c: pl.Utf8 for c in NEWS_OUTPUT_COLUMNS})

    df = pl.DataFrame(rows, schema=cols, orient="row")
    return df.select(NEWS_OUTPUT_COLUMNS)


def deduplicate_by_clean_headline(df: pl.DataFrame) -> pl.DataFrame:
    """ヘッドラインの「プロバイダ名:」プレフィクスを除いた本文部分で重複削除する。

    Bloomberg ニュースは同じ内容が複数プロバイダから配信されることが多いため、
    ``"RTRS: 速報　日銀..."`` のような ``"<source>:"`` プレフィクスを除いた
    本文部分で同一性を判定する。``CaptureTime`` 昇順で最古を残す。

    Args:
        df: ``Headline`` と ``CaptureTime`` 列を持つ DataFrame。

    Returns:
        重複削除後の DataFrame。
    """
    return (
        df.with_columns(
            pl.col("Headline").str.replace(r"^[^:]+:\s*", "").alias("_clean_hl")
        )
        .sort("CaptureTime")
        .unique(subset=["_clean_hl"], keep="first")
        .drop("_clean_hl")
    )
