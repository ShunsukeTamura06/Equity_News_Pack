"""OpenAI 互換 API でニュースを分析するクライアント。

旧版では AWS Bedrock を直接叩いていたが、AWS 環境への移行に伴い OpenAI 互換
エンドポイント（モデルは ``LLM_MODEL`` 環境変数で指定。Gemini / GPT 系を想定）
に切替。

提供するもの:

- :class:`LLMAnalyzer`: スレッド並列で LLM を呼び出すクライアント。
- 戻り値の Pydantic モデル（``MarketImplication`` / ``JapaneseSummary`` /
  ``DailyAbstract``）。
- ノートブック側で扱いやすいパイプライン関数
  （``filter_by_market_implication`` / ``add_summary_and_sentiment`` /
  ``generate_daily_abstracts``）。
"""
from __future__ import annotations

import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Type, TypeVar

import polars as pl
from openai import APIError, OpenAI, RateLimitError
from pydantic import BaseModel, Field
from pydantic.types import conint
from tqdm.auto import tqdm

from src.settings import LLMConfig

# ============================================================================
# Pydantic レスポンスモデル
# ============================================================================


class MarketImplication(BaseModel):
    """ヘッドラインに市場インプリケーションがあるか。"""

    has_implication: conint(ge=0, le=1) = Field(
        ..., description="1 if the headline has market implication, 0 otherwise"
    )


class JapaneseSummary(BaseModel):
    """日本語要約 + 株式市場センチメント。"""

    summary_ja: str = Field(..., description="Japanese summary of the input news body")
    market_sentiment: Optional[int] = Field(
        None,
        description="Sentiment for the equity market: -1 (negative), 0 (neutral), 1 (positive)",
    )


class DailyAbstract(BaseModel):
    """日次アブストラクト。"""

    abstract: str = Field(
        ..., description="A slightly detailed Japanese summary of the day's events"
    )


# ============================================================================
# プロンプト
# ============================================================================

FILTER_PROMPT = r"""
You are a STRICT financial news screener. You will be given ONLY a news headline.
Your task: decide whether the headline has a clear potential to impact financial markets (stocks, bonds, FX, commodities, credit, volatility) or tradable assets.

Return:
- has_implication = 1  ONLY when the headline contains a CLEAR, DIRECT market-moving signal.
- has_implication = 0  in ALL other cases (including ambiguity).

## Output format (mandatory)
Output ONLY a JSON object exactly like:
{"has_implication": 1}
or
{"has_implication": 0}

## Hard rule (strictness)
If you are not confident it can move prices or materially update market expectations, output 0.

## Mark as 0 (NO implication) if ANY of the following holds
- The headline is empty, truncated, nonsensical, or looks like a placeholder/navigation/header.
- Pure system/operational/admin messages; tests; "ignore"; maintenance; login/status; formatting artifacts.
- General human-interest, lifestyle, entertainment, sports, culture, crime, accidents, weather, local events
  UNLESS it explicitly links to markets via a company/asset/commodity supply shock, major economic policy, or quantified economic impact.
- Generic politics/geopolitics that does NOT explicitly mention economic policy, sanctions/trade restrictions, energy supply disruption, market impact, or a major macro variable.
- Mentions a person/organization with no clear tie to a publicly traded company, major private systemic institution, sector, or macro policy.
- Vague "outlook", "commentary", "interview", "opinion", "explainer" headlines without a concrete event, decision, number, or market action.

## Mark as 1 (HAS implication) ONLY if the headline clearly includes at least one of these DIRECT triggers
### A) Company / Credit / Security-specific (explicit)
- Earnings, revenue, profit, guidance, outlook revision, margin, cash flow, dividends, buybacks.
- M&A, takeover, merger, acquisition, divestiture, spin-off, IPO/listing/delisting.
- Bankruptcy, default, restructuring, covenant/credit event, liquidity/solvency issues.
- Analyst actions and explicitly market-relevant calls: upgrade/downgrade, target price changes, "initiated coverage".
- Material legal/regulatory actions: investigations, charges, major lawsuits, antitrust, approvals/denials by key regulators.
- Material operational events: major product recall, safety issue, cyberattack, outage, plant shutdown, supply-chain disruption
  WHEN clearly tied to a specific company/issuer or a key sector.
- Explicit market language tied to an asset: "shares/stock/bonds surge/fall", "yields", "spreads", "CDS", "rating".

### B) Macro / Policy / Data (explicit)
- Central bank actions/signals: rate decisions, hikes/cuts, policy statements, QT/QE, interventions.
- Major macroeconomic releases or revisions: inflation, jobs/employment, GDP, PMI, retail sales, trade balance,
  when presented as an event or number that updates expectations.
- Fiscal/tax/budget policy with clear economic/market consequences.
- Sanctions, tariffs, export controls, trade restrictions with explicit economic/market relevance.

### C) Commodities / Energy / FX drivers (explicit)
- OPEC/major producer decisions, supply disruptions, shipping chokepoints, pipeline/refinery outages,
  or other events explicitly affecting oil/gas/metals/agriculture markets.
- FX/sovereign risk events explicitly mentioning currency moves, capital controls, debt issuance, auctions, or default risk.

## Tie-breaker
- If the headline contains clear market terminology (stocks, bonds, yields, spreads, futures, commodities, FX, credit rating)
  AND it refers to a concrete event/move/decision, output 1.
- Otherwise output 0.
"""

