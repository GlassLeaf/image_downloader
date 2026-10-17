<a id="configuration-reference"></a>

# 設定リファレンス

<a id="config-layers"></a>

workflowの逐次ログを制御する設定は次のとおり。

```yaml
workflow_logging:
  progress_enabled: true
```

`workflow_logging.progress_enabled`は厳密なbool、既定true。CLIの`--workflow-progress-log enabled|disabled`と
APIの`workflow_progress_log`が上書きする。falseでも周回ごとの実行予定ファイルは必須。
保存先はprofileの`logs/workflow`で成果物の`--output-dir`に依存しない。
`workflow_history`の期限・容量制御から独立し、自動削除しない。
[記録内容・注意点](logging.md#workflow-execution-files)を参照する。

この文書は YAML 設定、layer 解決、path safety、secret reference の正本である。完全なコメント付き雛形は [config-template.yaml](../../../src/image_downloader/config-template.yaml) である。`AppConfig` / `validate_config` への直接入力では unknown field を拒否する。user-managed YAML の layer 解決では obsolete/unknown static key を検証前に除外し、下記の rewrite 設定によってファイルから削除する場合がある。

<a id="config-discovery"></a>

`DEFAULT_CONFIG` は全 field にこの文書の default を持つ immutable `AppConfig` である。`ConfigurationLayer(role: str, path: Path | None, status: LayerStatus)` は一つの採用/未採用 layer、`ResolvedApplicationConfig(config, main_config_path, config_root, source, selected_profile, layers, origins)` は解決結果である。`source` は `defaults|user|explicit`、`origins` は effective dotted key から source label への read-only mapping である。

<a id="layers-origins-and-mutation"></a>

## 設定レイヤー・由来・書き換え

明示 `--config` がなければ CLI は CWD の `app.yaml` を読まない。platform user config がなければ bundled baseline と platform default root を使う。低い方から高い方への merge 順は次のとおりである。

1. `AppConfig`の既定値
2. 同梱の不変な基準設定
3. メインの`app.yaml`
4. `profiles/<profile>/app.yaml`
5. `sites/global.yaml`
6. プロファイル内のサイト共通レイヤー
7. 登録可能ドメインから完全なホスト名までのサイトレイヤー
8. 同じホストのプロファイル内サイトレイヤー
9. CLIの初期設定・アプリケーション実行時の上書き

mapping は再帰 merge、scalar/list/`null` は高い layer が置換する。IDNA、registrable domain、IPv4/IPv6 filename の正規化、検討した layer、値の origin は `config explain --json` で確認する。

| レイヤーの役割 | `profile` | `storage` | `plugins` | `security` |
| --- | --- | --- | --- | --- |
| 同梱の基準設定 / メインの`app.yaml` | 許可 | 許可 | 許可 | 許可 |
| `profiles/<name>/app.yaml` | 禁止 | 禁止 | 禁止 | 許可 |
| 全`sites/*.yaml`（共通・ホスト別、プロファイル内を含む） | 禁止 | 禁止 | 禁止 | 禁止 |

違反は layer を黙って無視せず `ConfigurationError` にする。これは bootstrap root と verification policy を site ごとに切替えて security boundary を曖昧にしないためである。

<a id="initialization-commands"></a>

## 初期化コマンド

`config init [ABSOLUTE_PATH]` は引数なしなら platform user config、引数ありなら absolute path に main config を一回だけ作る。`--config` と `--profile` は拒否され、`--data-root` と `--plugin-root` は作成する main config の bootstrap values にだけ書き込む。既存 file は上書きしない。`--yes` は受理されるが confirmation を使わない。

`config profile init NAME` の `NAME` は `[A-Za-z0-9_-]+` である。`--config` は main config の absolute path として受理し、存在しなければ main config も作成する。profile file は `<config-root>/profiles/NAME/app.yaml` に一回だけ作る。profile init では `--data-root`、`--plugin-root`、`--profile` は拒否され、`--yes` は no-op である。作成された profile layer には上表の profile-layer 制約が適用される。

<!-- claim: TAX-CONFIG-REWRITE -->
`resolve_application_config(..., rewrite_user_layers=True)` と `load_application_config(..., rewrite_user_layers=True)` は obsolete/unknown static key を user-managed YAML から atomic に削除し得る。default は `False` で read-only である。download、update listing、cookie operation、plugin mutation は initial config の作成と rewrite を試みる。initial config の作成に失敗しても root を解決済みなら主 operation の終了状態は維持し、safe warning を stderr に出す。`doctor`、`config path`、`config explain`、plugin list は file/directory を作成・書換えしない。

既存 config、overlay、data/plugin root、managed output は symlink または Windows reparse point を含められない。不安全な path を「欠落」として無視せず `ConfigurationError` または storage safety failure にする。

<a id="config-schema"></a>

<a id="schema"></a>

## 設定スキーマ

| キー | 型・既定値・制約 |
| --- | --- |
| `profile.default` | `[A-Za-z0-9_-]+` 文字列。 `default` |
| `storage.data_root`, `plugins.root` | `null` または 絶対パス文字列; `null` は OS標準のルート |
| `download.chapter_concurrency` | 厳密な整数 `>=1`; `3` |
| `download.image_concurrency_per_chapter` | 厳密な整数 `>=1`; `8` |
| `download.continue_on_image_error`, `download.allow_empty_chapter_manifest` | 厳密な真偽値; `true`, `false` |
| `output.directory_format`, `output.filename_format` | 文字列。 `%CHAPTER_NUMBER%_%CONTENT_TITLE%_%CHAPTER_TITLE%`, `%IMAGE_INDEX%.%EXT%`。`%NUM%`、`%TITLE%`、`%SUBTITLE%` は 設定エラー |
| `output.existing_file` | `overwrite|skip|rename|error`; `overwrite` |
| `output.image_format` | `ORIGINAL|JPEG|PNG|WEBP`; `ORIGINAL`。`ORIGINAL` は プラグインの変換・プロセッサーの完了後の成果物のバイト列を、本体が再エンコードせず保存する |
| `output.isolate_by_plugin` | 厳密な真偽値; `false` |
| `output.max_component_length` | `null`または 厳密な整数 `>=16`; `null`。利用者指定のPython文字数による短縮。OSの要素上限対策は別途適用 |
| `output.lock_timeout_seconds` | 有限の数値 `>=0`; `30`; 0は待機しない |
| `media.input_validation` | `content_type|decode|both`; `both` |
| `media.content_type_mismatch` | `accept|error`; `accept` |
| `media.max_image_pixels` | `null`または 厳密な整数 `>=1`; `null` |
| `logging.console.enabled` | 厳密な真偽値; `true` |
| `logging.safe_query_parameters`, `logging.safe_fragment_parameters` | 重複のない非秘密の識別子一覧; `[]` |
| `network.request_concurrency` | 厳密な整数 `>=1`; `8` |
| `network.origin_request_concurrency`, `network.registrable_domain_request_concurrency` | `null`または 厳密な整数 `1..network.request_concurrency`; `null` |
| `network.request_timeout_seconds`, `network.connect_timeout_seconds`, `network.read_timeout_seconds`, `network.write_timeout_seconds`, `network.pool_timeout_seconds` | requestは有限な`>0`で既定値`30`。他は`null`または有限な`>0`で、`null`なら`network.request_timeout_seconds`を継承 |
| `network.max_attempts` | 厳密な整数 `>=1`; `3` 初回の要求を含む |
| `network.retry_max_delay_seconds` | 有限の数値 `>=0`; `30` |
| `network.auth_refresh_attempts` | 厳密な整数 `>=0`; `1` 追加の認証更新 |
| `network.global_request_interval_seconds` | 有限の数値 `>=0`; `0` |
| `network.pool_max_connections`, `network.pool_max_idle_connections` | 厳密な整数 `>=1` / `0..network.pool_max_connections`; `8` / `8` |
| `network.max_response_bytes` | 厳密な整数 `>=1`; `67108864` |
| `network.http2`, `network.follow_redirects` | 厳密な真偽値; どちらも`true` |
| `network.proxy` | `null`または 文字列。 `null` |
| `network.headers` | 文字列のマッピング; `{"User-Agent": "image-downloader/0.0.0.1b0"}` |
| `notification.enabled` | 厳密な真偽値; `false` |
| `notification.methods` | `desktop|email`の一覧; `[desktop]` |
| `notification.notify_on` | 通知分類の一覧。既定値 `fetch_error, process_error, save_error, auth_error, config_error, plugin_error, update_error, storage_error, runtime_error` |
| `notification.routes` | 通知分類から通知方法一覧へのマッピング; `{}` |
| `notification.desktop` | マッピング。 `{}` |
| `notification.email.smtp_host`, `notification.email.from`, `notification.email.username` | 文字列。 `''` |
| `notification.email.smtp_port` | 厳密な整数 `1..65535`; `465` |
| `notification.email.use_tls` | 厳密な真偽値; `true` |
| `notification.email.to` | 文字列の一覧; `[]` |
| `notification.email.credential_service` | 文字列。 `image-downloader.smtp` |
| `security.plugin_verification` | `strict|warn|off`; `strict`; メイン・プロファイルのアプリ設定レイヤーだけで指定可能 |
| `image_processors.chain` | 重複のない逆DNS形式の画像処理プラグインID; `[]` |
| `image_processors.transport_metadata_access` | 画像処理プラグインIDから、有効な非組み込みサイトプラグインIDの空でなく重複しない一覧へのマッピング; `{}`。チェーンに含まれるプロセッサーと、列挙したサイトの組合せだけが生の画像通信メタデータを受け取る |
| `plugin_settings.<id>.enabled` | 厳密な真偽値; `true` |
| `plugin_settings.<id>.config` | マッピング。 `{}` |
| `plugin_settings.<id>.secrets` | `lower_snake_case -> UPPERCASE_REFERENCE` マッピング。 `{}` |
| `plugin_settings.<id>.download_policy.request_concurrency` | `null`または 厳密な整数 `>=1`; 選択した操作のHTTP通信上限。実効値は `network.request_concurrency` との最小値 |
| `plugin_settings.<id>.download_policy.chapter_concurrency` | `null`または 厳密な整数 `>=1`; 実効値は `download.chapter_concurrency` との最小値 |
| `plugin_settings.<id>.download_policy.image_concurrency_per_chapter` | `null`または 厳密な整数 `>=1`; 実効値は `download.image_concurrency_per_chapter` との最小値 |
| `plugin_settings.<id>.download_policy.preserve_image_start_order` | 厳密な真偽値; `true` なら 章・画像の処理開始をmanifestの順に直列化する。既定値`false` |
| `fallback.generic_html.enabled` | 厳密な真偽値; `true` |

`download.allow_empty_chapter_manifest`は章一覧0件だけを制御し、各章の画像0件は制御しない。
`false`はプラグインの`DownloadManifest.allow_empty_chapters=True`より優先して拒否する。
`true`でもプラグインが`allow_empty_chapters=False`を返せば拒否する。
この設定は互換性のため当面維持する、将来の整理対象である。今回は削除・無効化・自動移行を
行わず、整理時期と移行方法は別途決定する。[空結果の規則](plugin-hooks.md#empty-result-permissions)を参照する。

`finite number`の設定では、float変換限界を超える巨大整数も、項目名・設定層を持つ既存の
`ConfigurationError`として拒否する。bool・文字列・NaN・無限値、正数／非負数の既存条件と
既定値は維持し、実用的な数値上限や新しい設定項目は追加しない。入力値を診断へ出力しない。

`NotificationCategory` の全 literal は `fetch_error`、`process_error`、`save_error`、`auth_error`、`parse_error`、`plugin_error`、`config_error`、`storage_error`、`runtime_error`、`update_error`、`auth_cookie_store_access`、`auth_credential_store_access`、`auth_login_success`、`auth_session_refresh_success`、`download_success`、`download_partial_success` である。category が設定可能であることと core が自動発火することは同義ではない。[ログ・イベント・通知の解説](../explanation/observability.md) を参照する。

<a id="config-storage-layout"></a>

<a id="inputs-storage-layout-and-output-names"></a>

## 入力・保存先の構成・出力名

設定ファイル自体は入力であり、保存ルートではありません。`storage.data_root`と`plugins.root`は絶対パスまたは`null`だけを受理し、`null`ならOS標準の場所を使います。相対パス、既存の構成要素にシンボリックリンク・再解析ポイントを含むパス、安全に解決できないルートは拒否します。`resolve_paths(config)`は選択したプロファイルから、次のパスを導出します。

| 用途 | 保存先 | 読み書きを行う処理 |
| --- | --- | --- |
| メイン設定 | 明示的な`--config`、省略時はOS標準の利用者設定`conf/app.yaml` | 設定解決を行う全コマンド。状態変更コマンドは初回に作成できる |
| プロファイルのデータルート | `<storage.data_root or platform data root>/profiles/<profile>` | 全実行操作で解決 |
| 取得画像と章レポート | `<profile data root>/downloads` | 通常の`download`が書き込み、`--list-updated-urls`は画像出力を割り当てない |
| 暗号化Cookie jarとlock | `<profile data root>/cookie/cookies.enc`、`cookie/cookies.lock` | Cookie操作と構築済みダウンロードサービス |
| 更新スナップショットとlock | `<profile data root>/state/updates.json`、`state/updates.lock` | 更新一覧と`DownloadService.check_updates()` |
| ワークフローの完了状態 | `<profile data root>/state/workflow.json`、`state/workflow.lock` | 通常のワークフロー。オフラインの状態表示では読み取りのみ |
| ワークフロー実行履歴とlock | `<profile data root>/state/workflow-history.json`、`state/workflow-history.lock` | 通常のワークフローが自動記録、オフライン表示が読み取り、pruneが整理 |
| ログ | `<profile data root>/logs` | 構築済みダウンロードサービスとログ処理 |
| サイト・画像処理のソース単位とカタログ | `<plugins.root or platform data root/plugins>` | プラグインコマンドと実行時の発見処理。カタログはこのルート直下の`catalog.json` |

出力ディレクトリは、プロファイルのdownloadsルートに`output.directory_format`を適用したものです。章内のファイル名は`output.filename_format`で決まります。`directory_format`は`%CHAPTER_NUMBER%`（4桁の`Chapter.number`）、`%CONTENT_TITLE%`（`DownloadManifest.title`）、`%CHAPTER_TITLE%`、`%CHAPTER_SUBTITLE%`、既存の`%EXT%`展開（`jpeg`）を受け付けます。`filename_format`では、これらの作品・章トークンに加えて、`%IMAGE_INDEX%`（4桁の`ImageResource.index`）と`%EXT%`（保存画像の拡張子から先頭のドットを除いたもの）を使えます。また、ファイル名でだけ`%ORIGINAL_STEM%`、`%ORIGINAL_FILENAME%`、`%ORIGINAL_EXT%`を使えます。それぞれ、選択元ファイル名から最後の拡張子を除いた名前、選択元の完全なファイル名、最後の拡張子から先頭のドットを除いた値です。

`%NUM%`、`%TITLE%`、`%SUBTITLE%`は、どちらの形式でも受理しません。意図した階層に合う作品・章・画像の明示的なトークンへ移行してください。`directory_format`に画像専用の`%IMAGE_INDEX%`や`%ORIGINAL_*%`を指定すると設定エラーです。`%CHAPTER_NUMBER%`はファイル名でも使えるため、たとえば`%CHAPTER_NUMBER%_%IMAGE_INDEX%.%EXT%`で両方の階層を識別できます。

処理に参加するプラグインは、任意の`OutputFormatValueProvider.output_format_values()`フックで、操作内では変化しない非秘密の値を返せます。参照形式は`%PLUGIN[com.example.gallery:SERIES_ID]%`です。プラグインは`{"SERIES_ID": "..."}`だけを返し、逆DNS形式のID名前空間はコアが管理します。プラグイントークンは両方の形式で使用できます。フックは検査前に一度だけ呼び、その不変の値を全章・全画像で共有します。フック未実装、無効・未選択のプラグインID、存在しないキーはエラーにせず、トークンをそのまま残してから通常の名前の安全化を行います。そのため、誤記や削除済みキーはダウンロードを失敗させず、出力名に見える形で残ります。値は再帰的に展開せず、秘密情報を含めてはいけません。不正な`%PLUGIN[...]%`構文は設定エラーですが、その他の未知のパーセント区切りの文字列は互換性のためそのまま残します。

元のファイル名は、サイトプラグインの非秘密な`ImageResource.original_filename`、成功応答の`Content-Disposition`（`filename*`を`filename`より優先）、最終応答URLのパス、manifestの絶対URL形式のlocatorのパス、4桁の画像番号の順で選びます。URLのクエリ・フラグメントは使用しません。`filename*`はRFC 8187に従って復号します。基底名と元の拡張子は、常に同じ一つの選択名から取ります。拡張子がなければ`%ORIGINAL_EXT%`は空で、MIME、Pillow、優先度の低い別名から拡張子を補いません。保存形式が元と異なり得る場合は`%ORIGINAL_STEM%.%EXT%`を使ってください。`%ORIGINAL_FILENAME%`は元の拡張子を保つため、加工後のバイト列と一致しないことがあります。

その後、整形処理で安全な一つのパス要素に変換し、連続するアンダースコアをまとめ、末尾のアンダースコアを削除します。トークンの値をパス区切りとしては扱いません。`output.isolate_by_plugin=true`なら、画像出力ルートと整形済みの章ディレクトリの間に、安全な`<normalized-host>/<selected-plugin-id>/`を挿入します。既定のルートは`downloads`です。通常のダウンロードの`--output-dir ABSOLUTE_PATH`はこのルートだけを置き換え、プロファイルのCookie・ログ・状態のルートは変えません。競合時の`existing_file`の扱いは[実行時の挙動](runtime-behavior.md#output-allocation-and-locks)を参照してください。

次は、メイン設定とプロファイル・サイトの重ね合わせ設定について、妥当な全体構造を示す例です。各レイヤーで許可するキーだけを載せています。既定値と許可値の完全な参照は、コメント付きの[設定テンプレート](../../../src/image_downloader/config-template.yaml)を参照してください。

```yaml
# app.yaml（メインのレイヤー）
profile: {default: comics}
storage: {data_root: 'D:/image-data'}
plugins: {root: 'D:/image-plugins'}
output:
  directory_format: '%CHAPTER_NUMBER%_%CONTENT_TITLE%_%CHAPTER_TITLE%'
  filename_format: '%IMAGE_INDEX%.%EXT%'
  existing_file: skip
security: {plugin_verification: strict}

# profiles/comics/app.yaml（プロファイルのレイヤー。profile・storage・pluginsは指定不可）
network: {max_attempts: 4}

# sites/example.test.yaml（サイトのレイヤー。profile・storage・plugins・securityは指定不可）
plugin_settings:
  com.example.gallery:
    config: {page_size: 50}
    download_policy:
      image_concurrency_per_chapter: 1
```

YAMLは永続設定を指定します。CLIの実行時上書き（`--existing-file`、`--image-format`、`--directory-format`、プラグイン設定・方針の上書きJSON、フォールバックモード）は、その操作のレイヤー解決後に適用し、ファイルへ書き戻しません。`--output-dir`も`AppConfig`のフィールドではなく、ダウンロード専用の依存構築時の上書きであり、保存しません。対応する設定上書きとその由来は`config explain --json`で確認できます。このコマンドは、ダウンロード専用の`--directory-format`・`--output-dir`を受理しません。

画像の保存形式は次の優先順位で決まる。

| 優先順位 | 参照元 | 効果 |
| --- | --- | --- |
| 1 | `DownloadRequest(force_image_format=...)` / CLI `--force-image-format` | plugin 指定も上書きする。`--image-format` とは排他的。 |
| 2 | plugin の `ImageResource.save_options.format` | 画像単位の保存形式。 |
| 3 | CLI `--image-format` または YAML の `output.image_format` | plugin 指定がないときの既定。CLI はその operation 限り。 |
| 4 | bundled default `ORIGINAL` | 上位の指定がない新規設定の既定。 |

`ORIGINAL` は HTTP 応答の raw-download モードではなく、site transform と processor
chain 後の最終 artifact を core が decode/re-encode せず保存するモードである。
`media.input_validation` は維持され、processor 完了後の artifact も検証する。extension
は artifact、Content-Type、locator、`.bin` の順で決める。

<a id="image-input-validation"></a>

### 保存前の画像検査

既定の`media.input_validation: both`は、site transform後とprocessor完了後の画像を検査する。
空本文は全モードで拒否する。`content_type`／`both`では、許可された画像MIMEと形式未指定
（Content-Typeなし・`application/octet-stream`）だけを受け付ける。許可する画像MIMEは
`image/jpeg`、`image/png`、`image/webp`、`image/gif`、`image/bmp`、`image/tiff`、`image/x-icon`、
`image/vnd.microsoft.icon`、`image/avif`、`image/heic`、`image/heif`、`image/svg+xml`である。
未知の`image/*`、HTML、JSON等は拒否する。`decode`はこのMIME許可リストを適用しない。

`decode`／`both`はPillowの構造検査に加え、全フレームの画素を読み込む。各フレームに
`max_image_pixels`を適用し、切り詰めや読込み失敗は`image_decode_error`として保存を中止する。
ORIGINALでも検査するが、正常な入力bytesを再エンコードしない。複数フレームや大きな画像では
検査のCPU使用量・処理時間が増える。検査は利用環境のPillowが読める形式に限られ、
SVG・HEIC等を読めない環境では既定で拒否する。

`content_type_mismatch: accept`が既定で、正常な画像の宣言MIMEと実体の不一致は許容する。
`error`では宣言が`image/*`の場合にデコードを行って一致を確認する。形式未指定は不一致判定の対象外。
`content_type`だけを選択し、`accept`を維持した場合は非空本文の実体を検査しない。
Pillow非対応形式をORIGINALで保存する必要がある利用者は、この弱い検査を明示的に選択できるが、
本文が正常な画像であることは保証されない。

既存設定は自動移行しない。初回操作で生成された設定snapshotに`content_type`が残る場合、
アップデートだけでは`both`に変わらない。`image-downloader config explain --json`で有効値を確認し、
使用しているmain／profile／site設定の`media.input_validation`を`both`へ変更する。
これは保存前検査であり、保存済みファイルのhash検査や`verify workflow`の検査範囲は変更しない。

<a id="config-plugin-settings"></a>

<a id="plugin-settings-and-secrets"></a>

## プラグインの設定と秘密情報

<a id="config-secrets"></a>

manifest `config_file` が指す author-default YAML（固定名ではない）、persistent `plugin_settings.<id>.config`、operation override はこの順に deep merge する。author-default file の root key は `config` のみである。disabled site plugin は selection candidate ではない。chain 上の disabled processor は error でなく skip される。processor に `secrets` を置くことは 設定エラー である。

`core.generic-html` は通常の site plugin ではなく、外部 plugin が一致しないときだけ選ばれる組み込み fallback である。この ID を `plugin_settings`、`--plugin-config`、`--plugin-config-file`、`--plugin`、`--force-plugin` の対象にはできず、指定は 設定エラー になる。generic fallback が自動選択された download operation に限り、core 所有の一回限りの `--plugin-download-policy core.generic-html=<JSON>` または `--plugin-download-policy-file` は利用できる。これは persistent 設定ではなく、画像抽出や request/auth の振る舞いを変更しない。fallback の有効/無効は `fallback.generic_html.enabled` または `--fallback-generic` で制御する。

`plugin_settings.<id>.download_policy` は plugin private `config` と別の、core 所有の operation 制御である。`request_concurrency` は selected operation 内の transport/retry を、chapter/image concurrency は image job scheduler を制限する。いずれも resolved global/host setting と最小値を採用する。`preserve_image_start_order: true` は chapter と image concurrency をともに `1` にする。plugin は `config.network.request_concurrency` を読めるが、それだけでは core scheduler を変更できない。CLI の一回限りの policy override は [CLI options](cli.md#cli-options) を参照する。

`image_processors.transport_metadata_access` は、selected site plugin の画像 fetch に関する raw request/response metadata を processor へ渡すための明示 allow-list である。key は enabled な `image_processors.chain` の processor ID、value は enabled かつ non-builtin な site plugin ID の重複なし配列にする。wildcard は使えない。未登録の processor/site、chain 外 processor、builtin/disabled site は 設定エラー である。許可されない processor も transform ごとに同型の metadata を受けるが、URL query、header/cookie 値、plugin data は redacted される。これは persistent config だけで指定でき、CLI/runtime の一時 override はない。

```yaml
image_processors:
  chain:
    - com.example.watermark
  transport_metadata_access:
    com.example.watermark:
      - com.example.gallery
```

<!-- claim: TAX-CONFIG-SECRETS -->
raw credential を YAML、manifest、catalog、plugin source、log、exception に書かない。logical secret `name` の reference は、次の environment variable 名で解決する。

```text
IMAGE_DOWNLOADER_PLUGIN_<ID_HEX>_<REFERENCE_HEX>
```

`ID_HEX` と `REFERENCE_HEX` は、それぞれ元の plugin ID と reference 全体に `value.encode("utf-8", "surrogatepass").hex().upper()` を適用した値である。短縮、hash、文字置換は行わない。接頭辞に版番号を付けず、アプリの版更新だけでは変更しない恒久的な規約とする。直接 API と複数 runtime でも同じ規則を使い、Windows の大文字・小文字を区別しない環境でも名前が衝突しないよう、符号化後は大文字に統一する。

plugin ID `com.example.a-b`、reference `TOKEN` の名前は次になる。

```text
IMAGE_DOWNLOADER_PLUGIN_636F6D2E6578616D706C652E612D62_544F4B454E
```

空でない environment value をそのまま優先する。未設定または空文字なら、従来と同じ keyring service `image-downloader.plugin.<plugin-id>` の username `<REFERENCE>` を参照する。keyring が `None` を返せば `SecretNotFoundError("required plugin secret is unavailable")`、空文字なら従来どおりその値を返す。logical name が未構成、または reference が空なら取得前に `SecretNotFoundError("required plugin secret is not configured")` となる。取得は各 `get(name)` 時に行い、値を cache しない。

旧形式 `IMAGE_DOWNLOADER_PLUGIN_<NORMALIZED_ID>_<NORMALIZED_REFERENCE>` は読み出さない。現行の有効 ID は `.` が必須なので、旧名の接頭辞以降には区切りを含めて `_` が最低2個あり、新名では1個だけである。有効な現行 ID から生成される新旧名は衝突しない。旧名への fallback、値の自動コピー、環境変数の自動削除は行わない。

YAML、ID・reference の検証規則、keyring の識別子、公開 API は変更しない。keyring のみの利用者は移行不要で、プラグインの再署名も不要である。環境変数の利用者は名前を移行し、他の plugin/profile が使っていないと確認した旧名を定義元から削除する。[移行・旧名削除手順](../maintenance/migration.md#plugin-secret-environment-migration) を参照する。

<a id="config-migration"></a>

<a id="migration-compatibility"></a>

## 移行と互換性

v3 は旧 descriptor/tree を discovery しない。network の旧 static key は current model の timeout、retry、pool、response-size fields に置き換え、`config explain --json` の `origins` と `layers` で移管結果を確認する。user-managed YAML の obsolete/unknown static key は `rewrite_user_layers=True` のときだけ削除候補になり、read-only resolve は書換えない。
## Workflow実行履歴の保持設定

```yaml
workflow_history:
  max_age_days: 90
  max_size_bytes: 104857600
```

`workflow_history.max_age_days`と`workflow_history.max_size_bytes`は正の整数。既定はprofile全体で90日・100MiB。容量は`state/workflow-history.json`全体のUTF-8保存サイズに適用し、成果物、debug.log、ロック、一時ファイルを含めない。

通常workflowはCLI/APIともに複数回の実行結果を自動保存する。dry-runは記録しない。既存`state/workflow.json`はschema 1の最新候補・URL単位完了管理を維持し、履歴ファイルも別のschema 1とする。履歴は終了日時から保持期間を判定し、境界を含む期限切れを表示から除外するが、表示ではファイルを変更しない。新規記録時と明示的な`state workflow prune`で物理削除する。

通常保存では期限切れを削除し、その後は古い実行から容量整理する。ただし新しい単一結果だけで容量上限を超える場合、その結果を省略せず保存し、期間内の既存履歴も容量理由では削除しない。上限超過と整理の推奨を警告する。明示的pruneは期限切れを先に削除し、古い順に容量整理するが、期間内の最後の1件は残す。期限切れなら最後の1件も削除できる。

容量は例外を認める上限であり、厳密な最大サイズではない。最後の1件が大きすぎる場合は上限変更または履歴退避が必要。`prune --dry-run`は削除予定と整理前後の容量を表示し、本整理時には共有ロック下で再計算する。設定変更は次の読込み・保存・整理から適用する。
