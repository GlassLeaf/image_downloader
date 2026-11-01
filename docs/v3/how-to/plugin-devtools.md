# プラグイン開発支援CLI

`image-downloader-dev` はローカルプラグインAPI v3の開発用CLIです。
サイトプラグインと画像プロセッサーの生成、読込み、単体呼出し、試験実行に対応します。
`python -m image_downloader.devtools` でも同じ機能を起動できます。

```powershell
python -m pip install -e ".[dev,parser]"

# 対話ウィザード
image-downloader-dev init C:\work\my-gallery

# 非対話生成。全機能のIDは init --help を参照
image-downloader-dev init C:\work\my-gallery --non-interactive `
  --id com.example.gallery --features json,pagination,updates,cleanup

image-downloader-dev load C:\work\my-gallery --json
image-downloader-dev methods C:\work\my-gallery
image-downloader-dev call C:\work\my-gallery matches --args '["https://example.test/gallery"]'
```

## プロジェクトと生成設定

実行用の `plugin/` と、`fixtures/`、`tests/`、`plugin-dev.yaml` を分離します。
ログ、本文、保管庫、一時出力、生成履歴、差分適用前のバックアップは `.plugin-dev/` に保存します。
署名対象は `plugin/` です。

`init --answers answers.yaml --non-interactive` は以下の形式を受け付けます。
ウィザードの回答も同じ形式の `plugin-dev.yaml` に保存します。

```yaml
id: com.example.gallery
kind: site_plugin                 # または image_processor_plugin
class: GalleryPlugin
features: [json, pagination, api-key, updates, cleanup]
sample_url: https://example.test/gallery
config:
  hosts: [example.test]
  endpoint: ''                    # 空なら入力URL
  images_field: images            # ドット区切りの項目パスも可
  url_field: url
  title_field: title
  next_field: next_cursor
  cursor_parameter: cursor
  max_pages: 50
secret_references:
  api_token: API_TOKEN
secrets:
  api_token: development-token
cookies:
  - name: session
    value: development-cookie
    domain: example.test
application:
  network:
    max_attempts: 2
scenarios: []
```

設定値は作者既定値、`application.plugin_settings`、開発設定の`config`、`--config`の順に重ねます。
`--app-config`は開発設定の`application`に重ねます。
試験用のデータ・プラグインルートと通知の無効化は開発ツールが設定します。

HTML抽出とCSRFログインのCSS解析には `parser` extraが必要です。
不足しているサイト固有情報は説明付き `NotImplementedError` を生成し、
`check`で行番号とともに確認できます。
任意フックは選択時だけ生成します。必須フックには契約に合う空実装を用意します。
認証方式はAPIキー、Bearer、OAuth更新、CSRFから一つ選択します。
OAuth認可画面の操作・認可コード取得は含みません。

## メソッド単体実行

対象にはプロジェクト、プラグインディレクトリ、`manifest.json`、Pythonソースを指定できます。
単独ソースのクラス候補が複数の場合は `--class`、必要なら `--kind` で指定します。
相対importはプラグインのディレクトリを基準に解決します。
開発時は編集可能なソースを読み、通常利用のカタログへインストールしません。

`call --args JSON --kwargs JSON` または `call --input input.yaml` で引数を渡せます。
公式フックの `context`、`config`、`app_settings`、`url` には既定の構築補助があります。
必要な画像、応答、artifactなどは明示指定します。

```yaml
# create_image_request用のinput.yaml
args:
  - $type: ImageResource
    url: https://example.test/image.png
    image_id: image1
    index: 1