SUMMARY_WITH_SENTIMENT_PROMPT = r"""
You are a professional Japanese financial analyst.

TASK:
1. Read the input news body text.
2. Summarize the text into concise Japanese (1-3 sentences).
3. Judge the impact on the equity market and assign a sentiment label.

Sentiment labels (THIS IS STRICTLY REQUIRED):
-  1 : Positive for the equity market (supportive, bullish)
-  0 : Neutral / mixed / unclear impact on the equity market
- -1 : Negative for the equity market (harmful, bearish)

ABSOLUTE RULES:
- You MUST always choose exactly one of -1, 0, 1.
- NEVER output null or omit "market_sentiment".
- If the impact is weak or uncertain, choose 0 (neutral).
- Do not say you cannot judge; you must still choose one label.

OUTPUT FORMAT (STRICT):
- Output ONLY a single JSON object.
- DO NOT output any extra text, comments, or Markdown.
- Keys and types MUST be exactly:
  - "summary_ja": string  (Japanese, 1-3 sentences)
  - "market_sentiment": integer  (one of -1, 0, 1)

Example of a VALID output:
{
  "summary_ja": "日本語の要約文...",
  "market_sentiment": 1
}
"""

DAILY_ABSTRACT_PROMPT = r"""
You are a professional Japanese financial analyst.
Below are multiple summaries of news from the same day.
Synthesize them into a coherent daily abstract in Japanese.
The abstract should be slightly long and informative (about 200-400 characters).
- Output ONLY a JSON object.
- Key: "abstract"
"""


# ============================================================================
# 進捗トラッカー
# ============================================================================


class ProgressTracker:
    """``_invoke_batch`` 用のスレッドセーフな進捗トラッカー。"""

    def __init__(
        self,
        total: int,
        desc: str = "Processing",
        use_tqdm: bool = True,
    ):
        self.total = total
        self.desc = desc
        self._completed = 0
        self._succeeded = 0
        self._failed = 0
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._pbar: tqdm | None = (
            tqdm(
                total=total,
                desc=desc,
                unit="item",
                bar_format=(
                    "{l_bar}{bar}| {n_fmt}/{total_fmt} "
                    "[{elapsed}<{remaining}, {rate_fmt}]"
                ),
            )
            if use_tqdm
            else None
        )

    def update(self, success: bool = True) -> None:
        with self._lock:
            self._completed += 1
            self._succeeded += int(success)
            self._failed += int(not success)
            succeeded, failed = self._succeeded, self._failed
        if self._pbar is not None:
            self._pbar.update(1)
            self._pbar.set_postfix({"ok": succeeded, "fail": failed}, refresh=True)

    def close(self) -> None:
        if self._pbar is not None:
            self._pbar.close()
        elapsed = time.time() - self._start_time
        rate = self._completed / elapsed if elapsed > 0 else 0
        print(
            f"\n[{self.desc}] {self._succeeded}/{self.total} succeeded, "
            f"{self._failed} failed in {elapsed:.1f}s ({rate:.1f} items/sec)"
        )


# ============================================================================
# LLM クライアント
# ============================================================================

T = TypeVar("T", bound=BaseModel)


