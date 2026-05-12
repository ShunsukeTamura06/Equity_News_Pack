実行環境をBQuantからAWSへ移行することを考えています。
移行に伴い、以下の変更が加わります。
- Sparkが使えなくなる
- S3が使えなくなる
- ニュースデータの取得元が変わる。S3からDBになる。詳しくはNEW_DB_INFO.md参照。
- LLM APIのプロバイダーが変わる。bedrockからOpenAIcompatibleAPIになる。詳しくはNEW_LLMAPI_INFO.md参照。
- BQLが使えなくなる。代わりにblpapiを使う。