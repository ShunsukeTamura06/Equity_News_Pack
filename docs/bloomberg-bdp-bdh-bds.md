# Bloomberg Excel関数 BDP / BDH / BDS 仕様リファレンス（Claude Code向け）

> 目的: Claude Codeがこのファイルを読み、Bloomberg Excel Add-inの `BDP` / `BDH` / `BDS` を**正しい引数で**生成できるようにする。
> 対象: Excel数式（主）、VBAからの挿入・更新、Python `xbbg` との対応（従）。
> 凡例: **【要実機確認】** = 公開資料で裏付けが取れていない項目。断定せず、末尾「実機確認テスト」で確認する。

---

## 0. Claude向け生成ルール（最優先）

1. **関数はフィールドの種類で選ぶ**（§1）。迷ったら `FLDS <GO>` でフィールド種別を確認するようユーザーに依頼する。
2. 証券は `"<ティッカー> <イエローキー>"` 形式で書く（§2）。イエローキーを省略しない。
3. 日付は **セル参照 / `DATE(y,m,d)` / `TODAY()`** で渡す。`"10/01/2024"` のような地域依存の文字列は使わない。
4. BDH/BDSの**オプション**は `"Key=Value"` を1引数ずつ並べる（例: `"Per=M","Days=A"`）。BDP/BDSの**オーバーライド**は名前と値を別々の引数で渡す（例: `"BEST_FPERIOD_OVERRIDE","1BF"`）。
5. BDH/BDSは結果を**右・下の隣接セルに展開する**。出力先の右下に十分な空きを確保する前提で配置する。
6. 存在が不確かなフィールド名・オプションを**創作しない**。必要なら「【要実機確認】」と明記して提案する。
7. 対象外（BQL, BDIB, BDIT, BEQS, BSRCH）は使わない。ユーザーが明示した場合のみ扱う。

---

## 1. 関数の選択

| 欲しいもの | 関数 | フィールド例 | 戻り値 |
|---|---|---|---|
| 現在値・静的属性（1値） | `BDP` | `PX_LAST`, `NAME`, `CRNCY`, `CUR_MKT_CAP`, `ID_ISIN`, `GICS_SECTOR_NAME` | 1セル |
| 日次などの時系列 | `BDH` | `PX_LAST`, `PX_OPEN`, `PX_HIGH`, `PX_LOW`, `PX_VOLUME`, `CUR_MKT_CAP` | 日付×値の表（複数セル） |
| 表形式のバルクデータ | `BDS` | `INDX_MEMBERS`, `INDX_MWEIGHT`, `DVD_HIST_ALL`, `FUT_CHAIN`, `OPT_CHAIN`, `BLOOMBERG_PEERS` | 複数行×複数列 |

- `FLDS <GO>`（またはExcelのField Search）で各フィールドの種別を確認できる。BDHで使えるのは「ヒストリカル対応」のフィールドだけ。
- バルクフィールド（`INDX_MEMBERS` など）を `BDP` で呼ぶと正しく取れない。必ず `BDS` を使う。

---

## 2. 証券識別子

| 形式 | 例 |
|---|---|
| ティッカー + イエローキー | `"7203 JT Equity"`, `"IBM US Equity"`, `"NKY Index"`, `"USDJPY Curncy"`, `"CL1 Comdty"` |
| 識別子プレフィクス | `"/isin/JP3633400001"`, `"/cusip/459200101"`, `"/sedol1/2005973"`, `"/bbgid/BBG000BLNNH6"` |
| ISIN + イエローキー（債券など） | `"US912810TM08 Govt"`, `"XS1234567890 Corp"` |
| 価格ソース指定（債券）【要実機確認】 | `"US912810TM08@BGN Govt"` |

- イエローキー: `Equity`, `Index`, `Curncy`, `Comdty`, `Govt`, `Corp`, `Mtge`, `M-Mkt`, `Muni`, `Pfd`
- 取引所コード例: `JT`（日本・総合）, `US`（米国・総合）, `LN`（ロンドン）, `HK`（香港）
- 大文字・小文字は区別しない。ただし生成するときは上の表記に揃える。