kwargs: {}
```

```powershell
image-downloader-dev call C:\work\my-gallery create_image_request --input input.yaml
```

`$type`と`$ref`は、引数として渡すオブジェクトの指定方法です。
型を指定して構築する場合は`$type`、すでに存在する値を参照する場合は`$ref`を使います。

| 指定 | 値が表すもの | 処理と結果の型 | 例 |
|---|---|---|---|
| `$type` | ツールに登録された型名 | フィールドから指定した型の値を構築する | `{$type: ImageResource, url: 'https://example.test/image.png'}` → `ImageResource` |
| `$ref` | 既存オブジェクトの参照名・ドット区切りのパス | 複製や型変換をせず、参照先の値をそのまま取得する。型は参照先による | `{$ref: context}` → 準備済みの`PluginExecutionContext` |

「公開DTO・Enumの名前を使う」とは、**`$type`キーの値に型名を書く**という意味です。
DTOの各フィールドは`$type`に隣接するキーで指定します。
`$type`を省略した辞書は、型注釈やフィールド名にかかわらず通常の辞書として渡されます。
Enumは `{$type: AdditionalFileHookPoint, value: after_download}` のように型名と列挙値を指定します。
この例の結果は`AdditionalFileHookPoint.AFTER_DOWNLOAD`です。
日時は `{$type: datetime, value: '2026-10-31T00:00:00+00:00'}` で指定します。
`PluginExecutionContext`、`TransformContext`、`PluginFormatContext`も登録されています。
任意のPythonクラス名やモジュールパスを指定して読み込む機能はありません。

`$ref`では、次の参照を利用できます。

| 参照名 | 参照する値・利用条件 |
|---|---|
| `plugin` | 呼出し対象として読み込んだプラグインインスタンス。`call`時に用意される |
| `context` | ツールが準備した`PluginExecutionContext`。対象メソッドの引数名が`context`または`_context`など、先頭の`_`を除くと`context`になる場合に、呼出し前に用意される |
| `last` | 同じセッション内の直近の成功した返却値。呼出し成功後に利用できる |
| `save_as`で指定した名前 | 同じセッションやシナリオ内で、その名前で保存した返却値 |

たとえば`{$ref: manifest.chapters.0.images.0}`は、`save_as: manifest`で保存した返却値から、
最初の章の最初の画像を取得します。ドットの後には属性名・辞書のキー・リストなどの添字を指定できます。
存在しない参照名はエラーになり、型名として解釈したり、新しいオブジェクトを構築したりはしません。
返却値の参照は別々に起動した`call`コマンド間では引き継がれません。

次の入力では、画像DTOを`$type`で構築し、`$ref`で既存の実行コンテキストを渡します。
先の`create_image_request`の例と同じく`call --input input.yaml`で指定できます。

```yaml
args:
  - $type: ImageResource
    url: https://example.test/image.png
    image_id: image1
    index: 1
kwargs:
  context:
    $ref: context
```

`context`は型名ではなく参照名です。引数名が`ctx`などの場合は、
`PluginExecutionContext`という型注釈だけではこの参照を新たに用意しません。
`{$type: PluginExecutionContext}`だけでは必須引数が不足します。
構築には`config`、`app_settings`、`manifest`、`catalog`、`secrets`、`requests`の指定が必要です。
`$type`が通信や秘密情報のサービスを自動で準備するわけではないため、
ツールが準備済みのコンテキストを使う場合は`{$ref: context}`を指定します。

この`context`参照は常に`PluginExecutionContext`です。
公式フックの引数を省略したときの補完は別の仕組みで、
`transform`・`transform_image`では`TransformContext`、
`output_format_values`では`PluginFormatContext`を用意します。
これらのフックには、引数を省略する補助か、対応する`$type`で構築した値を使います。

`$file`は入力ファイルからの相対パスのバイナリ、`$base64`はBase64のバイナリを読みます。
入力のPython式の評価は行いません。

公式フックは本体の返却値検証を共用します。
追加ファイルの個々の保存条件など、operation全体が必要な条件は `simulate` で検証します。

## 試験ダウンロードと記録・再生

```powershell
# 実通信でmanifestと画像要求まで確認。画像本文は取得しない
image-downloader-dev simulate C:\work\my-gallery https://example.test/gallery

# 実通信で取得・変換・追加ファイル・保存まで試験
image-downloader-dev simulate C:\work\my-gallery https://example.test/gallery --mode download --record

# 固定応答による再現。未登録要求は失敗し、実通信へ切り替わらない
image-downloader-dev simulate C:\work\my-gallery https://example.test/gallery `
  --mode replay --fixture C:\work\my-gallery\fixtures\replay.yaml
