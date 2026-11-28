# 設定形式v2への移行

プラグインAPI v3はそのままです。ここでのv2はユーザー設定ファイルの形式番号です。
通常実行はメイン設定の整数`config_version: 2`を要求します。番号を追加するだけでは移行できません。
未知・廃止キーや誤配置はエラーになり、`rewrite_user_layers=True`を指定しても既存ファイルを書き換えません。

## 配置と適用範囲

```text
conf/
  app.yaml
  plugin-settings/com.example.gallery.yaml
  auth.yaml
  profiles/work/
    app.yaml
    plugin-settings/com.example.gallery.yaml
    auth.yaml
  sites/global/{app.yaml,auth.yaml,plugin-settings/...}
  sites/example.com/{app.yaml,auth.yaml,plugin-settings/...}
  profiles/work/sites/global/{app.yaml,auth.yaml,plugin-settings/...}
  profiles/work/sites/example.com/{app.yaml,auth.yaml,plugin-settings/...}
```

`{...}`は配置可能なファイルをまとめた表記です。任意includeやファイル名順の上書きはありません。
省略したファイルは不要です。メイン以外に`config_version`を書きません。

| ファイル | 内容と範囲 |
| --- | --- |
| `app.yaml` | 通信、User-Agent、出力、ログ、通知等。`profile/storage/plugins`はメインのみ、`security`は共通・プロファイルのみ |
| `plugin-settings/<id>.yaml` | `enabled/config/download_policy`だけ。共通・プロファイル・サイトで使用可能 |
| `auth.yaml`の`plugins.<id>.bindings/defaults/host_groups` | 資格情報参照、認証範囲、許可、保存方針。共通・プロファイルのみ |
| `auth.yaml`の`plugins.<id>.connections` | 作者のendpoint・認証確認方針への部分上書き。サイトでも使用可能 |
| `auth.yaml`の`smtp.credential_reference` | SMTP資格情報の選択。共通・プロファイルのみ |
| `auth.yaml`の`execution.auth_refresh_attempts` | 追加の認証更新回数、厳密な整数`>=0`、既定1。サイトでも使用可能 |

サイト配下の`auth.yaml`はプラグインごとの`connections`とルートの`execution`だけを受理します。
`bindings`等を置くと、値が同じでもエラーになります。
SMTPのホスト、ポート、TLS、送信元、宛先は`app.yaml`に残ります。

低い優先度から、スキーマ既定値→同梱設定→メイン共通→プロファイル→サイト共通→プロファイル内サイト共通→
登録可能ドメインから完全ホスト名まで各ホストの共通・プロファイルを交互に適用→対応する実行時上書き、の順です。
資格情報・許可・認証共有範囲・保存方針・SMTP資格情報選択は共通→プロファイルの2層で解決します。
マッピングは再帰マージし、リスト・スカラー・許可された`null`は置換します。
入力URLのホストで選んだネットワーク設定をoperation中に維持し、通信先・CDN・リダイレクト先ごとには選び直しません。
カレントディレクトリを探索しないメイン設定の選択、ホスト・IPの正規化は従来どおりです。

## 設定例

```yaml
# conf/app.yaml
config_version: 2
profile:
  default: default
network:
  request_concurrency: 8
  request_timeout_seconds: 30
  headers:
    User-Agent: MyDownloader/1.0
notification:
  email:
    smtp_host: smtp.example.com
    smtp_port: 465
    use_tls: true
    from: sender@example.com
    to: [recipient@example.com]
```

```yaml
# conf/plugin-settings/com.example.gallery.yaml
enabled: true
config:
  language: ja
download_policy:
  request_concurrency: 2
```

```yaml
# conf/auth.yaml
smtp:
  credential_reference: MAIL_ACCOUNT
execution:
  auth_refresh_attempts: 1
plugins:
  com.example.gallery:
    defaults:
      session:
        persistence: persistent
    host_groups:
      identity: [example.com]
    bindings:
      personal:
        origins: ["https://${host_groups.identity}", "https://*.${host_groups.identity}"]
        credentials:
          username: GALLERY_USER
          password: GALLERY_PASSWORD
        credential_access: allow
        login: ask
    connections:
      host_groups:
        services: [example.com]
      authentication:
        verify_interval_seconds: 300
      endpoints:
        api:
          origin: "https://api.${host_groups.services}"
```

