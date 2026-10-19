<a id="v3-corpus-claim-coverage-ledger"></a>

# 旧v3文書との仕様対応台帳

この台帳は、[再編前のスナップショット](../../../archive/docs/legacy/v3-pre-reorg/README.md)と[分類整理前のスナップショット](../../../archive/docs/legacy/v3-pre-taxonomy/README.md)を参照元とします。最終的な正本は実装です。各行に原文の断片、整理した要件、実装根拠、現行の正本を記録します。`carried`は同じ意味での移管、`merged`は統合、`superseded`は旧記述を再導入せず、変更理由と現行の正本を示すことを表します。

参照ファイルの略記として、`pre-reorg`は再編前の履歴スナップショット、`pre-taxonomy`は分類整理を始めた時点の文書を表します。行内の原文断片は、参照元の該当小節を再検索できる短い引用です。照合に使う原文の引用は英語も含めてそのまま残します。

| 仕様IDの接頭辞 | 動作テストの根拠 |
| --- | --- |
| `LEGACY-API`, `LEGACY-CONFIG`, `TAX-CONFIG` | `tests/v3/configuration/` と `tests/v3/cli/` |
| `LEGACY-PLUGIN`, `LEGACY-SITE`, `TAX-PLUGIN` | `tests/v3/plugins/` |
| `LEGACY-LIBRARY`, `TAX-LIB`, `TAX-RUNTIME` | `tests/v3/application/` と `tests/v3/distribution/test_public_api_and_docs.py` |
| `LEGACY-OPS`, `TAX-COOKIE`, `TAX-STATE` | `tests/v3/observability/`、`tests/v3/storage_output/`、`tests/v3/transport/` |
| `LEGACY-TEST`, `LEGACY-RELEASE`, `LEGACY-INDEX`, `TAX-CLI` | `tests/v3/distribution/` と該当コマンドのテスト |

<a id="repeated-pre-taxonomy-source-claims"></a>

## 分類整理前の文書に重複する仕様

分類整理前の文書は、直前の平坦なリファレンス群です。次の仕様群は、過去の参照元と同じ要件を繰り返しています。重複を別IDにして正本を二重化しないよう、対応する`LEGACY-*`行を参照元の断片と移管先の両方として使います。次節の`TAX-*`は、この対応だけでは表せない固有の訂正・追加確認です。

| 分類整理前の参照元 | 対応する個別仕様 |
| --- | --- |
| `docs/v3/README.md`, ルート・docs・テンプレート・ソースのREADME | `LEGACY-INDEX-001`, `LEGACY-API-007`, `LEGACY-PLUGIN-016`–`017`, `LEGACY-OPS-002` |
| `api-contract-inventory.md`, `api-reference.md`, `library-api.md` | `LEGACY-LIBRARY-001`–`008`, `LEGACY-SITE-001`, `TAX-LIB-001`–`002` |
| `cli-reference.md`, `configuration-and-cli.md` | `LEGACY-CONFIG-001`–`023`, `TAX-CLI-001`–`004`, `TAX-COOKIE-001`, `TAX-RUNTIME-002` |
| `configuration-reference.md` | `LEGACY-API-001`–`002`, `LEGACY-CONFIG-003`–`018`, `TAX-CONFIG-001`–`003` |
| `plugin-author-guide.md`, `plugin-development-reference.md` | `LEGACY-API-003`–`005`, `LEGACY-PLUGIN-001`–`017`, `LEGACY-SITE-002`–`004`, `TAX-PLUGIN-001`–`003` |
| `execution-lifecycle.md` | `LEGACY-LIBRARY-003`–`007`, `LEGACY-SITE-002`–`004`, `TAX-RUNTIME-001` |
| `distribution-and-operations.md` | `LEGACY-OPS-001`–`002`, `LEGACY-PLUGIN-005`–`007`, `TAX-STATE-001` |
| `testing-migration-and-release.md` | `LEGACY-TEST-001`–`006`, `LEGACY-RELEASE-001`–`002` |



