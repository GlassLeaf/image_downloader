# 追加originへの認証許可

plugin.yamlのendpoints.assetsで画像originへのsession認証を宣言します。ホスト変更時はplugin_settingsのendpoints.assets.originと、ユーザー側のauth_access.sites.<site>.originsを更新してください。APIと別ポートの画像要求へ認証を付け、未許可originへのredirectは本体が送信前に拒否します。

トークンは[総合手順の保管庫への登録と参照](../README.md#保管庫への登録と参照)で、このプラグインIDの`LAB_TOKEN`として登録します。credential-galleryと同じ参照名でも別の登録です。取得には同じ試験用データルート・defaultプロファイルを使います。

[準備・署名・実行・期待結果・異常系・後片付け](../README.md)を先に確認してください。
manifestは署名なしの最小形です。利用者の鍵で署名したコピーをstrictでinstallすることもできます。
このパッケージはpublic v3 APIとPython標準ライブラリだけを使い、他のサンプルをimportしません。

- プラグインID：`local.image-downloader.lab-origin-auth-gallery`
- 対象URL：`http://127.0.0.1:<port>/samples/origin-auth-gallery/work`
- 作者設定：`origin`と`asset_origin`。prepareが実際のポートで利用者設定を生成します。
- 通常は2章・各2画像を保存します。実サイト／実アカウントは使用しません。
- 秘密情報名：`api_token`、参照名：`LAB_TOKEN`。値は架空の`lab-token`だけを使用します。

ユーザー管理のホスト変数と限定ワイルドカードの設定例は[auth-access-hosts.yaml](auth-access-hosts.yaml)を参照してください。DNSホスト用の例で、ローカル試験用IPへそのまま置き換える設定ではありません。一つのsiteが資格情報・許可・匿名セッションの共有単位となり、`*`は左端1階層だけで本体を含みません。[仕様と表示方法](../../../docs/v3/how-to/auth-access-and-ephemeral-sessions.md#ユーザー管理のホスト変数とワイルドカード)。

ユーザー設定例は形式v2です。メインの形式番号に加え、隣接する`auth.yaml`と必要な`plugin-settings/`も一式として使用してください。認証用と接続先用のホスト変数は独立しています。
