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

読み込み対象の既存 config、overlay、使用する data/plugin root、managed output は symlink または Windows reparse point を含められない。不安全な path を「欠落」として無視せず `ConfigurationError` または storage safety failure にする。

設定解決は`plugins.root`の型・絶対パスを検証するが、プラグインを使用しない操作ではその配置を検査しない。問い合わせ指定のないstate操作、verify workflow、Cookie、credential、vaultは、プラグインが未配置・検査不能でも保存済み情報を処理できる。`config explain`は設定値と由来を表示し、配置・プラグイン検証の診断は`doctor`で行う。明示的な`--plugin-root`には引き続き安全な絶対ディレクトリパスを要求する。

`state workflow targets/set/reset`で`--query-targets-from-workflow-url WORKFLOW_INPUT_URL`を指定すると、`--plugin-root`と`--plugin-verification-override`を受理し、通常workflowと同じ優先順位でその実行の探索・検証に適用する。サイト設定は問い合わせに使うworkflow入力URLのホストで解決する。設定ファイルは書き換えない。`--filter-workflow-url`による保存済み登録の絞り込みを含む、問い合わせ指定のないstate操作・verifyでは両プラグインオプションを拒否する。

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
| `output.image_format` | `null|ORIGINAL|JPEG|PNG|WEBP`; `null`。操作単位の形式指定を優先し、`null` は最終画像の形式を維持 |
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
| `notification.email.credential_service` | 非使用の互換項目。文字列、既定`image-downloader.smtp` |
| `notification.email.credential_reference` | SMTP保管庫の参照名。大文字英数字・アンダースコア、既定`null`。旧`username`も非使用 |
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
| 暗号化Cookie jarとlock | `<profile data root>/cookie/cookies.v2.enc`、`cookie/cookies.v2.lock` | Cookie操作と構築済みダウンロードサービス |
| 更新スナップショットとlock | `<profile data root>/state/updates.json`、`state/updates.lock` | 更新一覧と`DownloadService.check_updates()` |
| ワークフローの対象状態 | `<profile data root>/state/workflow.json`、`state/workflow.lock` | 通常workflowと`state workflow set/reset`が保存。状態表示とdry-runは読み取りのみ |
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

YAMLは永続設定を指定します。CLIの実行時上書き（`--existing-file`、`--directory-format`、プラグイン設定・方針の上書きJSON、フォールバックモード）は、その操作のレイヤー解決後に適用し、ファイルへ書き戻しません。`--output-dir`も`AppConfig`のフィールドではなく、ダウンロード専用の依存構築時の上書きであり、保存しません。対応する設定上書きとその由来は`config explain --json`で確認できます。このコマンドは、ダウンロード専用の`--directory-format`・`--output-dir`・`--image-format`・`--image-quality`を受理しません。

<a id="image-save-format"></a>

### 変換後の画像と保存形式

保存形式は、操作の`image_format` / CLI `--image-format`、設定の`output.image_format`、最終`ImageArtifact`の実データ形式の順に決める。`output.image_format`は`null`（既定）・`ORIGINAL`・`JPEG`・`PNG`・`WEBP`を受理する。通常の設定レイヤーで解決し、上位レイヤーの`null`は下位の指定を解除する。操作引数の`None`は設定を参照し、操作単位の指定を設定ファイルへ保存しない。`--force-image-format`と旧API引数は受け付けない。API番号・マニフェストのバージョン値は変更しない。

設定の`null`は形式を指定せず、プラグインの補助設定による同じ形式への再エンコードを許容する。設定の`ORIGINAL`はCLIでの明示指定と同じく最終バイト列を維持し、エンコード設定を適用しない。操作引数が未指定で設定が`ORIGINAL`の場合も、品質指定との併用は`output.image_format`を示す設定エラーとして、通信・プラグイン呼び出し前に拒否する。CLIや操作引数で`JPEG`等へ上書きすれば品質指定を利用できる。検査・更新一覧では保存形式の解決や保存条件フックを実行しない。