`origins`、`credential_access`、`login`は各bindingで必須です。`credentials`は省略時`{}`です。
参照名だけを記述し、秘密値は`credential plugin set com.example.gallery GALLERY_PASSWORD`等で
現在のデータルート・プロファイルの資格情報保管庫へ登録します。SMTPは`credential smtp set MAIL_ACCOUNT`です。
プロファイルを変えると資格情報の取得先も変わるため、必要な資格情報を各プロファイルへ登録してください。

認証用`host_groups`は同じプラグインの`bindings.origins`だけが参照します。
`connections.host_groups`はendpoint用で、認証範囲へ暗黙に流用しません。
認証範囲から一般の`config`変数は参照できません。endpointでは従来の`config`・操作URL変数を使用できます。
HTTPSワイルドカードは左端の1階層だけを表し、本体originは別指定します。
異なるbindingの重複範囲は拒否します。endpointの変更によってアプリ管理の認証範囲は広がりません。

```yaml
# conf/sites/example.com/app.yaml
network:
  request_concurrency: 3
  request_timeout_seconds: 60
  headers:
    User-Agent: GalleryDownloader/1.0
```

```yaml
# conf/sites/example.com/auth.yaml
execution:
  auth_refresh_attempts: 2
plugins:
  com.example.gallery:
    connections:
      authentication:
        verify_before_request: true
```

プロファイルで同名bindingを指定すると**全置換**します。下位の資格情報参照や許可は残りません。
`bindings.personal: null`は共通bindingを削除します。部分的に`login`だけを書くことはできません。
bindingの`session.persistence`を省略すると（`session: {}`も含む）、最終的な`defaults.session.persistence`を継承します。
既定は`persistent`です。`ephemeral`は保存済みCookieを読み込まず、空の一時セッションで開始し、終了時も保存しません。
一時セッションでも、そのoperationで許可された資格情報提供・ログインは実行できます。
bindingを削除しただけでは保存済みCookieの使用禁止になりません。利用しない場合は`ephemeral`を選んでください。
同じbinding内のoriginは認証セッションの共有単位です。originを追加する前に共有範囲を確認してください。

## 一回限りの上書き

従来の`--plugin-auth-access`とPythonの`plugin_auth_access_overrides`は、
`mode/sites/secrets`を使う互換入口として部分マージを維持します。
新しい入口ではファイル形式のbindingを使います。

```powershell
# 選択されたbinding、未登録URLなら既定方針のログイン・保存方針を変更する
image-downloader inspect https://example.com/gallery `
  --plugin-auth-access-patch 'com.example.gallery={"login":"deny","session":{"persistence":"ephemeral"}}'

# この実行で共通bindingを削除する
image-downloader inspect https://example.com/gallery `
  --plugin-auth-access-replace 'com.example.gallery={"personal":null}'
```

`--plugin-auth-access-replace-file ABSOLUTE_JSON_PATH`は`{"plugin_id":"com.example.gallery","bindings":{...}}`、
`--plugin-auth-access-patch-file`は`{"plugin_id":"com.example.gallery","patch":{...}}`を受け取ります。
JSONファイル→インラインの順に適用し、同名bindingは最後の完全な指定で置換します。
処理順は全置換→入力originでグループを選択・保存方針を継承→限定変更です。
限定変更は`credential_access/login/session.persistence`だけを受理します。
同じプラグインで旧方式と新方式を併用したり、未選択プラグインを指定するとエラーです。
workflowでは各operationの選択結果へ適用し、リダイレクトで選び直しません。ファイルへ保存しません。

Python Requestには`plugin_auth_access_replacements`と`plugin_auth_access_patches`を追加しています。
既存の`AppConfig`、`PluginAuthAccess.mode/sites`、`SiteAuthAccess.secrets`、呼び出し方は維持します。
追加の`PluginAuthAccess.host_groups`が`None`の既存API入力だけが、従来の共有ホスト変数を使います。
新形式ローダーは認証用マップを空でも明示します。作者の認証実装・宣言・署名形式は変更しません。

## 初期化と診断

`config init`と初回自動作成は、コメント、形式番号、明示指定した初期設定だけを保存します。
全既定値のスナップショットは作りません。初回自動作成でCLI上書きを保存せず、プラグイン・認証ファイルも作りません。
`config profile init work`は空のプロファイル設定を作成し、メインの既定プロファイルを変更しません。

```powershell
image-downloader config validate --config C:/conf/app.yaml --json
image-downloader config explain --config C:/conf/app.yaml --host https://example.com/gallery --json
image-downloader doctor --config C:/conf/app.yaml --host https://example.com/gallery --json
```

`validate`は全プロファイル・サイトの形式・配置・継承・静的な認証範囲を読み取り専用で検証します。
プラグインコード、通信、資格情報保管庫を利用しません。プラグイン固有`config`の検証や作者宣言との完全合成は行いません。
`explain`は利用者レイヤーの値、出典、認証グループと継承理由を表示し、作者既定値の未合成を明記します。
`doctor`は作者既定値も合成し、接続先に重なる許可bindingと合成の完全性を表示します。
動的な操作URLに依存する最終展開と実サイトでの認証成功は実行時に確認してください。
診断では秘密値・資格情報参照名、一般ヘッダーの値、proxy設定を秘匿します。

## 移行手順と復帰

最初に設定一式をバックアップし、旧実行環境も復帰用に保持します。
現在の未版付けv3設定一式だけが移行対象です。それ以前の廃止済み形式の復旧は対象外です。

```powershell
# 検査だけ。移行先のディレクトリやファイルを作成しない
image-downloader config migrate --config C:/old-conf/app.yaml --destination C:/new-conf --json

