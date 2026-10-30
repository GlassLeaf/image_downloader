# 署名なし・ホスト判定だけのプラグイン

`HostCheckPlugin.matches()`でURLのホストが対応対象か判定するv3サイトプラグインのひな型です。
`plugin.py`の`SUPPORTED_HOSTS`を変更して使います。HTTP(S)だけを受け付け、ホスト名は完全一致で判定します。
既定では`example.test`と`www.example.test`に対応し、それ以外のサブドメインは含めません。
判定はURL文字列だけを使い、サイトへの接続や実際のサービス対応状況の確認は行いません。

`manifest.json`は署名・鍵・ハッシュ・作者設定のない最小構成です。
既定の`strict`では読み込めないため、以下の操作ごとに`bypass-all`を明示します。
これはカタログ・署名・ツリー検証を迂回するローカル開発用の実行方法です。

リポジトリのルートからPowerShellで実行します（アプリと依存パッケージはインストール済みとします）。
専用ルートを使うことで、他の外部プラグインを読み込まずに確認できます。

```powershell
$pluginRoot = Join-Path (Get-Location).Path '.local/host-check-plugins'
image-downloader plugin install ./examples/plugin-v3-host-check --plugin-root "$pluginRoot" --plugin-verification-override bypass-all --yes
image-downloader doctor --plugin-root "$pluginRoot" --plugin-verification-override bypass-all --fallback-generic enabled --host https://example.test/gallery --json
image-downloader doctor --plugin-root "$pluginRoot" --plugin-verification-override bypass-all --fallback-generic enabled --host https://other.test/gallery --json
```

`doctor`のJSONの`execution.result.configuration.selection`で、
`id: "com.example.host-check"`の`matched`が対応ホストでは`true`、非対応ホストでは`false`になります。
非対応ホストでも判定結果を表示するため汎用フォールバックを有効にしています。その場合は組込みプラグインも候補に表示されますが、通信はしません。
終了コードはdoctorの健全性を表し、ホストの対応可否そのものではありません。
`doctor --host`は通信・ダウンロードを行わず、設定・カタログ・Cookie・画像ファイルを書き換えません。
`plugin install`は準備操作としてプラグインとカタログを専用ルートへ配置します。
ソースを変更したら同じinstallコマンドで再配置してください。

v3の必須フックはそろえていますが、`inspect()`は画像も章も含まない空の結果を返すだけです。
認証・リクエスト再試行は行わず、画像変換は受け取った値をそのまま返します。
画像リクエスト生成は未実装であり、通常は空の結果なので呼ばれません。
ホスト判定だけを確認する場合は、アプリ側で履歴等の初期化を伴うdownload/inspectではなく、上記のdoctorを使ってください。

署名する通常のひな型は[plugin-v3-template](../plugin-v3-template/README.md)、
検証モードの詳細は[プラグインパッケージリファレンス](../../docs/v3/reference/plugin-package.md#catalog-and-verification)を参照してください。