画像処理は、取得→サイト変換→入力検証→全プロセッサー→最終検証→保存条件フック→必要な場合だけ一度エンコード→保存の順に行う。プラグイン自身の変換・エンコードはこの回数に含まない。形式を変えるプラグインは`data`・`content_type`・`extension`を一致させる。MIME不一致の扱いと入力検証設定は維持する。

サイトと各有効プロセッサーの任意同期フック`image_save_options(artifact, context)`は、同じ最終Artifactを受け取り、`ImageSaveOptions`または`None`を返す。コンテキストは各プラグイン自身の設定と既存のtransport metadataアクセス制約を使う。

| 条件 | 集約方法 |
| --- | --- |
| `min_width`・`min_height` | 各項目の最大値。正の整数。EXIF Orientationを考慮した表示寸法の下限 |
| `quality` | 0〜100の最大値。非可逆再エンコード時の設定値の下限で、入力画像の実画質を保証しない |
| `lossless` | 一つでもTrueなら追加劣化なしを要求。Falseは他の要求を解除しない |
| `optimize`・`progressive`・`compress_level`・`exif` | サイト→プロセッサーの順に、項目ごとの最後の明示指定を採用 |

全項目の未指定値は`None`。空のオプションやフックの`None`は前段の条件を解除しない。例えば1920×1080・品質90と800×600・ロスレスは、1920×1080以上・品質90以上・追加劣化なしへ集約する。

寸法条件だけで拡大・縮小せず、品質下限だけでも再エンコードしない。JPEGを含めバイト列維持は追加劣化なしとみなす。既にプラグインが行った縮小・劣化を巻き戻さない。補助設定のためだけにロスレス要求を破ることも避ける。

`--image-quality 80` / `image_quality=80`はJPEG・WEBPでの非可逆再エンコード要求であり、プラグインの品質90やロスレス要求より優先する。品質非対応形式では形式を維持して非適用を記録する。`--image-format ORIGINAL`と品質指定の併用は入力エラー。JPEGへの変換でロスレス要求を満たせない場合、CLI品質未指定なら品質100を使う。

寸法不足・判定不能・CLIによる品質低下・適用できない補助設定は通常ログと章ログの`image_save_policy_unmet`へ記録する。`field`、`requested`、`actual`、`reason`と安全な画像識別情報を含め、保存は成功扱いとし、不足だけでは再試行しない。不正なフック戻り値は`PluginError`、デコード・エンコード失敗は通常の処理エラーとする。

`ORIGINAL` は全変換後のバイト列を維持する指定で、HTTP応答へ戻すものではない。明示指定時はエンコード設定を適用しないが、画像検証は行う。

`ORIGINAL` の保存拡張子は、最終artifactの次の情報から順に決める。

1. 有効な `artifact.extension`（前後空白を除去し、ドット省略可の1～16文字の英数字を小文字化）。
2. `artifact.content_type` に対応する既知の画像拡張子。
3. `artifact.source_url` のパス末尾にある有効な拡張子。
4. いずれも得られなければ `.bin`。

この拡張子選択では、`artifact.data` のデコード結果を使わない。`source_url` は通常manifestの
locatorであり、リダイレクト後の応答URLではない。例えば実体PNG・宣言`image/jpeg`・
`artifact.extension`未指定のまま`ORIGINAL`にすると、既定の`accept`ではPNG bytesが
`.jpeg`名で保存され得る。プラグインが形式を変えた場合は `data` に合わせて
`content_type` と `extension` も更新する。明示した `artifact.extension` が実体と異なる場合も、
コアは `ORIGINAL` の拡張子を自動補正しない。

