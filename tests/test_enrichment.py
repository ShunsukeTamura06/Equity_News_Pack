"""enrichment モジュールのテスト。

純粋ロジック関数は単体テストで網羅、blpapi 依存関数は ``@pytest.mark.blpapi``
マーカで分離する（Bloomberg Terminal が立ち上がっていない環境ではスキップ）。
"""
from __future__ import annotations

import polars as pl
import pytest

from src.enrichment import (
    add_date_from_capture_time,
    aggregate_by_suid,
    convert_rics_to_bbg_tickers,
    exclude_by_suid,
    explode_related_rics,
    fetch_index_universe_tickers,
    fetch_sectors_for_tickers,
    filter_ai_generated_news,
    filter_to_index_universe,
    split_by_ric_presence,
)
from src.region_config import JP_CONFIG, US_CONFIG


# ============================================================================
# 純粋ロジック関数（外部依存なし）
# ============================================================================


def test_filter_ai_generated_news_jp():
    df = pl.DataFrame(
        {
            "Body": [
                "ブルームバーグ・オートメーションが配信した記事...",
                "通常のニュース本文",
                "  ブルームバーグ・オートメーション\n機械記事",  # 先頭空白あり
                None,
            ]
        }
    )
    out = filter_ai_generated_news(df, JP_CONFIG.ai_generated_prefix_pattern)
    assert out.height == 2  # 2 行目と 4 行目（None は fill_null で空文字扱い）
    assert out["Body"].to_list() == ["通常のニュース本文", None]


def test_filter_ai_generated_news_us():
    df = pl.DataFrame(
        {
            "Body": [
                "By Bloomberg Automation...",
                "Normal article",
            ]
        }
    )
    out = filter_ai_generated_news(df, US_CONFIG.ai_generated_prefix_pattern)
    assert out.height == 1
    assert out["Body"].to_list() == ["Normal article"]


def test_split_by_ric_presence():
    df = pl.DataFrame(
        {
            "SUID": ["A", "B", "C", "D"],
            "related_rics": ["7203.T 6758.T", "  ", None, "AAPL.O"],
        }
    )
    has, missing = split_by_ric_presence(df)
    assert has["SUID"].to_list() == ["A", "D"]
    assert missing["SUID"].to_list() == ["B", "C"]


def test_aggregate_by_suid_with_list_cols():
    df = pl.DataFrame(
        {
            "SUID": ["A", "A", "B"],
            "BBG_Ticker": ["7203 JP Equity", "6758 JP Equity", "9984 JP Equity"],
            "Headline": ["h1", "h1", "h2"],
            "Body": ["b1", "b1", "b2"],
        }
    )
    out = aggregate_by_suid(df, list_cols=["BBG_Ticker"]).sort("SUID")
    assert out.height == 2
    a_row = out.filter(pl.col("SUID") == "A").row(0, named=True)
    assert sorted(a_row["BBG_Ticker"]) == ["6758 JP Equity", "7203 JP Equity"]
    assert a_row["Headline"] == "h1"


def test_aggregate_by_suid_no_list_cols():
    df = pl.DataFrame({"SUID": ["A", "A", "B"], "x": [1, 1, 2]})
    out = aggregate_by_suid(df).sort("SUID")
    assert out["SUID"].to_list() == ["A", "B"]
    assert out["x"].to_list() == [1, 2]


def test_add_date_from_capture_time():
    df = pl.DataFrame(
        {"CaptureTime": ["2026-01-05 10:35:39", "2026-01-06 12:00:00"]}
    )
    out = add_date_from_capture_time(df)
    assert out["date"].to_list() == ["2026-01-05", "2026-01-06"]


def test_exclude_by_suid():
    df = pl.DataFrame({"SUID": ["A", "B", "C"], "x": [1, 2, 3]})
    excl = pl.DataFrame({"SUID": ["B"]})
    out = exclude_by_suid(df, excl)
    assert out["SUID"].to_list() == ["A", "C"]


def test_exclude_by_suid_empty_exclude_returns_original():
    df = pl.DataFrame({"SUID": ["A", "B"]})
    excl = pl.DataFrame({"SUID": []}, schema={"SUID": pl.Utf8})
    out = exclude_by_suid(df, excl)
    assert out["SUID"].to_list() == ["A", "B"]


# ============================================================================
# RIC -> BBG Ticker 変換（純粋ロジック）
# ============================================================================


def test_convert_rics_to_bbg_tickers_jp():
    rics = ["7203.T", "6758.T", "9984.OS"]
    out = convert_rics_to_bbg_tickers(rics, JP_CONFIG.ric_suffix_to_bbg_label)
    assert out == ["7203 JP Equity", "6758 JP Equity", "9984 JP Equity"]


def test_convert_rics_to_bbg_tickers_us():
    rics = ["AAPL.O", "MSFT.O", "JPM.N"]
    out = convert_rics_to_bbg_tickers(rics, US_CONFIG.ric_suffix_to_bbg_label)
    assert out == ["AAPL US Equity", "MSFT US Equity", "JPM US Equity"]


