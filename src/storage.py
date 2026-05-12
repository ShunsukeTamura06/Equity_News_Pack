"""ローカル Parquet 入出力と TSV 出力ヘルパ。

旧版では S3 Parquet を扱っていたが、AWS 移行後は S3 が使えないためローカル
ファイルシステム（``data/`` ディレクトリ）に蓄積する。
"""
from __future__ import annotations

from pathlib import Path

import polars as pl


def read_parquet_local(path: Path | str) -> pl.DataFrame | None:
    """ローカル Parquet を読み込む。存在しない場合は ``None``。

    Args:
        path: 読み込み対象のパス。

    Returns:
        DataFrame、存在しない場合は ``None``。
    """
    p = Path(path)
    if not p.exists():
        return None
    return pl.read_parquet(p)


def upsert_parquet_local(
    path: Path | str,
    new_df: pl.DataFrame,
    key_col: str = "SUID",
) -> pl.DataFrame:
    """ローカル Parquet ファイルへ追記する（重複時は新規を優先）。

    既存ファイルがあれば読み込み、新規データを縦結合してから ``key_col`` で
    重複削除（``keep="last"``）して上書き保存する。親ディレクトリは自動生成。

    Args:
        path: 対象 Parquet のパス。
        new_df: 追記する新規 DataFrame。
        key_col: 重複削除に使うキー列。

    Returns:
        マージ後の DataFrame。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    existing_df = read_parquet_local(p)
    merged = (
        new_df
        if existing_df is None
        else pl.concat([existing_df, new_df], how="vertical_relaxed")
    )
    merged = merged.unique(subset=[key_col], keep="last")
    merged.write_parquet(p)
    return merged


def serialize_list_columns_to_string(
    df: pl.DataFrame,
    cols: list[str],
    sep: str = ",",
) -> pl.DataFrame:
    """List 型カラムを区切り文字列に変換する（CSV/TSV 書き出し前の前処理）。

    Polars の List 型はそのままでは ``write_csv`` できないため、
    ``cast(Utf8) → list.join`` で 1 セル 1 文字列に変換する。
    対象列が List 型でない場合は何もしない。

    Args:
        df: 対象 DataFrame。
        cols: 変換対象の列名。
        sep: 結合に用いる区切り文字。

    Returns:
        指定列を文字列化した DataFrame。
    """
    transforms = [
        pl.col(col)
        .list.eval(pl.element().cast(pl.Utf8, strict=False))
        .list.join(sep)
        .alias(col)
        for col in cols
        if (dtype := df.schema.get(col)) is not None and str(dtype).startswith("List")
    ]
    return df.with_columns(transforms) if transforms else df


def write_all_tsv(
    df: pl.DataFrame,
    path: Path | str,
    *,
    write_header_when_empty: bool = True,
) -> None:
    """``JP_ALL.csv`` / ``US_ALL.csv`` 形式（TSV + 行番号インデックス）で書き出す。

    形式仕様（参照ファイルに準拠）:

    - 区切り文字はタブ
    - 行区切りは CRLF（``\\r\\n``）。**末尾行に改行は付けない**
    - 先頭列は無名のインデックス列（1, 2, 3, ...）
    - List 型カラムは事前に :func:`serialize_list_columns_to_string` で文字列化しておく
    - セル内のタブ文字は半角スペースに置換する（区切り文字と衝突するため）

    Args:
        df: 書き出し対象の DataFrame（List 型カラムは事前にシリアライズ済み想定）。
        path: 出力先パス。親ディレクトリは自動生成する。
        write_header_when_empty: ``df`` が空のときヘッダだけを書くか（既定: True）。
            ``False`` にすると 0 byte ファイルを生成する。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if df.is_empty() and not write_header_when_empty:
        p.write_bytes(b"")
        return

    cols = df.columns
    lines: list[str] = ["\t".join([""] + cols)]
    for i, row in enumerate(df.iter_rows(), start=1):
        cells = [str(i)]
        for v in row:
            if v is None:
                cells.append("")
            else:
                # タブはセパレータと衝突するためスペースに置換
                cells.append(str(v).replace("\t", " "))
        lines.append("\t".join(cells))

    p.write_bytes("\r\n".join(lines).encode("utf-8"))
