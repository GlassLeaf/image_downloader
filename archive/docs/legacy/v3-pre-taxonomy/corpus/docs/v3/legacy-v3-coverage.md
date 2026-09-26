# Legacy v3 claim coverage ledger

旧 v3 snapshot は [archive](../../archive/docs/legacy/v3-pre-reorg/README.md) に byte-preserving で保存するが、それだけでは現行仕様にならない。この ledger は snapshot にあった外部観測可能な normative claim を小さな ID で棚卸しし、現行正本の移管先を固定する。`carried` は同じ意味の記載、`merged` は複数旧 claim を一つの現行説明に統合、`superseded` は implementation と矛盾した旧記述である。全行の destination anchor は contract test が検証する。

| ID | source claim | status | destination / rationale |
| --- | --- | --- | --- |
| LEGACY-API-001 | plugin API v3 configuration tree | carried | [configuration layers](configuration-reference.md#config-layers) |
| LEGACY-API-002 | fixed user config and package baseline | carried | [discovery](configuration-reference.md#config-discovery) |
| LEGACY-API-003 | directory unit layout | carried | [unit layout](plugin-development-reference.md#plugin-layout) |
| LEGACY-API-004 | manifest wrapper is signed | carried | [manifest](plugin-development-reference.md#plugin-manifest) |
| LEGACY-API-005 | catalog trust controls load | carried | [catalog](plugin-development-reference.md#plugin-catalog) |
| LEGACY-API-006 | RuntimeComposer/DownloadService ownership | carried | [public methods](api-reference.md#api-public-methods) |
| LEGACY-API-007 | v2 descriptors/trees are not loaded | carried | [configuration migration](configuration-reference.md#config-migration) |
| LEGACY-INDEX-001 | v3 documentation reader routing | merged | [current entry](README.md#docs-entry) |
| LEGACY-CONFIG-001 | minimum download invocation | carried | [canonical forms](cli-reference.md#cli-forms) |
| LEGACY-CONFIG-002 | automatic config bootstrap side effect | carried | [state effects](cli-reference.md#cli-state-effects) |
| LEGACY-CONFIG-003 | layer precedence is deterministic | carried | [discovery](configuration-reference.md#config-discovery) |
| LEGACY-CONFIG-004 | mapping deep merge and scalar/list replacement | carried | [discovery](configuration-reference.md#config-discovery) |
| LEGACY-CONFIG-005 | profile/site overlay restrictions | carried | [discovery](configuration-reference.md#config-discovery) |
| LEGACY-CONFIG-006 | `profile.default` schema | carried | [schema](configuration-reference.md#config-schema) |
| LEGACY-CONFIG-007 | storage/plugins roots must be absolute or null | carried | [discovery](configuration-reference.md#config-discovery) |
| LEGACY-CONFIG-008 | download concurrency/error schema | carried | [schema](configuration-reference.md#config-schema) |
| LEGACY-CONFIG-009 | output format/collision/lock schema | carried | [schema](configuration-reference.md#config-schema) |
| LEGACY-CONFIG-010 | media validation and pixel limit | carried | [schema](configuration-reference.md#config-schema) |
| LEGACY-CONFIG-011 | logging safe parameter schema | carried | [schema](configuration-reference.md#config-schema) |
| LEGACY-CONFIG-012 | request/origin/domain concurrency | carried | [schema](configuration-reference.md#config-schema) |
| LEGACY-CONFIG-013 | timeout/retry/auth refresh schema | carried | [schema](configuration-reference.md#config-schema) |
| LEGACY-CONFIG-014 | pool/response-limit/HTTP schema | carried | [schema](configuration-reference.md#config-schema) |
| LEGACY-CONFIG-015 | notification/email schema | carried | [schema](configuration-reference.md#config-schema) |
| LEGACY-CONFIG-016 | verification/fallback/processor schema | carried | [schema](configuration-reference.md#config-schema) |
| LEGACY-CONFIG-017 | plugin settings merge/enabled/secrets rules | carried | [plugin settings](configuration-reference.md#config-plugin-settings) |
| LEGACY-CONFIG-018 | legacy network key replacements | carried | [migration](configuration-reference.md#config-migration) |
| LEGACY-CONFIG-019 | cookie export/import keeps secrets private | carried | [success JSON](cli-reference.md#cli-success-json) |
| LEGACY-CONFIG-020 | global and per-command option applicability | carried | [options](cli-reference.md#cli-options) |
| LEGACY-CONFIG-021 | exit-code meanings | carried | [exit status](cli-reference.md#cli-exit-status) |
| LEGACY-CONFIG-022 | JSON success/error/redaction contract | merged | [success JSON](cli-reference.md#cli-success-json) |
| LEGACY-CONFIG-023 | error optional fields were shown as null placeholders | superseded | Current dispatch omits unavailable `response_url`/`http_status`/`output_path`; see [errors](cli-reference.md#cli-errors). |
| LEGACY-PLUGIN-001 | author config only has `config` root key | carried | [unit layout](plugin-development-reference.md#plugin-layout) |
| LEGACY-PLUGIN-002 | signer metadata is runtime-independent input | carried | [unit layout](plugin-development-reference.md#plugin-layout) |
| LEGACY-PLUGIN-003 | manifest exact fields and ID/path rules | carried | [manifest](plugin-development-reference.md#plugin-manifest) |
| LEGACY-PLUGIN-004 | file-tree/link/cache rules | carried | [manifest](plugin-development-reference.md#plugin-manifest) |
| LEGACY-PLUGIN-005 | catalog normal/content pins | carried | [catalog](plugin-development-reference.md#plugin-catalog) |
| LEGACY-PLUGIN-006 | verification modes and bypass semantics | carried | [catalog](plugin-development-reference.md#plugin-catalog) |
| LEGACY-PLUGIN-007 | install/trust/revoke/uninstall safety | carried | [signing](plugin-development-reference.md#plugin-signing) |
| LEGACY-PLUGIN-008 | selection priority and tie failure | carried | [selection](plugin-development-reference.md#plugin-selection) |
| LEGACY-PLUGIN-009 | config-aware matching rules | carried | [selection](plugin-development-reference.md#plugin-selection) |
| LEGACY-PLUGIN-010 | all site hook return contracts | carried | [hooks](plugin-development-reference.md#plugin-hooks) |
| LEGACY-PLUGIN-011 | image processor transform and cleanup | carried | [hooks](plugin-development-reference.md#plugin-hooks) |
| LEGACY-PLUGIN-012 | context capabilities/immutable mappings | carried | [hooks](plugin-development-reference.md#plugin-hooks) |
| LEGACY-PLUGIN-013 | API pagination must complete in inspect | carried | [hooks](plugin-development-reference.md#plugin-hooks) |
| LEGACY-PLUGIN-014 | signed URLs use request/recovery hooks | carried | [hooks](plugin-development-reference.md#plugin-hooks) |
| LEGACY-PLUGIN-015 | AuthFlow origin scope and secret errors | carried | [hooks](plugin-development-reference.md#plugin-hooks) |
| LEGACY-PLUGIN-016 | unsupported browser/interactive features | carried | [examples](plugin-development-reference.md#plugin-examples) |
| LEGACY-PLUGIN-017 | plugin author test acceptance | carried | [plugin tests](plugin-development-reference.md#plugin-tests) |
| LEGACY-LIBRARY-001 | stable facade imports are contractual | carried | [API inventory](api-contract-inventory.md#api-inventory) |
| LEGACY-LIBRARY-002 | compose/config loading and rewrite behavior | merged | [configuration facade](api-reference.md#api-configuration) |
| LEGACY-LIBRARY-003 | run/update serialisation and close | carried | [public methods](api-reference.md#api-public-methods) |
| LEGACY-LIBRARY-004 | operation override shape | carried | [public methods](api-reference.md#api-public-methods) |
| LEGACY-LIBRARY-005 | result DTO fields and failure correlation | carried | [download result](api-reference.md#api-download-result) |
| LEGACY-LIBRARY-006 | errors vs image outcome failures/cancellation | carried | [exception catalog](api-reference.md#api-errors) |
| LEGACY-LIBRARY-007 | dependency injection boundary | carried | [runtime components](api-reference.md#api-public-methods) |
| LEGACY-LIBRARY-008 | internal modules are not stable | carried | [common rules](api-reference.md#api-common) |
| LEGACY-SITE-001 | RequestSpec body/auth/retry fields | carried | [request value object](api-reference.md#api-common) |
| LEGACY-SITE-002 | image URL and Referer handling | merged | [hooks](plugin-development-reference.md#plugin-hooks) |
| LEGACY-SITE-003 | concrete HTML/API/auth examples | carried | [examples](plugin-development-reference.md#plugin-examples) |
| LEGACY-SITE-004 | auth refresh and origin rules | carried | [hooks](plugin-development-reference.md#plugin-hooks) |
| LEGACY-SITE-005 | network limits and response size | carried | [schema](configuration-reference.md#config-schema) |
| LEGACY-TEST-001 | configuration acceptance matrix | carried | [configuration tests](testing-migration-and-release.md#testing-config-cli) |
| LEGACY-TEST-002 | manifest/catalog acceptance matrix | carried | [plugin trust tests](testing-migration-and-release.md#testing-package-trust) |
| LEGACY-TEST-003 | update state schema migration | carried | [migration](testing-migration-and-release.md#testing-migration) |
| LEGACY-TEST-004 | selection/context/operation acceptance | carried | [runtime tests](testing-migration-and-release.md#testing-runtime-library) |
| LEGACY-TEST-005 | doctor acceptance matrix | carried | [configuration tests](testing-migration-and-release.md#testing-config-cli) |
| LEGACY-TEST-006 | v2 manual migration and operations checklist | carried | [migration](testing-migration-and-release.md#testing-migration) |
| LEGACY-RELEASE-001 | release preflight checks | carried | [release](testing-migration-and-release.md#testing-release) |
| LEGACY-RELEASE-002 | release stop conditions | carried | [release](testing-migration-and-release.md#testing-release) |
| LEGACY-OPS-001 | privacy-safe logging | carried | [logging facade](api-reference.md#api-logging) |
| LEGACY-OPS-002 | doctor diagnostics and operator recovery | carried | [state effects](cli-reference.md#cli-state-effects) |

The source corpus is `plugin-api-v3.md`, `v3/README.md`, `v3/configuration-and-cli.md`, `v3/library-api.md`, `v3/plugin-author-guide.md`, `v3/site-plugin-integration-guide.md`, `v3/trust-and-operations.md`, `v3/testing-and-migration.md`, and `v3/release-checklist.md`. Snapshot root-index prose is preservation metadata rather than a current behavioral claim.