---

## 3. BDP — 単一値（リファレンス/リアルタイム）

### シグネチャ

```
=BDP(security, field, [override1, value1, override2, value2, ...])
```

| 引数 | 型 | 必須 | 説明 |
|---|---|---|---|
| `security` | 文字列/セル | ✓ | 証券は**1つだけ**（§2） |
| `field` | 文字列/セル | ✓ | フィールドニーモニック（例 `PX_LAST`） |
| `override, value` | 文字列の組 | – | オーバーライドフィールド名と値のペア |

- オーバーライドは**名前と値のペア**で渡すのが標準。`"NAME=VALUE"` を1引数で渡す書き方が通るかは【要実機確認】。
- `UpdateFrequency`（ミリ秒、既定300、最小300）の指定もできる（出典: bettersolutions）。

### 主なオーバーライド

| オーバーライド | 用途 | 値の例 |
|---|---|---|
| `BEST_FPERIOD_OVERRIDE` | コンセンサス予想の対象期 | `"1BF"`（来期）, `"2BF"`, `"1FY"` |
| `EQY_FUND_CRNCY` | ファンダメンタルの表示通貨 | `"USD"`, `"JPY"` |
| `FUND_PER` | 決算期間の種類 | `"Q"`, `"Y"` |
| `SETTLE_DT` | 債券の受渡日 | `"20241031"` |

### 例

```
=BDP("7203 JT Equity","PX_LAST")
=BDP("IBM US Equity","NAME")
=BDP($A2,B$1)
=BDP("IBM US Equity","BEST_EPS","BEST_FPERIOD_OVERRIDE","1BF")
=BDP("7203 JT Equity","CUR_MKT_CAP","EQY_FUND_CRNCY","USD")
```

- `=BDP($A2,B$1)`: A列に証券、1行目にフィールドを並べ、右下へコピーして表にする。

---

## 4. BDH — 時系列

### シグネチャ

```
=BDH(security, fields, start_date, [end_date], ["Key=Value", ...])
```

| 引数 | 型 | 必須 | 説明 |
|---|---|---|---|
| `security` | 文字列/セル | ✓ | 証券（§2） |
| `fields` | 文字列/セル範囲 | ✓ | 1フィールドの文字列、または**横に並べたセル範囲**（例 `B1:D1`） |
| `start_date` | 日付 | ✓ | Excel日付・`DATE()`・セル参照。相対指定（`"-1Y"`, `"-3M"`, `"-10D"`）は【要実機確認】 |
| `end_date` | 日付 | – | 省略または `""` で当日まで（【要実機確認】）。確実に書くなら `TODAY()`。未来日は不可 |
| オプション | `"Key=Value"` | – | 下表 |

- 複数フィールドを1引数のカンマ区切り文字列（`"PX_LAST,PX_VOLUME"`）で渡せるかは【要実機確認】。**セル範囲を推奨**する。

### オプション

| Key | 値 | 既定 | 意味 |
|---|---|---|---|
| `Per`（`Period`） | `D` / `W` / `M` / `Q` / `S` / `Y`（カレンダー基準 `cd`/`cw`/`cm`/`cq`/`cs`/`cy` も可） | `D` | 期間 |
| `Days` | `T`（取引日）/ `W`（平日）/ `A`（全暦日） | `T` | 行に含める日 |
| `Fill` | `P`（前値で埋める）/ `B`（空白）【`B` は要実機確認】 | – | `Days=W/A` のとき、データのない日の埋め方 |
| `Dir` | `V`（縦）/ `H`（横）【値は要実機確認】 | `V` | 出力の向き |
| `Dts`（`Dates`） | `S`（日付を表示）/ `H`（日付を非表示） | `S` | 日付列の表示 |
| `Sort` | `A`（昇順）/ `D`（降順） | `A` | 日付の並び順 |
| `Curr`（`Currency`） | ISO通貨コード `USD`, `JPY` など | 現地通貨 | 指定通貨に換算 |
| `CshAdjNormal` | `Y` / `N`【要実機確認】 | DPDF設定【要実機確認】 | 通常の現金配当で価格を調整 |
| `CshAdjAbnormal` | `Y` / `N`【要実機確認】 | DPDF設定【要実機確認】 | 特別配当などで価格を調整 |
| `CapChg` | `Y` / `N`【要実機確認】 | DPDF設定【要実機確認】 | 分割・スピンオフなど資本異動で調整 |

