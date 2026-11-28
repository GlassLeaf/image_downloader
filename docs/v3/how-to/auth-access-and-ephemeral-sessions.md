# サイト別の認証許可と匿名セッション

資格情報の登録、プラグインへの提供、ログイン・更新の実行、セッションの保存は別の設定です。保管庫へ登録しても、それだけでプラグインへ提供したりログインを許可したりはしません。

この文書の`mode/sites/secrets`を使う例は、互換性を維持するPython APIと旧CLI上書きの形式です。
設定ファイルでは[形式v2の配置と認証設定](configuration-v2-migration.md)を使います。
`sites`は`bindings`、`secrets`は`credentials`、`mode`は`session.persistence`へ対応し、
新形式のbindingは`credential_access/login`が必須で、同名bindingは全置換します。
資格情報・許可・保存方針は共通・プロファイルだけに置き、認証用と接続先用のホスト変数は独立します。
以下のAPI形式のYAML表現を`app.yaml`としてそのまま保存することはできません。

## Python APIでのサイト設定

```yaml
plugin_settings:
  com.example.gallery:
    auth_access:
      mode: standard
      sites:
        main:
          origins: [https://gallery.example.test, https://login.example.test]
          secrets:
            username: GALLERY_USERNAME
            password: GALLERY_PASSWORD
          credential_access: allow
          login: ask
        temporary:
          origins: [https://other.example.test]
          mode: anonymous
          secrets:
            api_token: OTHER_TOKEN
          login: deny
```

`origins`は文字列または文字列配列です。大文字小文字や既定ポートを正規化し、具体的なoriginはスキーム・ホスト・ポートの完全一致で判定します。ユーザー管理のホスト変数と、HTTPSの左端1階層ワイルドカードも使用できます。site間で範囲が重なる指定、パス、userinfo、query、fragmentは拒否します。開始URLから一つのサイトを選択し、operation中は固定します。追加の認証先は作者の`endpoints`にもユーザーの`origins`にも含めます。作者の対応ホスト宣言だけでは資格情報共有を許可しません。

`secrets`はlogical nameから保管庫参照名への対応です。値は従来の`credential plugin set PLUGIN_ID REFERENCE`で登録します。同じプラグインでもサイトごとに異なる参照名を設定でき、同じサービスを複数originで利用する場合は同じサイトにまとめます。

| 項目 | 指定値 | 省略時 |
| --- | --- | --- |
| `credential_access` | `allow` / `deny` | サイトに参照設定があればallow、それ以外はdeny |
| `login` | `allow` / `ask` / `deny` | 資格情報提供が許可されていればask、それ以外はdeny |
| `mode` | `standard` / `anonymous` | サイトは上位のmodeを継承、上位はstandard |

明示設定を優先します。`credential_access: deny`でも`login: allow`を指定すれば資格情報不要の認証を実行できます。参照も提供許可もない未登録サイトでは新規ログインを行わず、通常モードでは既存Cookieのみ利用します。画像プロセッサーは`auth_access`・`host_groups`を設定できません。組込みHTMLフォールバックは`auth_access`・`host_groups`だけ設定できます。

実行時だけ変更する例:

```console
image-downloader download https://gallery.example.test/work --plugin-auth-access 'com.example.gallery={"mode":"anonymous","sites":{"main":{"login":"allow"}}}'
```

CLIの上書きはユーザー設定へ部分マージし、その実行だけに適用します。公開リクエスト型では同じ内容を`plugin_auth_access_overrides`に渡します。選択していないプラグインへの上書きは設定エラーです。

## ユーザー管理のホスト変数とワイルドカード

ユーザー設定の同じプラグインに、用途別のホスト一覧を定義できます。

```yaml
plugin_settings:
  com.example.gallery:
    host_groups:
      sso: [example.com, example.co.jp]
      public: [another.com]
    auth_access:
      sites:
        shared:
          origins:
            - "https://${host_groups.sso}"
            - "https://*.${host_groups.sso}"
          secrets:
            username: GALLERY_USERNAME
            password: GALLERY_PASSWORD
          login: ask
        public:
          origins: "https://${host_groups.public}"
          mode: anonymous
          credential_access: deny
          login: deny
```

作者のplugin.yamlでは、同じユーザー管理の一覧を参照できます。

