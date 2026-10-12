# QS-015 修正確認：公開例外のError接尾辞

## 対象と保証

issue [#8](https://github.com/GlassLeaf/image_downloader/issues/8)。最新mainから独立した
codex/fix-qs-015-exception-error-suffixを作成した。検証時の基点SHAはraw/qs-015-verification.jsonに記録する。
基点はmainの097faf819210791ec29a482c2e0debbd3226969fである。

UnsupportedSiteFeatureとSecretNotFoundの公開名を、それぞれUnsupportedSiteFeatureErrorと
SecretNotFoundErrorに変更する。定義・root export・内部import/raise・例外catalog・API一覧・
現行テスト・文書を更新し、両import経路から旧名を削除する。aliasやfallbackは設けない。
親例外・constructor・固定メッセージ・error code・CLI終了コードは維持する。
Ruffの通常selectにN818を追加して再発を検査する。
分類は書式・保守性（PEP 8 Exception Names）で、特定CWEへの分類はない。

## 互換性と移行

破壊的API変更である。旧名のimport/except、ErrorInfo.exceptionや診断の例外名を比較するコードを
新名へ変更する必要がある。PluginError／AuthenticationErrorによる捕捉と、
unsupported_site_feature／secret_not_foundのcodeによる分類はそのまま利用できる。
移行手順はdocs/v3/maintenance/migration.mdに記載する。

旧名を参照する外部プラグインはソースを更新して再署名する。同梱plugin-sourcesには旧名がなく、
署名済みソース・manifest・catalogを変更しない。パッケージ公開や版番号変更は行わない。
過去の監査資料・プローブ・ログに記載した旧名称は歴史的記録として保持する。

## 検証

通常回帰テストtests/v3/distribution/test_exception_names.pyで、新名の両import経路、旧名の
import失敗、親例外による捕捉、catalog codeと秘匿、実gateway・secret取得時の新例外、
CLI終了コードを検証する。模擬通信・架空のsecretだけを使用する。
既存API・文書・同梱プラグインのstrict loadも確認する。
重点テストは172 passed / 1 skipped（23.82秒）。skipはローカルでのsymlink作成不可によるもの。
Ruffの通常規約とN818単独（ignore/noqa無効化）はともに違反0件、mypyは96 source files成功。

v3全体は2128 passed / 17 skipped（647.68秒）、coverage 88.71%。
Ruff・N818単独・mypy（96 source files）・pip checkは成功した。失敗と外部期限による停止はない。
17 skipはsymlink作成不可5件とopt-in Mailpit 12件であり、理由は検証JSONに記録する。
raw/qs-015-verification.jsonにはツール版・環境・source/test/tool SHA-256、
raw/qs-015-focused.stdoutとraw/qs-015-v3.stdoutには生ログを記録する。
12 test job（3 OS × Python 3.11〜3.14）と3 package smoke、統合後mainの対象SHAを確認し、
run URLをissueへ記録して閉じる。