形式指定も宣言MIMEもなく、データが画像としてデコードできない場合は、既定の`both`で
`ImageDecodeError`になり、保存しない。形式不明のデータを自動的にJPEGへ変換する処理はない。
設定も操作引数も形式未指定なら、必要なエンコード設定がない限り最終バイト列を維持する。
`media.input_validation: content_type` と既定の `content_type_mismatch: accept` を明示的に使う場合は、
非空本文と空のMIMEでも検証を通る。有効な保存形式が`ORIGINAL`ならそのbytesを保存し、
拡張子の手掛かりもなければ `.bin` になる。空本文はこの場合も拒否する。

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

`media.max_image_pixels`は既定・明示的な`null`ともに無制限である。巨大画像を扱う必要がない場合は、
例えば次のように各フレームを5,000万画素までに制限できる。値は必要な画像サイズと利用可能なメモリに合わせて選ぶ。

```yaml
media:
  max_image_pixels: 50000000
```

圧縮されたHTTP応答のサイズ制限は、展開後の画素メモリを制限しない。Pillowを別プロセスで実行しても、
OSによるメモリ・CPU・処理時間の強制上限は設けていない。この値も全フレームの合計画素数や、
加工プラグインが独自に確保するメモリの上限ではない。既存設定の`null`を自動変更しない。

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

