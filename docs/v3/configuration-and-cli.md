# 設定と CLI

## 最小の実行

インストール後は console command と module invocation が同じ user configuration、
data root、plugin root 規則を使う。CWD の `app.yaml` は読み込まない。

```powershell
image-downloader "https://example.test/gallery"
python -m image_downloader "https://example.test/gallery"
```

通常実行、Cookie 操作、`doctor`、plugin 管理は、`--config` がなければ platformdirs の固定
user config path だけを読む。CWD の `app.yaml` は候補にしない。固定 path、存在状態、package baseline、
OS 標準 root は `image-downloader config path` でいつでも確認できる。

既定 user config が未作成でも起動は継続する。package 同梱 baseline の
`storage.data_root: null` は `PlatformDirs("image-downloader", appauthor=False).user_data_path`、
`plugins.root: null` はその `plugins` 子 directory を表す。設定がない状態で download/update、cookie
export/import/browser-import、plugin install/trust/revoke を開始すると、主操作の前に固定 user config を
作成する。作成 file は同梱 `app.yaml` と schema default から決まる全設定を保存し、automatic root は
absolute path として実値化する。CLI の profile/root/output/logging/JSON option、plugin override、fallback
override は保存しない。完全 snapshot のため、その後の bundled default 更新は自動反映されない。主操作が
全件失敗・例外・中断しても、開始前に作成済みの file は残る。明示 `--config` の欠落では作成しない。
自動作成に失敗しても root が解決済みなら主操作の exit status は維持し、stderr に警告を出す。`doctor`、
`config path`／`config explain`、plugin list は設定 file・root directory を作成・書換えしない。`--config`、
非 null の `storage.data_root` / `plugins.root`、`--data-root`、`--plugin-root` は absolute path でなければ
ならない。

```powershell
image-downloader config init `
  --data-root "D:\Images\image-downloader" `
  --plugin-root "C:\Users\you\AppData\Local\image-downloader\plugins"
```

source checkout は editable install してから使う。

## 設定 tree

package 同梱の `app.yaml` は immutable baseline であり、CLI と
`load_application_config()` の両方で必ず一度だけ読む。利用者設定は絶対 `--config`、または
platformdirs の固定 user config path から読む。設定 file がない場合は main layer を適用せず、
baseline と schema default だけで解決する。

`config init [ABSOLUTE_PATH]` は path 省略時に固定 user config を作成する。これは明示的な作成操作で
あり確認を求めない。生成 file は全設定をコメントで示す sparse template で、指定した root だけを
実値として保存するため、将来の bundled default 更新を固定しない。初回の自動作成はこれとは別に、
同梱 baseline の完全 snapshot を保存する。`config profile init NAME` は base config がなければ sparse
file を作成してから overlay を作る。

```text
<config-root>/
  app.yaml
  sites/
    global.yaml
    <canonical-host>.yaml
    ipv6-<32-lowercase-hex>.yaml
  profiles/
    <profile>/
      app.yaml
      sites/
        global.yaml
        <canonical-host>.yaml
```

profile configuration は `<config-root>/profiles/<name>/` に置く。profile data は常に
`<storage.data_root>/profiles/<name>/` に置く。`storage.data_root: null` の場合は platformdirs の
OS 標準 user-data path がこの root になる。別の場所を使う場合だけ絶対 `storage.data_root` を指定する。

既存の config、overlay、data root、plugin root/source は symbolic link と Windows reparse
point を含められない。存在する YAML は regular non-link file でなければならず、読込不能・
directory・root外参照を「欠落」として無視しない。managed output も link/reparse point と
hard link を拒否する。これは別 process による path 差替えを検出して `StorageSafetyError` に
するための trust boundary である。

## profile と設定統合順

profile 名は `--profile NAME` があればそれを、なければ main `app.yaml` の
`profile.default` を使い、overlay を読む**前**に固定する。profile app/site file に
`profile:` があれば設定エラーである。これは profile overlay による選択先と data
保存先の不一致を防ぐ。

mapping は再帰的に merge し、scalar、list、`null` は後の layer が丸ごと置換する。
削除 marker はない。

