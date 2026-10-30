# ローカル機能ラボ

ダウンロード以外の拡張点を、架空のHTTPサイトで試すサンプル集です。外部サービスや実アカウントは不要です。
各プラグインは独立したパッケージです。`base.py`は説明を読みやすくする共通部分のコピーで、他のサンプルやラボのimportを必要としません。
`gallery_base.py`がコピー元です。更新時は各パッケージのコピーも同じ内容にしてください。

**このラボの準備・サーバー・一括操作はブラウザを探索しません。**
実Chromeの読み取りは[隔離環境専用の手動試験](../../manual-tests/browser-cookies/README.md)へ分離しています。
CI・Codex・他のAIでその手動試験を起動せず、生成されたプロファイルや結果をAIへ渡さないでください。

## 1. 準備と署名なしでの起動

Python 3.11以降と、このリポジトリの依存パッケージを使います。リポジトリのルートからPowerShellで実行します。
以下の作業先は**架空データ用**です。通常の設定と別のルートにし、既存のラボがあれば新しい名前を選びます。

```powershell
python -m pip install -e '.[dev]'
$work = Join-Path (Get-Location).Path '.runtime/feature-lab-demo'
python examples/plugin-v3-feature-lab/lab.py serve --work-dir $work --workflow-failure
```

サーバーを動かしたまま、別のターミナルで次を実行します。

```powershell
$work = Join-Path (Get-Location).Path '.runtime/feature-lab-demo'
$env:NO_PROXY = '127.0.0.1,localhost'
python examples/plugin-v3-feature-lab/lab.py prepare --work-dir $work
$origin = (Get-Content (Join-Path $work 'endpoints.json') -Raw | ConvertFrom-Json).origin
$config = Join-Path $work 'app.yaml'
image-downloader doctor --config $config --json
```

`prepare`は作業先にsources・plugins・設定を作り、署名なしのコピーをinstallします。生成する設定は`security.plugin_verification: off`です。
普段の設定には適用しないでください。サーバーは127.0.0.1の2ポートだけで待ち受けます。再起動でポートが変わるので、新しい作業先で準備し直してください。
HTTPプロキシ環境変数が設定されている端末では、上記のNO_PROXYもCLIを実行するターミナルへ設定します。
実機試験用ランナーの確認HTTPは環境プロキシを使わない別経路です。

## 2. 利用者の鍵でstrict実行

同梱の秘密鍵はありません。署名や鍵作成は自動実行しません。作業用コピーを署名してから再installします。

```powershell
$key = 'C:\secure\feature-lab-signing.pem'
# 初回だけ --create-key を付ける。既存鍵を使う場合は省略する。
python tools/sign_local_site_plugin.py --key $key --create-key (Join-Path $work 'sources/additional-files')
$units = 'credential-gallery','cookie-gallery','origin-auth-gallery','output-gallery','configured-host-gallery','short-lived-gallery'
foreach ($unit in $units) {
    python tools/sign_local_site_plugin.py --key $key (Join-Path $work "sources/$unit")
}
python examples/plugin-v3-feature-lab/lab.py install --work-dir $work --strict
image-downloader doctor --config $config --json
```

`app.yaml`と`output.yaml`がstrictへ切り替わります。編集後は作業用コピーを再署名してinstallしてください。
リポジトリの既存署名付きパッケージは変更しません。

## 3. 推奨順の使用例

| 順 | パッケージ／操作 | 実装する内容 | 正常時の確認 |
| --- | --- | --- | --- |
| 1 | [additional-files](../plugin-v3-additional-files/README.md) | 全7段階、必須補助情報、受信／保存callback、cleanup | 画像4件と章別metadata。再実行はskipし、callbackが既存bytesを受け取る |
| 2 | [credential-gallery](credential-gallery/plugin.py) | プロファイル別保管庫からのtoken参照 | 登録後に画像4件。未設定はsecretエラー |
| 3 | [cookie-gallery](cookie-gallery/plugin.py) | 本体jarを経由するCookie認証 | 人工jarを使う自動テスト。実ブラウザは専用手順のみ |
| 4 | [origin-auth-gallery](origin-auth-gallery/plugin.py) | 追加originへの認証許可 | APIと別ポートの画像を取得。未許可originへのredirectは拒否 |
| 5 | [output-gallery](output-gallery/plugin.py) | 画像別保存オプション、元名、独自token | JPEG/PNG、`cover-*`の名前、`_lab`のtoken値 |
| 6 | [configured-host-gallery](configured-host-gallery/plugin.py) | 設定によるURL選択 | 設定のoriginだけに一致、不正設定は検証エラー |
| 7 | [short-lived-gallery](short-lived-gallery/plugin.py) | URL直前発行、410後の再発行、開始順 | 初回ticketを失効させ、再発行で取得成功 |
| 8 | workflow操作 | 更新、予定、再試行、保持、履歴、検査 | 下記の一連のコマンドで確認 |

### 追加ファイル

```powershell
image-downloader download "$origin/samples/additional-files/work" --config $config --json
image-downloader download "$origin/samples/additional-files/work" --config $config --json
```

最初はsaved、再実行はskippedです。章内の`metadata`には全段階の公開情報を保存します。
必須補助データが足りない場合は、追加処理のoptional失敗だけで終わらず、後続のinspect／画像要求で明示的に失敗させます。
失敗・キャンセル・callback例外は自動テストで人工的に再現します。

### 保管庫への登録と参照

