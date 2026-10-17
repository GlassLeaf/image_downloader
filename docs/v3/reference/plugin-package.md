<a id="plugin-package-and-trust-reference"></a>

# プラグインパッケージと信頼検証リファレンス

<a id="plugin-package"></a>

サイト用のプラグインは`<plugin-root>/site_plugins/<unit>/`、画像プロセッサー用は`<plugin-root>/image_processor_plugins/<unit>/`に配置します。実行時に必要なソースは`manifest.json`、manifestの`config_file`が指す作者既定値のYAML、入口のモジュールと相対importする補助モジュールです。作者既定値のファイル名は固定ではありません（テンプレートの`sample_plugin.yaml`も有効です）。このYAMLのルートキーは`config`だけです。パッケージのスキーマと署名の仕様はこの文書で定義します。フックの実装は[プラグインフックリファレンス](plugin-hooks.md)を参照してください。

<a id="plugin-layout"></a>

<a id="plugin-signer-metadata"></a>

`plugin-metadata.json`は付属の署名ツールが任意で受け取る入力であり、実行時には読みません。別の方法で完全なmanifestを生成・署名する場合、このファイルは不要です。付属の`tools/sign_local_site_plugin.py`は、メタデータの`kind`に応じてサイト用と画像プロセッサー用の両方を署名できます。署名ツールが読むメタデータは、未知のフィールドもフィールドの欠落も許さない厳密なオブジェクトです。

| フィールド | 値 |
| --- | --- |
| `id` | 逆DNS形式のプラグインID |
| `publisher` | 小文字の発行者ID |
| `version` | PEP 440形式のバージョン |
| `kind` | `site_plugin`または`image_processor_plugin` |
| `capabilities` | 情報表示用の文字列リスト。実行権限は付与しません。 |
| `match_priority` | サイト選択に使う整数。画像プロセッサーではメタデータとして保持します。 |
| `entry` | `file`と`class`を持つオブジェクト |
| `config_file` | 作者既定値を格納した`.yaml`ファイルの相対パス |

<a id="plugin-manifest"></a>

<a id="manifest-and-file-tree"></a>

## manifestとファイルツリー

厳密な`manifest.json`の外側のオブジェクトには、`manifest`と`signature`の2つのキーだけを指定します。内側のmanifestは14フィールドからなる厳密なオブジェクトで、未知のフィールドもフィールドの欠落も許しません。

| 厳密なmanifestのフィールド | 検証条件・意味 |
| --- | --- |
| `schema_version` | 整数`1` |
| `id` | 逆DNS形式のプラグインID |
| `publisher` | 小文字の発行者ID |
| `version` | PEP 440形式のバージョン |
| `api_version` | 文字列`"3"` |
| `kind` | `site_plugin`または`image_processor_plugin` |
| `capabilities` | 文字列リスト。情報表示用であり、権限の付与は行いません。 |
| `match_priority` | サイト候補の選択優先順位を表す整数 |
| `entry` | 相対パスの`file`と空でないクラス名の`class`だけを持つオブジェクト |
| `config_file` | 作者既定値の`.yaml`ファイルを指す相対パス。ファイル名は固定ではありません。 |
| `public_key` | Base64形式のEd25519公開鍵 |
| `key_id` | 鍵の識別子 |
| `file_tree` | ソースファイルのパスからSHA-256へのマッピング |
| `file_tree_sha256` | 正規化したファイルツリーのダイジェスト |

`signature`は内側のmanifestに対するEd25519署名です。`config`は厳密なmanifestのフィールドではありません。作者既定値は、`config_file`が指すYAMLのルートキー`config`にだけ指定します。

<a id="relaxed-manifest-used-by-bypass-modes"></a>

### 検証を迂回するモードで使う緩和manifest

`off`（実効モードは`bypass-all`）、`bypass-all`、`bypass-signature`で読むソースには、緩和manifestを使用できます。これは厳密なスキーマではありません。外側の`signature`は省略でき、内側のmanifestで必須なのは`id`、`kind`、`entry`だけです。`match_priority`がなければ実行時に`0`を補完します。`config_file`は任意ですが、指定した場合は相対パスの`.yaml`ファイルとして検証します。`bypass-catalog`では緩和manifestを使えず、署名とツリーを検証できる完全なmanifestが必要です。緩和モードは厳密なソースの14フィールドのスキーマを変更するものではなく、本番の信頼ポリシーには使わないでください。

`file_tree`は、`manifest.json`を除く全通常ソースファイルのPOSIX相対パスを、小文字のSHA-256の16進表記へ対応させるマッピングです。リンク、Windowsの再解析ポイント、ハードリンク、通常ファイル以外は許可しません。`__pycache__/*.pyc`だけは除外します。補助モジュール、メタデータ、作者既定値のYAMLを変更したら、ツリー・ハッシュ・署名を再生成してください。

<a id="source-loading-and-relative-imports"></a>

### ソースの読込みと相対import

入口とプラグイン名前空間内の相対 import は Python ソースから読み込む。
読込み時に受理済み tree の SHA-256 と照合し、その同じ bytes をコンパイルする。
署名検証後に入口・helper を変更すると、次の import は実行前に `PluginError` となる。
遅延 import・入れ子・namespace package にもこの照合を適用する。
`bypass-signature` の content pin は、検証時に digest と照合した同じ実測 tree を使用する。
`off` / `bypass-all` はハッシュ照合を迂回し、各 import 時点のソースを読む。