| 順序 | layer |
|---:|---|
| 1 | core の `AppConfig` default |
| 2 | package 同梱 immutable baseline |
| 3 | `<config-root>/app.yaml` |
| 4 | `<config-root>/profiles/<profile>/app.yaml` |
| 5 | `sites/global.yaml` |
| 6 | `profiles/<profile>/sites/global.yaml` |
| 7 | registrable domain から完全 host までの各 `sites/<host>.yaml` |
| 8 | 同じ host ごとの `profiles/<profile>/sites/<host>.yaml` |
| 9 | CLI の app/bootstrap override |

たとえば `book.cdn.example.co.uk` は PSL の registrable domain
`example.co.uk` から、`example.co.uk`、`cdn.example.co.uk`、
`book.cdn.example.co.uk` の順に読む。各 host では通常 layer の直後に profile layer
を読む。DNS name は IDNA A-label の小文字へ正規化する。IPv4/IPv6 は継承せず完全一致
のみで、IPv6 `2001:db8::1` は
`ipv6-20010db8000000000000000000000001.yaml` になる。

## 設定 schema

top-level と nested model は strict である。整数に bool や `1.0`、秒数に文字列・`NaN`・
`Infinity` は使えない。型・値域違反と layer 制約違反は、後続 layer が同じ値を上書きしても無視されず、
file・layer・dotted key を示す configuration error で起動を停止する。user 管理の main/profile/site
layer にある削除済み key と static schema の未知 key は最終値へ反映しない。状態変更操作では無表示で
物理削除し、`doctor`、`config explain`、plugin list など read-only 操作では無表示で無視するだけで
書換えない。任意 key を許す `network.headers`、`notification.desktop`、plugin private `config` は
unknown key ではなく、その値は各 schema／plugin が検証する。雛形は `image-downloader config init` で
作成する。

| key | 用途・既定 |
|---|---|
| `profile.default` | profile 名。`default` |
| `storage.data_root` | `null` または絶対 data root。`null` は OS 標準 data root。profile data はこの配下 |
| `plugins.root` | `null` または絶対 plugin/catalog root。`null` は OS 標準 data root の `plugins` |
| `download.chapter_concurrency` | 同時に処理する chapter 数。厳密な整数 `>= 1`、既定 `3` |
| `download.image_concurrency_per_chapter` | chapter ごとの fetch/process/save job 数。厳密な整数 `>= 1`、既定 `8` |
| `download.continue_on_image_error` | recoverable な画像単位エラー後に続行するか。既定 `true` |
| `download.allow_empty_chapter_manifest` | chapter が一つもない manifest を許可するか。既定 `false` |
| `output.*` | name format、既存 file 方針、JPEG/PNG/WEBP、plugin 別出力分離 |
| `output.max_component_length` | path component の任意上限。既定 `null`（切り詰めなし）。指定時は hash suffix 付きで決定的に切り詰める |
| `output.lock_timeout_seconds` | 同一章ディレクトリの保存ロックを待つ秒数。既定 `30`、`0` は待機なし。負数・非有限値は無効。タイムアウトは画像単位で再試行せず操作全体を失敗させる |
| `media.*` | input MIME/decode validation と MIME mismatch 方針 |
| `media.max_image_pixels` | 入力画像と processor 出力の幅×高さの任意上限。既定 `null`（寸法検査なし） |
| `logging.*` | console と公開してよい URL parameter 名 |
| `network.request_concurrency` | 全 origin 合計の in-flight HTTP request 数。厳密な整数 `>= 1`、既定 `8` |
| `network.origin_request_concurrency` | scheme/host/port ごとの request 上限。`null` は global を継承、指定時は `1..request_concurrency` |
| `network.registrable_domain_request_concurrency` | registrable domain ごとの request 上限。`null` は global を継承、指定時は `1..request_concurrency` |
| `network.request_timeout_seconds` | phase timeout の既定。有限数 `> 0`。個別 phase timeout の `null` はこれを継承 |
| `network.max_attempts` | 初回を含む総試行回数。厳密な整数 `>= 1`。`1` は retry なし |
| `network.retry_max_delay_seconds` | retry wait の上限。有限数 `>= 0`。jitter を含めてこの値を超えず、`0` は待機なし |
| `network.auth_refresh_attempts` | 認証 refresh の追加試行回数。厳密な整数 `>= 0` |
| `network.global_request_interval_seconds` | 全 origin 共通の request 開始間隔。有限数 `>= 0` |
| `network.pool_max_connections` / `pool_max_idle_connections` | HTTP pool の総 connection 数／idle 数。整数で、idle は `0..total` |
| `network.max_response_bytes` | `Content-Length` と実受信量の双方に適用する上限。既定 64 MiB |
| `notification.*` | desktop/email notification、event category別route、credential service。既定で無効。値は起動時に型検証される |
| `security.plugin_verification` | `strict`、`warn`、`off`。main/profile app layer のみ。`off` は実行時承認も必要 |
| `fallback.generic_html.enabled` | builtin generic HTML fallback。既定 `true` |
| `image_processors.chain` | 実行する processor ID の順序。重複はエラー |
| `plugin_settings` | plugin ごとの利用者設定。後述 |