```yaml
config: {}
endpoints:
  viewer:
    origin: "https://viewer.${host_groups.sso}"
    auth: session
```

グループ名は小文字英字から始まる英小文字・数字・アンダースコアの1〜64文字です。大文字やドットは使えません。値はDNSホスト名の配列です。大小文字・IDNA・末尾ドットを正規化し、重複を除きます。URL・ポート・IP・ワイルドカード・変数参照は値にできません。IP接続には完全なoriginを直接指定します。

参照は同じプラグインのユーザー設定だけから解決し、作者のconfig、環境変数、他プラグインから補完しません。プラグイン固有config内では展開しません。グループ名は一覧の識別子で、publicやno_loginという名前だけではログインを禁止しません。許可はcredential_access・login、保存方針はmodeで明示します。

一覧は変数の展開元であり、直接記述したoriginへの追加制限ではありません。定義だけで認証を許可したりsiteを生成したりしません。一つのsiteは一つの認証共有単位です。例のsharedでは両ドメインに同じ資格情報・許可を適用し、コマンド内の承認と匿名セッションを共有します。別アカウントや別の許可を使う場合はsiteを分けます。

認証のoriginsではhost_groupsの参照だけを許可します。endpointでは既存のconfig.*・operation.*も使えます。一つのテンプレートで配列参照は1回までです。複数グループを使う場合はorigins配列に別々のテンプレートを記述してください。再帰展開・式評価・配列同士の組合せ展開は行いません。

https://*.example.comはhttps://viewer.example.com:443に一致し、本体のexample.com、a.viewer.example.com、HTTP、別ポートには一致しません。本体は別途指定します。公共接尾辞へのワイルドカード（*.co.jp・*.github.ioなど）は拒否し、PSLが利用できない場合も検証を失敗させます。異なるsiteで範囲が重なれば設定エラーです。同じsite内の重複は許容します。

設定レイヤーはグループ名ごとにマージし、同名グループの配列を上位レイヤーで置換します。空のマッピングは下位のグループを削除しません。マージ後に展開し、未定義・参照された空配列は通信・保管庫読取り前に拒否します。未使用でも不正なホスト名は拒否します。部分的な認証設定の上書きも、必須項目とsite間の重複を最終設定で検証します。

APIのplugin_auth_access_overridesとCLIの--plugin-auth-accessでもテンプレートを使用できます。展開元は選択済みユーザー設定で、追加の認証用`host_groups`を明示すれば、接続先用の一覧から独立させられます。汎用のアプリケーション設定上書きAPIでは変更可能です。

config explainとdoctorでは原文のorigins、展開済みのresolved_origins、auth_access.variables.host_groups内の各グループの値・出典・referenced_byを確認できます。doctorは作者とユーザーの有効endpointも表示し、未使用グループを警告します。警告だけでは失敗になりません。作者宣言の読込みに失敗した場合は未使用と断定せず、usage_completeをfalseにします。config explainでは作者側の参照を把握できないため未使用警告を出しません。保管庫参照は秘匿します。

追加の`PluginAuthAccess.host_groups`が未指定の既存Python API入力だけは、従来の共有変数を使います。その場合、一覧の変更でendpointと認証許可の両方の範囲が変わります。展開結果と利用箇所を確認してください。承認と匿名セッションの識別には展開後の範囲を使うため、名前変更や並べ替えだけでは識別を変えません。ワイルドカードを持つEndpointから要求を作る場合はfor_origin()で具体的なoriginを選択します。

### 旧記法からの移行と誤記の検出

旧`allowed_hosts`と`${allowed_hosts}`は廃止しました。配列を`host_groups.default`へ移し、参照を`${host_groups.default}`へ変更してください。自動変換やユーザーファイルの自動書換えは行いません。

ユーザー設定と開発ツールが読み込むYAMLは、同じマッピング内の明示キー重複をエラーにします。アンカー、通常のマージ、別レイヤーの同名項目による上書きは利用できます。plugin_settingsの共通設定キー、auth_access、各siteの未知キーは自動削除せず拒否します。プラグイン独自configの任意キーと、新形式ではその他の未知・廃止キーもエラーになり、自動整理しません。

## ログインの承認

`ask`ではコアがプラグインID、サイト、認証先origin、資格情報提供の有無、理由を表示します。秘密値は表示しません。許可・拒否は同じコマンドの同じサイト・参照設定で共有し、workflow再試行や並列要求で繰り返し確認しません。承認を設定ファイルに保存しません。

