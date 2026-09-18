# 設定と CLI

## 最小の実行

インストール後は console command と module invocation が同じ user configuration、
data root、plugin root 規則を使う。CWD の `app.yaml` は読み込まない。

```powershell
image-downloader "https://example.test/gallery"
python -m image_downloader "https://example.test/gallery"
```

通常実行、Cookie 操作、`doctor`、plugin 管理は、`--config` がなければ platformdirs の固定
user config path を読む。既定 user config が未作成なら package 同梱 `app.yaml` を設定源にする。
同梱設定または既定 user config の `storage.data_root` / `plugins.root` が未設定・相対 path の
場合、対話端末では不足した各 root を absolute path として入力する。入力後の保存確認で `Y` を
選ぶと AppData 側 user config に保存し、`N` なら入力値をその invocation だけに適用する。非対話
または `--json` では対話せず、必要な `--data-root` / `--plugin-root` を示す設定エラーで終了する。

```powershell
image-downloader config init "C:\Users\you\AppData\Local\image-downloader\conf\app.yaml" `
  --data-root "D:\Images\image-downloader" `
  --plugin-root "C:\Users\you\AppData\Local\image-downloader\plugins"
```

`--config`、`storage.data_root`、`plugins.root`、`--data-root`、`--plugin-root` は相対 path を
受け付けない。明示 `--config` の root が未設定・相対 path の場合はその file を変更せず、
不足した CLI root option を指定する。
source checkout は editable install してから使う。

## 設定 tree

package 同梱の `app.yaml` は immutable baseline であり、CLI と
`load_application_config()` の両方で必ず最初に読む。`--config` がなく既定 user config も
ない CLI 実行では、この同梱 file を main 設定としても使う。利用者設定は絶対 `--config`、
または platformdirs の固定 user config path から読む。

同梱設定を使う run で root が不足していると、対話端末は data root と plugin root を利用者に
入力させる。保存確認の `Y` は AppData 側に user config を作成し、既存 user config の root を
修復する場合は他の設定値を維持する。`N` は file を変更せず、入力値を一時 bootstrap override と
して実行する。`--data-root` と `--plugin-root` の両指定も保存しない invocation 限定 override である。
同梱 file は変更しない。通常実行の明示 `--config`、非対話、`--json` は root 対話を行わない。

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
`<storage.data_root>/profiles/<name>/` に置く。`use_platformdirs` は廃止され、OS 間の
保存先差異は利用者が絶対 `storage.data_root` を指定して解決する。

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

top-level と nested model は strict である。未知 key は設定エラーになる。主な設定を
次に示す。雛形は `image-downloader config init` で明示的に作成する。

| key | 用途・既定 |
|---|---|
| `profile.default` | profile 名。`default` |
| `storage.data_root` | 必須の絶対 data root。profile data はこの配下 |
| `plugins.root` | 必須の絶対 plugin/catalog root |
| `output.*` | name format、既存 file 方針、JPEG/PNG/WEBP、plugin 別出力分離 |
| `output.max_component_length` | path component の任意上限。既定 `null`（切り詰めなし）。指定時は hash suffix 付きで決定的に切り詰める |
| `output.lock_timeout_seconds` | 同一章ディレクトリの保存ロックを待つ秒数。既定 `30`、`0` は待機なし。負数・非有限値は無効。タイムアウトは画像単位で再試行せず操作全体を失敗させる |
| `media.*` | input MIME/decode validation と MIME mismatch 方針 |
| `media.max_image_pixels` | 入力画像と processor 出力の幅×高さの任意上限。既定 `null`（寸法検査なし） |
| `logging.*` | console と公開してよい URL parameter 名 |
| `network.*` | concurrency、timeout、retry、HTTP pool、proxy、headers |
| `network.max_response_bytes` | `Content-Length` と実受信量の双方に適用する上限。既定 64 MiB |
| `notification.*` | desktop/email notification、event category別route、credential service。既定で無効。値は起動時に型検証される |
| `continue_on_error` | image 単位の recoverable failure 後も続行するか。既定 `true` |
| `allow_empty_manifest` | chapter が一つもない manifest を許可するか。既定 `false` |
| `security.plugin_verification` | `strict`、`warn`、`off`。main/profile app layer のみ。`off` は実行時承認も必要 |
| `fallback.generic_html.enabled` | builtin generic HTML fallback。既定 `true` |
| `image_processors.chain` | 実行する processor ID の順序。重複はエラー |
| `plugin_settings` | plugin ごとの利用者設定。後述 |