### 全 key の型と既定値

以下は雛形にある app 設定の完全な値域である。`string` と `integer` は YAML の暗黙変換を
行わない strict 型であり、`true`/`false` を整数として、`1.0` を整数としては受理しない。
秒数の `number` は有限値だけであり、`NaN` と `Infinity` は無効である。

| key | 型・値域・既定値 |
|---|---|
| `profile.default` | strict string `[A-Za-z0-9_-]+`。`default` |
| `storage.data_root` | `null` または絶対 path string。`null`（OS 標準 data root） |
| `plugins.root` | `null` または絶対 path string。`null`（OS 標準 data root の `plugins`） |
| `download.chapter_concurrency` | strict integer `>= 1`。`3` |
| `download.image_concurrency_per_chapter` | strict integer `>= 1`。`8` |
| `download.continue_on_image_error` | strict boolean。`true` |
| `download.allow_empty_chapter_manifest` | strict boolean。`false` |
| `output.directory_format` / `output.filename_format` | strict string。`%NUM%_%TITLE%_%SUBTITLE%` / `%NUM%.%EXT%` |
| `output.existing_file` | `overwrite`、`skip`、`rename`、`error`。`overwrite` |
| `output.image_format` | `JPEG`、`PNG`、`WEBP`。`JPEG` |
| `output.isolate_by_plugin` | strict boolean。`false` |
| `output.max_component_length` | `null`（上限なし）または strict integer `>= 16`。`null` |
| `output.lock_timeout_seconds` | 有限 number `>= 0`。`30`。`0` は待機なし |
| `media.input_validation` | `content_type`、`decode`、`both`。`content_type` |
| `media.content_type_mismatch` | `accept`、`error`。`accept` |
| `media.max_image_pixels` | `null`（無制限）または strict integer `>= 1`。`null` |
| `logging.console.enabled` | strict boolean。`true` |
| `logging.safe_query_parameters` / `safe_fragment_parameters` | URL parameter 名の list。各要素は非機密の正規 identifier、重複不可。`[]` |
| `network.request_concurrency` | strict integer `>= 1`。`8` |
| `network.origin_request_concurrency` / `registrable_domain_request_concurrency` | `null`（global を継承）または strict integer `1..request_concurrency`。`null` |
| `network.request_timeout_seconds` | 有限 number `> 0`。`30` |
| `network.connect_timeout_seconds` / `read_timeout_seconds` / `write_timeout_seconds` / `pool_timeout_seconds` | `null`（`request_timeout_seconds` を継承）または有限 number `> 0`。`null` |
| `network.max_attempts` | 初回を含む strict integer `>= 1`。`3` |
| `network.retry_max_delay_seconds` | 有限 number `>= 0`。`30`。`0` は待機なし |
| `network.auth_refresh_attempts` | strict integer `>= 0`。`1` |
| `network.global_request_interval_seconds` | 有限 number `>= 0`。`0`（間隔なし） |
| `network.pool_max_connections` | strict integer `>= 1`。`8` |
| `network.pool_max_idle_connections` | strict integer `0..pool_max_connections`。`8` |
| `network.max_response_bytes` | strict integer `>= 1`。`67108864` |
| `network.http2` / `follow_redirects` | strict boolean。ともに `true` |
| `network.proxy` | `null` または strict string。`null` |
| `network.headers` | `string -> string` mapping。`{User-Agent: image-downloader/0.0.0.1b0}`。表示時は値を伏せる |
| `notification.enabled` | strict boolean。`false` |
| `notification.methods` | `desktop` / `email` の list。`[desktop]` |
| `notification.notify_on` | notification category の list。`[fetch_error, process_error, save_error, auth_error, config_error, plugin_error, update_error, storage_error, runtime_error]` |
| `notification.routes` | `notification category -> [desktop, email]` mapping。`{}` |
| `notification.desktop` | desktop backend 向け mapping。`{}` |
| `notification.email.smtp_host` / `from` / `username` | strict string。いずれも `''` |
| `notification.email.smtp_port` | strict integer `1..65535`。`465` |
| `notification.email.use_tls` | strict boolean。`true` |
| `notification.email.to` | strict string の list。`[]` |
| `notification.email.credential_service` | strict string。`image-downloader.smtp` |
| `security.plugin_verification` | `strict`、`warn`、`off`。`strict` |
| `image_processors.chain` | reverse-DNS plugin ID の重複なし list。`[]` |
| `plugin_settings.<plugin-id>.enabled` | strict boolean。`true` |
| `plugin_settings.<plugin-id>.config` | plugin author が検証する mapping。`{}` |
| `plugin_settings.<plugin-id>.secrets` | `lower_snake_case -> UPPERCASE_REFERENCE` mapping。`{}` |
| `fallback.generic_html.enabled` | strict boolean。`true` |