次のコマンドは利用者が対話ターミナルで実行する操作です。自動テストではOSストアを人工実装へ置き換えます。

```powershell
python examples/plugin-v3-feature-lab/lab.py credentials-set --work-dir $work
# 非表示入力には架空の値 lab-token を入力する。
image-downloader download "$origin/samples/credential-gallery/work" --config $config --json
image-downloader download "$origin/samples/origin-auth-gallery/work" --config $config --json
python examples/plugin-v3-feature-lab/lab.py credentials-delete --work-dir $work
```

新CLIへ`--data-root <work-dir>/data`を明示し、独立した試験用保管庫のdefaultプロファイルだけを操作します。
二つのサンプルIDの`LAB_TOKEN`を登録・削除し、マスターキーは削除しません。
資格情報用環境変数の上書きは廃止しました。削除後の取得は`SecretNotFoundError`になります。

### ブラウザCookie

このページでは通常の`cookie browser-import`を実行しません。
自動テストは人工jarだけを使用し、[手動試験](../../manual-tests/browser-cookies/README.md)が専用Chromeプロファイルの読取りを担当します。
profile・Cookie DB・Local State・暗号鍵・実行結果を、この作業先やCIへコピーしないでください。

### 追加origin・出力・設定付き選択

```powershell
image-downloader download "$origin/samples/output-gallery/work" --config (Join-Path $work 'output.yaml') --json
image-downloader download "$origin/samples/output-gallery/work" --config (Join-Path $work 'output.yaml') --force-image-format PNG --existing-file overwrite --json
image-downloader doctor --config $config --host "$origin/samples/configured-host-gallery/work" --fallback-generic enabled --json
image-downloader doctor --config $config --host 'http://127.0.0.1:1/samples/configured-host-gallery/work' --fallback-generic enabled --json
```

通常出力は各章でJPEG（quality=70・progressive・EXIF維持）とPNG（compress_level=4・EXIF除去）が1枚ずつです。
force指定は画像側の形式を上書きします。元名は非秘密の`cover-<id>.png`、独自tokenは`_lab`です。
doctorのselectionで対象IDのmatchedを比較します。終了コードはホストの対応可否そのものではありません。
`configured-host-gallery`は不正な設定をmatcherでFalseにし、`validate_config`で説明可能なエラーにします。

追加originの許可対象は生成設定のasset_originだけです。`/samples/origin-auth-gallery/redirect-denied`は未許可originへ転送する異常系です。
認証許可を外した場合とredirect拒否は自動テストで確認し、転送先へ認証情報を送らないことを検査します。

### 短命URLとworkflow

```powershell
image-downloader workflow "$origin/samples/short-lived-gallery/feed" --config $config --dry-run --json
image-downloader workflow "$origin/samples/short-lived-gallery/feed" --config $config --workflow-retries 1 --workflow-retry-delay 0 --json
image-downloader state workflow history --config $config --json
image-downloader state workflow list --config $config --json
```

`--workflow-failure`付きで起動したサーバーは画像1件を一度503にし、次の周回で成功させます。
HTTP再試行はmax_attempts=1にして、workflow再試行を観察できるようにします。成功した他の画像は同一呼出し内で保持します。
同じサーバーに対して先に通常downloadを実行すると一度限りの失敗を消費するため、workflow例は新規サーバーで実行します。
画像URLの初回ticketは410にし、recover hookで再発行します。時間待ちや実時間TTLには依存しません。

`preserve_image_start_order: true`で画像開始を直列化しています。falseとの比較はsourcesを変更するのではなく
`--plugin-download-policy 'local.image-downloader.lab-short-lived-gallery={"preserve_image_start_order":false}'`で行えます。
並行時の順序そのものは保証しません。自動テストでは直列時の発行・取得順を検査します。

履歴のRUN_IDと出力ルートを指定して検証します。

```powershell
$downloads = Join-Path $work 'data/profiles/default/downloads'
image-downloader verify workflow RUN_ID --config $config --output-dir $downloads --json
image-downloader verify logs (Join-Path $work 'data/profiles/default/logs') --json
```

通知は既定で無効です。自動テストは通知先を偽物に差し替えます。
実通知を試す場合は利用者が作業設定のnotificationを有効にし、desktopまたは自分で用意したローカルSMTPを指定してください。
ネットワークやOSによって送信できない場合は、サンプルの取得失敗と区別します。

## 後片付けと検証範囲

サーバーは最初のターミナルでCtrl+Cを押して終了します。`credentials-delete`でサンプルの資格情報を削除し、
作業ディレクトリを確認して利用者が削除します。署名鍵は利用者の管理下に残し、自動削除しません。

自動テストはパッケージの署名可能性・strict install・7例の取得、全追加段階、秘密情報の優先順位、
origin境界、出力形式、選択、workflowの再試行・履歴・照合を確認します。
実Chromeの抽出・復号が成功したという保証ではありません。実機試験の結果は隔離環境内で別管理します。

## マスターキー方式への切替

資格情報とCookieは試験専用の`<work-dir>/data`に独立した保管庫を作って保存します。
OSストアにはその保管庫のマスターキーだけを登録します。旧Cookie鍵・旧プラグイン資格情報には触れません。
本番のデータルートを指定しないでください。実OSストア・ブラウザー試験は人間の隔離した手動試験だけで実行します。
資格情報の環境変数上書きと`env-names`は廃止しました。feature labの`credentials-set`・`credentials-delete`にも
`--work-dir`が必須です。補助スクリプトは新CLIへ試験用データルートを明示して呼び出します。
