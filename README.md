# Equity News Pack

PostgreSQL 上のニュースデータから、LLM（OpenAI 互換 API）で市場インプリケーションのある記事を抽出・要約し、Bloomberg blpapi で銘柄エンリッチを行ってローカル CSV に蓄積するパイプライン。

## ディレクトリ構成

```
Equity_News_Pack/
├── README.md
├── pyproject.toml                 # uv で管理する依存定義
├── .env.example                   # 接続情報のテンプレ
├── Equity_News_Pack_JP.ipynb      # 日本株版・実行ノートブック（JP_All.csv 出力）
├── Equity_News_Pack_US.ipynb      # 米国株版・実行ノートブック（US_All.csv 出力）
├── data/                          # 蓄積 Parquet（gitignore）
├── src/
│   ├── settings.py                # .env から接続設定をロード
│   ├── region_config.py           # JP / US の地域別パラメータ
│   ├── news_loader.py             # PostgreSQL からニュース取得
│   ├── enrichment.py              # AI除外・RIC→BBG変換・blpapi 銘柄エンリッチ
│   ├── llm_analyzer.py            # OpenAI 互換 API クライアント + 後処理
│   └── storage.py                 # ローカル Parquet と TSV 出力
└── tests/
    ├── test_storage.py            # 単体（外部依存なし）
    ├── test_enrichment.py         # 単体 + 実 blpapi（要 Bloomberg Terminal）
    ├── test_news_loader.py        # 単体 + psycopg モック
    └── test_llm_analyzer.py       # 単体 + OpenAI モック
```

## セットアップ

### 1. 仮想環境

uv で venv を作成し、依存関係をインストール:

```bash
uv venv --python 3.12
uv sync --dev
# blpapi は Bloomberg のプライベート repo から
uv pip install --index-url https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi
```

### 2. 環境変数

``.env.example`` を ``.env`` にコピーして実値を埋める:

| 変数名 | 用途 |
|---|---|
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | PostgreSQL 接続情報 |
| `DB_NEWS_TABLE` | ニュース格納テーブル名（``"<schema>.<table>"`` 形式可） |
| `LLM_API_TOKEN` | OpenAI 互換 API のアクセストークン |
| `LLM_BASE_URL` | エンドポイントのベース URL |
| `LLM_MODEL` | 使用モデル ID（例: `gemini-3-flash-preview`） |
| `BLPAPI_HOST` / `BLPAPI_PORT` | 通常は `localhost:8194`（Bloomberg Terminal が稼働中の前提） |

### 3. ニュースデータのスキーマ

`DB_NEWS_TABLE` に指定するテーブルは以下の列を持つことを前提とする:

| 列名 | 型 | 用途 |
|---|---|---|
| `unique_story_index` | text | 記事 ID（重複排除キー） |
| `headline` | text | 見出し |
| `story_body` | text | 本文 |
| `story_date_time_utc9` | timestamp | 配信日時 |
| `related_rics` | text | 関連 RIC（スペース区切り） |
| `language` | char(2) | `"ja"` / `"en"` 等 |
| `topics` | text | トピックタグ（スペース区切り） |
| `attribution` | varchar | プロバイダ名 |
| `year_utc9` / `month_utc9` / `day_utc9` | char | パーティション枝刈り用 |

### 4. 動作確認

```bash
# 単体テスト + blpapi 実テスト（Terminal 必須）
.venv/Scripts/python -m pytest

# blpapi なしで実行する場合
.venv/Scripts/python -m pytest -m "not blpapi"
```

## 実行方法

ノートブックを上から順に実行する:

- ``Equity_News_Pack_JP.ipynb`` → ``JP_All.csv``
- ``Equity_News_Pack_US.ipynb`` → ``US_All.csv``

期間は先頭セルの ``START_DATE`` / ``END_DATE`` で指定。蓄積データは ``data/{JP|US}_accumulated.parquet`` に保存され、``unique_story_index`` で重複排除しつつ追記される。

## 処理フロー

1. **データ取得**: 指定期間 + トピック + 言語条件のニュースを取得
2. **重複削除**: プロバイダ違いの同一記事を統合
3. **AI 生成除去**: 地域別の正規表現で機械生成記事を除外
4. **RIC 展開・変換**: 関連 RIC をスペース区切りで展開し、地域マップで RIC → BBG Ticker 変換
5. **指数構成銘柄に絞り込み**: blpapi で取得した NKY / SPX 構成銘柄に semi-join
6. **Sector 付与**: blpapi で構成銘柄の ``INDUSTRY_SECTOR`` を一括取得して left-join
7. **LLM Task 1**: 市場インプリケーション判定（``has_implication == 1`` のみ残す）
8. **SUID 集約**: 同一記事の複数銘柄をリスト化して 1 行にまとめる
9. **LLM Task 2**: 本文の日本語要約 + センチメント分類（-1 / 0 / +1）
10. **LLM Task 3**: 日付単位で要約をまとめて日次アブストラクト生成（参考出力）
11. **保存**: ローカル Parquet にアップサート → ``{region}_All.csv`` を TSV 形式で書き出し

## 地域追加

新しい地域を追加する場合は ``src/region_config.py`` に ``RegionConfig`` インスタンスを 1 つ足し、ノートブックの ``region = ...`` を差し替えるだけ。