`storage` と `plugins` は main app layer だけに置ける。`security` は main/profile app
layer にだけ置け、site overlay には置けない。`profile` は main app layer にだけ置ける。
`plugin_settings` は全 non-bootstrap layer に置ける。catalog path は常に
`plugins.root/catalog.json` である。

`null` は generic な削除 marker ではない。mapping は再帰的に merge し、scalar、list、`null` は
後の layer が置換する。`null` を受け付けるのは schema に明示された optional key だけであり、root は
OS 標準値、concurrency と phase timeout は継承、画像・path の optional 上限は無制限を意味する。
最終値、main config の種別、適用された layer、各 key の由来は
`config explain [--host HOST] [--json]` で確認できる。

次の旧キーは user 管理 layer で無表示に削除する。設定値を置換先へ自動変換・移動しないため、必要な
値は利用者が新キーへ明示的に設定する。

| 旧キー | 置換先 |
|---|---|
| `network.max_retries` | `network.max_attempts` |
| `network.max_retry_wait_seconds` | `network.retry_max_delay_seconds` |
| `network.max_auth_retries` | `network.auth_refresh_attempts` |
| `network.max_concurrency` | `network.request_concurrency` |
| `network.host_max_concurrency` | `network.origin_request_concurrency` |
| `network.site_max_concurrency` | `network.registrable_domain_request_concurrency` |
| `network.request_interval_seconds` | `network.global_request_interval_seconds` |
| `network.max_connections` / `max_keepalive_connections` | `network.pool_max_connections` / `pool_max_idle_connections` |
| `network.max_chapter_concurrency` | `download.chapter_concurrency` |
| `continue_on_error` / `allow_empty_manifest` | `download.continue_on_image_error` / `download.allow_empty_chapter_manifest` |

通知を使う場合は`notification.enabled: true`を指定する。新規設定の既定の通知対象は`fetch_error`、
`process_error`、`save_error`、`auth_error`、`config_error`、`plugin_error`、`update_error`、
`storage_error`、`runtime_error`で、既定の配信手段はdesktopである。`save_error`は画像保存段階、
`storage_error`は画像保存段階外の既知保存失敗、`runtime_error`は既知カテゴリに分類できない失敗だけを表す。
既存の明示 `notify_on` は自動変更しないため、必要なカテゴリは明示的に追加する。desktop通知には
`image-downloader[notify]`の追加インストールが必要。emailを選ぶ場合は`smtp_host`、`from`、
`to`を設定する。実際に使用するrouteの配信手段はservice構築時に検証され、未設定なら
`ConfigurationError`となる。`routes`で全対象カテゴリをemailへ切り替えた場合、desktop用の
追加依存は不要である。配信が失敗してもダウンロード結果は変わらず、失敗イベントと診断ログに残る。

診断ログの書込みや終了処理に失敗しても、保存済み画像は`SAVED`のままで、
`SAVE_SUCCESS`を`SAVE_FAILED`へ変更しない。ログ障害は秘密情報を含まない短い警告を
stderrへ出す。画像ファイルそのものの書込み失敗は従来どおり保存失敗となる。

`save_error`は画像保存失敗だけを示し、画像処理失敗は`process_error`で通知する。両方とも新規設定の
既定対象だが、既存の明示設定では必要なカテゴリを`notify_on`または`routes`へ追加する。

