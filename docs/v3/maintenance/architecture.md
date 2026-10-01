# Architecture and non-stable boundaries

Only package root and the six documented facades are stable. `configuration.*` resolves/validates YAML, `storage.*` owns trusted-root and interprocess safety, `transport.gateway` owns operation HTTP/auth, `plugins.*` owns discovery/verification/loading/selection, `media.*` owns artifact processing, `application.*` composes and runs operations, and `observability.*` owns diagnostics.

`RuntimeComposer` builds filesystem, state, event, notification, cookie, HTTP, image-processing, and logging dependencies. `DownloadService` coordinates their lifecycle. Direct construction of service internals and dependence on `service` implementation attributes are unsupported; `_RuntimeDependencies` is the deliberately stable advanced composition DTO.

Plugin modules are loaded into service-owned isolated namespaces. `PluginRecord` is an immutable metadata snapshot, not an imported module cache. loader/runtime/service close unloads its namespace without affecting a distinct service instance. These are maintenance explanations, not a promise that internal module paths remain import-compatible.

<a id="workflow-image-comparison-maintenance"></a>

## Workflow画像比較の保守方針

当面はworkflowの画像照合・再取得の動作仕様を維持し、比較条件と確定済み結果を保持する理由を
読み取りやすくする整理を優先する。現行の条件表は[runtime behavior](../reference/runtime-behavior.md#workflow-retry-rounds)、
利用者向けの説明は[FAQ](../how-to/faq.md#workflow-retry-faq)を正とする。

`application.workflow_retry.ImageLedger.begin()`は、同一pluginで、一意に照合できるchapter／画像について、
前回と今回の`ImageResource`全体をdataclassの等価比較で比較する。この一致と結果の再試行可否によって、
成功・skipおよび再試行対象外の失敗を保持する。これは画像本体の厳密な変更検知ではない。

| 条件を維持する観点 | 理由 |
| --- | --- |
| 成功成果物の保護 | 部分失敗の回復時に変更のない成功画像を再取得・再保存せず、`rename`の重複保存や`error`の保存衝突を抑える |
| 加工結果の正しさ | `metadata`や`index`はpluginの加工入力になり得るため、変化を無視しない |
| 取得条件の追随 | URL／locatorや`headers`／`referer`は要求・取得結果へ影響し得るため、変化を無視しない |
| 保存条件・名前の追随 | `save_options`と`original_filename`の変化も再取得対象とし、更新された指定で通常処理する。ただし保存結果は既存ファイル方針に従う |
| 既存pluginとの互換性 | 画像単位のrevision／hashを返さないpluginでも、画像情報が同じ成功結果を保持できる |

全体等価比較は短く書ける一方、`ImageResource`へフィールドを追加すると、dataclassの既定の
`compare=True`でそのフィールドも比較に参加する。結果として、明示的な判定分岐を追加しなくても、
その値の変化が再取得条件となる。DTOの拡張はworkflowの動作への影響もレビューする必要がある。

- フィールド追加時は、識別・取得・加工・保存のどこに影響する情報かを確認し、比較へ参加する意図を明記する。
- 比較対象の追加・除外は、公開動作への影響を確認し、条件表・FAQと必要な挙動テストを更新する。
- 比較を目的の分かる関数へ切り出す場合は、既存の全フィールド比較、曖昧な識別の扱い、結果の再試行可否を維持する。判定の整理に仕様変更を混ぜない。
- 保存だけに関わる条件を除外する案でも、pluginがその情報を加工入力として使っていないかを先に確認する。

保存条件・名前の比較除外や、比較可能な画像revision／hashの一致によるURL比較の省略は将来の検討対象であり、
現在の動作ではない。変更する場合は、それぞれ独立した仕様変更として扱う。

現行比較を維持するだけで加工結果の正しさが保証されるわけではない。manifest／chapterの情報や、
実要求・responseから得るrequest-time情報など、加工へ渡るすべての入力をこの画像比較で扱っているわけではない。
比較対象外の入力変更、同じ画像情報のままの本体変更、保存済み成果物の破損・削除を確認するには、
別途検証・設計が必要である。現行の制限は[runtime behavior](../reference/runtime-behavior.md#workflow-retry-rounds)を参照する。