すべての mode でプラグインの `__pycache__/*.pyc` を読込み・生成せず、既存キャッシュを削除しない。
プラグイン内のソースなし `.pyc` と native extension (`.pyd` / `.so`) の import は拒否する。
通常のインストール済み外部ライブラリの import は標準の仕組みを使う。
入口は `entry.source` など `.py` 以外の filename でも Python ソースを置ける。
module の元のファイル位置・package metadata、データファイルの参照、runtime ごとの class cache は維持する。
読込み済み module は現在の runtime の cache に残る。
署名・content pin 更新を伴う変更を反映する場合は、更新後の検証済み record または新しい runtime を用いる。

finder は active なプラグイン名前空間だけを扱い、import 失敗・unload・close で所有する登録を解放する。
公開 `PluginRecord` のコンストラクター・dataclass field は変更しない。
手動作成 record は宣言 tree または content pin がある場合に照合し、最小 manifest は緩和扱いになる。
署名形式と catalog schema は変わらず、変更していないプラグインの再署名は不要である。
この保証は本体が管理するプラグイン読込みに適用し、信頼済み Python コードを隔離する sandbox を提供しない。

<a id="catalog-and-verification"></a>

## カタログと検証

<a id="plugin-catalog"></a>

カタログのパスは常に`<plugin-root>/catalog.json`、ルートのスキーマバージョンは`1`です。ルート・各エントリーともに未知のフィールドは許可しません。通常のpinにはID、種別、発行者、バージョン、公開鍵、鍵ID、manifestのダイジェスト、ファイルツリーのダイジェスト、選択優先順位、失効状態を保持します。content pinは`id`、`kind`、`manifest_digest`、`content_digest`、`selection_priority`、`revoked`だけを持ちます。発行者や鍵のフィールドをnullの埋め合わせとしては持ちません。

`<plugin-root>/.catalog.lock`により、install・trust・revoke・uninstallの読込み・更新・書込みをプロセス間で直列化します。カタログは原子的に置き換えます。installの配置またはカタログの確定に失敗した場合は、ディレクトリを元に戻します。古いカタログエントリーは警告となり、他のプラグインの実行は妨げません。プラグインのルートとカタログがともに存在しない状態は、空のレジストリーとして正常です。

| モード | 動作 |
| --- | --- |
| `strict` | カタログと完全なmanifest・ツリー・署名が必要です。失敗したプラグインは診断をfailedとして記録し、読み飛ばします。 |
| `warn` | 構造検証後、カタログや検証の失敗を警告として扱い、pinなしの読込みを試みます。 |
| `off` / `bypass-all` | カタログ・署名・ツリーの検証を迂回し、最小限のmanifestを許可します。 |
| `bypass-catalog` | カタログの検証だけを迂回します。完全なソースの署名とツリーは必要です。 |
| `bypass-signature` | content pin付きのカタログと実際の内容のダイジェストを必須にし、署名検証だけを迂回します。 |

本番では`strict`を使ってください。検証を緩めるモードは一回限りの復旧・開発用であり、保存済みのポリシーは置き換えません。

<a id="plugin-signing"></a>

<a id="management-policy"></a>

## 管理ポリシー

installは一時領域へのコピー、署名・ツリー・クラスの検証、原子的な配置、カタログへの信頼登録を行います。trustはカタログエントリーを作成・更新し、revokeはエントリーを残して`revoked: true`にします。uninstallは配置先とエントリーをトランザクションとして削除します。識別にはソースディレクトリ名ではなくmanifestのIDを使います。

種別・発行者の変更と、同じバージョンでの内容・ツリーの変更は拒否します。バージョンを進めた鍵の更新、ダウングレード、失効済みIDの再信頼、選択優先順位の変更は、確認後に許可します。install・trust・revoke・uninstallには確認が必要です。非対話の自動実行では`--yes`を使ってください。署名用の秘密鍵をリポジトリ・fixture・配布パッケージに同梱したり、自動生成したりしてはいけません。明示的な署名ツールの`--create-key`だけが、指定先に鍵を生成できます。

<a id="plugin-signing-workflow"></a>

<a id="reproducible-bundled-signer-workflow"></a>

### 付属の署名ツールで再現可能な手順を実行する

付属の署名ツールは、ローカルで管理する鍵を使いやすくするための補助ツールであり、実行時の依存ではありません。`plugin-metadata.json`も、実行時に読む2つ目のmanifestではありません。入口のモジュール、補助モジュール、作者既定値のYAML、8フィールドを過不足なく持つ`plugin-metadata.json`を用意してから、次を実行します。

```powershell
# 利用者が管理する保護されたパスに、Ed25519秘密鍵を一度だけ作成する。
python tools/sign_local_site_plugin.py --key C:\secure\image-downloader-plugin.pem --create-key .\my-plugin

# ソース・既定値・補助ファイルを変更したら、file_tree・ダイジェスト・署名を再生成する。
python tools/sign_local_site_plugin.py --key C:\secure\image-downloader-plugin.pem .\my-plugin

# 生成したmanifestを検証してコピー・pin登録し、検査する。
image-downloader plugin install .\my-plugin --yes
image-downloader doctor --json
```

署名ツールは`plugin-metadata.json`だけを入力として読み、`manifest.json`を生成します。`file_tree`には`manifest.json`を除く全通常ソースファイルを含めます。署名後のmanifestは手動で編集しないでください。メタデータ、作者既定値のYAML、ソースを先に変更し、署名ツールを再実行します。`plugin install`は既定でソース・ツリー・署名・クラスを厳密に検証し、対応するpinをカタログに作成します。`image-downloader plugin trust SOURCE --yes`は、すでに配置したソースをpinで固定する場合だけに使ってください。テンプレートには手動で同じ操作をする手順と`sample_plugin.yaml`の対応関係、ソースの例には署名済みのサイト用・画像プロセッサー用プラグインがあります。