`plugins.root` は v3 の bootstrap setting で、`null` なら OS 標準 plugin root を使う。
廃止済み v2 の `plugins.<id>` configuration tree とは別物で、v3 は後者を読まない。

`output.isolate_by_plugin` は既定 `false`。false は従来の profile 内 output layout を
維持するため、別host/pluginが同じchapter/file名を生成すると `existing_file` 方針に従う。
true の場合だけ output は
`<data-root>/profiles/<profile>/downloads/<canonical-host>/<plugin-id>/...` へ分離される。
IPv6 host component は `ipv6-<32-lowercase-hex>` である。cookie、state、debug log は
profile単位で共有される。

`existing_file=overwrite` は別operation（別プロセスを含む）が保存済みのfileを上書きする。同じoperation内で
同じ名前を保存済み・予約中の場合は、後続を連番付きの名前へ変更して相互上書きを防ぐ。`rename`も
既存fileと実行中予約の両方を連番化し、`error`はどちらも失敗させる。`skip`は実行中予約の
完了を待ち、保存成功後だけskipする。先行保存が失敗した場合は、待機中の処理が元の名前を
引き継ぐ。衝突判定はOSに依存せず、Unicode NFC正規化後の大文字小文字を区別しない名前で
行う。連番を追加する場合も`output.max_component_length`を超えない。

`existing_file=error` の衝突はディスク書込み障害ではなく、上書き防止のための制御された失敗である。
各衝突は `ExistingFileConflictError`（`reason_code: existing_file_conflict`）として保存失敗に記録され、
`download.continue_on_image_error=true` なら他画像を継続する。結果に保存済みまたはskip済みがあれば
CLI は部分失敗、それ以外は失敗終了となる。通知と章ログには衝突先の安全な相対出力パスを含める。

同じ端末のローカル保存先に対し、協調する本アプリ実行は章ディレクトリ単位のプロセス間ロックを使う。
画像取得・処理は並列のまま、保存名の再確認から保存の確定／取消までだけを排他する。
ロックファイルはダウンロード先でなくprofileの`state/output-locks`に残し、次回実行でも同じ名前を使う。
保証対象は本アプリが保存する画像のファイル名であり、NAS等のネットワークファイルシステム、
ロックに協調しない外部アプリは対象外である。Cookieと更新履歴はそれぞれ別の専用ロックで保護する。

HTTP response はストリーミングで読み込み、`network.max_response_bytes` を超えた時点で停止する。
`network.follow_redirects`は既定で有効だが、各転送先を送信前に検査する。匿名の別origin転送では
元のheaderやCookieを引き継がず、認証済み転送はpluginが明示的に許可したoriginだけを追跡する。
transport retry は GET/HEAD/OPTIONS/TRACE のみが既定対象である。429/500/502/503/504 の
`Retry-After` を尊重し、上限付きjitterを加える。POST等を再試行しても安全だとpluginが保証できる
個別requestだけ、`RequestSpec.retry_non_idempotent=True` を指定する。

## 利用者 plugin 設定と secret

```yaml
plugin_settings:
  com.example.gallery:
    enabled: true                 # 省略時 true
    config:
      api_base: https://api.example.test
      retries:
        page: 2
    secrets:                      # site_plugin のみ
      api_token: PRODUCTION_TOKEN

image_processors:
  chain:
    - com.example.scaler
```

- ID は reverse-DNS form。存在しない ID は compose error。
- `config` は mapping。plugin author default と深く統合され、値の schema は plugin author が検証する。
- app layer の最終値と由来は `config explain`、author default を含む最終 plugin config は
  `doctor --host HOST` で確認する。
- `enabled: false` の site plugin は URL 選択候補から除外される。
- chain 内の disabled processor は error ではなく skip され、`doctor`/debug に表示される。
- processor に `secrets` を置くことはエラー。
- secret logical name は lower snake case、value は `A-Z0-9_` の外部 reference。
  値そのものを YAML に書かない。

secret はまず environment variable
`IMAGE_DOWNLOADER_PLUGIN_<NORMALIZED_ID>_<REFERENCE>`、次に OS keyring の service
`image-downloader.plugin.<plugin-id>` / username `<REFERENCE>` から読む。

## Cookie の保存と移送

runtime は profile ごとの Cookie を
`<storage.data_root>/profiles/<profile>/cookie/cookies.enc` に AES-256-GCM で保存する。
暗号鍵は OS keyring の `image-downloader.cookie-key` に保持し、Cookie 本体や鍵を app
configuration へ書き込まない。

