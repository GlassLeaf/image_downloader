# QS-016 修正確認記録

修正確認日：2026-10-06（Asia/Tokyo）。修正前コミット：`78351020373937c7aa660e5748bea10c7414f215`（QS-001・QS-002 の修正を含む）。この記録は当時の監査結果・再現プローブを置き換えず、修正後の保証と検証を追加する。

## 秘密参照の保証

環境変数名を `IMAGE_DOWNLOADER_PLUGIN_<ID_HEX>_<REFERENCE_HEX>` に統一した。各フィールドは元の文字列全体に `value.encode("utf-8", "surrogatepass").hex().upper()` を適用する。短縮、hash、正規化、文字置換は行わない。接頭辞に版番号を付けず、アプリの版更新だけでは変更しない恒久的な命名契約とする。

ID と reference の境界は1個の `_` で区切られ、符号化した各フィールドには `_` が含まれない。大文字に統一しているため Windows でも区別が失われない。直接 API と複数 runtime は同じ非公開の純粋関数を使い、registry や runtime の共有状態に依存しない。

現行の有効 plugin ID は `.` が必須なので、旧名の接頭辞以降には ID 内の置換とフィールド区切りで `_` が最低2個ある。新名では1個だけであり、有効な現行 ID から生成される新旧名は衝突しない。旧名の読み出し、fallback、自動コピー、自動削除は追加していない。

これにより [CWE-694](https://cwe.mitre.org/data/definitions/694.html) に対応する秘密参照の混同を解消する。信頼済みプラグインコードの OS 権限を隔離する機構ではなく、任意コードからの環境変数アクセスを禁止する保証は追加しない。

## 互換性と移行

- 空でない新環境変数を優先し、未設定・空文字は従来の keyring service `image-downloader.plugin.<plugin-id>` と元の reference の username へ進む。keyring の空文字は従来どおり返し、`None` は取得不能とする。
- 公開コンストラクター、`get(name)`、export、設定 schema、ID・reference の検証規則、遅延取得、`SecretNotFound` の型とメッセージを維持する。プラグインの再署名は不要である。
- keyring のみの利用者は移行不要である。環境変数の利用者は名前の後半を変更する。旧名だけでは keyring へ進み、利用可能な値がなければ `SecretNotFound` となる。
- 旧値の所有者を確認し、新名で認証・取得を確認する。他の plugin/profile が使っていないと確認した旧名を User/System 環境、shell 設定、`.env`、CI 設定などの定義元から削除し、再起動後に再注入されないことを確認する。曖昧な旧値の自動コピーや接頭辞全体の削除は行わない。

[名前のみを生成する Python 例と移行・旧名削除手順](../../v3/maintenance/migration.md#plugin-secret-environment-migration) を参照する。

## 回帰テストと検証記録

[通常の回帰テスト](../../../tests/v3/credentials/test_plugin_secrets.py) は、安全な取得結果を固定期待値で確認する。

- ID 内の `-` と `.` の衝突、および ID と reference の境界の衝突で、それぞれ固有の値を取得する。新旧併存時も旧値を取得せず、旧変数そのものは変更しない。
- 固定の新名、現行 ID 制約、新旧名の非衝突、Unicode・surrogate・直接 API の大小文字の区別を確認する。
- 新名優先、未設定・空値、従来の keyring identity、未構成・取得不能の安全な例外、遅延取得、同じ ID・reference の意図された共有を確認する。
- 署名済みの同梱 API キー、OAuth 更新、CSRF ログインプラグインを strict runtime の context から使い、環境変数・keyring の両経路と複数 runtime を確認する。MockTransport の送信結果を検査し、通常ログに秘密値・reference がないことを確認する。

実 credential store・外部通信は使わず、各テスト内の架空の値と一時ディレクトリを使う。当時の `behavior_probe.py` と `raw/probes.stdout` は保存し、旧挙動を期待する監査プローブは修正後の回帰テストとして実行しない。

## 実行結果

ローカル環境は Windows 10・Python 3.11.4、HTTPX 0.28.1、Pillow 12.3.0。pytest は 9.1.1、pytest-cov は 7.1.0 である。

| 検証 | 結果 | 記録 |
| --- | --- | --- |
| v3 全体・カバレッジ付き | 1,622 成功、17 スキップ、731.03 秒 | [pytest stdout](raw/qs-016-pytest-v3.stdout) |
| statement + branch coverage | 88.12%、既存の75%基準を達成 | [coverage JSON](raw/qs-016-coverage.json) |
| 秘密参照・公開 API／文書・設定・同梱プラグイン | 103 成功、17.39 秒（QS-016 の追加40ケースを含む） | [関連テスト stdout](raw/qs-016-focused.stdout) |
| Ruff 0.16.5・mypy 2.3.1 | 合格、本体94ファイルを型検査 | [Ruff](raw/qs-016-ruff.stdout)、[mypy](raw/qs-016-mypy.stdout) |

17 スキップは opt-in Mailpit の12件と、この環境で symlink を作成できない既存5件である。QS-016 の追加ケースにスキップはない。

[実行環境・コマンド・基準コミットと作業ツリー状態・対象 SHA-256](raw/qs-016-validation-metadata.json) を保存した。全体検証の前後で本体94ファイルと追加テストの内容が一致することを照合した。修正コミットは本記録と実装・テストを含むブランチの Git 履歴および PR の head で特定できる。

## CI 確認結果（2026-10-07 JST）

検証した実装コミットは `aee66c92733270fe6434da1e59610e00eac5e466` である。[PR #11 の CI](https://github.com/GlassLeaf/image_downloader/actions/runs/37478431497) は、再実行を含めて15ジョブすべて成功した。

| CI 対象 | 結果 |
| --- | --- |
| Ubuntu・macOS × Python 3.11～3.14 | 各1,626成功・13スキップ、coverage 87.90～87.98% |
| Windows × Python 3.11～3.14 | 各1,627成功・12スキップ、coverage 88.13～88.21% |
| Ruff・mypy・pip check | 全12組合せで成功 |
| sdist/wheel build・非 editable install の package smoke | 3 OS で成功 |

[ジョブ ID・検証済みコミット・結果抜粋・初回失敗の記録](raw/qs-016-ci-results.json) も保存した。CI の13スキップは Mailpit の12件と Unix で適用されない Windows 専用1件、Windows の12スキップは Mailpit の12件である。CI の symlink テストは成功した。

初回の成功だけを示した結果ではない。PR の Windows 3.14 は、変更していない `InterProcessFileLock._ensure_lock_byte()` の `flush()` で `PermissionError` が発生し、子プロセス終了後の親側 queue がタイムアウトした。同じ実装コミットで該当ジョブを1回再実行して成功した。未実行でキャンセルされた macOS の3組合せも再開して成功した。

先行する [push 実行](https://github.com/GlassLeaf/image_downloader/actions/runs/37477284634) では Windows 3.12・3.14 の既存 workflow タイムアウトテストと、3.13 の既存並行更新テストの子プロセス終了判定が失敗した。同じコミットの PR 実行ではそれらも成功した。今回、該当する本体・既存テストは変更しておらず、これら Windows の待機・競合経路の不安定性は別課題として残る。

この CI 結果の追記は検証記録だけの後続コミットであり、検証済みの本体・テストは上記実装コミットと同じ内容である。後続コミットでは CI の再実行を抑止し、文書検証と対象 SHA-256 の照合を行う。
