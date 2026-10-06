# 現行実装・ドキュメントの再レビュー

確認日: 2026-10-05。コードの基準コミットは `f26f6981c84574d111b3af122bbc6ac0b9aa8af8`。
対象は現行v3のコア、汎用HTML、画像処理、保存、設定、workflow、検証、通知、導入・運用文書。
旧レビューの記述をそのまま継承せず、現行ソース・テスト・文書と照合した。
本レビューでは製品コード・設定・既存テストを変更しない。

P1はデータ保護、取得成功判定、設定保全、処理継続性に関わる早期対応項目。
P2は基本的な対応範囲と継続運用を改善する項目。P1は必ずしも現行仕様への違反を意味しない。
以下の多くは現行契約どおりの動作であり、互換性を検討して仕様を改善する提案である。
以前のIDを維持し、今回追加した論点をR20とする。

## 優先一覧

| ID | 優先度・区分 | 不足・問題と具体例 | 影響範囲・理由 | 推奨する対応 | 現行の根拠 |
| --- | --- | --- | --- | --- | --- |
| R05 | P1・保存仕様 | 別URLでも同じタイトル・章番号・画像番号なら同じ保存先になる。`/one`の赤い画像と`/two`の青い画像を順に取得すると前者が後者に上書きされた。 | コアの既定出力。独自プラグインでも名前が衝突すれば発生。既定のoverwriteと作品識別の不足が組み合わさり、以前の成果物を失う。 | 安定した作品IDまたはURL由来のキーで分離するか、保存先の所有関係を記録し別作品の上書きを拒否する。同一作品の更新は維持する。 | [出力既定値](../../../src/image_downloader/configuration/models.py#L57)、[衝突処理](../../../src/image_downloader/output/output_allocator.py#L158)、probe R05 |
| R03 | P1・成功判定 | 画像0件のログイン画面でも汎用HTMLは1章を返し、通常downloadは失敗なし、workflowはsuccess・completedになる。次回updatedは選択0件。 | 汎用HTMLで容易に発生し、他プラグインが空章を返す場合も同様。抽出失敗や未認証を取得成功と誤認する。`allow_empty_chapter_manifest=False`は章0件を拒否する設定なので防げない。 | 空画像の結果を明示し、既定では未取得扱いにする。正当な空ページを許可する互換性方針を設け、workflowとverifyの判定を揃える。 | [章数だけの判定](../../../src/image_downloader/application/service.py#L551)、[既存受入テスト](../../../tests/v3/application/test_generic_workflow.py#L220)、[verifyのno_images](../../../src/image_downloader/application/workflow_verification.py#L122)、probe R03 |
| R06 | P1・設定保全 | 旧キー・未知キーを除外し、状態変更コマンドではYAMLから無表示で削除する。`network.max_retries: 9`は`max_attempts`へ移行されず、綴り間違いも同様に消える。 | ユーザー管理YAML全体。設定値が既定へ戻り、書換え後は元の値も残らない。現在のREADMEに明記された仕様であり、削除処理自体は原子的・変更競合検知付き。 | 値の意味を考慮した明示的移行、差分表示、バックアップを設ける。未知キーは診断して利用者が訂正できるようにする。 | [除外処理](../../../src/image_downloader/configuration/layers.py#L139)、[YAMLからの削除](../../../src/image_downloader/configuration/layers.py#L322)、[README](../../../README.md#L47)、[read-onlyとの区別](../../v3/reference/configuration.md#L55)、probe R06 |
| R14 | P1・資源・停止制御 | 既定で画素数上限がなく、Pillowの標準上限もworker内で無効化。全フレーム読込みにフレーム数・総画素数・処理時間の上限がなく、worker待機にもtimeoutがない。 | すべてのPillow検査・変換。圧縮後64MiB以下でも展開画像は巨大になり得る。長いアニメーション・重いデコードがメモリ枯渇や長時間停止につながる。単一workerの停止は後続処理にも影響する。 | まず運用上の画素数上限を推奨し、既定値の採否を検討。総フレーム負荷・処理期限・期限到達後のworker終了と再生成を設計する。プロセス全体の待機バイト量も制御する。 | [max_image_pixels=None](../../../src/image_downloader/configuration/models.py#L94)、[worker待機・終了](../../../src/image_downloader/media/image_processor.py#L83)、[Pillow上限無効化](../../../src/image_downloader/media/image_processor.py#L113)、[本文全量保持](../../../src/image_downloader/transport/gateway.py#L396)、[既知の期限の限界](../../v3/how-to/faq.md#L245) |
| R20 | P2・今回の追加論点 | 完了済みの画像を削除してもupdatedは再取得しない。保存先・加工条件を変更しても候補のrevisionが同じなら同様。 | workflow全般。完了状態はURL・revisionに依存し、現在の成果物や取得条件との整合を確認しない。復旧・別ディレクトリへの保存が自動ではできない。 | 完了条件に保存条件と成果物の対応を持たせ、欠損・条件変更時の再取得を選択できるようにする。現行の回避策は通常downloadまたは`--download-scope all`。 | [候補選択](../../../src/image_downloader/storage/workflow.py#L151)、[現行の制約](../../v3/how-to/faq.md#L232)、probe R20 |
| R13 | P2・再開機能 | 実行中の再試行は成功画像を保持できるが、プロセスを終了して再起動すると未完了URLを画像単位で再開できない。1000枚中999枚成功でも次回は成功分を通常処理する。 | workflowの大量取得。既定overwriteなら再取得・再処理、renameなら複製、errorなら衝突となり得る。既存のworkflow履歴だけでは再開用の画像台帳にならない。 | 安定した画像識別と保存先・最終bytesの記録を永続化し、次回起動でも成功分を再利用できるようにする。 | [実行内のImageLedger](../../../src/image_downloader/application/workflow_retry.py#L41)、[次回起動の制約](../../v3/how-to/faq.md#L234)、[既存再試行テスト](../../../tests/v3/application/test_generic_workflow.py#L240) |
| R17 | P2・保存後検証 | verify workflowは存在・非空・履歴との対応などを調べるが、非空の非画像ファイルでもpassedになる。 | 長期保存・破損検出。R04の保存前検査は保存後の破損や置換を検出しない。verify自体は実装済みであり、完全性検査の追加が不足している。 | 存在確認・デコード・保存時hash照合を別の検証レベルとして提供する。通常downloadにも成果物台帳を適用する。hashは保存時との一致の確認であり取得元の正しさの証明ではない。 | [先頭1バイトの確認](../../../src/image_downloader/application/workflow_verification.py#L30)、[非画像を使う既存テスト](../../../tests/v3/application/test_workflow_verification.py#L69)、[ART計画](../../v3/maintenance/development-tasks.md#L27)、probe R17 |
| R08 | P2・画像抽出 | 汎用HTMLはimgのsrcだけを抽出。picture/sourceのsrcset、imgのsrcset、data-srcだけの画像は拾えない。srcがplaceholderなら本画像の代わりにそれを取得する。 | core.generic-html。標準的なレスポンシブ画像が不足し、非標準lazy属性にも対応しない。専用プラグインは別途対応可能。 | srcset・pictureの候補選択規則を実装。lazy属性は対応属性を限定して選択可能にする。JS実行は別機能として扱い、必要なページは専用プラグインを案内する。 | [img[src]だけの抽出](../../../src/image_downloader/plugins/builtin.py#L52)、probe R08 |
| R10 | P2・入力対応 | PNGそのもののURLを渡しても汎用HTMLはHTMLとして解析し、画像0件・失敗なしで終わる。 | core.generic-htmlへのfallback。直接画像URLという基本的な入力を扱えず、非対応という説明も得られない。 | 画像応答を1画像のmanifestへ変換する経路を設けるか、HTML以外を明確な非対応として拒否する。曖昧な0件成功は避ける。 | [すべてをHTMLとして解析](../../../src/image_downloader/plugins/builtin.py#L94)、probe R10 |
| R12 | P2・変換時の情報保全 | 2フレームGIFをWEBPへ変換すると1フレームだけになる。複数フレームを検査するが、変換保存は先頭フレームのみ。 | コアで再エンコードする場合。ORIGINALは元bytesを維持する。明示的に同形式へ再エンコードする場合もアニメーション保持は保証されない。 | 対応形式間の全フレーム変換とduration・loop・disposalの方針を実装。静止画形式への変換は先頭フレーム化を明示し、警告・拒否・明示許可のいずれかを定める。 | [単一画像としてのsave](../../../src/image_downloader/media/image_processor.py#L128)、probe R12 |
| R16 | P2・画像本体の更新検出 | 汎用HTMLのrevisionはURL・index・image_idだけ。同じURLのPNGをサーバー側で差し替えても画像一覧が同じならupdatedでは検出しない。 | core.generic-html。独自プラグインの更新検出はそのcheck_updates実装に依存する。画像URLが固定のサイトでは更新を取り逃がす。 | 選択可能なETag・Last-Modified・内容hashの検査を追加する。要求回数と負荷が増えるため現行の一覧比較と分ける。 | [revisionの入力](../../../src/image_downloader/plugins/builtin.py#L106)、[一覧比較の仕様](../../v3/how-to/faq.md#L442)、[既存テスト](../../../tests/v3/application/test_generic_workflow.py#L143) |
| R07 | P2・導入手順 | first downloadがexample.testを入力例にしており、コピーした手順だけで実際の取得成功を再現できない。 | 初回利用者。内部テスト用fixtureはあるが、利用者向けの起動・取得・期待結果まで揃ったローカルデモが不足する。 | 小さなHTML・画像のローカルデモ、起動コマンド、取得コマンド、期待ファイルを提供する。外部サイトに依存しない。 | [tutorial](../../v3/tutorials/first-download.md#L5)、[未解決DOC-EXT-001](../../v3/maintenance/open-documentation-todos.md#L15) |

推奨順序はR05・R03を先行し、R06・R14を続ける。
R03の仕様維持の対比と修正時の影響は[追加調査](r03-details.md)を参照する。
次にR13・R17・R20を「画像単位の永続的な成果物管理」として共通設計する。
再開、検証、欠損の再取得は目的が異なるので、台帳だけを追加して全部完了と扱わない。
汎用HTMLの対応範囲を広げるR08・R10、変換のR12、更新検出のR16は個別に採用できる。
R07はコード変更を待たず並行して整備できる。

## 優先一覧に入れなかった補足

- R11: `existing_file=skip`はHTTP取得・画像加工の後に保存を省略する。
  既存ファイルがあっても通信や検査に失敗すればskip成功にはならない。
  [実行順](../../../src/image_downloader/application/service.py#L1215)と[FAQ](../../v3/how-to/faq.md#L238)で確認。
  通信前skipには変換後の拡張子・動的名前・プラグインhookとの整合が必要なので、永続台帳を利用した最適化として検討する。
- R19: SMTPの一部宛先拒否情報を`send_message()`の戻り値から取り込まない。
  [送信箇所](../../../src/image_downloader/observability/notifications.py#L406)を精読して確認。
  主取得処理の不足より優先度は低いが、複数宛先を使う運用では配送結果の診断を補うべきである。
- R18: ARTの将来計画は既存verifyと区別されており、「verify全体が未実装」とは扱わない。
  一方、[SMTP-003](../../v3/maintenance/development-tasks.md#L16)の「ローカルサーバー統合は未整備」は
  [opt-in Mailpitテスト](../../../tests/v3/observability/test_mailpit_integration.py#L1)の存在と整合しない。
  平文SMTPの既存確認と、TLS・認証などの未整備部分を分けて更新する文書保守課題は残る。

## 修正済み・既存機能との区別

R01（相対URL・base・最終Referer）、R02（EXIF・向き）、R04（MIMEと全フレーム検査）、
R09（汎用HTMLの文字コード）、R15（OSの要素長・短い一時名・パス長診断）は今回の未解決一覧から除外した。
R15の環境依存の境界は[実測記録](../implementation-review-2026-10-04/r15-details.md)を参照。
本レビューで約32,000文字の境界プローブを再実行したという意味ではない。

profile、署名・信頼管理、cookie・secret参照、HTTP retry、atomic保存、保存先ロック、
workflowの更新選択・実行中の再試行・履歴・dry-run、verifyの存在検査は既にある。
これらを未実装と扱わず、今回の不足が既存機能のどこを補うものかを明記した。
統計的な文字コード推測、JSブラウザ実行、特定サイトの巡回は現行汎用HTMLの保証範囲ではない。

## 再現・検証記録

[behavior-probe.py](behavior-probe.py)は、修正後の期待ではなく現行挙動をassertするオフライン調査用プローブ。
既存のfixture・mock transport・小さな生成画像を再利用し、実サイトには通信しない。
通常のv3受入テストに組み込むものではない。

```powershell
python -m pytest docs/investigations/implementation-review-2026-10-05/behavior-probe.py -q -s
```

2026-10-05実行: **5 passed**。R03・R05・R06・R08・R10・R12・R17・R20の8挙動を確認。
R06のプローブは正規化結果を確認し、YAMLの書換え経路は現行コードと既存テストで別途確認した。
R14は制限・待機コードの静的確認であり、メモリ枯渇やworker停止を実際に起こした試験ではない。
R16はrevisionの計算式と既存受入テストによる確認。実サイト画像の差替え試験は行っていない。
外部SMTP、ブラウザcookie、別OSは今回の実行検証対象外。

関連v3テスト: **153 passed, 1 skipped**（44.71秒）。汎用HTML・workflow検証・画像処理・設定解決・
出力割当・文書契約の6ファイルを実行。全v3テストの再実行とは区別する。
テスト成功は現行仕様との整合を示し、本レポートの仕様改善案を否定するものではない。

調査プローブのruffと`git diff --check`も確認。追加ファイルは本レポートと調査プローブのみ。
