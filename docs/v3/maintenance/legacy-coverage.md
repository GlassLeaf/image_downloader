# v3 corpus claim coverage ledger

この ledger は [pre-reorg snapshot](../../../archive/docs/legacy/v3-pre-reorg/README.md) と [pre-taxonomy snapshot](../../../archive/docs/legacy/v3-pre-taxonomy/README.md) を source corpus とする。implementation が最終正本であり、各行は source fragment、正規化した requirement、実装根拠、canonical destination を記録する。`carried` は同義の移管、`merged` は統合、`superseded` は旧記述を再導入せず理由と現行 destination を示す。

Source file shorthand: `pre-reorg` は historical snapshot、`pre-taxonomy` は再編開始時点の現行文書である。行内の source fragment は、source document の当該小節を再検索できる識別可能な短い引用である。

| claim prefix | behavior-test evidence |
| --- | --- |
| `LEGACY-API`, `LEGACY-CONFIG`, `TAX-CONFIG` | `tests/v3/configuration/` と `tests/v3/cli/` |
| `LEGACY-PLUGIN`, `LEGACY-SITE`, `TAX-PLUGIN` | `tests/v3/plugins/` |
| `LEGACY-LIBRARY`, `TAX-LIB`, `TAX-RUNTIME` | `tests/v3/application/` と `tests/v3/distribution/test_public_api_and_docs.py` |
| `LEGACY-OPS`, `TAX-COOKIE`, `TAX-STATE` | `tests/v3/observability/`、`tests/v3/storage_output/`、`tests/v3/transport/` |
| `LEGACY-TEST`, `LEGACY-RELEASE`, `LEGACY-INDEX`, `TAX-CLI` | `tests/v3/distribution/` と該当 command test |

## Repeated pre-taxonomy source claims

pre-taxonomy corpus は直前の flat reference であり、次の claim set は historical source と同じ requirement を繰り返している。重複を別 ID にして二重の正本を作らないため、対応する `LEGACY-*` 行がその source fragment と destination を兼ねる。次節の `TAX-*` はこの対応では表せない固有の訂正または追加確認である。

| pre-taxonomy source | covered atomic claims |
| --- | --- |
| `docs/v3/README.md`, root/docs/template/source README | `LEGACY-INDEX-001`, `LEGACY-API-007`, `LEGACY-PLUGIN-016`–`017`, `LEGACY-OPS-002` |
| `api-contract-inventory.md`, `api-reference.md`, `library-api.md` | `LEGACY-LIBRARY-001`–`008`, `LEGACY-SITE-001`, `TAX-LIB-001`–`002` |
| `cli-reference.md`, `configuration-and-cli.md` | `LEGACY-CONFIG-001`–`023`, `TAX-CLI-001`–`004`, `TAX-COOKIE-001`, `TAX-RUNTIME-002` |
| `configuration-reference.md` | `LEGACY-API-001`–`002`, `LEGACY-CONFIG-003`–`018`, `TAX-CONFIG-001`–`003` |
| `plugin-author-guide.md`, `plugin-development-reference.md` | `LEGACY-API-003`–`005`, `LEGACY-PLUGIN-001`–`017`, `LEGACY-SITE-002`–`004`, `TAX-PLUGIN-001`–`003` |
| `execution-lifecycle.md` | `LEGACY-LIBRARY-003`–`007`, `LEGACY-SITE-002`–`004`, `TAX-RUNTIME-001` |
| `distribution-and-operations.md` | `LEGACY-OPS-001`–`002`, `LEGACY-PLUGIN-005`–`007`, `TAX-STATE-001` |
| `testing-migration-and-release.md` | `LEGACY-TEST-001`–`006`, `LEGACY-RELEASE-001`–`002` |