| ID | 参照元の節・引用断片 | 整理した要件 | 移管状況 | 実装根拠 | 現行の正本・変更理由 |
| --- | --- | --- | --- | --- | --- |
| LEGACY-API-001 | pre-reorg/plugin-api-v3.md; "Local plugin API v3" | プラグインAPI v3の設定ツリー | carried | `src/image_downloader/configuration/layers.py` | [設定レイヤー](../reference/configuration.md#config-layers) |
| LEGACY-API-002 | pre-reorg/plugin-api-v3.md; “fixed user config and package baseline” | 固定のユーザー設定とパッケージ同梱の基本設定 | carried | `src/image_downloader/configuration/layers.py` | [検出](../reference/configuration.md#config-discovery) |
| LEGACY-API-003 | pre-reorg/plugin-api-v3.md; “directory unit layout” | プラグイン単位のディレクトリ構成 | carried | `src/image_downloader/configuration/layers.py` | [プラグインの構成](../reference/plugin-package.md#plugin-layout) |
| LEGACY-API-004 | pre-reorg/plugin-api-v3.md; “manifest wrapper is signed” | manifestを含む外側のオブジェクトへの署名 | carried | `src/image_downloader/configuration/layers.py` | [manifest](../reference/plugin-package.md#plugin-manifest) |
| LEGACY-API-005 | pre-reorg/plugin-api-v3.md; “catalog trust controls load” | カタログの信頼情報による読込み制御 | carried | `src/image_downloader/configuration/layers.py` | [カタログ](../reference/plugin-package.md#plugin-catalog) |
| LEGACY-API-006 | pre-reorg/plugin-api-v3.md; “RuntimeComposer/DownloadService ownership” | RuntimeComposer・DownloadServiceの所有権 | carried | `src/image_downloader/configuration/layers.py` | [公開メソッド](../reference/library-api.md#api-public-methods) |
| LEGACY-API-007 | pre-reorg/plugin-api-v3.md; “v2 descriptors/trees are not loaded” | v2の記述子・ツリーを読み込まない | carried | `src/image_downloader/configuration/layers.py` | [設定の移行](../reference/configuration.md#config-migration) |
| LEGACY-INDEX-001 | pre-reorg/v3/README.md; “v3 documentation reader routing” | v3文書で読者を目的に応じて案内する | merged | `docs/v3/README.md` | [現行の索引](../README.md#docs-entry) |
| LEGACY-CONFIG-001 | pre-reorg/v3/configuration-and-cli.md; “minimum download invocation” | 最小限のダウンロード呼出し | carried | `src/image_downloader/commands` | [標準の呼出し形式](../reference/cli.md#cli-forms) |
| LEGACY-CONFIG-002 | pre-reorg/v3/configuration-and-cli.md; “automatic config bootstrap side effect” | 設定を自動初期化する副作用 | carried | `src/image_downloader/commands` | [状態への副作用](../reference/cli.md#cli-state-effects) |
| LEGACY-CONFIG-003 | pre-reorg/v3/configuration-and-cli.md; “layer precedence is deterministic” | 設定レイヤーの優先順位が一意に決まる | carried | `src/image_downloader/commands` | [検出](../reference/configuration.md#config-discovery) |
| LEGACY-CONFIG-004 | pre-reorg/v3/configuration-and-cli.md; “mapping deep merge and scalar/list replacement” | マッピングの深いマージと、スカラー・リストの置換 | carried | `src/image_downloader/commands` | [検出](../reference/configuration.md#config-discovery) |
| LEGACY-CONFIG-005 | pre-reorg/v3/configuration-and-cli.md; “profile/site overlay restrictions” | プロファイル・サイトの上書き制約 | carried | `src/image_downloader/commands` | [検出](../reference/configuration.md#config-discovery) |
| LEGACY-CONFIG-006 | pre-reorg/v3/configuration-and-cli.md; “`profile.default` schema” | `profile.default`のスキーマ | carried | `src/image_downloader/commands` | [スキーマ](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-007 | pre-reorg/v3/configuration-and-cli.md; “storage/plugins roots must be absolute or null” | 保存先・プラグインのルートは絶対パスまたはnull | carried | `src/image_downloader/commands` | [検出](../reference/configuration.md#config-discovery) |
| LEGACY-CONFIG-008 | pre-reorg/v3/configuration-and-cli.md; “download concurrency/error schema” | ダウンロードの並列数・エラー設定のスキーマ | carried | `src/image_downloader/commands` | [スキーマ](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-009 | pre-reorg/v3/configuration-and-cli.md; “output format/collision/lock schema” | 出力書式・衝突・ロック設定のスキーマ | carried | `src/image_downloader/commands` | [スキーマ](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-010 | pre-reorg/v3/configuration-and-cli.md; “media validation and pixel limit” | 画像検査と画素数の制限 | carried | `src/image_downloader/commands` | [スキーマ](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-011 | pre-reorg/v3/configuration-and-cli.md; “logging safe parameter schema” | ログで安全に表示するパラメーターのスキーマ | carried | `src/image_downloader/commands` | [スキーマ](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-012 | pre-reorg/v3/configuration-and-cli.md; “request/origin/domain concurrency” | 要求・origin・ドメインごとの並列数 | carried | `src/image_downloader/commands` | [スキーマ](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-013 | pre-reorg/v3/configuration-and-cli.md; “timeout/retry/auth refresh schema” | タイムアウト・再試行・認証更新のスキーマ | carried | `src/image_downloader/commands` | [スキーマ](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-014 | pre-reorg/v3/configuration-and-cli.md; “pool/response-limit/HTTP schema” | 接続プール・応答上限・HTTPのスキーマ | carried | `src/image_downloader/commands` | [スキーマ](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-015 | pre-reorg/v3/configuration-and-cli.md; “notification/email schema” | 通知・メールのスキーマ | carried | `src/image_downloader/commands` | [スキーマ](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-016 | pre-reorg/v3/configuration-and-cli.md; “verification/fallback/processor schema” | 検証・フォールバック・プロセッサーのスキーマ | carried | `src/image_downloader/commands` | [スキーマ](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-017 | pre-reorg/v3/configuration-and-cli.md; “plugin settings merge/enabled/secrets rules” | プラグイン設定のマージ・有効化・秘密情報の規則 | carried | `src/image_downloader/commands` | [プラグイン設定](../reference/configuration.md#config-plugin-settings) |
| LEGACY-CONFIG-018 | pre-reorg/v3/configuration-and-cli.md; “legacy network key replacements” | 旧通信設定キーの置換先 | carried | `src/image_downloader/commands` | [移行](../reference/configuration.md#config-migration) |
| LEGACY-CONFIG-019 | pre-reorg/v3/configuration-and-cli.md; “cookie export/import keeps secrets private” | Cookieのエクスポート・インポートで秘密情報を保護する | carried | `src/image_downloader/commands` | [成功時のJSON](../reference/cli.md#cli-success-json) |
| LEGACY-CONFIG-020 | pre-reorg/v3/configuration-and-cli.md; “global and per-command option applicability” | 全体オプションとコマンド別オプションの適用範囲 | carried | `src/image_downloader/commands` | [オプション](../reference/cli.md#cli-options) |
| LEGACY-CONFIG-021 | pre-reorg/v3/configuration-and-cli.md; “exit-code meanings” | 終了コードの意味 | carried | `src/image_downloader/commands` | [終了コード](../reference/cli.md#cli-exit-status) |
| LEGACY-CONFIG-022 | pre-reorg/v3/configuration-and-cli.md; “JSON success/error/redaction contract” | JSONの成功・エラー・秘匿化の契約 | merged | `src/image_downloader/commands` | [成功時のJSON](../reference/cli.md#cli-success-json) |
| LEGACY-CONFIG-023 | pre-reorg/v3/configuration-and-cli.md; “error optional fields were shown as null placeholders” | エラーの任意フィールドをnullの埋め合わせとして表示する旧記述 | superseded | `src/image_downloader/commands` | 現在は不明な`response_url`・`http_status`・`output_path`を省略します。[エラー](../reference/cli.md#cli-errors)を参照してください。 |
| LEGACY-PLUGIN-001 | pre-reorg/v3/plugin-author-guide.md; “author config only has `config` root key” | 作者既定値のルートキーは`config`だけ | carried | `src/image_downloader/plugins` | [プラグインの構成](../reference/plugin-package.md#plugin-layout) |
| LEGACY-PLUGIN-002 | pre-reorg/v3/plugin-author-guide.md; “signer metadata is runtime-independent input” | 署名ツールのメタデータは実行時から独立した入力 | carried | `src/image_downloader/plugins` | [プラグインの構成](../reference/plugin-package.md#plugin-layout) |
| LEGACY-PLUGIN-003 | pre-reorg/v3/plugin-author-guide.md; “manifest exact fields and ID/path rules” | manifestの厳密なフィールドとID・パスの規則 | carried | `src/image_downloader/plugins` | [manifest](../reference/plugin-package.md#plugin-manifest) |
| LEGACY-PLUGIN-004 | pre-reorg/v3/plugin-author-guide.md; “file-tree/link/cache rules” | ファイルツリー・リンク・キャッシュの規則 | carried | `src/image_downloader/plugins` | [manifest](../reference/plugin-package.md#plugin-manifest) |
| LEGACY-PLUGIN-005 | pre-reorg/v3/plugin-author-guide.md; “catalog normal/content pins” | カタログの通常のpinとcontent pin | carried | `src/image_downloader/plugins` | [カタログ](../reference/plugin-package.md#plugin-catalog) |
| LEGACY-PLUGIN-006 | pre-reorg/v3/plugin-author-guide.md; “verification modes and bypass semantics” | 検証モードと迂回時の扱い | carried | `src/image_downloader/plugins` | [カタログ](../reference/plugin-package.md#plugin-catalog) |
| LEGACY-PLUGIN-007 | pre-reorg/v3/plugin-author-guide.md; “install/trust/revoke/uninstall safety” | install・trust・revoke・uninstallの安全性 | carried | `src/image_downloader/plugins` | [署名](../reference/plugin-package.md#plugin-signing) |
| LEGACY-PLUGIN-008 | pre-reorg/v3/plugin-author-guide.md; “selection priority and tie failure” | 選択優先順位と同順位での失敗 | carried | `src/image_downloader/plugins` | [選択](../reference/plugin-hooks.md#plugin-selection) |
| LEGACY-PLUGIN-009 | pre-reorg/v3/plugin-author-guide.md; “config-aware matching rules” | 設定を考慮した選択規則 | carried | `src/image_downloader/plugins` | [選択](../reference/plugin-hooks.md#plugin-selection) |
| LEGACY-PLUGIN-010 | pre-reorg/v3/plugin-author-guide.md; “all site hook return contracts” | サイトの全フックの戻り値の契約 | carried | `src/image_downloader/plugins` | [フック](../reference/plugin-hooks.md#plugin-hooks) |
| LEGACY-PLUGIN-011 | pre-reorg/v3/plugin-author-guide.md; “image processor transform and cleanup” | 画像プロセッサーの変換と後始末 | carried | `src/image_downloader/plugins` | [フック](../reference/plugin-hooks.md#plugin-hooks) |
| LEGACY-PLUGIN-012 | pre-reorg/v3/plugin-author-guide.md; “context capabilities/immutable mappings” | コンテキストの権限と不変のマッピング | carried | `src/image_downloader/plugins` | [フック](../reference/plugin-hooks.md#plugin-hooks) |
| LEGACY-PLUGIN-013 | pre-reorg/v3/plugin-author-guide.md; “API pagination must complete in inspect” | APIのページ分割はinspect内で完結させる | carried | `src/image_downloader/plugins` | [フック](../reference/plugin-hooks.md#plugin-hooks) |
| LEGACY-PLUGIN-014 | pre-reorg/v3/plugin-author-guide.md; “signed URLs use request/recovery hooks” | 署名付きURLは要求・回復フックで扱う | carried | `src/image_downloader/plugins` | [フック](../reference/plugin-hooks.md#plugin-hooks) |
| LEGACY-PLUGIN-015 | pre-reorg/v3/plugin-author-guide.md; “AuthFlow origin scope and secret errors” | AuthFlowのorigin範囲と秘密情報のエラー | carried | `src/image_downloader/plugins` | [フック](../reference/plugin-hooks.md#plugin-hooks) |
| LEGACY-PLUGIN-016 | pre-reorg/v3/plugin-author-guide.md; “unsupported browser/interactive features” | ブラウザー・対話処理の未対応機能 | carried | `src/image_downloader/plugins` | [実装例](../reference/plugin-hooks.md#plugin-examples) |
| LEGACY-PLUGIN-017 | pre-reorg/v3/plugin-author-guide.md; “plugin author test acceptance” | プラグイン作者が確認する受入テスト | carried | `src/image_downloader/plugins` | [プラグインのテスト](testing.md#testing-package-trust) |
| LEGACY-LIBRARY-001 | pre-reorg/v3/library-api.md; “stable facade imports are contractual” | 安定したファサードからのimportは公開契約 | carried | `src/image_downloader/runtime.py` | [API一覧](../reference/api-contract-inventory.md#api-inventory) |
| LEGACY-LIBRARY-002 | pre-reorg/v3/library-api.md; “compose/config loading and rewrite behavior” | 実行環境の構成・設定読込み・書換えの挙動 | merged | `src/image_downloader/runtime.py` | [設定のファサード](../reference/library-api.md#api-configuration) |
| LEGACY-LIBRARY-003 | pre-reorg/v3/library-api.md; “run/update serialisation and close” | ダウンロード・更新確認の直列化と終了処理 | carried | `src/image_downloader/runtime.py` | [公開メソッド](../reference/library-api.md#api-public-methods) |
| LEGACY-LIBRARY-004 | pre-reorg/v3/library-api.md; “operation override shape” | 操作ごとの上書き設定の構造 | carried | `src/image_downloader/runtime.py` | [公開メソッド](../reference/library-api.md#api-public-methods) |
| LEGACY-LIBRARY-005 | pre-reorg/v3/library-api.md; “result DTO fields and failure correlation” | 結果DTOのフィールドと失敗の対応付け | carried | `src/image_downloader/runtime.py` | [ダウンロード結果](../reference/library-api.md#api-download-result) |
| LEGACY-LIBRARY-006 | pre-reorg/v3/library-api.md; “errors vs image outcome failures/cancellation” | 例外と画像結果の失敗・キャンセルの違い | carried | `src/image_downloader/runtime.py` | [例外一覧](../reference/library-api.md#api-errors) |
| LEGACY-LIBRARY-007 | pre-reorg/v3/library-api.md; “dependency injection boundary” | 依存性の注入の境界 | carried | `src/image_downloader/runtime.py` | [実行環境の構成要素](../reference/library-api.md#api-public-methods) |
| LEGACY-LIBRARY-008 | pre-reorg/v3/library-api.md; “internal modules are not stable” | 内部モジュールは安定APIの対象外 | carried | `src/image_downloader/runtime.py` | [共通規則](../reference/library-api.md#api-common) |
| LEGACY-SITE-001 | pre-reorg/v3/site-plugin-integration-guide.md; “RequestSpec body/auth/retry fields” | RequestSpecの本文・認証・再試行のフィールド | carried | `src/image_downloader/plugins/lifecycle.py` | [要求の値オブジェクト](../reference/library-api.md#api-common) |
| LEGACY-SITE-002 | pre-reorg/v3/site-plugin-integration-guide.md; “image URL and Referer handling” | 画像URLとRefererの扱い | merged | `src/image_downloader/plugins/lifecycle.py` | [フック](../reference/plugin-hooks.md#plugin-hooks) |
| LEGACY-SITE-003 | pre-reorg/v3/site-plugin-integration-guide.md; “concrete HTML/API/auth examples” | HTML・API・認証の具体例 | carried | `src/image_downloader/plugins/lifecycle.py` | [実装例](../reference/plugin-hooks.md#plugin-examples) |
| LEGACY-SITE-004 | pre-reorg/v3/site-plugin-integration-guide.md; “auth refresh and origin rules” | 認証更新とoriginの規則 | carried | `src/image_downloader/plugins/lifecycle.py` | [フック](../reference/plugin-hooks.md#plugin-hooks) |
| LEGACY-SITE-005 | pre-reorg/v3/site-plugin-integration-guide.md; “network limits and response size” | 通信制限と応答サイズ | carried | `src/image_downloader/plugins/lifecycle.py` | [スキーマ](../reference/configuration.md#config-schema) |
| LEGACY-TEST-001 | pre-reorg/v3/testing-and-migration.md; “configuration acceptance matrix” | 設定の受入条件の組合せ | carried | `tests/v3` | [設定のテスト](testing.md#testing-config-cli) |
| LEGACY-TEST-002 | pre-reorg/v3/testing-and-migration.md; “manifest/catalog acceptance matrix” | manifest・カタログの受入条件の組合せ | carried | `tests/v3` | [プラグイン信頼検証のテスト](testing.md#testing-package-trust) |
| LEGACY-TEST-003 | pre-reorg/v3/testing-and-migration.md; “update state schema migration” | 更新状態のスキーマ移行 | carried | `tests/v3` | [移行](testing.md#testing-migration) |
| LEGACY-TEST-004 | pre-reorg/v3/testing-and-migration.md; “selection/context/operation acceptance” | 選択・コンテキスト・操作の受入条件 | carried | `tests/v3` | [実行時のテスト](testing.md#testing-runtime-library) |
| LEGACY-TEST-005 | pre-reorg/v3/testing-and-migration.md; “doctor acceptance matrix” | doctorの受入条件の組合せ | carried | `tests/v3` | [設定のテスト](testing.md#testing-config-cli) |
| LEGACY-TEST-006 | pre-reorg/v3/testing-and-migration.md; “v2 manual migration and operations checklist” | v2の手動移行と運用チェックリスト | carried | `tests/v3` | [移行](testing.md#testing-migration) |
| LEGACY-RELEASE-001 | pre-reorg/v3/release-checklist.md; “release preflight checks” | リリース前の確認 | carried | `pyproject.toml and .github` | [リリース](testing.md#testing-release) |
| LEGACY-RELEASE-002 | pre-reorg/v3/release-checklist.md; “release stop conditions” | リリースの停止条件 | carried | `pyproject.toml and .github` | [リリース](testing.md#testing-release) |
| LEGACY-OPS-001 | pre-reorg/v3/trust-and-operations.md; “privacy-safe logging” | 秘密情報を保護するログ | carried | `src/image_downloader/observability` | [ログのファサード](../reference/library-api.md#api-logging) |
| LEGACY-OPS-002 | pre-reorg/v3/trust-and-operations.md; “doctor diagnostics and operator recovery” | doctorの診断と運用者による復旧 | carried | `src/image_downloader/observability` | [状態への副作用](../reference/cli.md#cli-state-effects) |

<a id="pre-taxonomy-correction-and-preservation-claims"></a>

## 分類整理時に訂正・保持した仕様

この表には、分類整理の開始時点の`docs/v3`とREADMEから再検証した、誤った仕様を再導入しやすい項目を記録します。`TAX-*`も旧文書の行と同じ検証対象にします。

| ID | 参照元の節・引用断片 | 整理した要件 | 移管状況 | 実装根拠 | 現行の正本・変更理由 |
| --- | --- | --- | --- | --- | --- |
| TAX-CLI-001 | pre-taxonomy/cli-reference.md; “`--plugin-config-file`” | プラグイン設定ファイルは厳密なJSONオブジェクトで、ファイル指定をインライン上書きより先に適用する | carried | `src/image_downloader/commands/setup.py:_runtime_overrides` | [オプションの契約](../reference/cli.md#cli-options) |
| TAX-CLI-002 | pre-taxonomy/cli-reference.md; “`--yes`” | `--yes`はプラグイン管理の変更操作だけを確認する | carried | `src/image_downloader/commands/plugin.py:_confirm` | [オプションの契約](../reference/cli.md#cli-options) |
| TAX-CLI-003 | pre-taxonomy/configuration-and-cli.md; “null placeholders” | 利用できないJSONエラーフィールドは常にnullで出力するという旧記述 | superseded | `src/image_downloader/commands/reporting.py` | 現在は不明なエラーフィールドを省略します。[エラーの契約](../reference/cli.md#cli-errors)を参照してください。 |
| TAX-CLI-004 | pre-taxonomy/cli-reference.md; “plugin config file” | `--plugin-config-file`がYAMLを受け付けるという旧記述 | superseded | `src/image_downloader/commands/setup.py:_runtime_overrides` | 現在受け付ける形式はJSONだけです。[オプションの契約](../reference/cli.md#cli-options)を参照してください。 |
| TAX-CONFIG-001 | pre-taxonomy/configuration-reference.md; “notification” | NotificationCategoryの全値と、既定の通知先の対応を文書に残す | carried | `src/image_downloader/configuration/models.py:NotificationCategory` | [設定スキーマ](../reference/configuration.md#config-schema) |
| TAX-CONFIG-002 | pre-taxonomy/configuration-reference.md; “rewrite_user_layers” | 設定解決は既定で読取り専用とし、書換え時はユーザー設定の旧静的キーを原子的に除去できる | carried | `src/image_downloader/configuration/layers.py:resolve_application_config` | [レイヤーと変更](../reference/configuration.md#config-layers) |
| TAX-CONFIG-003 | pre-taxonomy/configuration-reference.md; “plugin secrets” | 環境変数を先に調べ、その後にプラグイン別のkeyringを調べる | carried | `src/image_downloader/credentials/plugin_secrets.py` | [秘密情報の解決](../reference/configuration.md#config-secrets) |
| TAX-PLUGIN-001 | pre-taxonomy/plugin-development-reference.md; “plugin-metadata.json” | メタデータが実行時に必須であるという旧記述 | superseded | `src/image_downloader/plugins/plugin_manifest.py` | 現在は署名ツールへの入力だけに使います。[パッケージ構成](../reference/plugin-package.md#plugin-layout)を参照してください。 |
| TAX-PLUGIN-002 | pre-taxonomy/plugin-development-reference.md; “inspect” | inspectは有限で完全なmanifestを1つ返し、ブラウザーを実行せずページ分割を完結させる | carried | `src/image_downloader/plugins/lifecycle.py` | [フックの契約](../reference/plugin-hooks.md#plugin-hooks) |
| TAX-PLUGIN-003 | pre-taxonomy/plugin-development-reference.md; “processor cleanup” | `close()`より`aclose()`を優先し、すべての終了経路で後始末するという旧記述 | carried | `src/image_downloader/plugins/lifecycle.py` | [プロセッサーの後始末](../reference/plugin-hooks.md#plugin-hooks) |
| TAX-RUNTIME-001 | pre-taxonomy/execution-lifecycle.md; “parallel processing” | 設定済みのプロセッサーインスタンスを、1つの操作で並行して呼ぶという旧記述 | superseded | `src/image_downloader/media/artifact_pipeline.py` | 現在は同一のプロセッサーインスタンスを直列化します。[ライフサイクルの並行性](../explanation/execution-lifecycle.md#lifecycle-concurrency)を参照してください。 |
| TAX-RUNTIME-002 | pre-taxonomy/configuration-and-cli.md; “retry” | originをまたいでもCookie・Referer・プラグインヘッダーを維持するという旧記述 | superseded | `src/image_downloader/transport/httpx_transport.py` | 現在はoriginをまたぐリダイレクトで、それらのヘッダーを抑止します。[HTTP通信](../reference/runtime-behavior.md#http-transport)を参照してください。 |
| TAX-COOKIE-001 | pre-taxonomy/configuration-and-cli.md; “cookie storage” | Cookieの暗号化保存、keyring・パスフレーズ、マージ、ロックの仕様を残す | carried | `src/image_downloader/storage/cookies.py` | [Cookieの契約](../reference/runtime-behavior.md#runtime-cookies) |
| TAX-STATE-001 | pre-taxonomy/distribution-and-operations.md; “update state” | 状態のv2移行でlegacy_recordsを保持し、ロック下で完全なスナップショットを比較する | carried | `src/image_downloader/storage/state.py` | [更新状態の契約](../reference/runtime-behavior.md#runtime-update-state) |
| TAX-LIB-001 | pre-taxonomy/api-reference.md; “ImageFailure” | ImageFailure自体がURL・章・画像番号を持つという旧記述 | superseded | `src/image_downloader/models.py:ImageFailure` | 現在の対応付けには`ImageOutcome.image`だけを使います。[結果DTO](../reference/library-api.md#api-download-result)を参照してください。 |
| TAX-LIB-002 | pre-taxonomy/api-reference.md; “DownloadLogger” | ログの記録・終了操作が同期処理であるという旧記述 | superseded | `src/image_downloader/observability/logging.py` | 現在の記録・終了メソッドは非同期です。[ログリファレンス](../reference/logging.md#logging-reference)を参照してください。 |

<a id="snapshot-quotation-and-destination-proof"></a>

## スナップショットの引用と移管先の記載根拠

主台帳で整理した要件は、それぞれ1つの仕様を表します。次の補助表には、参照元のスナップショットを特定できる逐語的な引用と、現行の正本に表示される文言を記録します。同じ文書から複数の仕様を導いた場合は、文書の表題を引用することもあります。引用は変更しない原文群と照合します。移管先の記載根拠は、2つ目の仕様定義ではありません。本文の説明が削除されたのに移管先のアンカーだけが残ることを防ぐためのものです。

| ID | 原文からの逐語引用 | 移管先で確認する記載 |
| --- | --- | --- |
| LEGACY-API-001 | pre-reorg/plugin-api-v3.md; "Local plugin API v3" | "`AppConfig` / `validate_config` への直接入力では unknown field を拒否する。" |
| LEGACY-API-002 | pre-reorg/plugin-api-v3.md; "Local plugin API v3" | "`DEFAULT_CONFIG`" |
| LEGACY-API-003 | pre-reorg/plugin-api-v3.md; "Local plugin API v3" | "`plugin-metadata.json`は付属の署名ツールが任意で受け取る入力であり、実行時には読みません。別の方法で完全なmanifestを生成・署名する場合、このファイルは不要です。付属の`tools/sign_local_site_plugin.py`は、メタデータの`kind`に応じてサイト用と画像プロセッサー用の両方を署名できます。署名ツールが読むメタデータは、未知のフィールドもフィールドの欠落も許さない厳密なオブジェクトです。" |
| LEGACY-API-004 | pre-reorg/plugin-api-v3.md; "Local plugin API v3" | "manifestとファイルツリー" |
| LEGACY-API-005 | pre-reorg/plugin-api-v3.md; "Local plugin API v3" | "カタログのパスは常に`<plugin-root>/catalog.json`、ルートのスキーマバージョンは`1`です。ルート・各エントリーともに未知のフィールドは許可しません。通常のpinにはID、種別、発行者、バージョン、公開鍵、鍵ID、manifestのダイジェスト、ファイルツリーのダイジェスト、選択優先順位、失効状態を保持します。content pinは`id`、`kind`、`manifest_digest`、`content_digest`、`selection_priority`、`revoked`だけを持ちます。発行者や鍵のフィールドをnullの埋め合わせとしては持ちません。" |
| LEGACY-API-006 | pre-reorg/plugin-api-v3.md; "Local plugin API v3" | "実行環境のファサード" |
| LEGACY-API-007 | pre-reorg/plugin-api-v3.md; "Local plugin API v3" | "移行と互換性" |
| LEGACY-INDEX-001 | pre-reorg/v3/README.md; "local plugin API v3 ドキュメント" | "このディレクトリはAPI v3の現行仕様の正本です。文書を目的別に分けています。設定値、型、入出力、例外は**リファレンス**、フックの順序・回数・並行性は**実行ライフサイクルの解説**を正本とします。チュートリアルや操作手順では規則を再定義せず、対応する箇所を参照します。" |
| LEGACY-CONFIG-001 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "正規のコマンド形式は次のとおりです。" |
| LEGACY-CONFIG-002 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "config 作成・rewrite の command ごとの副作用は &lbrack;設定リファレンス&rbrack;(configuration.md#config-layers) が正本である。`config path` と `config explain` は観測専用である。" |
| LEGACY-CONFIG-003 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "`DEFAULT_CONFIG`" |
| LEGACY-CONFIG-004 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "`DEFAULT_CONFIG`" |
| LEGACY-CONFIG-005 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "`DEFAULT_CONFIG`" |
| LEGACY-CONFIG-006 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "設定スキーマ" |
| LEGACY-CONFIG-007 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "`DEFAULT_CONFIG`" |
| LEGACY-CONFIG-008 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "設定スキーマ" |
| LEGACY-CONFIG-009 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "設定スキーマ" |
| LEGACY-CONFIG-010 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "設定スキーマ" |
| LEGACY-CONFIG-011 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "設定スキーマ" |
| LEGACY-CONFIG-012 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "設定スキーマ" |
| LEGACY-CONFIG-013 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "設定スキーマ" |
| LEGACY-CONFIG-014 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "設定スキーマ" |
| LEGACY-CONFIG-015 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "設定スキーマ" |
| LEGACY-CONFIG-016 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "設定スキーマ" |
| LEGACY-CONFIG-017 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "プラグインの設定と秘密情報" |
| LEGACY-CONFIG-018 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "移行と互換性" |
| LEGACY-CONFIG-019 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "downloadの章ログは標準出力に出さず、人間向けのエラー診断は標準エラー出力へ送ります。JSON形式のdownloadでは、プラグインのPythonの`print()`も標準エラー出力へ転送します。OSの標準出力ファイル記述子へ直接書き込むネイティブコードは、この制御の対象外です。" |
| LEGACY-CONFIG-020 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "`--json`は全コマンドで受理します。**受理される指定でも効果があるとは限りません**。適用できないオプションは、設定解決前に`ArgumentError`となります。オプションはコマンド前、サブコマンド間、位置引数の後に置くことができ、受理条件と診断の優先順位は同じです。完全なオプション名と文書化した別名だけを受理し、`--j`や`--list`のような省略形は拒否します。" |
| LEGACY-CONFIG-021 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "code" |
| LEGACY-CONFIG-022 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "downloadの章ログは標準出力に出さず、人間向けのエラー診断は標準エラー出力へ送ります。JSON形式のdownloadでは、プラグインのPythonの`print()`も標準エラー出力へ転送します。OSの標準出力ファイル記述子へ直接書き込むネイティブコードは、この制御の対象外です。" |
| LEGACY-CONFIG-023 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "処理済みのJSONエラー" |
| LEGACY-PLUGIN-001 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "`plugin-metadata.json`は付属の署名ツールが任意で受け取る入力であり、実行時には読みません。別の方法で完全なmanifestを生成・署名する場合、このファイルは不要です。付属の`tools/sign_local_site_plugin.py`は、メタデータの`kind`に応じてサイト用と画像プロセッサー用の両方を署名できます。署名ツールが読むメタデータは、未知のフィールドもフィールドの欠落も許さない厳密なオブジェクトです。" |
| LEGACY-PLUGIN-002 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "`plugin-metadata.json`は付属の署名ツールが任意で受け取る入力であり、実行時には読みません。別の方法で完全なmanifestを生成・署名する場合、このファイルは不要です。付属の`tools/sign_local_site_plugin.py`は、メタデータの`kind`に応じてサイト用と画像プロセッサー用の両方を署名できます。署名ツールが読むメタデータは、未知のフィールドもフィールドの欠落も許さない厳密なオブジェクトです。" |
| LEGACY-PLUGIN-003 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "manifestとファイルツリー" |
| LEGACY-PLUGIN-004 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "manifestとファイルツリー" |
| LEGACY-PLUGIN-005 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "カタログのパスは常に`<plugin-root>/catalog.json`、ルートのスキーマバージョンは`1`です。ルート・各エントリーともに未知のフィールドは許可しません。通常のpinにはID、種別、発行者、バージョン、公開鍵、鍵ID、manifestのダイジェスト、ファイルツリーのダイジェスト、選択優先順位、失効状態を保持します。content pinは`id`、`kind`、`manifest_digest`、`content_digest`、`selection_priority`、`revoked`だけを持ちます。発行者や鍵のフィールドをnullの埋め合わせとしては持ちません。" |
| LEGACY-PLUGIN-006 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "カタログのパスは常に`<plugin-root>/catalog.json`、ルートのスキーマバージョンは`1`です。ルート・各エントリーともに未知のフィールドは許可しません。通常のpinにはID、種別、発行者、バージョン、公開鍵、鍵ID、manifestのダイジェスト、ファイルツリーのダイジェスト、選択優先順位、失効状態を保持します。content pinは`id`、`kind`、`manifest_digest`、`content_digest`、`selection_priority`、`revoked`だけを持ちます。発行者や鍵のフィールドをnullの埋め合わせとしては持ちません。" |
| LEGACY-PLUGIN-007 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "管理ポリシー" |
| LEGACY-PLUGIN-008 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "選択処理では、有効なサイトプラグインの`matches_with_config()`（実装されている場合）と`matches()`を評価し、`selection_priority`が最も高い候補を選びます。最高優先順位の候補が複数ある場合は、曖昧な選択として`PluginError`にします。`matches_with_config`が依存できるのはプラグイン固有設定だけであり、通信・秘密情報・ファイルシステムへはアクセスしません。" |
| LEGACY-PLUGIN-009 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "選択処理では、有効なサイトプラグインの`matches_with_config()`（実装されている場合）と`matches()`を評価し、`selection_priority`が最も高い候補を選びます。最高優先順位の候補が複数ある場合は、曖昧な選択として`PluginError`にします。`matches_with_config`が依存できるのはプラグイン固有設定だけであり、通信・秘密情報・ファイルシステムへはアクセスしません。" |
| LEGACY-PLUGIN-010 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "この文書はプラグインフックの入出力、権限、例外の正本です。呼出し順・回数・並行性は&lbrack;実行ライフサイクル&rbrack;(../explanation/execution-lifecycle.md)を参照してください。" |
| LEGACY-PLUGIN-011 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "この文書はプラグインフックの入出力、権限、例外の正本です。呼出し順・回数・並行性は&lbrack;実行ライフサイクル&rbrack;(../explanation/execution-lifecycle.md)を参照してください。" |
| LEGACY-PLUGIN-012 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "この文書はプラグインフックの入出力、権限、例外の正本です。呼出し順・回数・並行性は&lbrack;実行ライフサイクル&rbrack;(../explanation/execution-lifecycle.md)を参照してください。" |
| LEGACY-PLUGIN-013 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "この文書はプラグインフックの入出力、権限、例外の正本です。呼出し順・回数・並行性は&lbrack;実行ライフサイクル&rbrack;(../explanation/execution-lifecycle.md)を参照してください。" |
| LEGACY-PLUGIN-014 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "この文書はプラグインフックの入出力、権限、例外の正本です。呼出し順・回数・並行性は&lbrack;実行ライフサイクル&rbrack;(../explanation/execution-lifecycle.md)を参照してください。" |
| LEGACY-PLUGIN-015 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "この文書はプラグインフックの入出力、権限、例外の正本です。呼出し順・回数・並行性は&lbrack;実行ライフサイクル&rbrack;(../explanation/execution-lifecycle.md)を参照してください。" |
| LEGACY-PLUGIN-016 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "用途" |
| LEGACY-PLUGIN-017 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "v3のテストは、設定値の受理、CLIの振り分け・JSON・秘匿化、プラグインパッケージの信頼、ライフサイクル・認証・回復、通信・永続化の安全性、結果・例外の挙動、ソース形式の画像処理プラグインの例を確認します。" |
| LEGACY-LIBRARY-001 | pre-reorg/v3/library-api.md; "ライブラリ API" | "この一覧は、安定したファサードで公開する名前を網羅しています。個々の引数、戻り値、例外は&lbrack;ライブラリAPIリファレンス&rbrack;(library-api.md)、&lbrack;設定リファレンス&rbrack;(configuration.md)、&lbrack;CLIリファレンス&rbrack;(cli.md)、&lbrack;ログリファレンス&rbrack;(logging.md)で定義します。各ブロックが実装の<code>__all__</code>と一致することを、契約テストで確認します。" |
| LEGACY-LIBRARY-002 | pre-reorg/v3/library-api.md; "ライブラリ API" | "実行環境のファサード" |
| LEGACY-LIBRARY-003 | pre-reorg/v3/library-api.md; "ライブラリ API" | "実行環境のファサード" |
| LEGACY-LIBRARY-004 | pre-reorg/v3/library-api.md; "ライブラリ API" | "実行環境のファサード" |
| LEGACY-LIBRARY-005 | pre-reorg/v3/library-api.md; "ライブラリ API" | "値オブジェクト・結果・プロトコル" |
| LEGACY-LIBRARY-006 | pre-reorg/v3/library-api.md; "ライブラリ API" | "例外と呼び出し側の扱い" |
| LEGACY-LIBRARY-007 | pre-reorg/v3/library-api.md; "ライブラリ API" | "実行環境のファサード" |
| LEGACY-LIBRARY-008 | pre-reorg/v3/library-api.md; "ライブラリ API" | "値オブジェクト・結果・プロトコル" |
| LEGACY-SITE-001 | pre-reorg/v3/site-plugin-integration-guide.md; "サイト plugin 統合・認証ガイド" | "値オブジェクト・結果・プロトコル" |
| LEGACY-SITE-002 | pre-reorg/v3/site-plugin-integration-guide.md; "サイト plugin 統合・認証ガイド" | "この文書はプラグインフックの入出力、権限、例外の正本です。呼出し順・回数・並行性は&lbrack;実行ライフサイクル&rbrack;(../explanation/execution-lifecycle.md)を参照してください。" |
| LEGACY-SITE-003 | pre-reorg/v3/site-plugin-integration-guide.md; "サイト plugin 統合・認証ガイド" | "用途" |
| LEGACY-SITE-004 | pre-reorg/v3/site-plugin-integration-guide.md; "サイト plugin 統合・認証ガイド" | "この文書はプラグインフックの入出力、権限、例外の正本です。呼出し順・回数・並行性は&lbrack;実行ライフサイクル&rbrack;(../explanation/execution-lifecycle.md)を参照してください。" |
| LEGACY-SITE-005 | pre-reorg/v3/site-plugin-integration-guide.md; "サイト plugin 統合・認証ガイド" | "設定スキーマ" |
| LEGACY-TEST-001 | pre-reorg/v3/testing-and-migration.md; "テストと移行" | "v3のテストは、設定値の受理、CLIの振り分け・JSON・秘匿化、プラグインパッケージの信頼、ライフサイクル・認証・回復、通信・永続化の安全性、結果・例外の挙動、ソース形式の画像処理プラグインの例を確認します。" |
| LEGACY-TEST-002 | pre-reorg/v3/testing-and-migration.md; "テストと移行" | "v3のテストは、設定値の受理、CLIの振り分け・JSON・秘匿化、プラグインパッケージの信頼、ライフサイクル・認証・回復、通信・永続化の安全性、結果・例外の挙動、ソース形式の画像処理プラグインの例を確認します。" |
| LEGACY-TEST-003 | pre-reorg/v3/testing-and-migration.md; "テストと移行" | "v3のテストは、設定値の受理、CLIの振り分け・JSON・秘匿化、プラグインパッケージの信頼、ライフサイクル・認証・回復、通信・永続化の安全性、結果・例外の挙動、ソース形式の画像処理プラグインの例を確認します。" |
| LEGACY-TEST-004 | pre-reorg/v3/testing-and-migration.md; "テストと移行" | "v3のテストは、設定値の受理、CLIの振り分け・JSON・秘匿化、プラグインパッケージの信頼、ライフサイクル・認証・回復、通信・永続化の安全性、結果・例外の挙動、ソース形式の画像処理プラグインの例を確認します。" |
| LEGACY-TEST-005 | pre-reorg/v3/testing-and-migration.md; "テストと移行" | "v3のテストは、設定値の受理、CLIの振り分け・JSON・秘匿化、プラグインパッケージの信頼、ライフサイクル・認証・回復、通信・永続化の安全性、結果・例外の挙動、ソース形式の画像処理プラグインの例を確認します。" |
| LEGACY-TEST-006 | pre-reorg/v3/testing-and-migration.md; "テストと移行" | "v3のテストは、設定値の受理、CLIの振り分け・JSON・秘匿化、プラグインパッケージの信頼、ライフサイクル・認証・回復、通信・永続化の安全性、結果・例外の挙動、ソース形式の画像処理プラグインの例を確認します。" |
| LEGACY-RELEASE-001 | pre-reorg/v3/release-checklist.md; "公開ベータ確認事項" | "v3のテストは、設定値の受理、CLIの振り分け・JSON・秘匿化、プラグインパッケージの信頼、ライフサイクル・認証・回復、通信・永続化の安全性、結果・例外の挙動、ソース形式の画像処理プラグインの例を確認します。" |
| LEGACY-RELEASE-002 | pre-reorg/v3/release-checklist.md; "公開ベータ確認事項" | "v3のテストは、設定値の受理、CLIの振り分け・JSON・秘匿化、プラグインパッケージの信頼、ライフサイクル・認証・回復、通信・永続化の安全性、結果・例外の挙動、ソース形式の画像処理プラグインの例を確認します。" |
| LEGACY-OPS-001 | pre-reorg/v3/trust-and-operations.md; "信頼・配布・運用" | "ログの契約は&lbrack;ログリファレンス&rbrack;(logging.md)を正本とします。" |
| LEGACY-OPS-002 | pre-reorg/v3/trust-and-operations.md; "信頼・配布・運用" | "config 作成・rewrite の command ごとの副作用は &lbrack;設定リファレンス&rbrack;(configuration.md#config-layers) が正本である。`config path` と `config explain` は観測専用である。" |
| TAX-CLI-001 | pre-taxonomy/cli-reference.md; "CLI 参照" | "`--json`は全コマンドで受理します。**受理される指定でも効果があるとは限りません**。適用できないオプションは、設定解決前に`ArgumentError`となります。オプションはコマンド前、サブコマンド間、位置引数の後に置くことができ、受理条件と診断の優先順位は同じです。完全なオプション名と文書化した別名だけを受理し、`--j`や`--list`のような省略形は拒否します。" |
| TAX-CLI-002 | pre-taxonomy/cli-reference.md; "CLI 参照" | "`--json`は全コマンドで受理します。**受理される指定でも効果があるとは限りません**。適用できないオプションは、設定解決前に`ArgumentError`となります。オプションはコマンド前、サブコマンド間、位置引数の後に置くことができ、受理条件と診断の優先順位は同じです。完全なオプション名と文書化した別名だけを受理し、`--j`や`--list`のような省略形は拒否します。" |
| TAX-CLI-003 | pre-taxonomy/configuration-and-cli.md; "CLI と設定" | "処理済みのJSONエラー" |
| TAX-CLI-004 | pre-taxonomy/cli-reference.md; "CLI 参照" | "`--json`は全コマンドで受理します。**受理される指定でも効果があるとは限りません**。適用できないオプションは、設定解決前に`ArgumentError`となります。オプションはコマンド前、サブコマンド間、位置引数の後に置くことができ、受理条件と診断の優先順位は同じです。完全なオプション名と文書化した別名だけを受理し、`--j`や`--list`のような省略形は拒否します。" |
| TAX-CONFIG-001 | pre-taxonomy/configuration-reference.md; "設定参照" | "設定スキーマ" |
| TAX-CONFIG-002 | pre-taxonomy/configuration-reference.md; "設定参照" | "`AppConfig` / `validate_config` への直接入力では unknown field を拒否する。" |
| TAX-CONFIG-003 | pre-taxonomy/configuration-reference.md; "設定参照" | "manifest `config_file` が指す author-default YAML（固定名ではない）、persistent `plugin_settings.<id>.config`、operation override はこの順に deep merge する。author-default file の root key は `config` のみである。disabled site plugin は selection candidate ではない。chain 上の disabled processor は error でなく skip される。processor に `secrets` を置くことは 設定エラー である。" |
| TAX-PLUGIN-001 | pre-taxonomy/plugin-development-reference.md; "Plugin 開発参照" | "`plugin-metadata.json`は付属の署名ツールが任意で受け取る入力であり、実行時には読みません。別の方法で完全なmanifestを生成・署名する場合、このファイルは不要です。付属の`tools/sign_local_site_plugin.py`は、メタデータの`kind`に応じてサイト用と画像プロセッサー用の両方を署名できます。署名ツールが読むメタデータは、未知のフィールドもフィールドの欠落も許さない厳密なオブジェクトです。" |
| TAX-PLUGIN-002 | pre-taxonomy/plugin-development-reference.md; "Plugin 開発参照" | "この文書はプラグインフックの入出力、権限、例外の正本です。呼出し順・回数・並行性は&lbrack;実行ライフサイクル&rbrack;(../explanation/execution-lifecycle.md)を参照してください。" |
| TAX-PLUGIN-003 | pre-taxonomy/plugin-development-reference.md; "Plugin 開発参照" | "この文書はプラグインフックの入出力、権限、例外の正本です。呼出し順・回数・並行性は&lbrack;実行ライフサイクル&rbrack;(../explanation/execution-lifecycle.md)を参照してください。" |
| TAX-RUNTIME-001 | pre-taxonomy/execution-lifecycle.md; "実行ライフサイクルとデータフロー" | "`DownloadService.download()` と `check_updates()` は同一 service で直列化される。chapter は `download.chapter_concurrency`、image は chapter ごとの `download.image_concurrency_per_chapter` まで並行する。したがって `create_image_request` と site `transform_image` は並行呼出に安全でなければならない。configured processor は **同一 processor instance ごとに runtime lock で直列化** されるが、global mutable state や別 service instance との共有には依存してはならない。" |
| TAX-RUNTIME-002 | pre-taxonomy/configuration-and-cli.md; "CLI と設定" | "plugin は raw HTTP client を作らず `await context.requests.execute(RequestSpec(...))` を使う。core が timeout、pool、cookie、redirect、authentication、origin/domain concurrency、rate limit を管理する。`RequestSpec`、`RequestResponse`、`ImageResource` は immutable DTO であり、header/query を変える場合は新しい value を返す。" |
| TAX-COOKIE-001 | pre-taxonomy/configuration-and-cli.md; "CLI と設定" | "profile cookie" |
| TAX-STATE-001 | pre-taxonomy/distribution-and-operations.md; "配布・信頼・運用" | "profile state" |
| TAX-LIB-001 | pre-taxonomy/api-reference.md; "完全 API 参照" | "値オブジェクト・結果・プロトコル" |
| TAX-LIB-002 | pre-taxonomy/api-reference.md; "完全 API 参照" | "`image_downloader.observability.logging.__all__`は安定した公開APIです。ログは診断用です。結果の判定には`DownloadResult`、例外、CLIの終了コードを使ってください。" |
