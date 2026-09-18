# v2（0.3.0）開発タスクと完了基準（履歴資料）

> 現行の v3 完了基準は [テストと移行](../../../../docs/v3/testing-and-migration.md) を参照してください。この文書の entry point/sidecar 要件は適用しません。

この文書はv2の実装済み範囲と、変更時に維持する受入基準を示す正本です。

| 領域 | 完了条件 |
|---|---|
| 公開API | rootから`AppConfig`、DTO、Protocol、`RuntimeComposer`、`DownloadService`を公開。実行APIは`run`、`check_updates`、`close`のみ。 |
| 設定・モデル | strict/deep-frozen Pydantic設定、frozen/slots DTO、`allow_empty_manifest=false`。 |
| plugin | class entry point、operationごとのinstance、priority/catalog選択、generic fallback、権限を絞ったcontext。 |
| 通信 | 総試行回数としてのretry、auth refresh集約、非認証画像4xx/5xxの一度だけのrequest回復。 |
| 実行・artifact | 章・画像並列、continue/fail-fast/cancel、site transform→正規化→processor chain→最終正規化。 |
| 出力・観測 | operation内予約、画像単位衝突方針、追記log、index順stream、mask済みdebug／通知。 |
| 更新 | 完全snapshot、`(plugin_id, content_id)`またはURL key、added/changed/removed、原子的state保存。 |
| 配布 | RFC 8785 JCS + Ed25519 sidecar、catalog pin、strict/warn/off、doctor終了コード。 |
| 移行 | v1公開API、adapter、旧entry pointを削除。v1/sidecarなしpluginはimport前に拒否。 |

変更時は次をすべて通過させます。

```powershell
python -m pytest -q --cov=image_downloader --cov-branch
python -m ruff check .
python -m ruff format --check .
python -m mypy src/image_downloader
image-downloader doctor --json
```

分岐カバレッジは75%以上を維持します。新しいsite pluginは[plugin-testing-guide.md](plugin-testing-guide.md)のfixture受入項目を追加します。

## 実装照合記録

0.3.0の完了判定は、概要文だけでなく次の実装fileとtestを一次資料として行います。

| 領域 | 実装 | 主な回帰test |
|---|---|---|
| root API・DTO | `src/image_downloader/__init__.py`, `models.py`, `ports.py` | `tests/v2/test_runtime_contract.py::test_frozen_models_and_context_capability` |
| config・CLI・Cookie | `config.py`, `cli.py`, `cookies.py` | `tests/v2/test_configuration_cli_cookie.py` |
| service・HTTP・artifact | `runtime.py`, `media/image_processor.py` | `test_download_result_empty_manifest_and_ordered_partial_failures`, `test_existing_file_modes_and_fail_fast_queue`, `test_auth_coalescing_and_single_recovery` |
| plugin trust | `security.py` | `tests/v2/test_security_and_updates.py`のcatalog path、署名、sidecar import前拒否、warn test |
| storage安全性 | `storage/filesystem.py` | atomic state/outputおよびpath安全性を使用するv2 test |
| update | `runtime.py::UpdateState`, `DownloadService.check_updates` | `test_update_snapshot_fallback_key_removal_and_atomic_state` |

## 変更時の必須確認

### 公開API

- `image_downloader.__all__`の追加・削除をAPIリファレンスへ反映する。
- 公開DTOをfrozen/slotsのまま保ち、mapping/listのfreeze挙動をtestする。
- `DownloadService`をasync context managerと明示closeの両方でtestする。
- close後のoperationとclose複数回の挙動を固定する。

### plugin contract

- sidecarを読む前にentry point payloadをimportしない。
- entry pointがclassであること、classを引数なし生成できることを確認する。
- descriptor IDとsigned manifest IDの不一致を拒否する。
- `PluginExecutionContext`からHTTP client、Cookie、filesystem、loggerへ到達する公開属性を追加しない。
- 戻り値型違反を画像failureへ丸めず`PluginError`として停止する。

### 並行実行とエラー

- result tupleはmanifest宣言順、章logは画像index順であることを別々に確認する。
- fail-fast時に上限を超えるqueued jobが開始されないことを確認する。
- 外部cancel時にactive taskとatomic write temporary fileが残らないことを確認する。
- continue対象をFETCH/PROCESS/SAVEへ限定し、認証・設定・plugin・storage安全性違反は全体停止とする。

### 文書同期

- `docs/api-reference.md`へ公開constructor、field、property、例外条件を記載する。
- `docs/plugin-api-reference.md`へProtocol signature、呼出し回数、前後条件、戻り値型違反を記載する。
- `docs/plugin-development-guide.md`のsampleを構文検査し、root importだけを使用する。
- `docs/user-guide.md`の既定値を`AppConfig()`および`app.yaml`と照合する。
- `docs/plugin-package-template.md`のsidecar/catalog fieldを`security.py`のschemaと照合する。

## 現在残る完了条件

- 必要になった場合のみ、入手可能な履歴を一次資料としてv1 API資料一式を`archive/docs/legacy/v1/`へ復元する。現行checkoutにはv1 source/APIがなく、互換実装を推測で復元しない。
