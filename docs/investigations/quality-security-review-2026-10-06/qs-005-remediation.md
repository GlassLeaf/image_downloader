# QS-005：空結果の明示的な許可・拒否

2026-10-13に実装・検証。未指定の従来動作を維持する段階的な対応であり、すべての画像0件を
失敗にする修正ではない。過去のreport・findings・問題挙動プローブは変更していない。

## 対象コードと環境

- 最新のorigin/mainをfetchして作成したブランチ：`codex/fix-qs-005-empty-result-policy`。
- 基点：`f78b95f115ffa3b8b309cd0fa0f0018df56cd8aa`。2026-10-13のローカル検証は未コミットの作業ツリーで実施。
- Windows 10 build 19045、CPython 3.11.4、HTTPX 0.28.1、Pydantic 2.13.5。
- pytest 9.1.1、pytest-cov 7.1.0、Ruff 0.16.5、mypy 2.3.1。
- 既存の未コミットmain統合確認資料3ファイルは保持し、この修正の内容として扱わない。

各チェックのJSONに実行コマンド・環境・開始時刻・所要時間・終了コードを記録している。
[検証集約・本体と回帰テストのSHA-256](raw/qs-005-verification.json)も保存した。

## 修正保証

`DownloadManifest.allow_empty_chapters`と`Chapter.allow_empty_images`を追加した。
いずれもkeyword-only、boolまたはNone、既定None、repr対象外、通常のdataclass比較対象。
既存位置引数・位置パターン・未指定時のreprを維持する。constructorの不正型は値を含まない
固定文言のTypeError。プラグインhook内の構築失敗・不正な戻り値は既存のPluginError経路を使う。

| 対象 | 判定 |
| --- | --- |
| 章一覧0件、既存設定False | manifest指定によらず拒否 |
| 章一覧0件、既存設定True | manifestのNone/Trueを許可、Falseを拒否 |
| 各章の画像0件 | そのChapterのNone/Trueを許可、Falseを拒否 |
| 画像のある章 | False指定でも通常処理を継続 |

共通検査は[service.py](../../../src/image_downloader/application/service.py)の一か所で、各取得試行に
一度だけ行う。禁止された空章が一つでもあれば全章の通常画像ジョブを開始せず、
`PluginError("plugin returned an empty image list")`とする。章一覧0件の既存メッセージ
`plugin returned an empty manifest`を維持する。

inspect・ledger更新・BEFORE_MANIFESTの保留保存・全章のAFTER_MANIFESTを終えてから検査する。
既存のhookと保存の順序を維持するため、判定前の通信・追加ファイル保存は取り消さない。
失敗時の追加ファイル・processor・siteの後始末は既存処理を使う。

CLIのPluginError終了コード4、workflowの失敗・再試行、失敗通知の既存経路を使う。
拒否結果は成功通知・完了登録しない。設定された再試行ではmanifestを再取得し、毎試行
同じ共通検査を行う。全画像スキップは正常として扱い、既存の成功画像保持方式は変更しない。

inspectionは型検証だけを行い、空結果を返せる。詳細JSONのallに非Noneの指定だけを追加し、
未指定時のJSON形状とurl/httpの要約を維持する。`verify workflow`は画像取得を検証し続け、
許可された画像0件も`no_images` / `not_acquired`とする。

## 互換性・移行・制限

- `allow_empty_chapter_manifest`は互換性のため当面残し、Falseの拒否を優先する。
  将来の整理対象としてconfiguration reference・runtime仕様・設定テンプレートに注記した。
  今回は削除・無効化・自動移行せず、整理時期と移行方法は別途決定する。
- 未指定の旧プラグイン・組込み汎用HTMLは従来どおり画像0件でも成功し得る。
  認証切れ・抽出失敗を検出するには、サイト固有の判定と明示的なFalse指定が必要。
- 公開DTOのfields・signature・slotsは拡張される。新しい指定は比較にも影響する。
  新引数を旧本体へ渡すとTypeErrorになるため、対応本体のconstructor signatureまたは
  運用で指定した対応配布版を確認する。package版番号・署名形式・API version 3は変更していない。
- 新指定を使うよう変更した外部プラグインは通常どおり再署名・再インストールする。
  未変更のプラグインは再署名不要。同梱の署名済みソース・manifestは変更していない。
- 履歴・状態schema、例外catalog、成功分類全体を変更せず、既存の完了状態を自動解除しない。
  新しい方針を既存の完了対象にも確認したい場合は、利用者が既存のall scopeで再実行する。
- 空の画像一覧を扱う方針であり、画像bytesの空・形式不正・通信失敗の許可機能ではない。

## 検証結果

[回帰テスト](../../../tests/v3/application/test_empty_result_policy.py)を54ケース追加。
章一覧0件の全組合せ、章ごとの許可、禁止された空章の位置、他章の取得開始防止、
実際の成功通知の有無、完了状態、CLI終了コード、スキップ、再試行、型検証、
正規化、repr・比較・位置パターン、inspect JSON、追加ファイルの順序と保存保持を確認する。
通信と通知は模擬し、実メール・外部サイトへの通信は使わない。

| チェック | 結果 | 記録 |
| --- | --- | --- |
| v3全体・公開API・文書契約・カバレッジ | 2182 passed、17 skipped、失敗0件。branch coverageを含む総合88.75%、基準75%以上。pytest実測650.35秒、監視側652.47秒 | [stdout](raw/qs-005-v3-final.stdout)、[metadata](raw/qs-005-v3-final.json) |
| Ruff | 成功 | [stdout](raw/qs-005-ruff.stdout)、[metadata](raw/qs-005-ruff.json) |
| mypy | 96 source files、問題なし | [stdout](raw/qs-005-mypy.stdout)、[metadata](raw/qs-005-mypy.json) |
| pip check | 依存関係の問題なし | [stdout](raw/qs-005-pip-check.stdout)、[metadata](raw/qs-005-pip-check.json) |
| wheel / sdist build | 成功、no-isolation | [stdout](raw/qs-005-build.stdout)、[metadata](raw/qs-005-build.json) |
| package smoke | Windows / Python 3.11.4で成功。wheelの非editable installと既存署名pluginのstrict loadを確認 | [stdout](raw/qs-005-package-smoke.stdout)、[metadata](raw/qs-005-package-smoke.json) |

17件のskipは、Windowsのsymlink作成不可5件とlocal Mailpit未有効12件。
追加54ケースはすべて成功した。API文書・設定・同梱プラグイン・既存のロック／複数プロセス・
Cookie・状態・出力を含むv3全体の結果であり、この実行にテスト失敗はない。

既存CIの設定は3 OS × Python 3.11〜3.14の12 test jobと3 OSのpackage-smoke jobである。
2026-10-13のローカル検証時点では今回の変更に対するCI実行は未実施。Ubuntu・macOS・
Python 3.12〜3.14の結果を推測で補わず、基点mainのCI成功を今回の変更の検証結果として
流用しない。公開後のCI結果は別途確認して記録する。

初回の全体実行は600秒の外部監視期限で約88%までの進行時に中断した（監視側終了コード124）。
同環境の前回main全体検証の実測695.84秒より短い期限だったため、今回は1800秒で再実行した。
初回の進行中ログにテスト失敗の表示はないが、未完走なので成功の根拠には使わない。
[初回stdout](raw/qs-005-v3.stdout)、[初回metadata](raw/qs-005-v3.json)を保持する。
