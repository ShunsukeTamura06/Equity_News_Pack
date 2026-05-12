"""環境変数依存の接続設定を管理するモジュール。

PostgreSQL / OpenAI 互換 LLM API / blpapi の接続情報を ``.env`` から読み込む。
業務ロジック側のパラメータ（トピックタグ、対象指数等）は ``region_config.py`` 側に置く。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
"""プロジェクトのルートディレクトリ（``src/`` の親）。"""

DATA_DIR = PROJECT_ROOT / "data"
"""ローカル蓄積用 Parquet 等を置くディレクトリ。"""

# .env を 1 度だけロードする
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class DBConfig:
    """PostgreSQL 接続設定。

    Attributes:
        host: DB ホスト。
        port: DB ポート。
        database: DB 名。
        user: 接続ユーザー名。
        password: パスワード。
        news_table: ニュース格納テーブル名（スキーマ込みで ``"<schema>.<table>"`` 可）。
    """

    host: str
    port: int
    database: str
    user: str
    password: str
    news_table: str = "news_all"

    def to_conninfo(self) -> str:
        """``psycopg.connect()`` に渡す接続文字列を返す。"""
        return (
            f"host={self.host} port={self.port} dbname={self.database} "
            f"user={self.user} password={self.password}"
        )


@dataclass(frozen=True)
class LLMConfig:
    """OpenAI 互換 LLM API の接続設定。"""

    api_token: str
    base_url: str
    model: str


@dataclass(frozen=True)
class BlpapiConfig:
    """blpapi（Bloomberg API）の接続設定。"""

    host: str = "localhost"
    port: int = 8194


@dataclass(frozen=True)
class Settings:
    """プロジェクト全体の接続設定を束ねたオブジェクト。"""

    db: DBConfig
    llm: LLMConfig
    blpapi: BlpapiConfig = field(default_factory=BlpapiConfig)
    data_dir: Path = DATA_DIR

    @classmethod
    def from_env(cls) -> "Settings":
        """環境変数から ``Settings`` を構築する。

        Returns:
            ``Settings`` インスタンス。

        Raises:
            ValueError: 必須環境変数が未設定の場合。
        """
        return cls(
            db=DBConfig(
                host=_required("DB_HOST"),
                port=int(os.environ.get("DB_PORT", "5432")),
                database=_required("DB_NAME"),
                user=_required("DB_USER"),
                password=_required("DB_PASSWORD"),
                news_table=os.environ.get("DB_NEWS_TABLE", "news_all"),
            ),
            llm=LLMConfig(
                api_token=_required("LLM_API_TOKEN"),
                base_url=_required("LLM_BASE_URL"),
                model=_required("LLM_MODEL"),
            ),
            blpapi=BlpapiConfig(
                host=os.environ.get("BLPAPI_HOST", "localhost"),
                port=int(os.environ.get("BLPAPI_PORT", "8194")),
            ),
        )


def _required(key: str) -> str:
    """必須環境変数を取得する。未設定なら ``ValueError`` を送出。"""
    value = os.environ.get(key)
    if not value:
        raise ValueError(
            f"Required environment variable '{key}' is not set. "
            "Copy .env.example to .env and fill in the values."
        )
    return value
