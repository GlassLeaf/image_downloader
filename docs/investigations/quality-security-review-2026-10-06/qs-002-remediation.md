# QS-002 修正確認記録

修正日：2026-10-06（Asia/Tokyo）。修正前の基準：`1e5b47e0aadb64c726bd1f5d43076d73350475b4`（QS-001修正を含む）。

## 自動Refererの送信保証

組込みの`GenericHtmlPlugin`クラス本体だけで、HTML解析と画像URL解決後の自動Refererを制限する。
参照元は最終ページURL、送信先は解決済み画像URLであり、`base href`はRefererの参照元にしない。

| 条件 | 自動Referer |
| --- | --- |
| 同一オリジン | userinfo・fragmentを除いたページURL。パス・クエリを保持 |
| 別オリジン、HTTPS→HTTP以外 | ページの`scheme://host[:port]/`のみ |
| HTTPS→HTTP | `None` |
| HTTP(S)以外・解析不能・不正ポート | `None`。画像URL自体の既存検証・失敗処理は維持 |

オリジンはscheme・正規化host・実効portで比較する。既定portを同一扱いにし、port 0は区別する。
IPv6の等価な表記とIDNを照合でき、同一オリジンのクエリの順序・重複・percent encodingを保持する。
HTMLやHTTPの`Referrer-Policy`宣言で方針を緩和せず、403から完全URLへ戻す再送もしない。
この自動値の規則は[RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.1.3)と
[W3C Referrer Policy](https://w3c.github.io/webappsec-referrer-policy/#referrer-policy-strict-origin-when-cross-origin)を参考にする。

## 互換性と影響

- 共有HTML parser・要求生成hook・独自プラグイン・継承クラスは従来の扱いを維持する。
- 公開DTO・設定schema・共通gateway・認証・cookie・リダイレクトの処理は変更しない。
- 明示Refererは値と優先順位を維持する。自動値が`None`でも、`network.headers`に指定があれば送信する。
- 同一オリジンのクエリ、明示指定した秘密部分、既存の詳細inspection出力はこの制限の対象外。
- manifest・RequestSpecの`referer`がoriginや`None`に変わり、完全なページRefererが必要なCDNは403等になり得る。
  403に対する既存の`AuthenticationError`を維持する。必要なサイトでは独自プラグインで明示する。
- 画像URL・index・image_idとrevisionの計算方式を維持する。完了済み`updated`の一律再取得や状態移行は行わない。
- 追加周回の`ImageResource`全体比較を維持する。別オリジン画像でページクエリだけが変わっても、
  実際の自動Refererが同じoriginなら成功画像を保持する。同一オリジンではクエリ変更が再取得条件に残る。

## 回帰テストと記録

[汎用HTMLの回帰テスト](../../../tests/v3/application/test_generic_workflow.py)に固定期待値で境界値・模擬送信を追加した。
小さなPNGとMockTransportを使い、manifest・要求・preview・実送信、通常ログの秘密値秘匿、明示値の優先順位、
継承クラス、リダイレクトと前後のbase、403、revision、追加周回の成功画像保持を確認する。

元の[behavior_probe.py](behavior_probe.py)と[当時の記録](raw/probes.stdout)は履歴資料として保存する。
元のQS-002プローブは秘密値の送信をassertするため、修正後の成功条件には使用しない。

## 実行結果

ローカル環境はWindows 10・Python 3.11.4、HTTPX 0.28.1。

| 検証 | 結果 | 記録 |
| --- | --- | --- |
| v3全体・カバレッジ付き | 1,582成功、17スキップ、531.52秒 | [pytest stdout](raw/qs-002-pytest-v3.stdout) |
| statement + branch coverage | 87.96%、既存の75%基準を達成 | [coverage JSON](raw/qs-002-coverage.json) |
| 汎用HTML・encoding isolation・redirect・公開API／文書 | 124成功、119.34秒 | 通常回帰テストとして上記の全体ログにも包含 |
| Ruff 0.16.5・mypy 2.3.1 | 合格、本体94ファイルを型検査 | [Ruff](raw/qs-002-ruff.stdout)、[mypy](raw/qs-002-mypy.stdout) |

17スキップはopt-in Mailpitの12件と、この環境でsymlinkを作成できない既存5件。
symlink経路が成功したという意味ではない。QS-002の追加48ケースはすべて成功した。
[実行環境・コマンド・本体と変更テストのSHA-256](raw/qs-002-validation-metadata.json)も保存し、
全体検証後に同じファイル内容であることを照合した。

3 OS・Python 3.11〜3.14とpackage smokeは既存CIで検証し、結果は
[修正ブランチのActions](https://github.com/GlassLeaf/image_downloader/actions?query=branch%3Acodex%2Ffix-qs-002-auto-referer)とPRを参照する。