非対話実行では未承認のaskを`login_approval_required`で停止します。無人運転ではサイトに`login: allow`または`deny`を設定してください。一般の`--yes`はログイン承認に使いません。直接API利用では`RuntimeComposer(..., login_approver=callback)`に、`LoginApprovalRequest`を受け取りboolを返すasync callbackを渡せます。プロファイルと保管庫はサービスに固定され、承認は`DownloadService.close()`で破棄します。

## 匿名モードの意味

匿名モードは空のCookie jarで開始し、そのコマンド中だけ認証状態を保持します。保存済みCookieを読みません。許可された登録済み資格情報は保管庫から取得でき、ログイン・Cookie送信・認証ヘッダー付与・リダイレクト・更新には通常と同じ通信規則を使います。

同じコマンドの同じプラグイン・サイト・参照設定でCookieを共有し、operationやworkflowの試行ごとに空へ戻しません。通常セッションとは分離し、追加・更新・削除をCookie保存へ混入させません。正常終了、失敗、キャンセル時にセッションを解放し、次のコマンドは再び空で開始します。

保存しないのはCookie・取得トークンなどの認証状態です。既存の登録済み資格情報を削除する機能ではありません。ダウンロード結果、実行履歴、ログ、更新状態は通常どおり保存します。`AuthMode.NONE`は認証フローを通さない指定であり、匿名モードやCookie禁止を意味しません。 Cookieの送信・参照・inspectのプレビューにも、開始originと作者のendpoint宣言、およびユーザーのサイト許可の共通範囲を使います。追加originへのCookie共有には両方の設定が必要です。

`inspect`・workflow計画では選択セッションの分離コピーを使用し、そのCookie変更を元へ戻しません。ログイン承認は同じコマンドの共通窓口を通します。

## プラグインの移行

旧`plugin_settings.<id>.secrets`と一般の`context.secrets`は廃止しました。資格情報は認証フック内の`auth_context.credentials.get(name)`で取得します。提供拒否なら保管庫へアクセスする前に`CredentialAccessDeniedError`となり、未登録の`SecretNotFoundError`や保管庫障害の`VaultError`と区別します。取得用のオブジェクトを保持して認証フック外で使うことも拒否します。

- `initialize(auth_context) -> AuthLease | None`: 通信なしで既存状態を準備します。既定はNone。Cookie方式では既存Cookieを先に試すためAuthLeaseを返せます。保存済みトークンもここで読み込みます。
- `login(auth_context) -> AuthLease`: コアの許可後に新規ログインを実行します。
- `refresh(auth_context) -> AuthLease`: コアの許可後に認証状態を更新します。BaseAuthFlowの既定はloginへの委譲です。
- `verify()`: 現在の認証状態を確認します。新規ログインや更新は行いません。
- `apply()`: 同期・通信なしで準備済み資格情報を付与します。

AUTO要求は既存状態を先に使い、利用可能な認証情報がなければ認証付与なしで要求します。その応答も認証失敗判定の対象です。認証失敗・期限切れ・明示要求時にログインを許可判定します。REQUIRED要求は送信前に認証を準備します。`await context.auth.request_login(reason)`は許可・成功でTrue、拒否でFalse、処理失敗で例外です。

初期化中の通信、認証フックから通常のrequests/authへの再入、認証用ポートのフック外利用は`AuthReentryError`です。拒否・承認未取得は自動再試行しません。API番号3の開発中契約を破壊的に更新するため、作者はプラグインと設定を修正し、再署名・再インストールしてください。

本制御はホストAPIを使うプラグインの契約です。同一プロセスで実行する任意のPythonコードを隔離するものではありません。

## 開発ツールでの許可

`image-downloader-dev`も作者のconfig・secret_references・fixtureからサイトや許可を補完しません。認証付きの試験では、ユーザーの`auth_access`を`--app-config`で渡すか、開発者が`plugin-dev.yaml`の`application.plugin_settings`へ明示します。許可なしでは未登録サイトの既定の拒否を維持します。認証機能の生成replayテストも、明示設定がある場合に生成します。[オフライン試験の設定例](../../../examples/plugin-v3-auth-snapshot/auth-access.yaml)を参照してください。