class LLMAnalyzer:
    """OpenAI 互換 API を並列バッチで呼び出すクライアント。

    各リクエストは共有の :class:`OpenAI` クライアントから送信され、
    レート制限検知時には指数バックオフで再試行する。
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        max_concurrency: int = 8,
        max_retries: int = 6,
        retry_base_sleep_sec: float = 0.5,
        retry_jitter_sec: float = 0.2,
        show_progress: bool = True,
    ):
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")

        self.config = config
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_concurrency = max_concurrency
        self.max_retries = max_retries
        self.retry_base_sleep_sec = retry_base_sleep_sec
        self.retry_jitter_sec = retry_jitter_sec
        self.show_progress = show_progress

        self._client = OpenAI(api_key=config.api_token, base_url=config.base_url)
        self._executor = ThreadPoolExecutor(max_workers=max_concurrency)

    def close(self) -> None:
        self._executor.shutdown(wait=True)

    # --- 内部ヘルパ --------------------------------------------------------

    def _invoke(
        self,
        system_prompt: str,
        user_content: str,
        *,
        max_tokens: Optional[int] = None,
    ) -> str:
        response = self._client.chat.completions.create(
            model=self.config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=self.temperature,
            max_tokens=max_tokens or self.max_tokens,
        )
        return response.choices[0].message.content or ""

    def _invoke_with_retry(
        self,
        system_prompt: str,
        user_content: str,
        *,
        max_tokens: Optional[int] = None,
    ) -> str:
        for attempt in range(self.max_retries + 1):
            try:
                return self._invoke(system_prompt, user_content, max_tokens=max_tokens)
            except RateLimitError:
                if attempt >= self.max_retries:
                    raise
                sleep_s = (
                    self.retry_base_sleep_sec * (2**attempt)
                    + random.uniform(0, self.retry_jitter_sec)
                )
                time.sleep(sleep_s)
        raise RuntimeError("unreachable")

    def _invoke_and_parse(
        self,
        system_prompt: str,
        user_content: str,
        response_model: Type[T],
        *,
        max_tokens: Optional[int] = None,
    ) -> T:
        full_system_prompt = system_prompt + (
            "\n\nIMPORTANT: Output ONLY valid JSON. "
            "No markdown formatting, no explanatory text."
        )
        raw = self._invoke_with_retry(
            full_system_prompt, user_content, max_tokens=max_tokens
        )
        try:
            return response_model.model_validate(json.loads(_strip_code_fences(raw)))
        except Exception as e:
            raise ValueError(
                f"Failed to parse LLM response as {response_model.__name__}: {e}\n"
                f"Raw response (head): {raw[:500]}..."
            ) from e

    def _invoke_batch(
        self,
        system_prompt: str,
        texts: List[str],
        response_model: Type[T],
        *,
        max_tokens: Optional[int] = None,
        progress_desc: str = "Batch Processing",
    ) -> List[Optional[T]]:
        """テキスト群を並列にバッチ処理する（戻り順は入力順を保つ）。"""
        total = len(texts)
        if total == 0:
            return []

        results: List[Optional[T]] = [None] * total
        tracker = (
            ProgressTracker(total=total, desc=progress_desc, use_tqdm=self.show_progress)
            if self.show_progress
            else None
        )

        def worker(index: int, text: str):
            try:
                return index, self._invoke_and_parse(
                    system_prompt, text, response_model, max_tokens=max_tokens
                ), None
            except Exception as e:
                return index, None, e

        futures = {
            self._executor.submit(worker, i, text): i
            for i, text in enumerate(texts)
        }
        for fut in as_completed(futures):
            i, result, error = fut.result()
            results[i] = result
            if tracker:
                tracker.update(success=error is None)
            if error is not None:
                print(f"Warning: Failed to process item {i}: {error}")

        if tracker:
            tracker.close()
        return results

    # --- パブリック API ----------------------------------------------------

    def filter_market_implication_batch(
        self, headlines: List[str]
    ) -> List[Optional[MarketImplication]]:
        """ヘッドラインに市場インプリケーションがあるかを判定する。"""
        return self._invoke_batch(
            FILTER_PROMPT,
            headlines,
            MarketImplication,
            max_tokens=128,
            progress_desc="Filtering Headlines",
        )

    def summarize_with_sentiment_batch(
        self, bodies: List[str]
    ) -> List[Optional[JapaneseSummary]]:
        """本文を日本語要約し、株式市場センチメント（-1/0/1）を付与する。"""
        return self._invoke_batch(
            SUMMARY_WITH_SENTIMENT_PROMPT,
            bodies,
            JapaneseSummary,
            progress_desc="Summarizing Bodies (JA + sentiment)",
        )

    def summarize_daily_abstract_batch(
        self, daily_texts: List[str]
    ) -> List[Optional[DailyAbstract]]:
        """日次にまとめられた要約群から、日次アブストラクトを生成する。"""
        return self._invoke_batch(
            DAILY_ABSTRACT_PROMPT,
            daily_texts,
            DailyAbstract,
            progress_desc="Generating Daily Abstracts",
        )


# ============================================================================
# パース・テキスト処理ユーティリティ
# ============================================================================


def _strip_code_fences(raw: str) -> str:
    """LLM 出力にしばしば含まれる ``` フェンスを除去する。"""
    cleaned = raw.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    start = 1 if lines[0].startswith("```") else 0
    end = -1 if lines[-1].strip() == "```" else len(lines)
    return "\n".join(lines[start:end])


