# QS-012 修正確認記録

記録日：2026-10-12（Asia/Tokyo）。
修正基準コミット：`9ee3b5cdf666171d1c2877b6faca446bccb91a8b`。
本記録は当時の監査報告・プローブを保存し、修正後の仕様と検証を追加する。

## 修正内容と保証

本体の変更は `observability/notifications.py` のみである。`send_message()` の空の拒否辞書を
全宛先受理とし、非空辞書なら既存の SMTP 終了処理後に非公開の部分拒否例外を送出する。
例外のメッセージは固定で、保持する診断情報は拒否件数と安全に抽出したドメイン一覧だけである。
全拒否の `SMTPRecipientsRefused` は元の例外型・インスタンスを維持する。
接続・TLS・認証・DATA・SMTP 終了処理の既存例外も変更しない。

通知サービスは部分拒否を `smtp_partial_recipients_refused`、全拒否を
`smtp_all_recipients_refused` として失敗ログ・イベントを各1回記録する。
全受理の `True` と成功イベントは維持する。主 download/workflow 結果・CLI 終了コードは変更せず、
残りの通知経路は続行する。一部には送信済みなので自動再送は追加しない。

## 診断と秘匿の境界

既存 `logger.log()` により、有効な通常ログ・console・debug.log へ同じ診断を出力する。
`count` は拒否辞書の件数、`refused_domains` は重複なし・ソート済みの全ドメイン一覧、
`smtp_host` は組込み sender の不変設定にある送信先ホストである。
送信開始前に設定のホストを取得し、非同期送信中に sender の設定参照が差し替わっても
使用した設定と異なるホストを診断へ出さない。
メールアドレスは標準 `Address(addr_spec=...)` で解析し、`@` の単純分割は行わない。
DNS 名は小文字の IDNA ASCII、IPv4/IPv6 は正規化した表記とし、SMTP ホストのポートは除去する。
制御文字・不正形式・解析不能な値は `unknown` とし、元の文字列を診断へ戻さない。

メールアドレスのローカル部分・表示名・SMTP 応答本文・認証情報は出さない。
宛先ドメインと SMTP ホストは今回の仕様で明示的に表示するため、組織や送信先サービスを示す情報は
ログに残る。同一ドメイン内の個々の拒否宛先は特定できない。
独自 sender の SMTP ホストは取得できなければ `unknown` とする。
DNS/MX 検索、接続先 IP・配送先 MX の特定、受信箱への最終到達確認は行わない。

イベントの DTO・公開 API・設定・共通の秘匿処理は変更しない。
イベントは固定の `error_code` で拒否を区別し、ホスト情報は渡さない。
既存の `error` と例外名の秘匿を維持する。

## 検証

[追加回帰テスト](../../../tests/v3/observability/test_smtp_refusal.py) は fake SMTP、
MockTransport、小さな画像、一時ディレクトリを使用する。実メール・実認証情報・DNS/MX 通信は使わない。

- TLS 3 モードの全受理・部分拒否・全拒否、終了処理、1回の送信、元の全拒否例外の維持。
- 通常ファイル・console・debug の分類／件数／両ホスト、失敗イベント1回、他通知経路の継続。
- 同じドメインの複数宛先、複数ドメイン、大小文字、IDN、引用符内の `@`、IPv4/IPv6。
- 不正形式・制御文字・改行・深い入れ子コメントと `unknown`、ローカル部分／応答の秘密の非露出。
- 非同期送信中の設定参照の差替えでも、送信開始時の SMTP ホストを記録する。
- 既存 SMTP 各段階の例外、通知無効、download/workflow/CLI の成功・画像保存の維持。

当時の `behavior_probe.py::test_qs012_partial_smtp_rejection_is_ignored` は旧問題挙動を期待するため、
修正後の成功を確認するテストとしては使用しない。監査資料は書き換えない。

ローカル環境は Windows 10 build 19045、CPython 3.11.4 AMD64。
pytest 9.1.1、pytest-cov 7.1.0、Ruff 0.16.5、mypy 2.3.1、HTTPX 0.28.1、Pillow 12.3.0、
Pydantic 2.13.5、build 1.6.1 を使用する。開始時の作業ツリーは clean で、修正は未コミットである。
公開関数・コンストラクター10件の AST signature は基準コミットと一致する。

最終ソースの重点検証は114件成功、78.46秒。追加の SMTP 回帰テスト52件を含む。
wheel/sdist を `--no-isolation` でビルドし、`tools/release_smoke.py --offline` で実インストール後の
CLI と署名済みプラグインの smoke を確認した。開発途中の全体テストは、深いコメントへの対処と
非同期送信前のホスト取得を補強するため2回停止し、各回とも所有プロセス5件を終了した。
中断した試験は最終版の合否に使用せず、補強後のソースで全体試験を実行する。

最終ソースの検証結果は次のとおりである。時間は pytest stdout または外部 supervisor の記録に従う。

| 検証 | 結果 | 記録 |
| --- | --- | --- |
| v3 全体・カバレッジ付き | 1,826 成功、17 スキップ、失敗0、586.84秒 | [stdout](raw/qs-012-v3.stdout) |
| statement + branch coverage | 88.45%、75%基準を達成 | [coverage JSON](raw/qs-012-coverage.json) |
| SMTP・通知・診断失敗・公開API／文書 | 114 成功、78.46秒、スキップなし | [stdout](raw/qs-012-focused.stdout) |
| Ruff / mypy | 合格、本体95ファイルを型検査 | [Ruff](raw/qs-012-ruff.stdout)、[mypy](raw/qs-012-mypy.stdout) |
| pip check | 合格 | [stdout](raw/qs-012-pip_check.stdout) |
| wheel / sdist build | 合格、36.204秒 | [stdout](raw/qs-012-build.stdout) |
| package smoke（Windows / Python 3.11、offline） | 合格、40.688秒 | [stdout](raw/qs-012-package_smoke.stdout) |

全体試験の外部期限は1,800秒で、watchdog は発動していない。新規52ケースにスキップはない。
検証前後の本体95ファイルと対象テストの SHA-256 が一致し、wheel 内の通知モジュールも検証済みソースと
バイト単位で一致する。[環境・コマンド・件数・ハッシュ・中断記録](raw/qs-012-verification.json) を保存した。

既存 workflow は push / pull request で、3 OS × Python 3.11〜3.14 の12 test job と
3 OS の package smoke job を起動する。今回の未コミット変更に対する CI 結果は、
対応するコミットが CI で実行されるまで未検証とする。ローカル成功で他 OS・他 Python の結果を代用しない。
