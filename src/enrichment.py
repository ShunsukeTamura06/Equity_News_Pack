"""ニュース DataFrame の整形・エンリッチ処理。

Polars DataFrame に対する非 LLM 系の変換（フィルタリング、RIC 展開、
RIC→BBG Ticker 変換、Sector 付与、SUID 集約、指数ユニバース絞り込み）を提供する。

blpapi 依存の関数（``fetch_index_universe_tickers``, ``fetch_sectors_for_tickers``）
は引数で ``blpapi.Session`` を受け取るため、テスト時にモック差し替え可能。
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

import blpapi
import polars as pl

if TYPE_CHECKING:
    pass

ID_COL = "BBG_Ticker"
"""エンリッチ後の Ticker 列名（参照ファイル ``JP_ALL.csv`` の列名と揃える）。"""

SECTOR_COL = "Sector"


# ============================================================================
# 行フィルタ・分割
# ============================================================================


def filter_ai_generated_news(
    df: pl.DataFrame,
    prefix_pattern: str,
    body_col: str = "Body",
) -> pl.DataFrame:
    """AI 生成ニュース（先頭一致）を除外する。

    Args:
        df: ``body_col`` を持つニュース DataFrame。
        prefix_pattern: 除外対象の先頭一致正規表現。
        body_col: 本文列の名前。

    Returns:
        AI 生成記事を除いた DataFrame。
    """
    return df.filter(
        ~pl.col(body_col).fill_null("").str.contains(prefix_pattern)
    )


def split_by_ric_presence(
    df: pl.DataFrame,
    ric_col: str = "related_rics",
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """``related_rics`` の有無で DataFrame を 2 分割する。

    Args:
        df: ``ric_col`` を持つ DataFrame。
        ric_col: RIC 列の名前（新 DB のスキーマでは ``related_rics``）。

    Returns:
        ``(RIC あり, RIC なし)`` のタプル。
    """
    has_ric = (
        pl.col(ric_col)
        .fill_null("")
        .str.strip_chars()
        .ne("")
    )
    return df.filter(has_ric), df.filter(~has_ric)


def exclude_by_suid(
    df: pl.DataFrame,
    exclude_df: pl.DataFrame,
    key: str = "SUID",
) -> pl.DataFrame:
    """``key`` 列の値が ``exclude_df`` に含まれる行を除外する。

    Args:
        df: 本処理対象の DataFrame。
        exclude_df: 除外対象の DataFrame（``key`` 列を持つ）。
        key: 突き合わせに使う列名。

    Returns:
        除外後の DataFrame。
    """
    if exclude_df.is_empty():
        return df
    return df.join(exclude_df.select(key), on=key, how="anti")


# ============================================================================
# 集約・日付付与
# ============================================================================


def aggregate_by_suid(
    df: pl.DataFrame,
    list_cols: list[str] | None = None,
) -> pl.DataFrame:
    """SUID 単位でレコードを統合する。

    ``list_cols`` に指定した列はリスト化、それ以外は ``first`` で集約する。
    RIC 展開などで 1 記事が複数行に分かれているケースを 1 行にまとめる用途。

    Args:
        df: ``SUID`` 列を含む DataFrame。
        list_cols: リスト化したい列名（複数銘柄が紐づくケース等）。

    Returns:
        SUID 単位に集約された DataFrame。
    """
    list_cols = list_cols or []
    skip = {"SUID", *list_cols}
    other_cols = [c for c in df.columns if c not in skip]
    return df.group_by("SUID").agg(
        [pl.col(c) for c in list_cols]
        + [pl.col(c).first() for c in other_cols]
    )


def add_date_from_capture_time(
    df: pl.DataFrame,
    source_col: str = "CaptureTime",
    target_col: str = "date",
) -> pl.DataFrame:
    """``CaptureTime`` から日付部分（先頭 10 文字）を切り出して列を追加する。

    例: ``"2026-01-05 10:35:39"`` → ``"2026-01-05"``

    Args:
        df: ``source_col`` を持つ DataFrame。
        source_col: 切り出し元の列名。
        target_col: 追加する列名。

    Returns:
        日付列を追加した DataFrame。
    """
    return df.with_columns(
        pl.col(source_col).cast(pl.Utf8).str.slice(0, 10).alias(target_col)
    )


# ============================================================================
# RIC -> BBG Ticker 変換
# ============================================================================


def convert_rics_to_bbg_tickers(
    rics: list[str | None],
    suffix_to_label: dict[str, str],
) -> list[str | None]:
    """RIC のリストを BBG Ticker 形式（``"<TICKER> <LABEL> Equity"``）に変換する。

    対応するサフィックスが見つからない要素や空文字／``None`` は ``None`` を返す。

    Args:
        rics: RIC 文字列のリスト（例: ``["7203.T", "AAPL.O"]``）。
        suffix_to_label: RIC サフィックスから BBG ラベルへの変換マップ
            （例: ``{".T": "JP", ".OS": "JP"}``）。

    Returns:
        変換結果のリスト（入力と同じ長さ）。
    """
    # サフィックスは長いものから優先して照合する（例: ``.OS`` を ``.O`` より優先）
    sorted_suffixes = sorted(suffix_to_label.keys(), key=len, reverse=True)
    out: list[str | None] = []
    for ric in rics:
        if not isinstance(ric, str) or not ric.strip():
            out.append(None)
            continue
        ric_clean = ric.strip()
        matched: str | None = None
        for suffix in sorted_suffixes:
            if ric_clean.endswith(suffix):
                ticker = ric_clean[: -len(suffix)]
                matched = f"{ticker} {suffix_to_label[suffix]} Equity"
                break
        out.append(matched)
    return out


def explode_related_rics(
    df: pl.DataFrame,
    suffix_to_label: dict[str, str],
    ric_col: str = "related_rics",
) -> pl.DataFrame:
    """``related_rics``（スペース区切り）を行展開し、BBG Ticker 列を追加する。

    地域マップにマッチしない RIC は除外される。1 記事内のすべての RIC が
    マッチしない場合、その記事は最終的に DataFrame から消える。

    Args:
        df: ``ric_col`` を持つ DataFrame。
        suffix_to_label: RIC サフィックス → BBG ラベル のマップ。
        ric_col: RIC 列の名前。

    Returns:
        ``BBG_Ticker`` 列を追加し、RIC 単位に展開された DataFrame。
    """
    # 1) スペースで分割して 1 RIC 1 行に展開
    exploded = (
        df.with_columns(
            pl.col(ric_col)
            .cast(pl.Utf8)
            .str.split(" ")
            .list.eval(pl.element().str.strip_chars())
        )
        .explode(ric_col)
        .filter(pl.col(ric_col).is_not_null() & (pl.col(ric_col) != ""))
    )

    if exploded.is_empty():
        return exploded.with_columns(pl.lit(None, dtype=pl.Utf8).alias(ID_COL))

    # 2) RIC -> BBG Ticker 変換（地域マップに無いものは None になる）
    rics = exploded[ric_col].to_list()
    bbg_tickers = convert_rics_to_bbg_tickers(rics, suffix_to_label)
    exploded = exploded.with_columns(pl.Series(ID_COL, bbg_tickers, dtype=pl.Utf8))

    # 3) 変換失敗（地域外）の行を除外
    return exploded.filter(pl.col(ID_COL).is_not_null())


# ============================================================================
# 指数ユニバース絞り込み
# ============================================================================


def filter_to_index_universe(
    df: pl.DataFrame,
    universe: pl.DataFrame,
    ticker_col: str = ID_COL,
) -> pl.DataFrame:
    """指数構成銘柄に含まれる行のみへ絞り込む（semi-join）。

    Args:
        df: ``ticker_col`` を持つニュース DataFrame。
        universe: ``ticker_col`` を持つユニバース DataFrame。
        ticker_col: 突き合わせに使う列名。

    Returns:
        ユニバースに含まれる行のみを残した DataFrame。
    """
    return df.join(universe.select(ticker_col), on=ticker_col, how="semi")


# ============================================================================
# blpapi 依存（指数構成銘柄取得・Sector 取得）
# ============================================================================


_REFDATA_SERVICE = "//blp/refdata"


def _send_reference_request(
    session: "blpapi.Session",
    securities: list[str],
    fields: list[str],
    overrides: dict[str, str] | None = None,
) -> list[dict]:
    """``ReferenceDataRequest`` を送信し、結果を ``list[dict]`` として返す。

    Args:
        session: 開始済みの blpapi セッション。
        securities: 対象銘柄リスト。
        fields: 取得フィールドリスト。
        overrides: フィールドオーバーライド（``{"END_DATE_OVERRIDE": "20260324"}`` 等）。

    Returns:
        ``[{"security": ..., "fields": {field_name: element}}, ...]``
        ``element`` は blpapi.Element そのもの（呼び出し側で型に応じて取り出す）。
    """
    service = session.getService(_REFDATA_SERVICE)
    req = service.createRequest("ReferenceDataRequest")
    for s in securities:
        req.append("securities", s)
    for f in fields:
        req.append("fields", f)
    if overrides:
        ovr_arr = req.getElement("overrides")
        for fid, value in overrides.items():
            o = ovr_arr.appendElement()
            o.setElement("fieldId", fid)
            o.setElement("value", value)

    session.sendRequest(req)

    results: list[dict] = []
    while True:
        ev = session.nextEvent(15000)
        for msg in ev:
            if msg.messageType() != blpapi.Name("ReferenceDataResponse"):
                continue
            for sd in msg.getElement("securityData").values():
                sec = sd.getElementAsString("security")
                fd = sd.getElement("fieldData")
                fields_out = {f: fd.getElement(f) for f in fields if fd.hasElement(f)}
                results.append({"security": sec, "fields": fields_out})
        if ev.eventType() == blpapi.Event.RESPONSE:
            break
    return results


def fetch_index_universe_tickers(
    session: "blpapi.Session",
    index_ticker: str,
    reference_date: str,
    bbg_exchange_codes: list[str],
    canonical_label: str,
) -> pl.DataFrame:
    """指数構成銘柄を blpapi で取得し、地域の正規表記に揃えた Ticker 一覧を返す。

    blpapi の ``INDX_MWEIGHT_HIST`` は ``"<TICKER> <EXCH_CODE>"`` 形式
    （例: NKY → ``"7203 JT"``、SPX → ``"AAPL UW"``）で銘柄を返すため、
    ``bbg_exchange_codes`` に挙げた exchange code を ``canonical_label`` に置換した上で
    ``" Equity"`` サフィックスを付ける。

    Args:
        session: 開始済みの blpapi セッション（``//blp/refdata`` 利用可能）。
        index_ticker: 指数識別子（例: ``"NKY Index"``）。
        reference_date: 構成銘柄の基準日（``YYYYMMDD``）。
        bbg_exchange_codes: 置換対象の市場コード（例: ``["JT"]``、``["UN", "UW", ...]``）。
        canonical_label: 置換後ラベル（例: ``"JP"``、``"US"``）。

    Returns:
        ``BBG_Ticker`` 列のみを持つ一意な DataFrame。
    """
    results = _send_reference_request(
        session,
        securities=[index_ticker],
        fields=["INDX_MWEIGHT_HIST"],
        overrides={"END_DATE_OVERRIDE": reference_date},
    )

    members: list[str] = []
    for r in results:
        if "INDX_MWEIGHT_HIST" not in r["fields"]:
            continue
        arr = r["fields"]["INDX_MWEIGHT_HIST"]
        for i in range(arr.numValues()):
            member = arr.getValueAsElement(i).getElementAsString("Index Member")
            members.append(member)

    pattern = rf"\s({'|'.join(re.escape(c) for c in bbg_exchange_codes)})$"
    bbg_tickers = sorted(
        {
            re.sub(pattern, f" {canonical_label}", m).strip() + " Equity"
            for m in members
        }
    )
    return pl.DataFrame({ID_COL: bbg_tickers})


def fetch_sectors_for_tickers(
    session: "blpapi.Session",
    bbg_tickers: list[str],
    *,
    chunk_size: int = 100,
) -> dict[str, str | None]:
    """BBG Ticker のリストに対して ``INDUSTRY_SECTOR`` を一括取得する。

    blpapi の ``ReferenceDataRequest`` には 1 リクエストあたりの上限があるため、
    ``chunk_size`` 件ずつに分けて送信する。

    Args:
        session: 開始済みの blpapi セッション。
        bbg_tickers: BBG Ticker のリスト（例: ``["7203 JP Equity"]``）。
        chunk_size: 1 リクエストで送る最大銘柄数。

    Returns:
        ``{ticker: sector}`` の dict。Sector が取得できなかった銘柄は ``None``。
    """
    out: dict[str, str | None] = {}
    unique_tickers = list(dict.fromkeys(bbg_tickers))  # 重複除去（順序保持）
    for i in range(0, len(unique_tickers), chunk_size):
        chunk = unique_tickers[i : i + chunk_size]
        results = _send_reference_request(session, chunk, ["INDUSTRY_SECTOR"])
        for r in results:
            sec_field = r["fields"].get("INDUSTRY_SECTOR")
            out[r["security"]] = sec_field.getValueAsString() if sec_field else None
    # リクエストに含まれなかった銘柄も None で埋める
    for t in unique_tickers:
        out.setdefault(t, None)
    return out