```

`preview`でも認証付与や画像URL解決用APIへの通信は行います。
`--fixture`は`preview`や`call`にも適用できます。
`--processor PATH`を繰り返すと、その順に画像プロセッサーを追加できます。
対象自体が画像プロセッサーの場合は、組込みHTML抽出と組み合わせて試験します。
ホスト不一致のサイトを明示的に試す場合は`--force-plugin`を指定します。

fixtureの形式は次のとおりです。

```yaml
requests:
  - match:
      method: POST               # 省略時GET
      url: https://example.test/api
      json: {page: 1}            # 任意の送信内容条件
      headers: {X-Mode: debug}    # 任意のヘッダー条件
    responses:                   # 同じ要求に返す順序
      - status: 401
      - status: 200
        json: {title: Book, images: []}
  - match:
      url: https://example.test/image.png
    response:
      headers: {content-type: image/png}
      body_file: image.png       # fixtureからの相対パス
```

応答には `body`、`body_file`、`body_base64`、`json`、接続失敗を表す`error`を指定できます。
要求条件には`body`や`body_sha256`も使えます。
応答列は消費され、`repeat_last: true`の場合だけ最後の応答を繰り返します。
並行要求は一致する要求ごとの応答列を使います。未消費応答はイベントに記録します。
`--record`の出力は実行ディレクトリの`recording.yaml`です。
途中で打ち切った本文は完全な応答として記録しません。
再生対象はAPI v3のHTTP経路です。プラグイン独自の通信やOSサンドボックスは対象外です。

## 状態保持セッションとシナリオ

```powershell
image-downloader-dev session C:\work\my-gallery --fixture fixtures\replay.yaml
```

```text
methods
call inspect {"args":["https://example.test/gallery"],"save_as":"manifest"}
call create_image_request {"args":[{"$ref":"manifest.chapters.0.images.0"}]}
refs
reload
quit
```

`call`間で同じインスタンスと返却値を保持します。独自・内部補助メソッドも明示指定で呼べます。
`last`は直近の成功した返却値、`plugin`は読み込んだプラグインインスタンスです。
`context`は対象メソッドの引数名に応じて用意する`PluginExecutionContext`です。
参照の指定方法と利用条件は「メソッド単体実行」の`$type`・`$ref`の説明を参照してください。
返却された認証フローなどのオブジェクトは`call flow.apply ...`の形式で呼び出せます。
`reload`はcleanup後にソースを読み直し、返却値参照を破棄します。
`simulate URL replay`は本体と同じoperation単位で新しいインスタンスを生成・cleanupします。

`plugin-dev.yaml`の`scenarios`、または`check --scenario PATH`でシナリオを実行できます。

```yaml
steps:
  - method: matches
    args: [https://example.test/gallery]
    expected: true
  - method: inspect
    args: [https://example.test/gallery]
    save_as: manifest
```

## 既存コードへの追加と署名

```powershell
image-downloader-dev extend C:\work\my-gallery --features updates,additional-files --non-interactive
image-downloader-dev extend C:\work\my-gallery --features updates,additional-files --non-interactive --apply
image-downloader-dev sign C:\work\my-gallery --key C:\work\keys\publisher.pem --create-key
```

`extend`は差分と競合を表示します。生成管理部分が手で変更されていた場合、
競合する変更は適用しません。手書きメソッドや設定は保持します。
生成済みfixture・テストも更新し、編集済みのファイルは競合として保護します。
競合部分の生成案は結果の`suggestions`に表示します。
適用時には変更検出とバックアップを行い、同じ指定による再適用で重複を作りません。
署名処理は既存の`tools/sign_local_site_plugin.py`と共用します。
署名鍵はプラグインのソースツリー外に指定します。

## 認証データ・出力・終了コード

通常は開発設定の`secrets`と`cookies`を使います。
`--profile NAME --profile-data-root PATH`を明示すると、既存保管庫の秘密値を必要時に読み、
Cookieを複製して使用します。試験中の変更は実行ディレクトリの保管庫へ保存します。
元プロファイルの資格情報・Cookieは更新しません。
開発用保管庫の鍵も実行ディレクトリに保存し、OSの鍵ストアを新規登録しません。

開発ツールのコンソール、JSON、ログ、本文には伏字を適用しません。起動時の追加確認もありません。
DTOは`repr`を使わずフィールドを展開します。
バイナリは`bodies/`に保存し、場所・サイズ・SHA-256を表示します。
`--binary base64`または`--binary hex`で全体表示もできます。
`--json`時は結果を標準出力、表示対象の診断情報を標準エラーへ出します。
`events.jsonl`、`stdout.log`、`stderr.log`、`result.json`で後から確認できます。
各操作の保存先は結果の`run_directory`に表示します。`--run-root`で親ディレクトリを変更できます。

### コンソール表示のフィルター

`load`、`methods`、`call`、`simulate`、`session`、`check`で次のオプションを共通に使えます。
表示条件は保存内容に影響しません。非表示のDEBUG、直接出力、通信本文もすべて伏字なしで保存します。
最終結果、終了コード、`run_directory`はフィルター対象外です。

| オプション | 指定内容 |
|---|---|
| `--log-view summary\|all` | 既定は`summary`。`all`は詳細を表示するプリセット |
| `--log-level LEVEL` | DEBUG / INFO / WARNING / ERROR / CRITICAL以上を表示。小文字も使用可能 |
| `--log-source SOURCE` | event / python / stdout / stderr。繰り返しで複数指定 |
| `--event PATTERN` / `--exclude-event PATTERN` | イベント名の包含／除外glob。繰り返し可能 |
| `--logger PATTERN` / `--exclude-logger PATTERN` | Pythonロガー名の包含／除外glob。繰り返し可能 |

既定の`summary`は構造化イベント・Pythonログともに**INFO以上**を表示します。
`print()`、`stdout.buffer`、`os.write()`、ネイティブ拡張、プラグインの子プロセスによる
標準出力・標準エラーは、いずれも既定では非表示です。
`all`を明示するとDEBUG以上と全出力元を表示します。
`--log-level`と`--log-source`の明示指定は、プリセットの対応する既定値を置き換えます。

同じ項目の複数指定はOR、異なる項目はANDで判定し、除外を優先します。
globは大文字小文字を区別します。`--logger`の包含指定時は一致するPythonログだけを表示し、
ロガー名を持たないイベントや直接出力は表示しません。
`--exclude-logger`だけを指定した場合は他の出力元に影響しません。
直接出力にはログレベルを推測して付けず、出力元とイベント名相当の`stdout`／`stderr`で制御します。

```powershell
# 既定: 両ログ種別ともINFO以上の要点表示
image-downloader-dev simulate . https://example.test/gallery
# WARNING以上、直接出力は非表示
image-downloader-dev simulate . https://example.test/gallery --log-level WARNING
# DEBUG・全出力元・完全なイベント内容
image-downloader-dev simulate . https://example.test/gallery --log-view all
# HTTPイベントだけを詳細表示（Pythonログ・直接出力は一致しない）
image-downloader-dev simulate . https://example.test/gallery --log-view all --event "http_*"
# 特定のPythonロガーのINFO以上だけを表示
image-downloader-dev simulate . https://example.test/gallery --logger "my_plugin.*"
# stdoutだけを表示
image-downloader-dev simulate . https://example.test/gallery --log-source stdout
```

構造化イベントには`source`と`level`を記録します。
操作開始・完了はINFO、再試行・HTTPの400以上・回復可能な通信失敗はWARNING、
メソッド・操作・cleanupの例外はERROR、通常のフック呼出し・HTTP詳細・画像単位の処理はDEBUGです。
Pythonログは独自レベルも含め元のレベルを保持し、数値で最低レベルと比較します。

`summary`の診断表示は、識別情報・メッセージ・例外の種類とメッセージを1行のJSONにまとめます。
引数、返却DTO、設定、ヘッダー、本文、traceback全体は`all`か保存ファイルで確認できます。
表示する値に伏字や文字数切り詰めは適用しません。バイナリ全体のBase64／16進表示は、
詳細表示では`--log-view all --binary base64`または`--log-view all --binary hex`を使用します。
終了時は、非表示イベント件数、非表示のstdout／stderrのバイト数、保存先を
`diagnostics_summary`として標準エラーへ表示します。この集計は表示フィルターの対象外です。

セッションの条件は起動時に決まり、`reload`でも維持します。
今回のフィルターは実行時オプション専用で、`plugin-dev.yaml`には保存しません。
不正なレベル・出力元・空パターンは、ワーカー起動前に終了コード`2`で拒否します。

既定の操作タイムアウトは120秒で、`--timeout`で変更できます。
中断時はcleanupを試み、終了しないワーカーを停止します。
終了コードは成功`0`、実行・検証失敗`1`、入力不正`2`、中断`130`です。