- **未調整の価格**が欲しいときは `"CshAdjNormal=N","CshAdjAbnormal=N","CapChg=N"` の3つを揃えて指定する。
- 関数ビルダーで作ると `"cols=2;rows=253"` のような文字列が自動で付く。これは展開サイズの目印なので、手で書くときは付けなくてよい。

### 例

```
=BDH("7203 JT Equity","PX_LAST",DATE(2024,1,1),TODAY())
=BDH("IBM US Equity",B1:D1,DATE(2020,1,1),DATE(2024,12,31),"Per=M")
=BDH("USDJPY Curncy","PX_LAST",DATE(2024,1,1),TODAY(),"Days=A","Fill=P")
=BDH("NKY Index","PX_LAST",DATE(2024,1,1),TODAY(),"Sort=D","Dts=H")
=BDH("7203 JT Equity","PX_LAST",DATE(2015,1,1),TODAY(),"CshAdjNormal=N","CshAdjAbnormal=N","CapChg=N")
=BDH("7203 JT Equity","PX_LAST",DATE(2024,1,1),TODAY(),"Curr=USD")
```

- 2行目: `B1:D1` に `PX_LAST` / `PX_VOLUME` / `CUR_MKT_CAP` を入れておく。月次。
- 3行目: 全暦日。休日は前の値で埋める。
- 4行目: 降順で、日付列を出さない。
- 5行目: 配当・分割で調整しない生の価格。

---

## 5. BDS — バルク（表形式）

### シグネチャ

```
=BDS(security, field, [override1, value1, ...], ["Key=Value", ...])
```

| 引数 | 型 | 必須 | 説明 |
|---|---|---|---|
| `security` | 文字列/セル | ✓ | 証券（§2） |
| `field` | 文字列/セル | ✓ | バルクフィールドを**1つ** |
| オーバーライド | 名前と値の組 | – | 期間などの指定 |
| オプション | `"Key=Value"` | – | 下表 |

### オプション

| Key | 値 | 意味 |
|---|---|---|
| `Headers` | `Y` / `N` | 列見出しを出すかどうか |
| `StartRow` / `EndRow` | 整数 | 返す行の範囲を絞る |
| `StartCol` / `EndCol` | 整数 | 返す列の範囲を絞る |

- オプション名は出典（O'Reilly付録）で確認済み。値の書式と既定値は【要実機確認】。

### 主なバルクフィールドとオーバーライド

| フィールド | 内容 | 使うオーバーライド |
|---|---|---|
| `INDX_MEMBERS` | 指数の構成銘柄 | – |
| `INDX_MWEIGHT` | 構成銘柄とウェイト | – |
| `INDX_MWEIGHT_HIST` | 過去時点の構成とウェイト | `END_DATE_OVERRIDE`（`"YYYYMMDD"`） |
| `DVD_HIST_ALL` | 配当の履歴 | `DVD_START_DT`, `DVD_END_DT`（`"YYYYMMDD"`） |
| `FUT_CHAIN` | 先物の限月一覧 | `INCLUDE_EXPIRED_CONTRACTS`（`"Y"`） |
| `OPT_CHAIN` | オプション銘柄の一覧 | – |
| `BLOOMBERG_PEERS` | 同業他社 | – |
| `DES_CASH_FLOW` | 債券のキャッシュフロー | – |

### 例

```
=BDS("NKY Index","INDX_MEMBERS")
=BDS("NKY Index","INDX_MWEIGHT","Headers=Y")
=BDS("NKY Index","INDX_MWEIGHT_HIST","END_DATE_OVERRIDE","20231231","Headers=Y")
=BDS("7203 JT Equity","DVD_HIST_ALL","DVD_START_DT","20200101","DVD_END_DT","20241231","Headers=Y")
=BDS("CL1 Comdty","FUT_CHAIN","INCLUDE_EXPIRED_CONTRACTS","Y")
```