| ID | source section / quotation fragment | normalized requirement | status | implementation evidence | canonical destination / rationale |
| --- | --- | --- | --- | --- | --- |
| LEGACY-API-001 | pre-reorg/plugin-api-v3.md; "Local plugin API v3" | plugin API v3 configuration tree | carried | `src/image_downloader/configuration/layers.py` | [configuration layers](../reference/configuration.md#config-layers) |
| LEGACY-API-002 | pre-reorg/plugin-api-v3.md; “fixed user config and package baseline” | fixed user config and package baseline | carried | `src/image_downloader/configuration/layers.py` | [discovery](../reference/configuration.md#config-discovery) |
| LEGACY-API-003 | pre-reorg/plugin-api-v3.md; “directory unit layout” | directory unit layout | carried | `src/image_downloader/configuration/layers.py` | [unit layout](../reference/plugin-package.md#plugin-layout) |
| LEGACY-API-004 | pre-reorg/plugin-api-v3.md; “manifest wrapper is signed” | manifest wrapper is signed | carried | `src/image_downloader/configuration/layers.py` | [manifest](../reference/plugin-package.md#plugin-manifest) |
| LEGACY-API-005 | pre-reorg/plugin-api-v3.md; “catalog trust controls load” | catalog trust controls load | carried | `src/image_downloader/configuration/layers.py` | [catalog](../reference/plugin-package.md#plugin-catalog) |
| LEGACY-API-006 | pre-reorg/plugin-api-v3.md; “RuntimeComposer/DownloadService ownership” | RuntimeComposer/DownloadService ownership | carried | `src/image_downloader/configuration/layers.py` | [public methods](../reference/library-api.md#api-public-methods) |
| LEGACY-API-007 | pre-reorg/plugin-api-v3.md; “v2 descriptors/trees are not loaded” | v2 descriptors/trees are not loaded | carried | `src/image_downloader/configuration/layers.py` | [configuration migration](../reference/configuration.md#config-migration) |
| LEGACY-INDEX-001 | pre-reorg/v3/README.md; “v3 documentation reader routing” | v3 documentation reader routing | merged | `docs/v3/README.md` | [current entry](../README.md#docs-entry) |
| LEGACY-CONFIG-001 | pre-reorg/v3/configuration-and-cli.md; “minimum download invocation” | minimum download invocation | carried | `src/image_downloader/commands` | [canonical forms](../reference/cli.md#cli-forms) |
| LEGACY-CONFIG-002 | pre-reorg/v3/configuration-and-cli.md; “automatic config bootstrap side effect” | automatic config bootstrap side effect | carried | `src/image_downloader/commands` | [state effects](../reference/cli.md#cli-state-effects) |
| LEGACY-CONFIG-003 | pre-reorg/v3/configuration-and-cli.md; “layer precedence is deterministic” | layer precedence is deterministic | carried | `src/image_downloader/commands` | [discovery](../reference/configuration.md#config-discovery) |
| LEGACY-CONFIG-004 | pre-reorg/v3/configuration-and-cli.md; “mapping deep merge and scalar/list replacement” | mapping deep merge and scalar/list replacement | carried | `src/image_downloader/commands` | [discovery](../reference/configuration.md#config-discovery) |
| LEGACY-CONFIG-005 | pre-reorg/v3/configuration-and-cli.md; “profile/site overlay restrictions” | profile/site overlay restrictions | carried | `src/image_downloader/commands` | [discovery](../reference/configuration.md#config-discovery) |
| LEGACY-CONFIG-006 | pre-reorg/v3/configuration-and-cli.md; “`profile.default` schema” | `profile.default` schema | carried | `src/image_downloader/commands` | [schema](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-007 | pre-reorg/v3/configuration-and-cli.md; “storage/plugins roots must be absolute or null” | storage/plugins roots must be absolute or null | carried | `src/image_downloader/commands` | [discovery](../reference/configuration.md#config-discovery) |
| LEGACY-CONFIG-008 | pre-reorg/v3/configuration-and-cli.md; “download concurrency/error schema” | download concurrency/error schema | carried | `src/image_downloader/commands` | [schema](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-009 | pre-reorg/v3/configuration-and-cli.md; “output format/collision/lock schema” | output format/collision/lock schema | carried | `src/image_downloader/commands` | [schema](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-010 | pre-reorg/v3/configuration-and-cli.md; “media validation and pixel limit” | media validation and pixel limit | carried | `src/image_downloader/commands` | [schema](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-011 | pre-reorg/v3/configuration-and-cli.md; “logging safe parameter schema” | logging safe parameter schema | carried | `src/image_downloader/commands` | [schema](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-012 | pre-reorg/v3/configuration-and-cli.md; “request/origin/domain concurrency” | request/origin/domain concurrency | carried | `src/image_downloader/commands` | [schema](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-013 | pre-reorg/v3/configuration-and-cli.md; “timeout/retry/auth refresh schema” | timeout/retry/auth refresh schema | carried | `src/image_downloader/commands` | [schema](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-014 | pre-reorg/v3/configuration-and-cli.md; “pool/response-limit/HTTP schema” | pool/response-limit/HTTP schema | carried | `src/image_downloader/commands` | [schema](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-015 | pre-reorg/v3/configuration-and-cli.md; “notification/email schema” | notification/email schema | carried | `src/image_downloader/commands` | [schema](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-016 | pre-reorg/v3/configuration-and-cli.md; “verification/fallback/processor schema” | verification/fallback/processor schema | carried | `src/image_downloader/commands` | [schema](../reference/configuration.md#config-schema) |
| LEGACY-CONFIG-017 | pre-reorg/v3/configuration-and-cli.md; “plugin settings merge/enabled/secrets rules” | plugin settings merge/enabled/secrets rules | carried | `src/image_downloader/commands` | [plugin settings](../reference/configuration.md#config-plugin-settings) |
| LEGACY-CONFIG-018 | pre-reorg/v3/configuration-and-cli.md; “legacy network key replacements” | legacy network key replacements | carried | `src/image_downloader/commands` | [migration](../reference/configuration.md#config-migration) |
| LEGACY-CONFIG-019 | pre-reorg/v3/configuration-and-cli.md; “cookie export/import keeps secrets private” | cookie export/import keeps secrets private | carried | `src/image_downloader/commands` | [success JSON](../reference/cli.md#cli-success-json) |
| LEGACY-CONFIG-020 | pre-reorg/v3/configuration-and-cli.md; “global and per-command option applicability” | global and per-command option applicability | carried | `src/image_downloader/commands` | [options](../reference/cli.md#cli-options) |
| LEGACY-CONFIG-021 | pre-reorg/v3/configuration-and-cli.md; “exit-code meanings” | exit-code meanings | carried | `src/image_downloader/commands` | [exit status](../reference/cli.md#cli-exit-status) |
| LEGACY-CONFIG-022 | pre-reorg/v3/configuration-and-cli.md; “JSON success/error/redaction contract” | JSON success/error/redaction contract | merged | `src/image_downloader/commands` | [success JSON](../reference/cli.md#cli-success-json) |
| LEGACY-CONFIG-023 | pre-reorg/v3/configuration-and-cli.md; “error optional fields were shown as null placeholders” | error optional fields were shown as null placeholders | superseded | `src/image_downloader/commands` | Current dispatch omits unavailable `response_url`/`http_status`/`output_path`; see [errors](../reference/cli.md#cli-errors). |
| LEGACY-PLUGIN-001 | pre-reorg/v3/plugin-author-guide.md; “author config only has `config` root key” | author config only has `config` root key | carried | `src/image_downloader/plugins` | [unit layout](../reference/plugin-package.md#plugin-layout) |
| LEGACY-PLUGIN-002 | pre-reorg/v3/plugin-author-guide.md; “signer metadata is runtime-independent input” | signer metadata is runtime-independent input | carried | `src/image_downloader/plugins` | [unit layout](../reference/plugin-package.md#plugin-layout) |
| LEGACY-PLUGIN-003 | pre-reorg/v3/plugin-author-guide.md; “manifest exact fields and ID/path rules” | manifest exact fields and ID/path rules | carried | `src/image_downloader/plugins` | [manifest](../reference/plugin-package.md#plugin-manifest) |
| LEGACY-PLUGIN-004 | pre-reorg/v3/plugin-author-guide.md; “file-tree/link/cache rules” | file-tree/link/cache rules | carried | `src/image_downloader/plugins` | [manifest](../reference/plugin-package.md#plugin-manifest) |
| LEGACY-PLUGIN-005 | pre-reorg/v3/plugin-author-guide.md; “catalog normal/content pins” | catalog normal/content pins | carried | `src/image_downloader/plugins` | [catalog](../reference/plugin-package.md#plugin-catalog) |
| LEGACY-PLUGIN-006 | pre-reorg/v3/plugin-author-guide.md; “verification modes and bypass semantics” | verification modes and bypass semantics | carried | `src/image_downloader/plugins` | [catalog](../reference/plugin-package.md#plugin-catalog) |
| LEGACY-PLUGIN-007 | pre-reorg/v3/plugin-author-guide.md; “install/trust/revoke/uninstall safety” | install/trust/revoke/uninstall safety | carried | `src/image_downloader/plugins` | [signing](../reference/plugin-package.md#plugin-signing) |
| LEGACY-PLUGIN-008 | pre-reorg/v3/plugin-author-guide.md; “selection priority and tie failure” | selection priority and tie failure | carried | `src/image_downloader/plugins` | [selection](../reference/plugin-hooks.md#plugin-selection) |
| LEGACY-PLUGIN-009 | pre-reorg/v3/plugin-author-guide.md; “config-aware matching rules” | config-aware matching rules | carried | `src/image_downloader/plugins` | [selection](../reference/plugin-hooks.md#plugin-selection) |
| LEGACY-PLUGIN-010 | pre-reorg/v3/plugin-author-guide.md; “all site hook return contracts” | all site hook return contracts | carried | `src/image_downloader/plugins` | [hooks](../reference/plugin-hooks.md#plugin-hooks) |
| LEGACY-PLUGIN-011 | pre-reorg/v3/plugin-author-guide.md; “image processor transform and cleanup” | image processor transform and cleanup | carried | `src/image_downloader/plugins` | [hooks](../reference/plugin-hooks.md#plugin-hooks) |
| LEGACY-PLUGIN-012 | pre-reorg/v3/plugin-author-guide.md; “context capabilities/immutable mappings” | context capabilities/immutable mappings | carried | `src/image_downloader/plugins` | [hooks](../reference/plugin-hooks.md#plugin-hooks) |
| LEGACY-PLUGIN-013 | pre-reorg/v3/plugin-author-guide.md; “API pagination must complete in inspect” | API pagination must complete in inspect | carried | `src/image_downloader/plugins` | [hooks](../reference/plugin-hooks.md#plugin-hooks) |
| LEGACY-PLUGIN-014 | pre-reorg/v3/plugin-author-guide.md; “signed URLs use request/recovery hooks” | signed URLs use request/recovery hooks | carried | `src/image_downloader/plugins` | [hooks](../reference/plugin-hooks.md#plugin-hooks) |
| LEGACY-PLUGIN-015 | pre-reorg/v3/plugin-author-guide.md; “AuthFlow origin scope and secret errors” | AuthFlow origin scope and secret errors | carried | `src/image_downloader/plugins` | [hooks](../reference/plugin-hooks.md#plugin-hooks) |
| LEGACY-PLUGIN-016 | pre-reorg/v3/plugin-author-guide.md; “unsupported browser/interactive features” | unsupported browser/interactive features | carried | `src/image_downloader/plugins` | [examples](../reference/plugin-hooks.md#plugin-examples) |
| LEGACY-PLUGIN-017 | pre-reorg/v3/plugin-author-guide.md; “plugin author test acceptance” | plugin author test acceptance | carried | `src/image_downloader/plugins` | [plugin tests](testing.md#testing-package-trust) |
| LEGACY-LIBRARY-001 | pre-reorg/v3/library-api.md; “stable facade imports are contractual” | stable facade imports are contractual | carried | `src/image_downloader/runtime.py` | [API inventory](../reference/api-contract-inventory.md#api-inventory) |
| LEGACY-LIBRARY-002 | pre-reorg/v3/library-api.md; “compose/config loading and rewrite behavior” | compose/config loading and rewrite behavior | merged | `src/image_downloader/runtime.py` | [configuration facade](../reference/library-api.md#api-configuration) |
| LEGACY-LIBRARY-003 | pre-reorg/v3/library-api.md; “run/update serialisation and close” | run/update serialisation and close | carried | `src/image_downloader/runtime.py` | [public methods](../reference/library-api.md#api-public-methods) |
| LEGACY-LIBRARY-004 | pre-reorg/v3/library-api.md; “operation override shape” | operation override shape | carried | `src/image_downloader/runtime.py` | [public methods](../reference/library-api.md#api-public-methods) |
| LEGACY-LIBRARY-005 | pre-reorg/v3/library-api.md; “result DTO fields and failure correlation” | result DTO fields and failure correlation | carried | `src/image_downloader/runtime.py` | [download result](../reference/library-api.md#api-download-result) |
| LEGACY-LIBRARY-006 | pre-reorg/v3/library-api.md; “errors vs image outcome failures/cancellation” | errors vs image outcome failures/cancellation | carried | `src/image_downloader/runtime.py` | [exception catalog](../reference/library-api.md#api-errors) |
| LEGACY-LIBRARY-007 | pre-reorg/v3/library-api.md; “dependency injection boundary” | dependency injection boundary | carried | `src/image_downloader/runtime.py` | [runtime components](../reference/library-api.md#api-public-methods) |
| LEGACY-LIBRARY-008 | pre-reorg/v3/library-api.md; “internal modules are not stable” | internal modules are not stable | carried | `src/image_downloader/runtime.py` | [common rules](../reference/library-api.md#api-common) |
| LEGACY-SITE-001 | pre-reorg/v3/site-plugin-integration-guide.md; “RequestSpec body/auth/retry fields” | RequestSpec body/auth/retry fields | carried | `src/image_downloader/plugins/lifecycle.py` | [request value object](../reference/library-api.md#api-common) |
| LEGACY-SITE-002 | pre-reorg/v3/site-plugin-integration-guide.md; “image URL and Referer handling” | image URL and Referer handling | merged | `src/image_downloader/plugins/lifecycle.py` | [hooks](../reference/plugin-hooks.md#plugin-hooks) |
| LEGACY-SITE-003 | pre-reorg/v3/site-plugin-integration-guide.md; “concrete HTML/API/auth examples” | concrete HTML/API/auth examples | carried | `src/image_downloader/plugins/lifecycle.py` | [examples](../reference/plugin-hooks.md#plugin-examples) |
| LEGACY-SITE-004 | pre-reorg/v3/site-plugin-integration-guide.md; “auth refresh and origin rules” | auth refresh and origin rules | carried | `src/image_downloader/plugins/lifecycle.py` | [hooks](../reference/plugin-hooks.md#plugin-hooks) |
| LEGACY-SITE-005 | pre-reorg/v3/site-plugin-integration-guide.md; “network limits and response size” | network limits and response size | carried | `src/image_downloader/plugins/lifecycle.py` | [schema](../reference/configuration.md#config-schema) |
| LEGACY-TEST-001 | pre-reorg/v3/testing-and-migration.md; “configuration acceptance matrix” | configuration acceptance matrix | carried | `tests/v3` | [configuration tests](testing.md#testing-config-cli) |
| LEGACY-TEST-002 | pre-reorg/v3/testing-and-migration.md; “manifest/catalog acceptance matrix” | manifest/catalog acceptance matrix | carried | `tests/v3` | [plugin trust tests](testing.md#testing-package-trust) |
| LEGACY-TEST-003 | pre-reorg/v3/testing-and-migration.md; “update state schema migration” | update state schema migration | carried | `tests/v3` | [migration](testing.md#testing-migration) |
| LEGACY-TEST-004 | pre-reorg/v3/testing-and-migration.md; “selection/context/operation acceptance” | selection/context/operation acceptance | carried | `tests/v3` | [runtime tests](testing.md#testing-runtime-library) |
| LEGACY-TEST-005 | pre-reorg/v3/testing-and-migration.md; “doctor acceptance matrix” | doctor acceptance matrix | carried | `tests/v3` | [configuration tests](testing.md#testing-config-cli) |
| LEGACY-TEST-006 | pre-reorg/v3/testing-and-migration.md; “v2 manual migration and operations checklist” | v2 manual migration and operations checklist | carried | `tests/v3` | [migration](testing.md#testing-migration) |
| LEGACY-RELEASE-001 | pre-reorg/v3/release-checklist.md; “release preflight checks” | release preflight checks | carried | `pyproject.toml and .github` | [release](testing.md#testing-release) |
| LEGACY-RELEASE-002 | pre-reorg/v3/release-checklist.md; “release stop conditions” | release stop conditions | carried | `pyproject.toml and .github` | [release](testing.md#testing-release) |
| LEGACY-OPS-001 | pre-reorg/v3/trust-and-operations.md; “privacy-safe logging” | privacy-safe logging | carried | `src/image_downloader/observability` | [logging facade](../reference/library-api.md#api-logging) |
| LEGACY-OPS-002 | pre-reorg/v3/trust-and-operations.md; “doctor diagnostics and operator recovery” | doctor diagnostics and operator recovery | carried | `src/image_downloader/observability` | [state effects](../reference/cli.md#cli-state-effects) |

## Pre-taxonomy correction and preservation claims

この table は taxonomy 開始時点の `docs/v3` と README から再検証した、誤仕様を再導入しやすい claim である。`TAX-*` も legacy 行と同じ検証対象にする。

| ID | source section / quotation fragment | normalized requirement | status | implementation evidence | canonical destination / rationale |
| --- | --- | --- | --- | --- | --- |
| TAX-CLI-001 | pre-taxonomy/cli-reference.md; “`--plugin-config-file`” | plugin config file is an exact JSON object and file entries precede inline overrides | carried | `src/image_downloader/commands/setup.py:_runtime_overrides` | [option contract](../reference/cli.md#cli-options) |
| TAX-CLI-002 | pre-taxonomy/cli-reference.md; “`--yes`” | `--yes` confirms only plugin-management mutation | carried | `src/image_downloader/commands/plugin.py:_confirm` | [option contract](../reference/cli.md#cli-options) |
| TAX-CLI-003 | pre-taxonomy/configuration-and-cli.md; “null placeholders” | unavailable JSON error fields are always emitted as null | superseded | `src/image_downloader/commands/reporting.py` | Dispatcher omits unknown error fields; see [error contract](../reference/cli.md#cli-errors). |
| TAX-CLI-004 | pre-taxonomy/cli-reference.md; “plugin config file” | YAML is accepted by `--plugin-config-file` | superseded | `src/image_downloader/commands/setup.py:_runtime_overrides` | The accepted format is JSON only; see [option contract](../reference/cli.md#cli-options). |
| TAX-CONFIG-001 | pre-taxonomy/configuration-reference.md; “notification” | all `NotificationCategory` literals and default notification routing remain documented | carried | `src/image_downloader/configuration/models.py:NotificationCategory` | [configuration schema](../reference/configuration.md#config-schema) |
| TAX-CONFIG-002 | pre-taxonomy/configuration-reference.md; “rewrite_user_layers” | resolve is read-only by default and rewrite may remove obsolete static user keys atomically | carried | `src/image_downloader/configuration/layers.py:resolve_application_config` | [layers and mutation](../reference/configuration.md#config-layers) |
| TAX-CONFIG-003 | pre-taxonomy/configuration-reference.md; “plugin secrets” | environment lookup precedes the per-plugin keyring lookup | carried | `src/image_downloader/credentials/plugin_secrets.py` | [secret resolution](../reference/configuration.md#config-secrets) |
| TAX-PLUGIN-001 | pre-taxonomy/plugin-development-reference.md; “plugin-metadata.json” | metadata is mandatory at runtime | superseded | `src/image_downloader/plugins/plugin_manifest.py` | Metadata is signer input only; see [package layout](../reference/plugin-package.md#plugin-layout). |
| TAX-PLUGIN-002 | pre-taxonomy/plugin-development-reference.md; “inspect” | `inspect` returns one finite, complete manifest and completes pagination without browser execution | carried | `src/image_downloader/plugins/lifecycle.py` | [hook contract](../reference/plugin-hooks.md#plugin-hooks) |
| TAX-PLUGIN-003 | pre-taxonomy/plugin-development-reference.md; “processor cleanup” | `aclose()` is preferred over `close()` and cleanup covers all terminal paths | carried | `src/image_downloader/plugins/lifecycle.py` | [processor cleanup](../reference/plugin-hooks.md#plugin-hooks) |
| TAX-RUNTIME-001 | pre-taxonomy/execution-lifecycle.md; “parallel processing” | configured processor instances are concurrently invoked for an operation | superseded | `src/image_downloader/media/artifact_pipeline.py` | A processor instance is serialized; see [lifecycle concurrency](../explanation/execution-lifecycle.md#lifecycle-concurrency). |
| TAX-RUNTIME-002 | pre-taxonomy/configuration-and-cli.md; “retry” | retry/redirect/size policy preserves cookie/referer/plugin headers across origins | superseded | `src/image_downloader/transport/httpx_transport.py` | Cross-origin redirects suppress those headers; see [HTTP transport](../reference/runtime-behavior.md#http-transport). |
| TAX-COOKIE-001 | pre-taxonomy/configuration-and-cli.md; “cookie storage” | encrypted cookie storage, keyring/passphrase, merge, and lock behavior remain specified | carried | `src/image_downloader/storage/cookies.py` | [cookie contract](../reference/runtime-behavior.md#runtime-cookies) |
| TAX-STATE-001 | pre-taxonomy/distribution-and-operations.md; “update state” | v2 state migration preserves `legacy_records` and compares complete snapshots under a lock | carried | `src/image_downloader/storage/state.py` | [update-state contract](../reference/runtime-behavior.md#runtime-update-state) |
| TAX-LIB-001 | pre-taxonomy/api-reference.md; “ImageFailure” | `ImageFailure` itself carries URL, chapter, and image index | superseded | `src/image_downloader/models.py:ImageFailure` | Correlation is only through `ImageOutcome.image`; see [result DTO](../reference/library-api.md#api-download-result). |
| TAX-LIB-002 | pre-taxonomy/api-reference.md; “DownloadLogger” | logger record/close operations are synchronous | superseded | `src/image_downloader/observability/logging.py` | Record and close methods are async; see [logging reference](../reference/logging.md#logging-reference). |

## Snapshot quotation and destination proof

The main ledger's normalized requirement is the atomic claim. This companion table
records a literal locating quotation from its source snapshot and a literal
visible phrase at the canonical destination. A quotation may be the source
document title when several atomic claims are derived from that document; it is
still checked against the immutable corpus. The destination proof is not a
second specification: it prevents a destination anchor from surviving after
the human-visible explanation is removed.

| ID | literal source quotation | literal destination proof |
| --- | --- | --- |
| LEGACY-API-001 | pre-reorg/plugin-api-v3.md; "Local plugin API v3" | "この文書は YAML 設定、layer 解決、path safety、secret reference の正本である。完全なコメント付き雛形は &lbrack;config-template.yaml&rbrack;(../../../src/image_downloader/config-template.yaml) である。unknown field は Pydantic validation が拒否する。" |
| LEGACY-API-002 | pre-reorg/plugin-api-v3.md; "Local plugin API v3" | "`DEFAULT_CONFIG`" |
| LEGACY-API-003 | pre-reorg/plugin-api-v3.md; "Local plugin API v3" | "`plugin-metadata.json` は bundled signer の任意入力であり runtime は読まない。別の方法で complete manifest を生成・署名する author は置く必要がない。bundled `tools/sign_local_site_plugin.py` は metadata の `kind` に従い site plugin と image processor plugin の両方を署名できる。signer が読む metadata は unknown/missing field を許さない exact object である。" |
| LEGACY-API-004 | pre-reorg/plugin-api-v3.md; "Local plugin API v3" | "Manifest and file tree" |
| LEGACY-API-005 | pre-reorg/plugin-api-v3.md; "Local plugin API v3" | "catalog path は常に `<plugin-root>/catalog.json`、root schema version は `1` であり unknown root/entry field は許可しない。normal pin は ID、kind、publisher、version、public key、key ID、manifest digest、file-tree digest、selection priority、revoked を持つ。content pin は `id`、`kind`、`manifest_digest`、`content_digest`、`selection_priority`、`revoked` だけを持ち、publisher/key fields は null placeholder ではない。" |
| LEGACY-API-006 | pre-reorg/plugin-api-v3.md; "Local plugin API v3" | "Runtime facade" |
| LEGACY-API-007 | pre-reorg/plugin-api-v3.md; "Local plugin API v3" | "Migration compatibility" |
| LEGACY-INDEX-001 | pre-reorg/v3/README.md; "local plugin API v3 ドキュメント" | "この directory は API v3 の現行正本である。文書は目的別に分かれる。設定値、型、入出力、例外は **Reference**、hook の順序・回数・並行性は **Execution lifecycle explanation** だけを正本とする。tutorial/how-to は規則を再定義せず、その anchor を参照する。" |
| LEGACY-CONFIG-001 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "Canonical command forms are:" |
| LEGACY-CONFIG-002 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "config 作成・rewrite の command ごとの副作用は &lbrack;configuration reference&rbrack;(configuration.md#config-layers) が正本である。`config path` と `config explain` は観測専用である。" |
| LEGACY-CONFIG-003 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "`DEFAULT_CONFIG`" |
| LEGACY-CONFIG-004 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "`DEFAULT_CONFIG`" |
| LEGACY-CONFIG-005 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "`DEFAULT_CONFIG`" |
| LEGACY-CONFIG-006 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "Schema" |
| LEGACY-CONFIG-007 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "`DEFAULT_CONFIG`" |
| LEGACY-CONFIG-008 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "Schema" |
| LEGACY-CONFIG-009 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "Schema" |
| LEGACY-CONFIG-010 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "Schema" |
| LEGACY-CONFIG-011 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "Schema" |
| LEGACY-CONFIG-012 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "Schema" |
| LEGACY-CONFIG-013 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "Schema" |
| LEGACY-CONFIG-014 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "Schema" |
| LEGACY-CONFIG-015 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "Schema" |
| LEGACY-CONFIG-016 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "Schema" |
| LEGACY-CONFIG-017 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "Plugin settings and secrets" |
| LEGACY-CONFIG-018 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "Migration compatibility" |
| LEGACY-CONFIG-019 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "Every successful `--json` command prints exactly one JSON object to stdout. download chapter logs are suppressed from stdout; diagnostic human errors use stderr. Plugin Python `print()` is redirected to stderr for JSON download. Native code that writes directly to an OS stdout descriptor is outside this control." |
| LEGACY-CONFIG-020 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "`--json` is accepted by every command. The parser also carries some global fields to every handler; **accepted** does not imply an effect. A handler rejects a listed incompatible option with `ConfigurationError`." |
| LEGACY-CONFIG-021 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "code" |
| LEGACY-CONFIG-022 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "Every successful `--json` command prints exactly one JSON object to stdout. download chapter logs are suppressed from stdout; diagnostic human errors use stderr. Plugin Python `print()` is redirected to stderr for JSON download. Native code that writes directly to an OS stdout descriptor is outside this control." |
| LEGACY-CONFIG-023 | pre-reorg/v3/configuration-and-cli.md; "設定と CLI" | "Handled JSON failure" |
| LEGACY-PLUGIN-001 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "`plugin-metadata.json` は bundled signer の任意入力であり runtime は読まない。別の方法で complete manifest を生成・署名する author は置く必要がない。bundled `tools/sign_local_site_plugin.py` は metadata の `kind` に従い site plugin と image processor plugin の両方を署名できる。signer が読む metadata は unknown/missing field を許さない exact object である。" |
| LEGACY-PLUGIN-002 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "`plugin-metadata.json` は bundled signer の任意入力であり runtime は読まない。別の方法で complete manifest を生成・署名する author は置く必要がない。bundled `tools/sign_local_site_plugin.py` は metadata の `kind` に従い site plugin と image processor plugin の両方を署名できる。signer が読む metadata は unknown/missing field を許さない exact object である。" |
| LEGACY-PLUGIN-003 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "Manifest and file tree" |
| LEGACY-PLUGIN-004 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "Manifest and file tree" |
| LEGACY-PLUGIN-005 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "catalog path は常に `<plugin-root>/catalog.json`、root schema version は `1` であり unknown root/entry field は許可しない。normal pin は ID、kind、publisher、version、public key、key ID、manifest digest、file-tree digest、selection priority、revoked を持つ。content pin は `id`、`kind`、`manifest_digest`、`content_digest`、`selection_priority`、`revoked` だけを持ち、publisher/key fields は null placeholder ではない。" |
| LEGACY-PLUGIN-006 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "catalog path は常に `<plugin-root>/catalog.json`、root schema version は `1` であり unknown root/entry field は許可しない。normal pin は ID、kind、publisher、version、public key、key ID、manifest digest、file-tree digest、selection priority、revoked を持つ。content pin は `id`、`kind`、`manifest_digest`、`content_digest`、`selection_priority`、`revoked` だけを持ち、publisher/key fields は null placeholder ではない。" |
| LEGACY-PLUGIN-007 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "Management policy" |
| LEGACY-PLUGIN-008 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "selection は enabled site unit の `matches_with_config()`（ある場合）と `matches()` を評価し、highest `selection_priority` を選ぶ。同じ最高 priority の複数 candidate は曖昧さとして `PluginError` にする。`matches_with_config` は private config にだけ依存でき、network/secret/filesystem access はしない。" |
| LEGACY-PLUGIN-009 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "selection は enabled site unit の `matches_with_config()`（ある場合）と `matches()` を評価し、highest `selection_priority` を選ぶ。同じ最高 priority の複数 candidate は曖昧さとして `PluginError` にする。`matches_with_config` は private config にだけ依存でき、network/secret/filesystem access はしない。" |
| LEGACY-PLUGIN-010 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "この文書は plugin hook の input/output、capability、例外の正本である。呼出順・回数・並行性は &lbrack;execution lifecycle&rbrack;(../explanation/execution-lifecycle.md) を使用する。" |
| LEGACY-PLUGIN-011 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "この文書は plugin hook の input/output、capability、例外の正本である。呼出順・回数・並行性は &lbrack;execution lifecycle&rbrack;(../explanation/execution-lifecycle.md) を使用する。" |
| LEGACY-PLUGIN-012 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "この文書は plugin hook の input/output、capability、例外の正本である。呼出順・回数・並行性は &lbrack;execution lifecycle&rbrack;(../explanation/execution-lifecycle.md) を使用する。" |
| LEGACY-PLUGIN-013 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "この文書は plugin hook の input/output、capability、例外の正本である。呼出順・回数・並行性は &lbrack;execution lifecycle&rbrack;(../explanation/execution-lifecycle.md) を使用する。" |
| LEGACY-PLUGIN-014 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "この文書は plugin hook の input/output、capability、例外の正本である。呼出順・回数・並行性は &lbrack;execution lifecycle&rbrack;(../explanation/execution-lifecycle.md) を使用する。" |
| LEGACY-PLUGIN-015 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "この文書は plugin hook の input/output、capability、例外の正本である。呼出順・回数・並行性は &lbrack;execution lifecycle&rbrack;(../explanation/execution-lifecycle.md) を使用する。" |
| LEGACY-PLUGIN-016 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "need" |
| LEGACY-PLUGIN-017 | pre-reorg/v3/plugin-author-guide.md; "plugin 作者ガイド" | "The v3 suite checks configuration acceptance, CLI dispatch/JSON/redaction, plugin package trust, lifecycle/auth/recovery, transport/persistence safety, result/exception behavior, and source processor examples." |
| LEGACY-LIBRARY-001 | pre-reorg/v3/library-api.md; "ライブラリ API" | "この inventory は stable facade の完全な名前一覧である。個々の引数、戻り値、例外は &lbrack;library API reference&rbrack;(library-api.md)、&lbrack;configuration reference&rbrack;(configuration.md)、&lbrack;CLI reference&rbrack;(cli.md)、&lbrack;logging reference&rbrack;(logging.md) に定義する。各 block は contract test が実装の <code>__all__</code> と一致することを確認する。" |
| LEGACY-LIBRARY-002 | pre-reorg/v3/library-api.md; "ライブラリ API" | "Runtime facade" |
| LEGACY-LIBRARY-003 | pre-reorg/v3/library-api.md; "ライブラリ API" | "Runtime facade" |
| LEGACY-LIBRARY-004 | pre-reorg/v3/library-api.md; "ライブラリ API" | "Runtime facade" |
| LEGACY-LIBRARY-005 | pre-reorg/v3/library-api.md; "ライブラリ API" | "Value objects, results, and protocols" |
| LEGACY-LIBRARY-006 | pre-reorg/v3/library-api.md; "ライブラリ API" | "Exceptions and caller handling" |
| LEGACY-LIBRARY-007 | pre-reorg/v3/library-api.md; "ライブラリ API" | "Runtime facade" |
| LEGACY-LIBRARY-008 | pre-reorg/v3/library-api.md; "ライブラリ API" | "Value objects, results, and protocols" |
| LEGACY-SITE-001 | pre-reorg/v3/site-plugin-integration-guide.md; "サイト plugin 統合・認証ガイド" | "Value objects, results, and protocols" |
| LEGACY-SITE-002 | pre-reorg/v3/site-plugin-integration-guide.md; "サイト plugin 統合・認証ガイド" | "この文書は plugin hook の input/output、capability、例外の正本である。呼出順・回数・並行性は &lbrack;execution lifecycle&rbrack;(../explanation/execution-lifecycle.md) を使用する。" |
| LEGACY-SITE-003 | pre-reorg/v3/site-plugin-integration-guide.md; "サイト plugin 統合・認証ガイド" | "need" |
| LEGACY-SITE-004 | pre-reorg/v3/site-plugin-integration-guide.md; "サイト plugin 統合・認証ガイド" | "この文書は plugin hook の input/output、capability、例外の正本である。呼出順・回数・並行性は &lbrack;execution lifecycle&rbrack;(../explanation/execution-lifecycle.md) を使用する。" |
| LEGACY-SITE-005 | pre-reorg/v3/site-plugin-integration-guide.md; "サイト plugin 統合・認証ガイド" | "Schema" |
| LEGACY-TEST-001 | pre-reorg/v3/testing-and-migration.md; "テストと移行" | "The v3 suite checks configuration acceptance, CLI dispatch/JSON/redaction, plugin package trust, lifecycle/auth/recovery, transport/persistence safety, result/exception behavior, and source processor examples." |
| LEGACY-TEST-002 | pre-reorg/v3/testing-and-migration.md; "テストと移行" | "The v3 suite checks configuration acceptance, CLI dispatch/JSON/redaction, plugin package trust, lifecycle/auth/recovery, transport/persistence safety, result/exception behavior, and source processor examples." |
| LEGACY-TEST-003 | pre-reorg/v3/testing-and-migration.md; "テストと移行" | "The v3 suite checks configuration acceptance, CLI dispatch/JSON/redaction, plugin package trust, lifecycle/auth/recovery, transport/persistence safety, result/exception behavior, and source processor examples." |
| LEGACY-TEST-004 | pre-reorg/v3/testing-and-migration.md; "テストと移行" | "The v3 suite checks configuration acceptance, CLI dispatch/JSON/redaction, plugin package trust, lifecycle/auth/recovery, transport/persistence safety, result/exception behavior, and source processor examples." |
| LEGACY-TEST-005 | pre-reorg/v3/testing-and-migration.md; "テストと移行" | "The v3 suite checks configuration acceptance, CLI dispatch/JSON/redaction, plugin package trust, lifecycle/auth/recovery, transport/persistence safety, result/exception behavior, and source processor examples." |
| LEGACY-TEST-006 | pre-reorg/v3/testing-and-migration.md; "テストと移行" | "The v3 suite checks configuration acceptance, CLI dispatch/JSON/redaction, plugin package trust, lifecycle/auth/recovery, transport/persistence safety, result/exception behavior, and source processor examples." |
| LEGACY-RELEASE-001 | pre-reorg/v3/release-checklist.md; "公開ベータ確認事項" | "The v3 suite checks configuration acceptance, CLI dispatch/JSON/redaction, plugin package trust, lifecycle/auth/recovery, transport/persistence safety, result/exception behavior, and source processor examples." |
| LEGACY-RELEASE-002 | pre-reorg/v3/release-checklist.md; "公開ベータ確認事項" | "The v3 suite checks configuration acceptance, CLI dispatch/JSON/redaction, plugin package trust, lifecycle/auth/recovery, transport/persistence safety, result/exception behavior, and source processor examples." |
| LEGACY-OPS-001 | pre-reorg/v3/trust-and-operations.md; "信頼・配布・運用" | "logging contract は &lbrack;logging reference&rbrack;(logging.md) が正本である。" |
| LEGACY-OPS-002 | pre-reorg/v3/trust-and-operations.md; "信頼・配布・運用" | "config 作成・rewrite の command ごとの副作用は &lbrack;configuration reference&rbrack;(configuration.md#config-layers) が正本である。`config path` と `config explain` は観測専用である。" |
| TAX-CLI-001 | pre-taxonomy/cli-reference.md; "CLI 参照" | "`--json` is accepted by every command. The parser also carries some global fields to every handler; **accepted** does not imply an effect. A handler rejects a listed incompatible option with `ConfigurationError`." |
| TAX-CLI-002 | pre-taxonomy/cli-reference.md; "CLI 参照" | "`--json` is accepted by every command. The parser also carries some global fields to every handler; **accepted** does not imply an effect. A handler rejects a listed incompatible option with `ConfigurationError`." |
| TAX-CLI-003 | pre-taxonomy/configuration-and-cli.md; "CLI と設定" | "Handled JSON failure" |
| TAX-CLI-004 | pre-taxonomy/cli-reference.md; "CLI 参照" | "`--json` is accepted by every command. The parser also carries some global fields to every handler; **accepted** does not imply an effect. A handler rejects a listed incompatible option with `ConfigurationError`." |
| TAX-CONFIG-001 | pre-taxonomy/configuration-reference.md; "設定参照" | "Schema" |
| TAX-CONFIG-002 | pre-taxonomy/configuration-reference.md; "設定参照" | "この文書は YAML 設定、layer 解決、path safety、secret reference の正本である。完全なコメント付き雛形は &lbrack;config-template.yaml&rbrack;(../../../src/image_downloader/config-template.yaml) である。unknown field は Pydantic validation が拒否する。" |
| TAX-CONFIG-003 | pre-taxonomy/configuration-reference.md; "設定参照" | "manifest `config_file` が指す author-default YAML（固定名ではない）、persistent `plugin_settings.<id>.config`、operation override はこの順に deep merge する。author-default file の root key は `config` のみである。disabled site plugin は selection candidate ではない。chain 上の disabled processor は error でなく skip される。processor に `secrets` を置くことは configuration error である。" |
| TAX-PLUGIN-001 | pre-taxonomy/plugin-development-reference.md; "Plugin 開発参照" | "`plugin-metadata.json` は bundled signer の任意入力であり runtime は読まない。別の方法で complete manifest を生成・署名する author は置く必要がない。bundled `tools/sign_local_site_plugin.py` は metadata の `kind` に従い site plugin と image processor plugin の両方を署名できる。signer が読む metadata は unknown/missing field を許さない exact object である。" |
| TAX-PLUGIN-002 | pre-taxonomy/plugin-development-reference.md; "Plugin 開発参照" | "この文書は plugin hook の input/output、capability、例外の正本である。呼出順・回数・並行性は &lbrack;execution lifecycle&rbrack;(../explanation/execution-lifecycle.md) を使用する。" |
| TAX-PLUGIN-003 | pre-taxonomy/plugin-development-reference.md; "Plugin 開発参照" | "この文書は plugin hook の input/output、capability、例外の正本である。呼出順・回数・並行性は &lbrack;execution lifecycle&rbrack;(../explanation/execution-lifecycle.md) を使用する。" |
| TAX-RUNTIME-001 | pre-taxonomy/execution-lifecycle.md; "実行ライフサイクルとデータフロー" | "`DownloadService.run()` と `check_updates()` は同一 service で直列化される。chapter は `download.chapter_concurrency`、image は chapter ごとの `download.image_concurrency_per_chapter` まで並行する。したがって `create_image_request` と site `transform_image` は並行呼出に安全でなければならない。configured processor は **同一 processor instance ごとに runtime lock で直列化** されるが、global mutable state や別 service instance との共有には依存してはならない。" |
| TAX-RUNTIME-002 | pre-taxonomy/configuration-and-cli.md; "CLI と設定" | "plugin は raw HTTP client を作らず `await context.requests.execute(RequestSpec(...))` を使う。core が timeout、pool、cookie、redirect、authentication、origin/domain concurrency、rate limit を管理する。`RequestSpec`、`RequestResponse`、`ImageResource` は immutable DTO であり、header/query を変える場合は新しい value を返す。" |
| TAX-COOKIE-001 | pre-taxonomy/configuration-and-cli.md; "CLI と設定" | "profile cookie" |
| TAX-STATE-001 | pre-taxonomy/distribution-and-operations.md; "配布・信頼・運用" | "profile state" |
| TAX-LIB-001 | pre-taxonomy/api-reference.md; "完全 API 参照" | "Value objects, results, and protocols" |
| TAX-LIB-002 | pre-taxonomy/api-reference.md; "完全 API 参照" | "`image_downloader.observability.logging.__all__` is stable. Logging is diagnostic only; use `DownloadResult`, exceptions, and CLI exit status as the result source of truth." |