別環境への移送には passphrase 付き export container を使う。実行中の URL と Cookie 操作は
組み合わせられない。

```powershell
image-downloader --export-cookies "D:\backup.cookies"
image-downloader --import-cookies "D:\backup.cookies"
image-downloader --import-browser-cookies example.test
```

export/import は passphrase を対話入力する。browser import には
`python -m pip install ".[browser-cookies]"` が必要である。

browser import は既存Cookieを保持して差分マージする。既存の暗号化ファイルが破損している場合は
上書きせずエラーになる。通常のservice終了も起動時から変更したCookieだけを最新版へ反映し、
同じprofileを使う本アプリの複数プロセスによる別Cookieの更新を失わない。Cookieファイルの
読込・更新・保存は同じ`cookie/cookies.lock`で排他し、ロックファイルは解放後も残す。
同一Cookie（domain・path・name）の値が異なる場合は後で確定した更新を採用する。同じ値なら
有効期限の長い方を採用し、期限なしのsession Cookieは永続Cookieより短期とする。明示的な
削除は後で確定したものを反映する。保証対象は同一端末上で協調する本アプリの実行であり、
NASや外部アプリによる変更は対象外である。
passphrase付きファイルの明示的なimportも別Cookieを保持する。破損ファイルからの復旧には
この明示的なimportを使える。ライブラリの`CookieStore.save()`は従来どおりJar全体の置換である。

## CLI

```text
image-downloader download URL [options]
image-downloader URL [options]
image-downloader doctor [--host HOST_OR_URL] [options]
image-downloader config path [--json]
image-downloader config explain [--host HOST_OR_URL] [options]
image-downloader config init [ABSOLUTE_PATH] [--data-root ABSOLUTE_PATH] [--plugin-root ABSOLUTE_PATH]
image-downloader config profile init NAME [--config ABSOLUTE_PATH]
image-downloader plugin install ABSOLUTE_DIRECTORY [options]
image-downloader plugin trust ABSOLUTE_DIRECTORY [options]
image-downloader plugin revoke PLUGIN_ID [options]
image-downloader plugin list [options]
image-downloader cookie export PATH [options]
image-downloader cookie import PATH [options]
image-downloader cookie browser-import DOMAIN [options]
```

CLIは上記5種類のtop-level subcommandを個別のhandlerへdispatchする。従来の
`image-downloader URL`形式と`--export-cookies`／`--import-cookies`／
`--import-browser-cookies`形式も互換入力として受け付け、同じdownload／cookie handlerで処理する。
command固有optionを別commandへ指定した場合はconfiguration errorになる。

| option | 意味 |
|---|---|
| `--config ABSOLUTE_PATH` | main user config を明示指定 |
| `--profile NAME` | この invocation の profile を選択 |
| `--data-root ABSOLUTE_PATH` | profile data root をこの invocation だけ上書き |
| `--plugin-root ABSOLUTE_PATH` | external plugin/catalog root をこの invocation だけ上書き |
| `--existing-file` | `overwrite`/`skip`/`rename`/`error` の一時 override |
| `--image-format` | `JPEG`/`PNG`/`WEBP` の一時 override |
| `--no-console-log` | console chapter log を無効化 |
| `--list-updated-urls` | download の代わりに update snapshot を比較 |
| `--json` | JSON結果を持つコマンドの成功結果を JSON 表示し、全対応コマンドの失敗を固定 JSON で返す。download時はconsole logを抑え、標準出力をJSON専用にする |

| `--fallback-generic` | `auto`（YAML）、`enabled`、`disabled` |
| `--allow-unverified-plugins` | `security.plugin_verification: off` をこの run だけ承認 |
| `--yes` | plugin 変更の承認。`config init` は常に明示的な作成操作として保存する |
| `--plugin-config ID=JSON_OBJECT` | plugin private config の inline override。繰返し可 |
| `--plugin-config-file ABSOLUTE_PATH` | `{"plugin_id":"…","config":{…}}` file。絶対 regular non-link file のみ。繰返し可 |

`--list-updated-urls --json` は `{"updated_urls": [URL, ...], "removed": 件数}` を
単一のJSON objectとして出力する。JSONなしでは従来どおりURLを行ごとに標準出力へ出す。
download のJSON出力中、pluginのPython `print()` は標準エラーへ送る。
native code がOSの標準出力file descriptorへ直接書く場合は制御対象外である。