---

## 6. エラー値と対処

| 表示 | 意味 | 対処 |
|---|---|---|
| `#N/A Requesting Data...` | 取得中（非同期） | 待つ。VBAでは§7.2の方法で待機する |
| `#N/A N/A` | データがない | 期間・フィールド・証券を見直す |
| `#N/A Invalid Security` | 証券識別子が不正 | イエローキー・取引所コード・ISINを確認する |
| `#N/A Field Not Applicable` | その証券には使えないフィールド | 別のフィールドにするか、証券の種類を確認する |
| `#N/A Invalid Field` | フィールド名が誤っている | `FLDS` で正しいニーモニックを調べる |
| `#N/A Invalid Override` | オーバーライド名か値が誤っている | `FLDS` でオーバーライドの一覧を確認する |
| `#N/A Authorization` | データの閲覧権限がない | 契約・権限の問題なのでユーザーに伝える |
| `#N/A Connection` | ターミナルに接続していない | Bloombergにログインし、Add-inを再接続する |

- 表示される文字列は【要実機確認】（Add-inのバージョンによって変わる可能性がある）。Claudeが判定するときは「`#N/A` で始まるかどうか」で判定する。

---

## 7. 注意点

### 7.1 Excel

- BDH/BDSの出力先の右下に既存データがあると、上書きされたりエラーになったりする。**専用シートか、十分な空きのある場所**に置く。
- 大量の銘柄 × 長い期間 × 多数のフィールドを一度に取ると、**データ使用量の上限**（日次・月次）に達するおそれがある。必要な範囲だけ取り、結果は値として貼り付けて保存する。
- 日本語ロケールでは `"01/02/2024"` が1月2日か2月1日か曖昧になる。日付は `DATE()` かセル参照で渡す。オーバーライドの日付は `"YYYYMMDD"` の文字列で渡す。

### 7.2 VBA（M端末：Pythonなし・VBAあり を想定）

```vba
' 数式を挿入する
Sub InsertFormulas()
    With ThisWorkbook.Worksheets("Data")
        .Range("B2").Formula = "=BDP(""7203 JT Equity"",""PX_LAST"")"
        .Range("D2").Formula = "=BDH(""7203 JT Equity"",""PX_LAST"",DATE(2024,1,1),TODAY())"
    End With
    Application.Run "RefreshEntireWorkbook"   ' Add-inのマクロ名は【要実機確認】
    Application.OnTime Now + TimeValue("00:00:02"), "WaitForBloomberg"
End Sub

' 取得完了を待つ。DoEventsでループし続けてはいけない（取得が進まない）
Sub WaitForBloomberg()
    Static tries As Long
    If CountRequesting(ThisWorkbook.Worksheets("Data")) > 0 And tries < 60 Then
        tries = tries + 1
        Application.OnTime Now + TimeValue("00:00:02"), "WaitForBloomberg"
    Else
        tries = 0
        Call AfterDataLoaded   ' 続きの処理（値の貼り付けなど）
    End If
End Sub

Function CountRequesting(ws As Worksheet) As Long
    Dim c As Range
    For Each c In ws.UsedRange
        ' 文字列かエラー値かが未確定のため、表示テキストで判定する
        If InStr(1, c.Text, "Requesting", vbTextCompare) > 0 Then CountRequesting = CountRequesting + 1
    Next
End Function
```

- 取得は**非同期**で、マクロが制御を返したあとに進む。同じマクロの中でループして待っても値は入らないので、`Application.OnTime` で間隔をあけて確認し直す。
- 更新用マクロ: `RefreshEntireWorkbook` / `RefreshEntireWorksheet` / `RefreshCurrentSelection` / `RefreshAllStaticData`（いずれも【要実機確認】）。

### 7.3 Python `xbbg`（S端末など、Bloomberg Terminal + blpapi がある環境のみ）

