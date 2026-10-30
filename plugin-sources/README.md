<a id="local-plugin-sources"></a>

# ローカルプラグインのソース

保存クレデンシャルの参照、追加ファイル、追加origin認証、画像ごとの保存指定、設定付き選択、短命URL、workflowをローカルで試す例は、
[機能ラボ](../examples/plugin-v3-feature-lab/README.md)にあります。実ブラウザCookieはAI・CIから分離した[手動試験](../manual-tests/browser-cookies/README.md)で扱います。

ここには、ローカルプラグインAPI v3の署名済みソース一式があります。実行時の配置先は`.runtime/config/app.yaml`で設定します。実行時のルートへファイルを手動でコピーしないでください。変更したプラグインを再署名してから`image-downloader plugin install`を使うことで、カタログのpinも原子的に更新できます。

同梱の各プラグインは、付属の署名ツールへの入力に`plugin-metadata.json`を使います。実行時には読みません。署名ツールが、生成した鍵・ファイルツリー・署名のフィールドとともに`manifest.json`へ反映します。メタデータ、入口のソース、作者既定値のYAML、補助モジュールを変更してから署名ツールを再実行し、生成済みのmanifestは直接編集しないでください。正確なスキーマ、実行時には任意であること、ファイルツリーの規則は[プラグインパッケージリファレンス](../docs/v3/reference/plugin-package.md#plugin-package)に記載しています。
これらのソースで独自に定義したPythonの関数・メソッドには、すべて型注釈があります。実装したい処理パターンに合うプラグインを、入出力の例として参照してください。同梱した第三者のコードは、この保証の対象外です。フックの意味、検証、ライフサイクル、例外の扱いは[プラグインフックリファレンス](../docs/v3/reference/plugin-hooks.md)に記載しています。
付属の署名ツールは`site_plugin`と`image_processor_plugin`の両方に対応し、各プラグインのメタデータで種別を選びます。

```powershell
python tools/sign_local_site_plugin.py `
  --key "$env:LOCALAPPDATA\image-downloader\keys\local-site-plugins-ed25519.pem" `
  plugin-sources/generic-css-selector plugin-sources/paginated-catalog

$env:PYTHONPATH = 'src'
python -m image_downloader plugin install "${PWD}/plugin-sources/generic-css-selector" `
  --config "${PWD}/.runtime/config/app.yaml" --plugin-root "${PWD}/.runtime/plugin-root" --yes
```

`local.image-downloader.generic-css-selector`は既定で有効です。永続設定の`plugin_settings`、または実行ごとの上書きでCSSセレクターを指定できます。

```powershell
image-downloader URL --plugin-config 'local.image-downloader.generic-css-selector={"selector":".viewer > img[src]"}'
```

`local.image-downloader.paginated-catalog`は、`https://catalog.example.test/collections/<slug>`というテスト用サイトに対応する例です。このURL形式では、優先順位が高いため汎用プラグインより先に選ばれます。

<a id="image-processor-reference-units"></a>

## 画像プロセッサーの実装例

`artifact-history-processor`は、実行可能な最小のプロセッサー契約の例です。`config.label`を検証し、そのラベルを`ImageArtifact.history`へ追加した新しい値を返します。`resize-processor`は`max_width`・`max_height`が正であることを検証し、Pillowでその範囲内へ縮小します。取得元URLと画像IDを維持してPNGを出力します。リポジトリ内に置くだけでは有効になりません。

```powershell
python tools/sign_local_site_plugin.py `
  --key "$env:LOCALAPPDATA\image-downloader\keys\local-processors-ed25519.pem" `
  plugin-sources/artifact-history-processor plugin-sources/resize-processor

$env:PYTHONPATH = 'src'
python -m image_downloader plugin install "${PWD}/plugin-sources/artifact-history-processor" `
  --config "${PWD}/.runtime/config/app.yaml" --plugin-root "${PWD}/.runtime/plugin-root" --yes
python -m image_downloader plugin install "${PWD}/plugin-sources/resize-processor" `
  --config "${PWD}/.runtime/config/app.yaml" --plugin-root "${PWD}/.runtime/plugin-root" --yes
```

片方または両方を、アプリケーションのYAMLで明示的に有効にします。

```yaml
image_processors:
  chain:
    - local.image-downloader.artifact-history-processor
    - local.image-downloader.resize-processor
plugin_settings:
  local.image-downloader.artifact-history-processor:
    config: {label: processed}
  local.image-downloader.resize-processor:
    config: {max_width: 1600, max_height: 1600}
```

<a id="migrated-v2-pattern-samples"></a>

## v2の処理パターンを移行したサンプル

以下の独立したv3プラグインは、v2の入口ソースのファイル名とクラス名を維持しています。テスト専用の例であり、`example.test`のホストは実在するサービスではありません。

| 分類 | プラグイン | 処理パターン |
| --- | --- | --- |
| カタログ | `chaptered-catalog` | JSONカタログ、章、更新スナップショット。更新一覧を提供する完成例。 |
| HTML | `public-gallery` | Origin・Referer付きの公開HTMLから画像を検出 |
| API | `cursor-api-gallery` | APIトークンの秘密情報とカーソルによるページ分割 |
| 認証 | `csrf-login-gallery` | ユーザー名・パスワードの秘密情報を使うCSRFフォームログイン |
| 認証 | `oauth-media-api` | OAuthのBearerトークン更新 |
| CDN | `signed-cdn-gallery` | 署名付きURLの作成と失敗時の回復 |

同じローカル署名鍵で、6つすべてを署名してインストールします。

```powershell
python tools/sign_local_site_plugin.py `
  --key "$env:LOCALAPPDATA\image-downloader\keys\local-site-plugins-ed25519.pem" `
  plugin-sources/chaptered-catalog plugin-sources/public-gallery `
  plugin-sources/cursor-api-gallery plugin-sources/csrf-login-gallery `
  plugin-sources/oauth-media-api plugin-sources/signed-cdn-gallery

$env:PYTHONPATH = 'src'
$units = 'chaptered-catalog','public-gallery','cursor-api-gallery','csrf-login-gallery','oauth-media-api','signed-cdn-gallery'
foreach ($unit in $units) {
    python -m image_downloader plugin install "${PWD}/plugin-sources/$unit" `
      --config "${PWD}/.runtime/config/app.yaml" --plugin-root "${PWD}/.runtime/plugin-root" --yes
}
```

認証情報はプラグインの外部で管理します。`plugin_settings`には資格情報そのものではなく、秘密情報への参照を設定してください。
次のYAMLを利用者設定へ追加し、続くCLIで参照名に対応する値を登録します。`EXAMPLE_*`は環境変数名ではありません。

```yaml
plugin_settings:
  local.image-downloader.csrf-login-gallery:
    secrets:
      username: EXAMPLE_GALLERY_USERNAME
      password: EXAMPLE_GALLERY_PASSWORD
  local.image-downloader.cursor-api-gallery:
    secrets:
      api_token: EXAMPLE_GALLERY_API_TOKEN
  local.image-downloader.oauth-media-api:
    secrets:
      access_token: EXAMPLE_OAUTH_ACCESS_TOKEN
      refresh_token: EXAMPLE_OAUTH_REFRESH_TOKEN
