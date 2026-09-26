# テストと移行

## v3 の検証範囲

v3 の test は実サイト・実 account・実 secret に接続しない。temporary config root と
temporary absolute plugin root、fixture source、固定 HTTP response を使用する。現在の
repository test entry point は `tests/v3/` である。

`tests/v3/`は`cli/`、`configuration/`、`plugins/`、`application/`、
`transport/`、`storage_output/`、`observability/`、`distribution/`に
機能別に分ける。リポジトリroot、署名済みplugin source、ローカルHTML fixtureの場所は
`tests/v3/conftest.py`の共通fixtureから取得し、テストファイルの深さに依存しない。
Cookie保存と画像処理は別ファイルで検証する。

```powershell
python -m pytest -q
python -m pytest -q --cov=image_downloader
python -m ruff check .
python -m mypy src/image_downloader
```

release/CI ではこれに加え、package install 後の `doctor --json`、strict/warn/off fixture、
署名済み install fixture を検証する。branch coverageを含む全production moduleのcoverageは
75%以上を必須とし、runtime/logging/notification等を除外しない。

CIはPython 3.11、3.12、3.13、3.14と、Windows、Linux、macOSの直積matrixで上記の品質検査を
実行する。`requires-python`の下限は3.11であり、package classifierもこの検証範囲に一致させる。

`tests/legacy/v2` は互換性のない過去APIの資料であり、現行テストではない。pytestの
`testpaths`と`norecursedirs`、Ruffの`extend-exclude`で明示的に除外する。現行の
受入条件はすべて`tests/v3`へ移植してから変更する。以後の品質ゲートは
`python -m ruff check .`に統一する。実行時data rootの`.runtime`と第三者vendor sourceは
検査対象外とし、生成物や外部コードへ修正を加えない。

Ruffの行長上限は120文字で、`E501`を有効にする。長い式や条件は名前付きの値、builder、
DTO変換関数へ分け、第三者vendorや生成物以外に包括的な`E501`除外を追加しない。
署名済み`plugin-sources`はbyte列の変更で署名が無効になるためformatter対象外とし、既存の
長行を持つ署名済みsourceだけをper-file指定で除外する。lint自体は引き続き実行する。

## 設定受入ケース

- core → app → profile app → global → profile global → parent host → full host → CLI の全順序。
- main app profile と `--profile` の選択、overlay による `profile:` reject、profile data path。
- IDNA A-label、PSL registrable domain 継承、IPv4 exact match、IPv6 32-hex filename。
- unknown key、site-layer `security`、旧 `plugins.<id>` tree / `plugin_catalog`、旧 config path の reject。
- mapping deep merge、scalar/list/null replace、CLI app override、operation plugin override。
- `plugin_settings` ID/kind/secret schema、processor secret reject、disabled setting。

## manifest/catalog 受入ケース

- wrapper/inner unknown field、missing field、schema/API/kind/ID/version/path/base64/key ID の reject。
- Ed25519 valid/invalid signature、canonical manifest digest、file tree digest、extra file/hash mismatch。
- symlink と Windows reparse point、non-regular file、`__pycache__/*.pyc` exception。
- duplicate manifest ID、parent directory kind mismatch、catalog duplicate/unknown/stale/revoked entry。
- strict catalog absence/breakage、warn diagnostic、off の structural validation。
- install staged copy、cache non-copy、class contract failure rollback、catalog atomic replace、target
  directory name collision、same-ID update、kind/publisher/content-version constraints。
- trust/revoke/list と selection priority、revocation recovery、key rotation/downgrade confirmation。

fixture author は signing key を test のみで生成する。production private key を repository や
fixture に置かない。

## 更新履歴のschema移行

profileごとの`state/updates.json`はschema version 2を使用し、plugin IDと
呼び出し時のfeed URLのSHA-256の組ごとにsnapshotを保持する。feed URL自体は
namespace keyとして保存しない。旧version 1（version欄のない
形式を含む）は次回の更新確認時に自動移行する。旧形式にはfeed URLがないため、旧項目は
`legacy_records`に保全する。初回確認ではURLとcontent IDが完全一致する候補だけを
比較に使い、旧項目だけを根拠とした`REMOVED`は出さない。そのfeedの新snapshotを
保存した次回以降に通常の削除判定を始める。未対応の将来schemaや破損stateは
上書きせずエラーにする。

