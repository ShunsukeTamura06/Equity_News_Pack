"""地域別パラメータの定義。

JP / US で異なる業務ロジック上のパラメータ（トピックタグ、対象指数、AI 生成
判定パターン、RIC→BBG マッピング等）を ``RegionConfig`` に集約する。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RegionConfig:
    """地域別ニュース処理パラメータ。

    Attributes:
        region_code: 地域識別子（``"JP"`` / ``"US"``）。出力ファイル名等に使用。
        individual_topics: 個別株系として抽出するトピックタグ（OR 条件）。
        language: ニュースの言語コード（``"ja"`` / ``"en"``）。新 DB の ``language``
            列の表記に合わせる。
        ai_generated_prefix_pattern: AI 生成ニュース判定用の先頭一致正規表現。
        index_ticker: 対象指数の Bloomberg 識別子（例: ``"NKY Index"``）。
        index_reference_date: 指数構成銘柄の基準日（``YYYYMMDD``、blpapi の
            ``END_DATE_OVERRIDE`` に渡す形式）。
        canonical_exchange_label: 出力する BBG Ticker の市場ラベル（``"JP"`` / ``"US"``）。
            参照ファイル ``JP_ALL.csv`` の ``BBG_Ticker`` 形式に合わせる。
        ric_suffix_to_bbg_label: RIC のサフィックスを BBG の市場ラベルに変換するマップ
            （例: ``{".T": "JP", ".OS": "JP"}``）。ニュース DB の ``related_rics``
            から該当地域銘柄を抽出 + BBG 形式へ変換する際に使用する。
        bbg_index_exchange_codes: blpapi の ``INDX_MWEIGHT_HIST`` で返る指数構成銘柄の
            市場コード（例: NKY なら ``["JT"]``、SPX なら ``["UN", "UW", ...]``）。
            これらを ``canonical_exchange_label`` に置換することで本処理側の表記と揃える。
        macro_topics: マクロ・需給系トピック（今回のスコープでは未使用、将来用）。
        exclude_topics: 除外トピック（個別株系から SUID ベースで除外）。
    """

    region_code: str
    individual_topics: list[str]
    language: str
    ai_generated_prefix_pattern: str
    index_ticker: str
    index_reference_date: str
    canonical_exchange_label: str
    ric_suffix_to_bbg_label: dict[str, str]
    bbg_index_exchange_codes: list[str]
    macro_topics: list[str] = field(default_factory=list)
    exclude_topics: list[str] = field(default_factory=list)


# --- 地域別インスタンス -------------------------------------------------------

JP_CONFIG = RegionConfig(
    region_code="JP",
    individual_topics=["JPS", "KABU", "BFWJP"],
    macro_topics=["BOJ", "POLI", "JPGOV", "JNECO"],
    language="ja",
    ai_generated_prefix_pattern=r"(?i)^\s*ブルームバーグ・オートメーション",
    index_ticker="NKY Index",
    index_reference_date="20260324",
    canonical_exchange_label="JP",
    # 東証 / 大証 / 札証 / 福証 など主要日本市場の RIC サフィックス
    ric_suffix_to_bbg_label={
        ".T": "JP",
        ".OS": "JP",
        ".SP": "JP",
        ".FU": "JP",
    },
    # blpapi が NKY 構成銘柄として返す市場コード（"JT" = Tokyo Primary）
    bbg_index_exchange_codes=["JT"],
)

US_CONFIG = RegionConfig(
    region_code="US",
    individual_topics=["USS", "ERN"],
    exclude_topics=["HEADS"],
    language="en",
    ai_generated_prefix_pattern=r"(?i)^\s*By Bloomberg Automation",
    index_ticker="SPX Index",
    index_reference_date="20260324",
    canonical_exchange_label="US",
    # NYSE / Nasdaq / AMEX / NYSE Arca 等の RIC サフィックス
    ric_suffix_to_bbg_label={
        ".N": "US",
        ".O": "US",
        ".A": "US",
        ".P": "US",
    },
    # SPX の構成銘柄として返る代表的な市場コード
    bbg_index_exchange_codes=["UN", "UW", "UQ", "UA", "UR", "UF"],
)