`storage` と `plugins` は main app layer だけに置ける。`security` は main/profile app
layer にだけ置け、site overlay には置けない。`profile` は main app layer にだけ置ける。
`plugin_settings` は全 non-bootstrap layer に置ける。catalog path は常に
`plugins.root/catalog.json` である。

通知を使う場合は`notification.enabled: true`を指定する。既定の通知対象は`fetch_error`、
`process_error`、`save_error`、`auth_error`で、既定の配信手段はdesktopである。desktop通知には
`image-downloader[notify]`の追加インストールが必要。emailを選ぶ場合は`smtp_host`、`from`、
`to`を設定する。実際に使用するrouteの配信手段はservice構築時に検証され、未設定なら
`ConfigurationError`となる。`routes`で全対象カテゴリをemailへ切り替えた場合、desktop用の
追加依存は不要である。配信が失敗してもダウンロード結果は変わらず、失敗イベントと診断ログに残る。

診断ログの書込みや終了処理に失敗しても、保存済み画像は`SAVED`のままで、
`SAVE_SUCCESS`を`SAVE_FAILED`へ変更しない。ログ障害は秘密情報を含まない短い警告を
stderrへ出す。画像ファイルそのものの書込み失敗は従来どおり保存失敗となる。

以前の`save_error`は画像処理失敗も含んでいたが、現在は保存失敗だけを示す。画像処理失敗の通知を
継続したい設定では、`notify_on`または`routes`に`process_error`を追加する。

`plugins.root` は v3 の必須 bootstrap setting である。廃止済み v2 の
`plugins.<id>` configuration tree とは別物で、v3 は後者を読まない。

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
- `config` は mapping。plugin author default と深く統合される。
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
image-downloader config init ABSOLUTE_PATH [--data-root ABSOLUTE_PATH] [--plugin-root ABSOLUTE_PATH]
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
| `--data-root ABSOLUTE_PATH` | profile data root を一時上書き。不足した対話 root は入力対象になる |
| `--plugin-root ABSOLUTE_PATH` | external plugin/catalog root を一時上書き。不足した対話 root は入力対象になる |
| `--existing-file` | `overwrite`/`skip`/`rename`/`error` の一時 override |
| `--image-format` | `JPEG`/`PNG`/`WEBP` の一時 override |
| `--no-console-log` | console chapter log を無効化 |
| `--list-updated-urls` | download の代わりに update snapshot を比較 |
| `--json` | result / doctor / plugin command を JSON 表示。download時はconsole logを抑え、標準出力をJSON専用にする |

| `--fallback-generic` | `auto`（YAML）、`enabled`、`disabled` |
| `--allow-unverified-plugins` | `security.plugin_verification: off` をこの run だけ承認 |
| `--yes` | plugin 変更の承認に加え、非対話 `config init` の設定保存を承認 |
| `--plugin-config ID=JSON_OBJECT` | plugin private config の inline override。繰返し可 |
| `--plugin-config-file ABSOLUTE_PATH` | `{"plugin_id":"…","config":{…}}` file。絶対 regular non-link file のみ。繰返し可 |

`--list-updated-urls --json` は `{"updated_urls": [URL, ...], "removed": 件数}` を
単一のJSON objectとして出力する。JSONなしでは従来どおりURLを行ごとに標準出力へ出す。
download のJSON出力中、pluginのPython `print()` は標準エラーへ送る。
native code がOSの標準出力file descriptorへ直接書く場合は制御対象外である。

`config init` は root option が不足していれば対話入力する。保存確認の `Y`、または root option が
揃った非対話実行での `--yes` だけが destination に `app.yaml` を作成する。`N`、または `--yes`
なしの非対話実行では、作成予定の完全な YAML を標準出力し、file は作成しない。

`config profile init` は既存 user config または明示 `--config` の root が不足していれば同じ入力・
保存確認を行う。`Y` はその app.yaml の root を保存してから profile overlay を作成する。`N` でも
基底 app.yaml が既に存在すれば root を一時適用して overlay を作成する。基底 user config がない
場合の `N` は overlay を作らず、先に `config init` を実行するよう表示して終了する。

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