`--json` の実行時・引数・構文エラーは、終了コードを維持したまま、標準出力へ次の単一 object を返す。

```json
{"error":{"code":"...","reason":"...","exception":"...","message":"...","operation":"..."}}
```

HTTP status、最終応答 URL、画像段階、画像 URL、出力先は該当時だけ追加される。`message` は固定の安全な
公開メッセージであり、例外文字列そのものではない。アプリケーション管理下の診断は標準エラーへ送る。
`--help` とキャンセル（終了 130）はこの error object に変換しない。

`config path` は fixed user config、存在状態、package baseline、OS 標準 root を read-only で表示する。
`config explain` は selected profile と host に対し、適用・未存在の全 layer、機密値を伏せた有効設定、
CLI override、各 key の最終値の由来を表示する。

`config init` は root option の有無にかかわらず destination に sparse template を作成する。path を
省略した場合の destination は fixed user config である。既存 file は上書きしない。`config profile init`
は base config がなければ同じ sparse template を作成してから profile overlay を作る。

plugin config file は出現順に統合し、全 inline `--plugin-config` はその後に統合する。
file の既存祖先 component と leaf は symbolic link／Windows reparse point を含められず、
leaf は regular file でなければならない。config root 外の明示指定 file は許可する。
設定付き一致を実装する site plugin では、operation override も候補選択に使われる。
enabled site plugin と enabled processor chain 以外の ID はエラーで、選択winner以外の
site plugin を指定してもエラーである。`doctor --host` は bare host でも
`https://<host>/` として選択判定するため、runtime plugin config を含めて確認できる。

main profile の overlay は省略可能である。main の `profile.default` と異なる
`--profile NAME` は `profiles/<name>/app.yaml` が必要で、作成には
`config profile init NAME` を使う。site/global/host overlay はすべて任意である。

## doctor と終了 status

```powershell
image-downloader doctor --json
image-downloader doctor --host example.co.uk
image-downloader doctor --host "https://example.co.uk/gallery/42" `
  --plugin-config 'com.example.gallery={"mode":"test"}'
```

bare host も URL も network request をせず、host overlay、plugin selection、選択 plugin
の runtime config validation まで行う。

通常表示と`--json`は、従来のplugin diagnosticに加え、次の実行時情報を必ず出力する。

- application と library の version、および root directory。application root は package parent、
  library root は`image_downloader` package directoryである。
- 実際に discovery に使った絶対`plugin_root`、plugin verification mode、profile data path群。
- `--config`の場所、選択profile、対象hostの有無、最終的に統合されたapp configuration、
  CLI runtime override（plugin config/fallbackを含む）。
- manifestを受理した各pluginの ID、kind、publisher、version、API version、source directory、
  entry/config file、enabled状態、match priority、key/tree/manifest fingerprint、trust状態、
  catalog selection priority、作者既定config、統合後のplugin config。

JSONでは前者を`application`、`library`、`plugin_root`、`configuration`、`paths`に、plugin
metadataを`loaded_plugins[]`に入れる。従来の発見・検証diagnosticは互換のため`plugins[]`に
残る。`loaded_plugins[]`は受理済みunitだけであり、受理前に失敗したunitは`plugins[]`だけに
現れる。static validationで失敗した受理済みunitは`loaded_plugins[].status="failed"`と
diagnosticの両方に現れる。

doctorは read-only で、設定・profile/data directory を作成しない。資格情報を表示しない。
`network.headers`の値、`network.proxy`、pluginの`secrets`、password/token/secretに加えて
session/refresh/csrf/signature/private等の機密名を持つ設定値は`"<redacted>"`に置き換える。
この機密名policyは任意logのmaskingと共有する。表示される設定は
この伏字を除いてdoctor起動時の有効値であり、秘密値そのものを解決・取得するものではない。
診断は plugin registry だけをcomposeし、暗号化cookieの読込、HTTP clientの生成、network requestは
しない。このため、既存cookieの復号鍵が使用できない場合もplugin/config診断結果を得られる。

warning のみなら healthy/exit 0。failed plugin が一件でもあれば unhealthy/exit 4。
configuration error は 2、authentication は 3、plugin operation error は 4、一般 failure
は 1、partial download は 5 である。