```

```powershell
$config = Join-Path (Get-Location).Path '.runtime/config/app.yaml'
$data = Join-Path (Get-Location).Path '.runtime/sample-data'
$plugins = Join-Path (Get-Location).Path '.runtime/plugin-root'
image-downloader credential plugin set local.image-downloader.csrf-login-gallery EXAMPLE_GALLERY_USERNAME --config $config --data-root $data --profile default
image-downloader credential plugin set local.image-downloader.csrf-login-gallery EXAMPLE_GALLERY_PASSWORD --config $config --data-root $data --profile default
image-downloader credential plugin set local.image-downloader.cursor-api-gallery EXAMPLE_GALLERY_API_TOKEN --config $config --data-root $data --profile default
image-downloader credential plugin set local.image-downloader.oauth-media-api EXAMPLE_OAUTH_ACCESS_TOKEN --config $config --data-root $data --profile default
image-downloader credential plugin set local.image-downloader.oauth-media-api EXAMPLE_OAUTH_REFRESH_TOKEN --config $config --data-root $data --profile default

# インストール済みの対象プラグインで実行する。example.testは架空サイトの例。
image-downloader download 'https://csrf-login.example.test/works/1' --config $config --data-root $data --plugin-root $plugins --profile default
```

秘密値は各コマンドの非表示入力で指定します。登録と実行には同じ`--config`・`--data-root`・`--profile`を指定してください。
別プロファイルでは同じプラグインID・参照名も別の登録です。OSストアには保管庫ごとのマスターキーだけを保存し、
資格情報本体はデータルートの`profiles/default/credentials/credentials.enc`へ暗号化保存します。
環境変数・旧keyring資格情報へのフォールバックはありません。管理・一覧・削除は[資格情報保管庫](../docs/v3/reference/credential-vault.md)を参照してください。

プラグインの`context.secrets.get(name)`契約と署名形式は変わりません。この保存方式の切替だけでプラグインの再署名は不要です。
OAuth例で更新したaccess tokenはAuthFlowのメモリー内だけに保持し、access token・refresh tokenを保管庫へ自動保存しません。