def test_convert_rics_to_bbg_tickers_filters_out_unmatched():
    """非対応サフィックス（例: ``.HK`` を JP 用マップに通す）は ``None`` で落とす。"""
    rics = ["7203.T", "0700.HK", "AAPL.O"]
    out = convert_rics_to_bbg_tickers(rics, JP_CONFIG.ric_suffix_to_bbg_label)
    assert out == ["7203 JP Equity", None, None]


def test_convert_rics_handles_empty_and_whitespace():
    out = convert_rics_to_bbg_tickers(["", "  ", None, "7203.T"], JP_CONFIG.ric_suffix_to_bbg_label)
    assert out == [None, None, None, "7203 JP Equity"]


# ============================================================================
# RIC 展開（DataFrame レベル、純粋ロジック）
# ============================================================================


def test_explode_related_rics():
    df = pl.DataFrame(
        {
            "SUID": ["A", "B"],
            "related_rics": ["7203.T 6758.T", "AAPL.O"],
        }
    )
    out = explode_related_rics(df, JP_CONFIG.ric_suffix_to_bbg_label).sort(["SUID", "BBG_Ticker"])
    # JP マップなので AAPL.O は変換失敗で落ちる
    assert out["SUID"].to_list() == ["A", "A"]
    assert sorted(out["BBG_Ticker"].to_list()) == ["6758 JP Equity", "7203 JP Equity"]


def test_explode_related_rics_drops_rows_with_no_jp_match():
    """全ての RIC が地域マップにマッチしない場合、その行は完全に消える。"""
    df = pl.DataFrame(
        {
            "SUID": ["A", "B"],
            "related_rics": ["AAPL.O MSFT.O", "7203.T"],
        }
    )
    out = explode_related_rics(df, JP_CONFIG.ric_suffix_to_bbg_label)
    assert out["SUID"].to_list() == ["B"]


# ============================================================================
# 指数ユニバース絞り込み（純粋ロジック）
# ============================================================================


def test_filter_to_index_universe():
    df = pl.DataFrame(
        {
            "SUID": ["A", "B", "C"],
            "BBG_Ticker": ["7203 JP Equity", "9999 JP Equity", "6758 JP Equity"],
        }
    )
    universe = pl.DataFrame({"BBG_Ticker": ["7203 JP Equity", "6758 JP Equity"]})
    out = filter_to_index_universe(df, universe).sort("SUID")
    assert out["SUID"].to_list() == ["A", "C"]


# ============================================================================
# blpapi 依存テスト（実環境）
# ============================================================================


@pytest.fixture(scope="module")
def blpapi_session():
    """セッションを 1 度だけ立ち上げて使い回す。"""
    blpapi = pytest.importorskip("blpapi")
    opts = blpapi.SessionOptions()
    opts.setServerHost("localhost")
    opts.setServerPort(8194)
    session = blpapi.Session(opts)
    if not session.start():
        pytest.skip("blpapi session could not start (Bloomberg Terminal not running?)")
    if not session.openService("//blp/refdata"):
        pytest.skip("//blp/refdata service unavailable")
    yield session
    session.stop()


@pytest.mark.blpapi
def test_fetch_index_universe_tickers_nky(blpapi_session):
    """NKY Index から日経 225 構成銘柄を取得し、JP 形式に正規化する。"""
    universe = fetch_index_universe_tickers(
        blpapi_session,
        index_ticker="NKY Index",
        reference_date="20260324",
        bbg_exchange_codes=["JT"],
        canonical_label="JP",
    )
    assert universe.height == 225
    # 全行が " JP Equity" で終わる
    assert all(t.endswith(" JP Equity") for t in universe["BBG_Ticker"].to_list())
    # 7203 (Toyota) が含まれる
    assert "7203 JP Equity" in universe["BBG_Ticker"].to_list()


@pytest.mark.blpapi
def test_fetch_index_universe_tickers_spx(blpapi_session):
    universe = fetch_index_universe_tickers(
        blpapi_session,
        index_ticker="SPX Index",
        reference_date="20260324",
        bbg_exchange_codes=["UN", "UW", "UQ", "UA", "UR", "UF"],
        canonical_label="US",
    )
    assert universe.height >= 500  # S&P500 (実際は 500-505 程度)
    assert all(t.endswith(" US Equity") for t in universe["BBG_Ticker"].to_list())
    assert "AAPL US Equity" in universe["BBG_Ticker"].to_list()


@pytest.mark.blpapi
def test_fetch_sectors_for_tickers(blpapi_session):
    tickers = ["7203 JP Equity", "6758 JP Equity", "AAPL US Equity"]
    out = fetch_sectors_for_tickers(blpapi_session, tickers)
    # 戻り値は {ticker: sector} の dict
    assert out["7203 JP Equity"] == "Consumer, Cyclical"
    assert out["6758 JP Equity"] == "Consumer, Cyclical"
    assert out["AAPL US Equity"] == "Technology"