manifest `config_file` が指す author-default YAML（固定名ではない）、persistent `plugin_settings.<id>.config`、operation override はこの順に deep merge する。author-default file は必須の `config` と、任意の `authentication`・`endpoints` をルートに持つ。プラグイン固有の検証に渡すのは `config` だけで、接続先・認証方針はコアが扱う。[接続先と認証方針](#config-plugin-connections)を参照する。disabled site plugin は selection candidate ではない。chain 上の disabled processor は error でなく skip される。processor に `secrets` を置くことは 設定エラー である。

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
raw credentialをYAML、manifest、catalog、plugin source、log、exceptionへ書かない。
`plugin_settings.<id>.secrets`にはlogical nameから参照名（大文字英数字・アンダースコア）への対応だけを書く。
`context.secrets.get(name)`は現在のデータルート・プロファイルの暗号化保管庫から毎回取得する。
環境変数と旧keyring項目へのfallbackはない。未構成なら`SecretNotFoundError("required plugin secret is not configured")`、
未登録なら`SecretNotFoundError("required plugin secret is unavailable")`となる。

`credential plugin set <plugin-id> <REFERENCE>`でプロファイルごとに登録する。SMTPは
`credential smtp set <REFERENCE>`でユーザー名・パスワードを一組として登録し、
`notification.email.credential_reference`で選択する。旧`username`・`credential_service`は
非使用の互換項目として設定に残す。旧ユーザー名だけを指定した状態では再設定エラーになる。

SMTP認証を使う場合は`notification.email.use_tls: true`が必須である。
ポート465は暗黙TLS、それ以外はSTARTTLSを使い、TLSの確立に失敗した場合は認証しない。
`credential_reference`と`use_tls: false`の組合せは、資格情報の取得・SMTP接続前に
`ConfigurationError`となる。通知無効・メール経路未使用の設定は事前検証の対象外であり、
認証情報を指定しないローカルSMTPでは`use_tls: false`を引き続き使用できる。

OSストアにはマスターキーだけを保存し、資格情報とCookieの暗号文はデータルートへ保存する。
`--data-root`の変更は資格情報の取得先も変更する。旧データの移行・削除は行わない。
保管形式、上限、管理CLI、backup/restore、鍵更新、直接構築APIの変更は
[資格情報保管庫](credential-vault.md)を参照する。プラグインの再署名は不要である。

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

通常workflowはCLI/APIともに複数回の実行結果を自動保存する。dry-runは記録しない。`state/workflow.json`はschema 1でURL単位の`status`を保存し、旧`completed`形式は自動移行しない。状態管理コマンドによる変更は実行履歴へ追記しない。履歴ファイルは独立したschema 1とする。履歴は終了日時から保持期間を判定し、境界を含む期限切れを表示から除外するが、表示ではファイルを変更しない。新規記録時と明示的な`state workflow prune`で物理削除する。

通常保存では期限切れを削除し、その後は古い実行から容量整理する。ただし新しい単一結果だけで容量上限を超える場合、その結果を省略せず保存し、期間内の既存履歴も容量理由では削除しない。上限超過と整理の推奨を警告する。明示的pruneは期限切れを先に削除し、古い順に容量整理するが、期間内の最後の1件は残す。期限切れなら最後の1件も削除できる。

容量は例外を認める上限であり、厳密な最大サイズではない。最後の1件が大きすぎる場合は上限変更または履歴退避が必要。`prune --dry-run`は削除予定と整理前後の容量を表示し、本整理時には共有ロック下で再計算する。設定変更は次の読込み・保存・整理から適用する。

## 画像変換のワーカー数

`media.transform_workers` は厳密な整数1〜4、既定2。サービス所有の専用スレッド数で、サイトとprocessorの変換が共有する。通信並列数やコア画像検証プロセス数を変更しない。画素数制限の既定値Noneは維持する。[移行と終了待ち](../maintenance/synchronous-image-transforms.md)を参照する。

<a id="config-plugin-connections"></a>

## 接続先と認証方針

`authentication` は選択したサイトプラグインの認証確認・期限更新の共通方針、`endpoints` は接続先originごとの認証利用と確認方針を宣言する。ログイン方法、確認用URL、Bearer tokenの付与方法は設定から自動生成せず、プラグインの `AuthFlow` が実装する。ここでいう確認は `AuthFlow.verify()` の呼出しであり、HTTPの疎通確認や画像検査ではない。

作者はmanifestの `config_file` が指すYAMLに `config` と並べて記述する。利用者はmain/profile/site設定の `plugin_settings.<id>.authentication` と `plugin_settings.<id>.endpoints` で部分的に上書きする。作者設定に利用者設定をdeep mergeしてから、接続先と方針の整合性を検証する。省略項目は作者設定を維持する。`--plugin-config` / `--plugin-config-file` とプラグインの `validate_config()` は固有の `config` だけを扱い、この共通設定の操作には使わない。

<a id="config-authentication-defaults"></a>

### 省略時の動作

`authentication` と `endpoints` はそれぞれ省略できる。作者・利用者の両方が省略した場合の値は次のとおり。作者YAML自体は `config: {}` だけでよい。

```yaml
authentication:
  verify_on_start: false
  verify_before_request: false
  verify_interval_seconds: null
  refresh_skew_seconds: 0
endpoints: {}
```

`authentication` の省略や `verify_***: false` は認証全体の無効化ではない。フローがあれば最初の認証対象要求前に `initialize()` を実行し、同期 `apply()` で資格情報を付与する。期限切れ・明示的な無効化・応答判定による更新も行う。既定で省略するのは、方針による追加の `verify()` 呼出しである。フローを返さないサイトの `AUTO` 要求では、認証処理を実行しない。

operation開始URLのoriginに宣言がなければ、フローありでは `auth: session`、フローなしでは `auth: none` の既定接続先を補う。`context.endpoints["operation"]` も自動的に用意する。`operation` を明示宣言する場合は、そのoperationのoriginと一致しなければならない。単一originで完結する認証は `endpoints` なしで実装できるが、追加originの扱いは下記の認証モードによる。

<a id="config-authentication-policy"></a>

### 認証確認と期限更新の値

| `authentication` のキー | 値・既定値 | 呼出しのタイミング・役割 |
| --- | --- | --- |
| `verify_on_start` | 真偽値。既定 `false` | operationで初めて認証を準備する際、`initialize()` に加えて `verify()` を実行する。アプリ起動時・各ホストへの初回アクセス時という意味ではない。 |
| `verify_before_request` | 真偽値。既定 `false` | 認証対象要求の認証準備時に `verify()` を要求する。間隔設定より優先する。 |
| `verify_interval_seconds` | 正の有限数、または `null`。既定 `null` | 最後に `verify()` がVALIDを返してから指定秒数以上経過していれば、次の認証対象要求前に確認する。未確認なら初回要求前に確認する。`null` は間隔による確認を無効にする。 |
| `refresh_skew_seconds` | 0以上の有限数。既定 `0` | `AuthLease` の期限の何秒前から更新対象にするか。残存600秒・skew 30秒なら、約570秒後以降の認証準備時に更新する。 |

真偽値はYAMLの `true` / `false` を使う。秒数は数値の整数・小数を受け付け、文字列、真偽値、NaN、無限大は拒否する。間隔の0・負数、skewの負数も設定エラーである。無効化は間隔に `null`、skewに `0` を指定する。

間隔確認・期限更新は要求時に必要性を判定し、バックグラウンドの定期通信やタイマー更新は行わない。`AuthLease(expires_in_seconds=None)` のように期限不明なら、skewによる期限前更新は行わない。`initialize()` / `refresh()` の成功だけでは追加確認済み時刻を記録せず、更新後は未確認になる。期限・経過時間はコアの単調時計で管理する。

正常応答・期限切れなしの認証対象3要求を順番に送る場合、既定ではinitialize 1回・verify 0回・apply 3回、開始時確認だけならverify 1回、毎要求確認ならverify 3回となる。開始時確認と毎要求確認を併用しても初回に同じ確認を二重実行しない。同時要求は実行中の確認を共有し得るが、毎要求確認で完了済みの結果を後続要求へ使い回さない。

現行の「毎要求」はoperationの要求経路で認証準備する単位である。認証再試行・画像URL回復後の再送では再び準備するが、低層transport内部の通信再試行やリダイレクト各hopで `verify()` を呼び直すことはない。`inspect` の実効要求プレビューも認証準備を通るため、画像を送信しなくても確認通信が起こり得る。

`verify()` はプラグインが実装する。VALIDなら続行、INVALIDなら更新、不明なら `AuthStateUnknownError` で停止する。`BaseAuthFlow.verify()` の既定値はUNKNOWNなので、追加確認を有効にするサイトでは確認手順を実装する。`await context.auth.ensure_ready(check=True)` と応答分類UNKNOWNによる確認は、間隔内であっても明示的な確認を要求する。各フックとエラーの契約は[認証フロー](plugin-hooks.md#plugin-authentication)を参照する。

<a id="config-endpoints"></a>

### 接続先の値と上書き規則

| `endpoints.<name>` のキー | 値・省略時の動作 |
| --- | --- |
| `origin` | 必須のHTTP(S) origin、または空でない文字列配列。候補はOR条件。変数参照とホスト左端の `*.` を利用できる。パスは空または `/` だけで、ユーザー情報・query・fragmentは不可。 |
| `auth` | 必須の `session` / `none`。`session` はoperationの認証フローを使い、`none` は通常要求で認証フローを通さない。 |
| `verification.before_request` | 真偽値。省略時は `authentication.verify_before_request` を継承する。 |
| `verification.interval_seconds` | 正の有限数、または `null`。省略時は `authentication.verify_interval_seconds` を継承する。明示的な `null` はそのoriginの間隔確認を無効にする。 |

`authentication.verify_before_request` と `endpoints.<name>.verification.before_request` は、同じ `AuthFlow.verify()` の呼出しを制御する。前者は共通の既定値、後者はそのoriginだけの上書きである。接続先では `verify_on_start` と `refresh_skew_seconds` を上書きできない。

上書きはキーごとに行う。`before_request: false` だけでは継承した間隔確認を止めない。両方を無効にする場合は次を指定する。開始時・明示的な確認・応答UNKNOWNによる確認や、必要な更新は引き続き行う。

```yaml
verification:
  before_request: false
  interval_seconds: null
```

認証セッション・資格情報の世代・最後の確認時刻はoperation内で一つである。接続先ごとに別のセッションや確認時計を持たず、あるoriginへの要求前に成功した確認は、別originの間隔判定にも使う。接続先の宣言は、確認用のURLや確認処理そのものを指定するものではない。

originはscheme・正規化したhost・portで比較し、`https://example.test` と `https://example.test:443` は一致する。HTTPとHTTPS、異なるportは別originである。名前やURLパスでは方針を分けない。同一の完全一致origin、または同一ワイルドカードの別名は実効的な `auth`・確認方針が一致する必要があり、矛盾は設定エラーになる。完全一致指定はワイルドカードより優先し、起点の既定方針は明示ルールが一致しない場合だけ補う。リダイレクト先を自動追加しない。

<a id="config-endpoint-patterns"></a>

### 複数origin・変数・ワイルドカード

起点ごとにCDNが変わる場合、次の宣言なら対応ホストを追加してもendpointを増やす必要がない。`supported_hosts` 自体の意味やサイト選択はプラグインが実装する。

```yaml
config:
  supported_hosts: [host1.com, host2.com]
endpoints:
  images:
    origin: "https://cdn.${operation.site_domain}"
    auth: session
```

`https://host1.com/page` または `https://www.host1.com/page` が起点なら `https://cdn.host1.com:443` に解決する。起点変数はoperation開始URLから一度だけ解決し、リダイレクト後も変えない。

| 変数 | 値 |
| --- | --- |
| `${operation.origin}` | 起点のscheme・正規化host・port。既定portも含む。 |
| `${operation.host}` | 起点hostそのもの。www等を維持し、IPv6には角括弧を付ける。 |
| `${operation.site_domain}` | PRIVATE領域を含むPublic Suffix Listによる登録可能ドメイン。 |
| `${operation.scheme}` | http / https。 |
| `${operation.port}` | 既定値を補ったportの文字列。 |
| `${config.xxx}` | 作者・利用者・実行時上書きを反映した固有config。ドット区切りで階層参照可能。 |

config参照は空でない文字列または空でない文字列配列を受け取る。`origin: "https://cdn.${config.supported_hosts}"` は全対応ホストへ展開するため、host1.comの操作でもhost2.com系が同じ認証対象になる。URL配列なら `origin: "${config.image_origins}"` と直接参照できる。一つのテンプレートの配列参照は一つまで。変数値を再帰展開せず、式・環境変数・秘密情報も参照しない。未定義参照、不正な型、空配列、不正な展開結果は通信・`auth_flow()` 呼出し前に設定エラーとなる。

```yaml
endpoints:
  images:
    origin:
      - "https://cdn.${operation.site_domain}"
      - "https://*.images.${operation.site_domain}"
    auth: session
  public_images:
    origin: "https://public.images.${operation.site_domain}"
    auth: none
```

配列はOR条件で、正規化後に重複を除去する。利用者の `origin` 指定は文字列・配列とも値全体を置換する。上例のpublic_imagesは完全一致なのでimagesより優先する。

ワイルドカードはホスト左端の一ラベル全体だけに許可する。`https://*.example.com` は `cdn.example.com` に一致し、`example.com`、`a.cdn.example.com` には一致しない。`cdn*`、`**`、scheme・portのワイルドカード、IPに対するワイルドカードは不可。`*.com`・`*.co.jp`・`*.github.io` など公開サフィックス直下の指定と、サフィックスを判定できない指定も拒否する。

`site_domain` は `reader.alice.github.io` を `alice.github.io` とする。IP・localhost・未知サフィックス・公開サフィックスそのもの、またはPSLを利用できない環境では、この変数を参照した設定だけをエラーにする。通常の固定originにPSLによる有効性判定は行わない。認証範囲の導出に簡易的なドメイン推定は使わない。PSLはアプリに同梱した公式スナップショットをpublicsuffix2で読み、実行時に取得しない。[リリース時の更新手順](../maintenance/release.md#release-psl)を参照する。

`doctor` はconfig参照と静的ルールを検証するが、起点のない検証ではoperation変数を保留する。予約名 `endpoints.operation` の明示宣言は、起点が確定した時点で同じ単一の具体originに解決されなければならない。

<a id="config-authentication-scope"></a>

### 通常通信・認証用通信への適用範囲

ページ取得、API取得、画像取得、補助ファイル取得は、同じ要求経路を使う範囲で以下の方針を適用する。ログイン要求だけを制御する設定ではない。

| 通信・要求モード | 動作 |
| --- | --- |
| 通常要求の `AuthMode.AUTO`、フローあり、`auth: session` のorigin | 初期化・期限更新・方針による確認・apply・応答分類・必要な認証再試行を行う。 |
| 通常要求の `AUTO`、`auth: none` のorigin | 認証フローを通さず通常のHTTP処理を行う。 |
| フローがない通常要求の `AUTO` | 通常のHTTP処理。未宣言の追加originも、この認証設定によっては拒否しない。 |
| 通常要求の `AuthMode.NONE` | 認証フローを通さない。追加originの認証宣言も要求しない。 |
| 通常要求の `AuthMode.REQUIRED` | フローと認証対象originを必須にする。フローなし・`auth: none` は `AuthScopeError`。 |
| フローあり、未宣言の追加originへの `AUTO` / `REQUIRED` | `AuthScopeError`。公開の接続先は `auth: none` と宣言するか、通常要求で `NONE` を使う。 |
| 認証フック内の `AuthContext.requests` | 初期化・確認・apply・応答分類・認証更新を自動実行しない。operationの既定originを含む宣言済みoriginだけに送信でき、リダイレクト先も制限する。`NONE` でもこの制限を迂回できない。 |

`auth: session` の宣言に対して `auth_flow()` がNoneを返す場合は `AuthScopeError` になる。`endpoints` はアプリ全体の通信先を制限するファイアウォールではなく、このoperationの認証利用・認証用通信の範囲を決める。

`auth: none` と `AuthMode.NONE` はCookie jarを無効にせず、要求に明示した資格情報を自動的に削除する指定でもない。認証用クライアントも通常要求とCookie jarを共有する。ただしBearer等のヘッダーは認証フック側が必要に応じて明示する。cross-origin redirectのヘッダー除去・本文転送制限は別途適用する。

<a id="config-authentication-example"></a>

### 作者設定と利用者上書きの例

次は `AuthFlow.verify()` を実装したサイトプラグインの作者YAMLである。

```yaml
config: {}
authentication:
  verify_on_start: true
  verify_before_request: false
  verify_interval_seconds: 300
  refresh_skew_seconds: 30
endpoints:
  gallery:
    origin: https://www.example.test
    auth: session
  api:
    origin: https://api.example.test
    auth: session
    verification:
      before_request: true
  login:
    origin: https://accounts.example.test
    auth: none
  images:
    origin: https://cdn.example.test
    auth: none
```

galleryは共通の300秒間隔、apiは毎要求確認を使う。loginとimagesへの通常要求では認証フローを通さない。apiの確認が成功すればgalleryの間隔判定にもその時刻を使う。認証開始時の追加確認と期限前30秒の更新はoperation共通である。

利用者がapiの毎要求・間隔確認を無効にし、共通の間隔を600秒へ変える場合は、利用者YAMLへ次を指定する。作者のorigin・auth・他の接続先は維持する。

```yaml
plugin_settings:
  com.example.gallery:
    authentication:
      verify_interval_seconds: 600
    endpoints:
      api:
        verification:
          before_request: false
          interval_seconds: null
```

要求構築の例は[接続先から画像要求を作る](../how-to/dynamic-urls-and-auth.md#endpoint-image-requests)、Cookie共有と実装の移行は[認証APIの移行手順](../how-to/authentication-migration.md)を参照する。
