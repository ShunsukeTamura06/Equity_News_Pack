"""llm_analyzer モジュールのテスト。

OpenAI クライアントをモックして実 API 呼び出しなしで検証する。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import polars as pl
import pytest

from src.llm_analyzer import (
    DailyAbstract,
    JapaneseSummary,
    LLMAnalyzer,
    MarketImplication,
    _parse_summary_results,
    _strip_code_fences,
    add_summary_and_sentiment,
    filter_by_market_implication,
    generate_daily_abstracts,
)
from src.settings import LLMConfig


@pytest.fixture
def llm_cfg() -> LLMConfig:
    return LLMConfig(
        api_token="dummy-token",
        base_url="https://example.com",
        model="gemini-3-flash-preview",
    )


def _make_response(content: str) -> MagicMock:
    """OpenAI ``chat.completions.create`` の戻り値を模した mock。"""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


# ============================================================================
# 純粋ロジック
# ============================================================================


def test_strip_code_fences_removes_json_block():
    raw = '```json\n{"x": 1}\n```'
    assert _strip_code_fences(raw) == '{"x": 1}'


def test_strip_code_fences_removes_bare_block():
    raw = '```\n{"x": 1}\n```'
    assert _strip_code_fences(raw) == '{"x": 1}'


def test_strip_code_fences_passthrough_when_no_fence():
    assert _strip_code_fences('{"x": 1}') == '{"x": 1}'


def test_parse_summary_results_normal():
    results = [
        JapaneseSummary(summary_ja="要約1", market_sentiment=1),
        JapaneseSummary(summary_ja="要約2", market_sentiment=-1),
        None,  # 失敗
    ]
    summaries, sentiments = _parse_summary_results(results)
    assert summaries == ["要約1", "要約2", None]
    assert sentiments == [1, -1, None]


def test_parse_summary_results_invalid_sentiment_dropped():
    results = [
        JapaneseSummary(summary_ja="要約", market_sentiment=2),  # 無効値
    ]
    _, sentiments = _parse_summary_results(results)
    assert sentiments == [None]


def test_parse_summary_results_strips_whitespace():
    results = [JapaneseSummary(summary_ja="  要約  ", market_sentiment=0)]
    summaries, _ = _parse_summary_results(results)
    assert summaries == ["要約"]


# ============================================================================
# LLMAnalyzer (OpenAI クライアントを mock)
# ============================================================================


def test_init_creates_openai_client(mocker, llm_cfg):
    mock_openai = mocker.patch("src.llm_analyzer.OpenAI")
    LLMAnalyzer(llm_cfg)
    mock_openai.assert_called_once_with(
        api_key="dummy-token",
        base_url="https://example.com",
    )


def test_filter_market_implication_batch_parses_json(mocker, llm_cfg):
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        _make_response('{"has_implication": 1}'),
        _make_response('{"has_implication": 0}'),
    ]
    mocker.patch("src.llm_analyzer.OpenAI", return_value=mock_client)

    analyzer = LLMAnalyzer(llm_cfg, max_concurrency=1, show_progress=False)
    results = analyzer.filter_market_implication_batch(["headline 1", "headline 2"])

    assert len(results) == 2
    assert results[0].has_implication == 1
    assert results[1].has_implication == 0


def test_filter_market_implication_batch_handles_code_fences(mocker, llm_cfg):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_response(
        '```json\n{"has_implication": 1}\n```'
    )
    mocker.patch("src.llm_analyzer.OpenAI", return_value=mock_client)

    analyzer = LLMAnalyzer(llm_cfg, max_concurrency=1, show_progress=False)
    results = analyzer.filter_market_implication_batch(["h"])
    assert results[0].has_implication == 1


def test_filter_market_implication_batch_returns_none_for_invalid_json(mocker, llm_cfg):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_response("not json at all")
    mocker.patch("src.llm_analyzer.OpenAI", return_value=mock_client)

    analyzer = LLMAnalyzer(
        llm_cfg, max_concurrency=1, show_progress=False, max_retries=0
    )
    results = analyzer.filter_market_implication_batch(["h"])
    assert results == [None]


def test_summarize_with_sentiment_batch(mocker, llm_cfg):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_response(
        json.dumps({"summary_ja": "テスト要約", "market_sentiment": 1})
    )
    mocker.patch("src.llm_analyzer.OpenAI", return_value=mock_client)

    analyzer = LLMAnalyzer(llm_cfg, max_concurrency=1, show_progress=False)
    results = analyzer.summarize_with_sentiment_batch(["body"])
    assert results[0].summary_ja == "テスト要約"
    assert results[0].market_sentiment == 1


def test_summarize_daily_abstract_batch(mocker, llm_cfg):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_response(
        json.dumps({"abstract": "本日のまとめ..."})
    )
    mocker.patch("src.llm_analyzer.OpenAI", return_value=mock_client)

    analyzer = LLMAnalyzer(llm_cfg, max_concurrency=1, show_progress=False)
    results = analyzer.summarize_daily_abstract_batch(["combined text"])
    assert results[0].abstract == "本日のまとめ..."


def test_retries_on_rate_limit_then_succeeds(mocker, llm_cfg):
    """RateLimitError 系の例外時に指数バックオフで再試行する。"""
    from openai import RateLimitError

    rate_err = RateLimitError(
        message="rate limited",
        response=MagicMock(status_code=429, headers={}),
        body={},
    )

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        rate_err,
        rate_err,
        _make_response('{"has_implication": 1}'),
    ]
    mocker.patch("src.llm_analyzer.OpenAI", return_value=mock_client)
    # sleep を 0 秒に固定してテストを高速化
    mocker.patch("src.llm_analyzer.time.sleep")

    analyzer = LLMAnalyzer(
        llm_cfg, max_concurrency=1, show_progress=False, max_retries=3
    )
    results = analyzer.filter_market_implication_batch(["h"])
    assert results[0].has_implication == 1
    assert mock_client.chat.completions.create.call_count == 3


def test_does_not_retry_on_non_rate_limit_error(mocker, llm_cfg):
    from openai import APIError

    api_err = APIError(message="bad request", request=MagicMock(), body={})

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = api_err
    mocker.patch("src.llm_analyzer.OpenAI", return_value=mock_client)

    analyzer = LLMAnalyzer(
        llm_cfg, max_concurrency=1, show_progress=False, max_retries=3
    )
    results = analyzer.filter_market_implication_batch(["h"])
    assert results == [None]
    # 1 回だけで諦める（リトライしない）
    assert mock_client.chat.completions.create.call_count == 1


# ============================================================================
# 高レベル関数（DataFrame 統合）
# ============================================================================


def test_filter_by_market_implication_keeps_implication_1(mocker, llm_cfg):
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        _make_response('{"has_implication": 1}'),
        _make_response('{"has_implication": 0}'),
        _make_response('{"has_implication": 1}'),
    ]
    mocker.patch("src.llm_analyzer.OpenAI", return_value=mock_client)
    analyzer = LLMAnalyzer(llm_cfg, max_concurrency=1, show_progress=False)

    df = pl.DataFrame(
        {"SUID": ["A", "B", "C"], "Headline": ["h1", "h2", "h3"]}
    )
    out = filter_by_market_implication(df, analyzer).sort("SUID")
    assert out["SUID"].to_list() == ["A", "C"]


def test_add_summary_and_sentiment_drops_failures(mocker, llm_cfg):
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        _make_response(json.dumps({"summary_ja": "ok", "market_sentiment": 1})),
        _make_response("invalid"),
        _make_response(json.dumps({"summary_ja": "", "market_sentiment": 0})),  # 空要約 → 除外
    ]
    mocker.patch("src.llm_analyzer.OpenAI", return_value=mock_client)
    mocker.patch("src.llm_analyzer.time.sleep")

    analyzer = LLMAnalyzer(
        llm_cfg, max_concurrency=1, show_progress=False, max_retries=0
    )
    df = pl.DataFrame({"SUID": ["A", "B", "C"], "Body": ["b1", "b2", "b3"]})
    out = add_summary_and_sentiment(df, analyzer)
    assert out["SUID"].to_list() == ["A"]


def test_generate_daily_abstracts(mocker, llm_cfg):
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        _make_response(json.dumps({"abstract": "day1まとめ"})),
        _make_response(json.dumps({"abstract": "day2まとめ"})),
    ]
    mocker.patch("src.llm_analyzer.OpenAI", return_value=mock_client)
    analyzer = LLMAnalyzer(llm_cfg, max_concurrency=1, show_progress=False)

    df = pl.DataFrame(
        {
            "date": ["2026-01-05", "2026-01-05", "2026-01-06"],
            "Summarized_Text": ["s1", "s2", "s3"],
        }
    )
    out = generate_daily_abstracts(df, analyzer).sort("date")
    assert out["date"].to_list() == ["2026-01-05", "2026-01-06"]
    assert out["abstract"].to_list() == ["day1まとめ", "day2まとめ"]