| Excel | xbbg |
|---|---|
| `=BDP(sec, fld, "OVR", v)` | `blp.bdp(tickers=sec, flds=fld, OVR=v)` |
| `=BDH(sec, flds, s, e, "Per=M", "Days=A", "Fill=P")` | `blp.bdh(sec, flds, start_date=s, end_date=e, Per="M", Days="A", Fill="P")` |
| `=BDH(..., "CshAdjNormal=N", "CshAdjAbnormal=N", "CapChg=N")` | `blp.bdh(..., CshAdjNormal=False, CshAdjAbnormal=False, CapChg=False)` |
| `=BDS(sec, fld, "OVR", v)` | `blp.bds(sec, fld, OVR=v)` |

- xbbgでは、Excelと同じ名前のオプションをキーワード引数として渡せる（xbbgのREADMEにある例で確認）。
- ターミナルがログイン中でないと接続できない。

---

## 8. 実機確認テスト（1回のセッションでまとめて確認する）

新しいシートの `A1` から下に順に入力し、結果を記録して持ち帰る。結果がエラーなら、その項目は本書の記述を修正する。

| # | 確認する項目 | 入力する数式 | 期待される結果 |
|---|---|---|---|
| 1 | BDPのオーバーライドを `"K=V"` 1引数で書けるか | `=BDP("IBM US Equity","BEST_EPS","BEST_FPERIOD_OVERRIDE=1BF")` | ペア形式 `=BDP("IBM US Equity","BEST_EPS","BEST_FPERIOD_OVERRIDE","1BF")` と同じ値になる |
| 2 | BDHのフィールドをカンマ区切りで書けるか | `=BDH("IBM US Equity","PX_LAST,PX_VOLUME",TODAY()-10,TODAY())` | 2列の値が返る |
| 3 | BDHの相対日付 | `=BDH("IBM US Equity","PX_LAST","-1M","")` | 直近1か月分が返る |
| 4 | `end_date` を `""` にした場合 | `=BDH("IBM US Equity","PX_LAST",TODAY()-10,"")` | 当日までの値が返る |
| 5 | `Fill=B` | `=BDH("USDJPY Curncy","PX_LAST",TODAY()-14,TODAY(),"Days=A","Fill=B")` | 土日の行が空白になる |
| 6 | `Dir=H` | `=BDH("IBM US Equity","PX_LAST",TODAY()-5,TODAY(),"Dir=H")` | 横向きに出力される |
| 7 | BDSのオプション | `=BDS("NKY Index","INDX_MEMBERS","Headers=Y","StartRow=1","EndRow=5")` | 見出し + 5行が返る |
| 8 | エラー文字列 | `=BDP("XXXX ZZ Equity","PX_LAST")` / `=BDP("IBM US Equity","NOT_A_FIELD")` | 表示された文字列をそのまま記録する |
| 9 | `#N/A ...` の型（文字列かエラー値か） | `=ISTEXT(BDP("XXXX ZZ Equity","PX_LAST"))` / `=ISERROR(BDP("XXXX ZZ Equity","PX_LAST"))` | どちらがTRUEかを記録する |
| 10 | 調整フラグの値 | `=BDH("7203 JT Equity","PX_LAST",DATE(2015,1,1),DATE(2015,1,10),"CapChg=N")` と `"CapChg=Y"` | 分割前の期間で値が変わる |
| 11 | `@BGN` の位置 | `=BDP("US912810TM08@BGN Govt","PX_LAST")` | 価格が返る |
| 12 | VBAの更新マクロ名 | Immediateウィンドウで `Application.Run "RefreshEntireWorkbook"` | エラーにならない |

---

## 参考（公開情報）

- bettersolutions: Bloomberg Add-in — https://www.bettersolutions.com/excel/add-ins/bloomberg.htm
- O'Reilly『Unlocking Financial Data』付録A（BDSのオプション: HEADERS / STARTROW / ENDROW / STARTCOL / ENDCOL）
- xbbg（PyPI / GitHub）のREADMEにある `bdh` の例（`Per`, `Days`, `Fill`, `CshAdj*`, `CapChg`）
- 公式の詳細はターミナルの `HELP DAPI <GO>` と、Excelの関数ビルダー（fxボタン）で確認できる