def _parse_summary_results(
    results: List[Optional[JapaneseSummary]],
) -> tuple[List[Optional[str]], List[Optional[int]]]:
    """要約・センチメントを抽出して 2 リストに正規化する。

    要約またはセンチメント取得が失敗した要素は ``None`` を返す。
    センチメントは ``-1`` / ``0`` / ``1`` の整数のみを有効値とする。
    """
    summaries: List[Optional[str]] = []
    sentiments: List[Optional[int]] = []
    for r in results:
        summary: Optional[str] = None
        sentiment: Optional[int] = None
        if r is not None:
            if isinstance(r.summary_ja, str) and r.summary_ja.strip():
                summary = r.summary_ja.strip()
            sa = r.market_sentiment
            if isinstance(sa, int) and sa in (-1, 0, 1):
                sentiment = sa
            elif isinstance(sa, str) and sa.strip() in ("-1", "0", "1"):
                sentiment = int(sa.strip())
        summaries.append(summary)
        sentiments.append(sentiment)
    return summaries, sentiments


# ============================================================================
# DataFrame 統合用の高レベル関数
# ============================================================================


def filter_by_market_implication(
    df: pl.DataFrame,
    analyzer: LLMAnalyzer,
) -> pl.DataFrame:
    """LLM で「市場インプリケーションあり」と判定された記事のみを抽出する。

    Args:
        df: ``Headline`` 列を含むニュース DataFrame。
        analyzer: LLM クライアント。

    Returns:
        ``has_implication == 1`` の行のみを含む DataFrame。
    """
    headlines = df["Headline"].to_list()
    results = analyzer.filter_market_implication_batch(headlines)
    flags = [r.has_implication if r is not None else 0 for r in results]
    return (
        df.with_columns(pl.Series("has_implication", flags, dtype=pl.Int8))
        .filter(pl.col("has_implication") == 1)
    )


def add_summary_and_sentiment(
    df: pl.DataFrame,
    analyzer: LLMAnalyzer,
) -> pl.DataFrame:
    """本文を要約し、市場センチメントを付与する。失敗行は除外する。

    Args:
        df: ``Body`` 列を含む DataFrame。
        analyzer: LLM クライアント。

    Returns:
        ``Summarized_Text`` と ``Market_Sentiment`` を付与した DataFrame。
    """
    bodies = df["Body"].to_list()
    results = analyzer.summarize_with_sentiment_batch(bodies)
    summaries, sentiments = _parse_summary_results(results)
    enriched = df.with_columns(
        pl.Series("Summarized_Text", summaries),
        pl.Series("Market_Sentiment", sentiments),
    )
    cleaned = enriched.filter(
        pl.col("Summarized_Text").is_not_null()
        & (pl.col("Summarized_Text").str.strip_chars() != "")
        & pl.col("Market_Sentiment").is_not_null()
    )
    print(
        f"Removed {enriched.height - cleaned.height} rows "
        f"due to summarization/sentiment failure."
    )
    return cleaned


def generate_daily_abstracts(
    sum_df: pl.DataFrame,
    analyzer: LLMAnalyzer,
) -> pl.DataFrame:
    """日付ごとに要約をまとめ、日次アブストラクトを生成する。

    Args:
        sum_df: ``date`` および ``Summarized_Text`` 列を持つ DataFrame。
        analyzer: LLM クライアント。

    Returns:
        ``date`` と ``abstract`` を持つ DataFrame（日付昇順）。
    """
    daily_bundled = (
        sum_df.group_by("date")
        .agg(pl.col("Summarized_Text").str.join("\n\n"))
        .sort("date")
    )
    abstracts = analyzer.summarize_daily_abstract_batch(
        daily_bundled["Summarized_Text"].to_list()
    )
    return daily_bundled.with_columns(
        pl.Series(
            "abstract",
            [r.abstract if r is not None else "生成失敗" for r in abstracts],
        )
    ).select(["date", "abstract"])