同じprofileを使う本アプリの複数プロセスは`state/updates.lock`を共有し、最新版の読込から
比較・原子的保存までを排他する。異なるfeedの変更は両方保持し、同じfeedへの変更は
ロック取得順に確定する。旧APIの`UpdateState.save()`も同じロックを使用し、既存の
feed snapshotを保持する。ロック待ちの既定上限は30秒で、時間切れは更新確認全体の
エラーになる。ロックファイルは解放後も削除しない。保証対象は同一端末のローカル保存先で
協調する本アプリの実行であり、NASや外部アプリによる変更は対象外である。

## selection、context、operation 受入ケース

- enabled external site の `match_priority` → catalog `selection_priority`、同順位 error。
- `matches()` false、exception、non-bool return、fallback enabled/disabled/CLI override。
- disabled site candidate exclusion、disabled processor chain skip と doctor/debug diagnostic。
- processor chain の config/kind/duplicate/order、processor が site manifest/catalog だけを読むこと。
- selected site/chain 以外の runtime plugin config ID reject。
- `validate_config` が sync/side-effect-free かつ `None` return であること、run/check-updates
  時の selected validation と doctor の全 static plugin validation。
- `PluginExecutionContext` と `TransformContext` の immutable mapping、safe app settings に
  header/proxy/security/notification/credential が漏れないこと、secret value が表示されないこと。
- source entry の isolated namespace/relative helper import、service snapshot 後の filesystem
  変更が既存 service を変えないこと。

## doctor の受入ケース

- failed plugin が一件でも `healthy: false`/exit 4、warning のみ exit 0。
- unknown plugin-root entry、stale catalog、untrusted/invalid entry、disabled processor の表示。
- bare `--host` の host overlay validation。
- path を持つ absolute URL の selection/config validation、network 不使用。
- bare host/path のない URL に runtime plugin config を与えた時の configuration error。

## v2 からの手動移行

v3 は旧形式を読まない。migration script や adapter も提供しない。次の作業を明示的に行う。

1. 旧 source の site/processor 一つごとに v3 directory unit を作る。site と processor を
   一つの manifest/class に混在させない。
2. class descriptor の ID/priority を manifest の `id`/`match_priority` へ移し、
   `validate_config` を実装する。
3. author default を `<unit>/<config_file>.yaml` の `config:` mapping へ移す。利用者設定を
   `plugin_settings.<id>.config` へ移す。raw secret は移さず external reference にする。
4. `app.yaml`、`sites/global.yaml`、`sites/<host>.yaml`、
   `profiles/<name>/app.yaml`、`profiles/<name>/sites/...` へ configuration を手動で再配置する。
   profile overlay/site overlay の `profile:` は除去する。
5. old profile data が必要なら、設定済みの新 data root
   `<storage.data_root>/profiles/<name>/` へ内容を確認してコピーする。旧 path の fallback はない。
6. 全 regular file の SHA-256 tree、manifest digest、Ed25519 signature を新たに作る。
7. absolute source directory から `plugin install` または `plugin trust` を実行し、strict
   `doctor --json` と fixture test で検証する。

移行期間に v2/v3 を同時 discovery することはできない。同じ plugin root に legacy
entry-point metadata を置いても core は無視する。新しい root を用意して v3 を検証してから
runtime の `--plugin-root` または installed root を切り替える。

## 運用チェックリスト

- [ ] main/profile/site config に旧 key/旧 path が残っていない。
- [ ] manifest と catalog の ID/kind/publisher/version/key/tree digest が一致する。
- [ ] plugin directory に link/reparse point、未署名の helper、cache 以外の余分な file がない。
- [ ] `doctor --json` が healthy で、意図しない warning がない。
- [ ] fallback policy と disabled plugin/processor の意図を設定レビューした。
- [ ] `plugin_settings` に raw secret がなく、environment/keyring reference が解決できる。
- [ ] upgrade/revoke 手順と catalog backup/rollback 手順を管理者が保持している。