# 静的な同値検証に成功した場合だけ、新しい別ディレクトリへ出力する
image-downloader config migrate --config C:/old-conf/app.yaml --destination C:/new-conf --apply

image-downloader config validate --config C:/new-conf/app.yaml
image-downloader config explain --config C:/new-conf/app.yaml --host example.com --json
image-downloader doctor --config C:/new-conf/app.yaml --host example.com --json
```

移行器は旧仕様の既定値・読み取り規則・未知キー整理規則を隔離して保持します。
有効な明示値は既定値と一致していても保持し、無視されていた未知・廃止キーや非使用SMTP互換項目は除去対象として報告します。
廃止キーから新しい設定値を推測しません。認証の`sites/secrets/mode`は`bindings/credentials/session.persistence`へ変換し、
暗黙の提供・ログイン許可を旧仕様の実効値として明示します。endpointの動的テンプレートはそのまま保持します。

旧サイト共通レイヤーがプロファイルより後に適用される順序も含め、各プロファイルの認証方針を再構成します。
全既存プロファイル・ホストの適用パターンで、一般値、プラグイン入力、接続方針・テンプレート、展開後の認証許可を比較します。
ホストによって資格情報参照・許可・共有範囲・保存方針・SMTP資格情報選択が変わり、2層では同値に表せない場合は出力を拒否します。
権限の自動統合やプロファイルの自動追加はしません。利用者が目的別プロファイル等へ手動で整理してください。

移行先は未作成で、移行元と包含関係のない別ディレクトリに限定します。
既存先への上書き、リンク・Windows再解析ポイント、不安全なパスを拒否します。
出力中の`.migration-incomplete`が残る設定は通常ローダーが拒否します。
出力・検証の失敗や中断、移行元の変更が起きた場合は完了扱いにせず、別の未作成先でやり直します。
既存先への自動再開はありません。コメント・アンカー等の原文表現の完全保存は保証しません。

設定、保管庫、Cookie、プラグイン配置の移行元は変更しません。資格情報やCookieを取得・移送しません。
各プロファイルの診断と必要な実サイトでの確認後、起動スクリプト・タスクの`--config`を切り替えます。
OS標準位置は自動置換しません。復帰時は旧実行環境と旧設定への参照を戻します。

## 直接指定した認証情報の扱い

一般ヘッダーやproxy URLへの資格情報直接指定に新たな禁止は追加していません。
これらは`credential_access/login`で管理する保管庫・認証フックの許可とは独立し、`deny`でも送信される可能性があります。
直接指定したヘッダーは設定の複製・バックアップにも含まれ、移行時も値を変更せず保持します。
秘密値は保管庫を使い、直接指定が必要な場合はファイルのアクセス権・共有・バックアップ先を管理してください。
SMTPの接続先を変更すると選択済み資格情報の送信先も変わります。TLS、接続先、許可する運用範囲を確認してください。
資格情報保管庫、Cookieの暗号化形式、プロファイルごとのデータ配置は従来どおりです。
