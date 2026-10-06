# 本体コードの品質・セキュリティ監査

監査日：2026-10-06（Asia/Tokyo）。基準コミット：`afd7581a615975cf16bf366335b32eda3d66c4ed`。
Python本体93ファイル・18,054行を自動解析し、信頼境界・異常処理・永続化・状態判定を重点精読した。
**確認した問題・安全性／品質上の不足は14項目、書式の差分は2分類、未確定事項は1項目。**
現行契約に沿った挙動も含むため、14項目すべてを実装バグや脆弱性とは呼ばない。

優先対象はQS-001（条件付きの未署名コード実行）、QS-002（Refererによる秘密情報送信）、
QS-003（画像展開負荷・待機制御）、QS-004（別対象の画像上書き）である。
取得画像だけからの遠隔コード実行、一般ユーザーから管理者への特権昇格は確認していない。

QS-001の修正内容と修正後の検証は[修正確認記録](qs-001-remediation.md)を参照する。
QS-002の自動Referer制限と検証は[QS-002修正確認記録](qs-002-remediation.md)を参照する。
以下の問題一覧・再現プローブ・raw記録は、基準コミットで確認した当時の挙動を記録したものである。

## 対象・評価方法

一般ユーザー権限のCLI利用を前提に、取得先のHTML・画像・HTTP応答を信頼できない入力とした。
対象は`src/image_downloader`、本体のプラグイン署名・catalog・読込み機構、関連v3テスト、
設定・現行文書。外部プラグインの中身、旧版、依存ライブラリ内部・既知CVEの網羅調査、
第三者がURLを投入するサーバーの脅威モデルは対象外。

[PEP 8](https://peps.python.org/pep-0008/)は書式・命名・可読性、
[CERT-Cの規則一覧](https://cmu-sei.github.io/secure-coding-standards/sei-cert-c-coding-standard/rules/)は
初期化、変換範囲、戻り値・エラー処理、資源管理の参考にした。
[CERT-Cの適用範囲](https://cmu-sei.github.io/secure-coding-standards/sei-cert-c-coding-standard/front-matter/introduction/scope/)はC言語であり、
Pythonへの正式な準拠判定ではない。MISRA-Cも安全な入力・制御・資源管理という観点で参考にし、
[MISRA Compliance:2020](https://misra.org.uk/app/uploads/2021/06/MISRA-Compliance-2020.pdf)のような
条項別の適合証明や全MISRA条項の評価は行っていない。
これらは単一のテストセットではなく、規約・弱点分類・設計上の観点である。

重要度は、実行権限・入力を操作できる主体・影響・起こりやすさを合わせて評価した。
「高」でも成立条件を省略しない。「再現済み」は観測した挙動を指し、記載したすべての被害を
実際に発生させた意味ではない。DoSなどの被害予測は根拠と限界を明示した。
品質・仕様上の課題に無理にCWEを割り当てず、セキュリティ項目は具体的な弱点を対応付けた。

## 問題一覧

IDは根本原因単位で一意に付与した。既存の過去IDとは別のnamespaceとし、下記で対応付ける。
成立条件・ソース行・再現結果・改善案は各IDの詳細および[全件CSV](findings.csv)に含む。

| ID | 区分・問題の種類 | 重要度・確認 | 問題 | 起こり得るエラー・被害 |
| --- | --- | --- | --- | --- |
| [QS-001](#qs-001) | セキュリティ：実行コードと署名検証対象の不一致・検証後の変更 | 高（条件付き）・再現済み | 未署名のbytecodeがstrict検証を通過して実行される | 実行ユーザー権限で未署名コード実行。管理者への昇格は未確認。 |
| [QS-002](#qs-002) | セキュリティ：送信データへの秘密情報混入・Refererの未制限 | 高・再現済み | 別オリジンの画像取得へページURL内の秘密情報を送信する | パスワード・トークン・fragmentを外部画像サーバーへ送信。 |
| [QS-003](#qs-003) | セキュリティ・品質：画像展開の資源制限不足・worker待機期限なし | 高・静的根拠で確認（制御経路は再現済み） | 小さな圧縮画像でも大きな展開負荷や長時間停止を起こし得る | メモリ・CPU枯渇、処理・終了待機の長期化。実害を起こす負荷試験は未実施。 |
| [QS-004](#qs-004) | 品質・保存仕様：出力名の衝突・保存対象の識別不足 | 高・再現済み | 別URLの画像が同じ保存先となり既存画像を失う | 別の取得対象の画像を上書きして失う。 |
| [QS-005](#qs-005) | 品質・成功判定：空の処理結果の未識別・完了状態の誤認 | 中・再現済み | 画像0件のログイン画面を取得成功・完了として扱う | 画像未取得を完了と誤認し、次回updatedで取得しない。 |
| [QS-006](#qs-006) | 品質・設定保全：未知・旧設定キーの無表示削除 | 中・再現済み | 設定の誤記や旧キーが除外され、操作開始時にファイルからも消える | 設定が既定へ戻り、元の設定値もファイルから消える。 |
| [QS-007](#qs-007) | 品質・検証の保証範囲：存在検査と内容の完全性検査の差 | 参考・再現済み | verify workflowのpassedは画像内容が健全なことを示さない | 非画像・破損内容の見落とし。passedは存在検査の成功。 |
| [QS-008](#qs-008) | 品質・状態整合性：完了状態と現在の成果物・保存条件の未照合 | 中・再現済み | 取得済み画像が消えてもupdatedで再取得されない | 欠損が復旧せず、別の保存条件での取得も行われない。 |
| [QS-009](#qs-009) | 品質・画像変換：複数フレーム情報の無表示切捨て | 中・再現済み | アニメーションGIFのWEBP変換で先頭フレームだけが残る | 後続フレーム・動き・時間情報を失う。 |
| [QS-010](#qs-010) | 品質・入力検証：数値変換限界の未処理・例外境界の不足 | 低・再現済み | 巨大な整数設定がConfigurationErrorではなくOverflowErrorになる | 巨大整数でOverflowErrorとなり設定エラー診断が欠ける。 |
| [QS-011](#qs-011) | 品質・認証データ検証：JSON構造・必須項目の未検証・例外変換の漏れ | 中・再現済み | 不正cookieデータでAttributeErrorやKeyErrorが露出する | 不正cookieデータでAttributeError/KeyError、復旧操作も失敗し得る。 |
| [QS-012](#qs-012) | 品質・通知：API戻り値の未確認・部分失敗の見落とし | 低・再現済み | SMTPの一部宛先拒否を通知成功として扱う | 一部宛先に通知が届かず、拒否結果を見落とす。 |
| [QS-013](#qs-013) | 品質・資源管理：初期化失敗時の後始末不足 | 低・再現済み | runtime組立て失敗後も動的プラグインmoduleが残る | 組立て失敗後もmoduleが残り、同一プロセスの再試行で蓄積。 |
| [QS-016](#qs-016) | 品質・セキュリティ（条件付き）：秘密参照namespaceの名前衝突 | 中・再現済み | 異なるプラグインIDが同じ環境変数の秘密を受け取る | 別プラグインが同じ環境変数の秘密を受け取る。外部送信は未実測。 |

## 書式・保守性の別表

| ID | 観点 | 件数・評価 | 影響 |
| --- | --- | --- | --- |
| [QS-014](#qs-014) | 行長 | Ruff E501：1819箇所。文書行72字超の計測：319箇所（E501との重複あり）。 | 可読性。実行・セキュリティ上の被害は示さない。 |
| [QS-015](#qs-015) | 例外名 | N818：2箇所。UnsupportedSiteFeature、SecretNotFound。 | 命名方針との一貫性。公開名変更には互換性の検討が必要。 |

本体の既存Ruff規約（120字）では違反0件。PEP 8自身もプロジェクト固有規約を優先する。
全該当箇所・行・規則は[style-occurrences.csv](style-occurrences.csv)。
docstring・コメントの72字超は機械計測で、URLやコード例の個別例外を断定しない。
同じ行にE501と文書行計測の2レコードがある場合も、2つの実行不具合とは数えない。

## 未確定事項

| ID | 候補 | 成立条件・未確認点 |
| --- | --- | --- |
| [QS-017](#qs-017) | 保存先・lockの検証と使用間のリンク差替え | 共有rootへの攻撃者書込みが必要。OS上の競合・実害は未再現。確定脆弱性に含めない。 |

## 実行した検査と結果

環境：Windows-10-10.0.19045-SP0、Python 3.11.4。
ツール：ruff 0.16.5, mypy 2.3.1, pytest 9.1.1, pytest-cov 7.1.0。
起動時点で既存の未追跡ファイルは`docs/investigations/implementation-review-2026-10-05/`。
既存資料を保持し、製品ソース・API・設定・既存テストを変更していない。
開始時状態、UTC実行時刻、コマンド、終了コード、全93ファイルのSHA-256は
[metadata.json](raw/metadata.json)に保存した。監査後にも全SHA-256を照合した。

| 検査 | 終了コード | 時間 | 記録 |
| --- | --- | --- | --- |
| ruff-project | 0 | 0.48秒 | [stdout](raw/ruff-project.stdout) |
| ruff-security | 1 | 0.45秒 | [stdout](raw/ruff-security.stdout) |
| ruff-pep8 | 1 | 0.55秒 | [stdout](raw/ruff-pep8.stdout) |
| mypy | 0 | 5.55秒 | [stdout](raw/mypy.stdout) |
| pytest-v3 | 0 | 628.75秒 | [stdout](raw/pytest-v3.stdout) |

- 本体Ruff：検出0件。mypy：93ファイルで指摘なし。
- 追加セキュリティ規則：37件。S101=26、S105=5、S110=5、S311=1。
  全件精読し、内部不変条件、予約トークン、二次診断失敗の抑制、非暗号用途のjitterと判断した。
  この37件から確定脆弱性は採用しない。全箇所と判定は[security-triage.csv](security-triage.csv)。
- 厳格な追加書式検査：E501=1819、N818=2。規則に対する検出で終了コード1となる。
  自動解析の異常終了とは区別する。Ruffの全PEP 8推奨を網羅する検査ではない。
- v3全体：**1453 passed、16 skipped**。
  カバレッジ（statement+branch）：**87.77%**。
  statement：90.50%、branch：78.33%。
  75%の既存gateは通過。未実行statementは943、未網羅branchは624。
- スキップはMailpit opt-inの12件と、symlinkを作成できないWindows環境での4件。
  4件を限定再実行して理由を確認した：[symlink-checks.stdout](raw/symlink-checks.stdout)。
- 監査プローブ：**18 passed**。[probes.stdout](raw/probes.stdout)、[JUnit](raw/probes.xml)、
  [コマンド・終了コード](raw/probes-metadata.json)。
  小さな画像、temporary directory、MockTransport、fake SMTP、架空の秘密のみを使用。
  実メール・実アカウントの秘密・メモリ枯渇・無期限worker停止は使っていない。
- プローブは現行の問題挙動をassertする調査用資料。成功は修正済みという意味ではない。
  v3の正常仕様テストには組み込んでいない。

Ruff・mypy・pytestは現行契約への整合を示し、今回の設計・安全性の不足がないことを証明しない。
Bandit、pycodestyle、pip-auditは当該環境に未導入で、今回は新規導入せずRuff追加規則と精読を使った。
他OS・他Python版は今回実行していない。CIのmatrix定義と今回のWindows/Python 3.11実測を区別する。

## 確認した防御・指摘しなかった事項

- 未定義・初期化前の変数使用は、今回の本体Ruff/mypyと重点精読で確定指摘を得なかった。
  Python以外のライブラリ内部の未初期化メモリまで調べたものではない。
- HTTP(S)以外の送信先の拒否、クロスオリジンredirect時の認証ヘッダー除去、
  bodyの宣言サイズと実測サイズの上限、origin範囲を持つ認証flowの検証がある。
  監査のpositive-controlで認証ヘッダー除去とサイズ超過拒否も確認した。
  この防御がQS-002の自動Refererまで覆うわけではない。
- 通常のpath traversal、危険な要素名、link/reparse/hard linkの検査、原子的保存、
  core同士のプロセス間lockがある。共有rootでの悪意ある競合の保証はQS-017に分ける。
- cookieはAES-GCM、exportはscryptを使用し、鍵はkeyring、nonce等はsecretsから生成する。
  固定暗号鍵・randomによる認証nonce生成は確認しなかった。構造検証の不足はQS-011に分ける。
- warn/off/bypassを利用者が明示して信頼検証を弱める挙動は、その設定の仕様として扱う。
  信頼済みPythonプラグインの意図した実行をコード実行脆弱性として重複計上しない。
- 任意URLを取得するCLIでprivate IPへの取得自体をSSRFと断定しない。
  第三者がURLを投入するサーバー組込みの評価では、別の脅威モデルと対策が必要となる。

## 過去レビューとの対応

| 過去ID | 今回ID | 扱い |
| --- | --- | --- |
| R14 | QS-003 | 現行コードで再確認 |
| R05 | QS-004 | 現行コードで再確認 |
| R03, R10（空成功の共通原因） | QS-005 | 現行コードで再確認 |
| R06 | QS-006 | 現行コードで再確認 |
| R17 | QS-007 | 現行コードで再確認 |
| R20 | QS-008 | 現行コードで再確認 |
| R12 | QS-009 | 現行コードで再確認 |
| R19 | QS-012 | 現行コードで再確認 |

前報告のR01・R02・R04・R09・R15は、同報告で解決済みとされており、今回の未解決項目に再掲しない。
R08（srcset等）、R13（次回起動の画像単位再開）、R16（同URLの画像本文更新）は、
文書に示された対応範囲の拡張要望として参照し、独立した実装バグとは計上しない。
R10の直接画像URLの空成功はQS-005と共通の結果判定に含め、今回の独立プローブは空HTMLを対象とした。
R07（導入デモ）、R18（保守文書の整理）は本体優先の監査範囲外。
過去報告の実行結果を今回の再現結果として流用していない。

## 再実行

既存の品質用venvから次を実行する。別環境では同等の依存を備えたPythonへ置き換える。

```powershell
$auditPython = ".runtime/quality-venv/Scripts/python.exe"
$auditDirectory = "docs/investigations/quality-security-review-2026-10-06"
& $auditPython "$auditDirectory/audit_checks.py"
& $auditPython -m pytest "$auditDirectory/behavior_probe.py" -q -s
& $auditPython "$auditDirectory/build_report.py"
```

最初のコマンドは全v3テストを含み、今回約10分を要した。raw出力を更新する。
監査日の基準コミット・初期作業状態は今回の記録であり、将来の再実行ではその時点の記録と分ける。
build_reportは基準ソースのSHA-256と記載した行の一意性を検証し、本文・CSVを再生成する。

## 問題の詳細

<a id="qs-001"></a>

### QS-001 — 未署名のbytecodeがstrict検証を通過して実行される

**高（条件付き）・再現済み**。種類：実行コードと署名検証対象の不一致・検証後の変更。署名による実行コードの完全性保証の不足。

署名対象のfile treeから__pycache__/*.pycを除外する一方、SourceFileLoaderは対応するキャッシュを読み込む。署名済みソースの時刻・サイズに合う無署名キャッシュを置くと、ソースと署名を変更せず別のコードが実行される。検証済みrecordのソースを読込み前に差し替える経路にも再検証がない。

- 成立条件：有効な信頼済みプラグインがあり、攻撃者がそのディレクトリまたはキャッシュへ書き込めること。通常のinstallはコピー時にキャッシュを除去するため、改変キャッシュがそのままinstall経由で伝播するとは判断しない。配置後の書込み、既存ディレクトリの直接trust、検証とimportの間の変更が対象。
- エラー・被害：アプリの実行ユーザー権限で未署名コードを実行できる。ファイル・利用可能な秘密情報へのアクセス、改変、処理停止につながり得る。一般ユーザー実行から管理者への特権昇格、取得画像だけからの遠隔コード実行は確認していない。
- 根拠：QS-001-cache: strict検証成功、署名済みソース不変のまま無署名コードのclass markerがTrue。QS-001-recheck: verifierとloaderの境界で差し替えたソースのmarkerが実行された。後者は決定的な境界置換であり、OS上の競合タイミングを成功させた試験ではない。
- 該当箇所：[plugins/plugin_manifest.py:496](../../../src/image_downloader/plugins/plugin_manifest.py#L496)、[plugins/lifecycle.py:288](../../../src/image_downloader/plugins/lifecycle.py#L288)、[plugins/lifecycle.py:258](../../../src/image_downloader/plugins/lifecycle.py#L258)、[plugins/management.py:238](../../../src/image_downloader/plugins/management.py#L238)
- 分類・参考観点：[CWE-494](https://cwe.mitre.org/data/definitions/494.html)、[CWE-367](https://cwe.mitre.org/data/definitions/367.html)。完全性検証・検証と使用の一貫性。
- 改善案：entryと相対importされるhelperの双方を、検証済みbyteから実行する仕組みにする。署名対象外の既存キャッシュを実行に使用せず、検証・import中に変更できないsnapshotを用いる。sys.dont_write_bytecodeによる書込み停止だけでは既存キャッシュの読込みを防げない。共有plugin rootの書込み権限も制限する。

<a id="qs-002"></a>

### QS-002 — 別オリジンの画像取得へページURL内の秘密情報を送信する

**高・再現済み**。種類：送信データへの秘密情報混入・Refererの未制限。秘密情報を含むURLの送信境界に防御がない。

汎用HTMLは最終ページURLをそのまま画像のrefererとし、gatewayもそのままRefererヘッダーに入れる。ログのURL秘匿やリダイレクト時のヘッダー削除は、この最初の画像要求には適用されない。

- 成立条件：ページURLに認証情報・署名クエリ・秘密のfragmentがあり、そのHTMLに別オリジンまたはHTTPのimg srcが含まれること。攻撃者が画像取得先を運営するか、平文通信を観測できる場合に被害が成立する。
- エラー・被害：パスワード、URLトークン、fragment内データ、閲覧元のパスが外部画像サーバーへ漏洩する。トークンの権限次第で認証情報の悪用につながるが、本試験では実アカウントの侵害は行っていない。
- 根拠：QS-002: https://audit-user:audit-pass@gallery.test/?token=audit-token#private=audit-fragment が、http://asset.test/a.pngへのRefererとして完全に観測された。通信はMockTransportのみ、秘密は架空のcanaryである。
- 該当箇所：[plugins/builtin.py:57](../../../src/image_downloader/plugins/builtin.py#L57)、[plugins/builtin.py:118](../../../src/image_downloader/plugins/builtin.py#L118)、[transport/gateway.py:338](../../../src/image_downloader/transport/gateway.py#L338)
- 分類・参考観点：[CWE-201](https://cwe.mitre.org/data/definitions/201.html)。秘密情報の最小公開・送信先の信頼境界。
- 改善案：自動生成Refererから常にuserinfoとfragmentを除去する。異なるオリジンには既定でoriginだけを渡し、HTTPSからHTTPへは省略する。同一オリジンへの完全URL送信やプラグインの明示Refererは別の意図的な設定として扱う。

<a id="qs-003"></a>

### QS-003 — 小さな圧縮画像でも大きな展開負荷や長時間停止を起こし得る

**高・静的根拠で確認（制御経路は再現済み）**。種類：画像展開の資源制限不足・worker待機期限なし。現行仕様の安全性不足。

max_image_pixelsは既定Noneで、worker内のPillow標準画素上限も無効化する。上限を指定しても検査は各フレームの寸法のみで、総フレーム数・総展開画素・CPU時間を制限しない。future.result()とworker shutdownに期限がない。HTTP本文の64MiB上限は展開後の画像負荷を制限しない。

- 成立条件：信頼できない取得先が、高圧縮率・巨大寸法・多数フレーム・デコードに時間が掛かる画像を返すこと。workerを別プロセスにしても、OSの資源上限や強制終了は設定されていない。
- エラー・被害：メモリ・CPUの枯渇、ダウンロード停止、終了・キャンセル待機の長期化。他のアプリにも資源不足の影響が及び得る。画像からのコード実行は本監査では確認していない。
- 根拠：QS-003: 各フレーム4画素の上限で3フレーム・計12画素のGIFを受理。fake Futureでresultへtimeoutが渡されないことを確認。既定None、Pillow上限無効化、shutdown(wait=True)はソース確認。実際のメモリ枯渇・workerハングは起こしていない。
- 該当箇所：[configuration/models.py:94](../../../src/image_downloader/configuration/models.py#L94)、[media/image_processor.py:103](../../../src/image_downloader/media/image_processor.py#L103)、[media/image_processor.py:113](../../../src/image_downloader/media/image_processor.py#L113)、[media/image_processor.py:178](../../../src/image_downloader/media/image_processor.py#L178)、[media/image_processor.py:81](../../../src/image_downloader/media/image_processor.py#L81)、[transport/gateway.py:412](../../../src/image_downloader/transport/gateway.py#L412)
- 分類・参考観点：[CWE-409](https://cwe.mitre.org/data/definitions/409.html)、[CWE-770](https://cwe.mitre.org/data/definitions/770.html)。資源使用量と処理時間の上限。
- 改善案：既定の画素数、フレーム数、総展開量の上限を設ける。処理期限をworkerの終了・再生成と組み合わせ、同時に保持する取得本文の総量も制限する。暫定策としてmedia.max_image_pixelsを明示するが、それだけでは総フレーム負荷と処理期限は解決しない。

<a id="qs-004"></a>

### QS-004 — 別URLの画像が同じ保存先となり既存画像を失う

**高・再現済み**。種類：出力名の衝突・保存対象の識別不足。現行仕様に適合するがデータ保護上の不足。

既定の保存先はタイトル・章番号・画像番号に依存し、URLや安定した作品IDで分離しない。既定overwriteにより、別の取得対象であっても同じ名前なら前回の画像を置換する。

- 成立条件：同じ保存rootで、別URLが同じタイトル・章番号・画像番号を持つこと。通常の複数ページ取得でも発生し、取得先がタイトルを合わせることもできる。
- エラー・被害：既存の取得画像を上書きして失う。保存root外への任意ファイル書込みや特権昇格を示すものではない。
- 根拠：QS-004: /oneの赤いPNGと/twoの青いPNGが同一saved_filesとなり、前者のbyteが後者に置換された。
- 該当箇所：[configuration/models.py:58](../../../src/image_downloader/configuration/models.py#L58)、[configuration/models.py:60](../../../src/image_downloader/configuration/models.py#L60)、[output/output_allocator.py:170](../../../src/image_downloader/output/output_allocator.py#L170)
- 分類・参考観点：CWEは付けない（品質・仕様・書式上の指摘）。対象の一意性・意図しないデータ喪失の防止。
- 改善案：URLまたは安定した作品IDで出力を分離するか、保存先の所有対象を記録して別作品の上書きを拒否する。当面はexisting_file=error/rename、または対象ごとのoutput-dirで回避する。同一作品の更新との互換性を明示する。

<a id="qs-005"></a>

### QS-005 — 画像0件のログイン画面を取得成功・完了として扱う

**中・再現済み**。種類：空の処理結果の未識別・完了状態の誤認。現行仕様に適合するが運用上の不足。

汎用HTMLは画像がなくても1章を返す。章が存在し画像失敗がなければdownload/workflowは成功し、completedが保存される。allow_empty_chapter_manifest=Falseは章0件だけを制限する。

- 成立条件：ログイン要求や抽出対象外のHTMLなど、画像のない応答を受け取ること。正当な空ページもあり得るため、一律の仕様違反とは判断しない。
- エラー・被害：未取得を成功と誤認し、次のupdated実行から対象が外れる。認証・抽出失敗の検出が遅れる。verify workflowのno_images判定とも利用者から見た成功概念が一致しない。
- 根拠：QS-005: Sign inページのworkflowがsuccess、保存0件、completed=True。繰返し実行のselected_urlsは0件。
- 該当箇所：[plugins/builtin.py:104](../../../src/image_downloader/plugins/builtin.py#L104)、[application/service.py:551](../../../src/image_downloader/application/service.py#L551)、[commands/download.py:132](../../../src/image_downloader/commands/download.py#L132)、[application/workflow_verification.py:111](../../../src/image_downloader/application/workflow_verification.py#L111)
- 分類・参考観点：CWEは付けない（品質・仕様・書式上の指摘）。異常・境界条件の確認と結果判定。
- 改善案：空画像の結果を明示し、既定で未取得として扱う方針を設ける。正当な空ページを許す明示設定を分け、download・workflow・verifyで判定を揃える。

<a id="qs-006"></a>

### QS-006 — 設定の誤記や旧キーが除外され、操作開始時にファイルからも消える

**中・再現済み**。種類：未知・旧設定キーの無表示削除。README・設定参照に記載された仕様の改善課題。

user-managed YAMLの未知・旧キーを検証前に除外し、rewrite有効の状態変更コマンドでは元YAMLから削除する。旧設定値の移行、差分表示、バックアップがない。直接validate_configする場合のunknown拒否とは挙動が異なる。

- 成立条件：利用者のYAMLに旧キーまたは誤記があり、rewrite_user_layers=Trueの操作を実行すること。
- エラー・被害：意図したタイムアウトや試行設定が既定値となり、削除後は元の値を参照できない。設定の静かなデータ喪失。原子的保存や変更競合検知の欠如を主張するものではない。
- 根拠：QS-006: network.max_retries=9とrequset_timeout_seconds=90が除外。QS-006-rewrite: _apply_cleanupで実際の一時YAMLから両キーが消え、有効なmax_attempts=2は保持された。
- 該当箇所：[configuration/layers.py:137](../../../src/image_downloader/configuration/layers.py#L137)、[configuration/layers.py:322](../../../src/image_downloader/configuration/layers.py#L322)、[configuration/layers.py:407](../../../src/image_downloader/configuration/layers.py#L407)、[commands/download.py:53](../../../src/image_downloader/commands/download.py#L53)
- 分類・参考観点：CWEは付けない（品質・仕様・書式上の指摘）。無効入力の診断・設定保全。
- 改善案：未知キーを診断し、削除・移行前に差分と復元可能なバックアップを用意する。旧キーの値の意味を考慮した移行を提供する。既存の明記された正規化仕様を変更する場合は互換性を説明する。

<a id="qs-007"></a>

### QS-007 — verify workflowのpassedは画像内容が健全なことを示さない

**参考・再現済み**。種類：存在検査と内容の完全性検査の差。存在検査の現行契約を満たす。内容検査を保証しない。

成果物の存在・非空などを検査するが、画像デコードや保存時hashとの一致を検査しない。1byte読めれば、置換された非画像ファイルもpassedになり得る。

- 成立条件：保存後に画像が非空の別内容へ置換・破損しており、存在検査を画像完全性の検査と解釈すること。
- エラー・被害：内容の破損・改変の見落とし。本来の存在検査契約への違反や、任意のコード実行を示すものではない。
- 根拠：QS-007: 既存make_runが作る非画像・非空ファイルに対しverify_workflowがpassed。
- 該当箇所：[application/workflow_verification.py:30](../../../src/image_downloader/application/workflow_verification.py#L30)
- 分類・参考観点：CWEは付けない（品質・仕様・書式上の指摘）。検証結果の意味と保証範囲の明示。
- 改善案：存在、画像デコード、保存時hash照合を明確な検証レベルに分ける。現在のpassedが保証する内容を表示する。hash一致も取得元が正しいことまで保証しない。

<a id="qs-008"></a>

### QS-008 — 取得済み画像が消えてもupdatedで再取得されない

**中・再現済み**。種類：完了状態と現在の成果物・保存条件の未照合。FAQに記載されたupdated選択仕様の改善課題。

updated選択は候補revisionとcompletedを使い、現在のファイルの有無や保存先・変換設定と照合しない。

- 成立条件：完了済み候補のrevisionが変わらず、成果物が削除されたか保存条件が変更されたこと。
- エラー・被害：欠損が自動復旧せず、変更後の保存先や加工条件での取得も行われない。updatedを復旧処理と解釈した場合に未取得状態を見落とす。
- 根拠：QS-008: 初回workflowで保存したPNGを削除し、同じ入力の次回updatedの選択が0件になった。保存条件変更は候補選択式とFAQによる静的確認で、今回のプローブでは削除だけを再現した。
- 該当箇所：[storage/workflow.py:140](../../../src/image_downloader/storage/workflow.py#L140)、[plugins/builtin.py:114](../../../src/image_downloader/plugins/builtin.py#L114)
- 分類・参考観点：CWEは付けない（品質・仕様・書式上の指摘）。状態と実体の整合性。
- 改善案：成果物・保存条件と完了記録を対応付け、欠損・条件変更時の再取得を選択可能にする。当面の回避策は通常downloadまたはdownload-scope all。

<a id="qs-009"></a>

### QS-009 — アニメーションGIFのWEBP変換で先頭フレームだけが残る

**中・再現済み**。種類：複数フレーム情報の無表示切捨て。現行の再エンコード保証範囲の不足。

入力の全フレームを検査するが、save時に全フレームを保存する指定がない。WEBPへの変換でも先頭フレームの静止画となる。

- 成立条件：複数フレーム画像をORIGINAL以外で再エンコードすること。ORIGINALの元byte保存はこの問題の対象外。
- エラー・被害：動き・時間情報・後続フレームが失われ、取得した情報が不完全になる。
- 根拠：QS-009: 2フレームGIFから出力したWEBPのn_framesは1。
- 該当箇所：[media/image_processor.py:161](../../../src/image_downloader/media/image_processor.py#L161)
- 分類・参考観点：CWEは付けない（品質・仕様・書式上の指摘）。変換時の情報保全。
- 改善案：形式ごとのアニメーション保持方針を設け、duration・loop等も含む全フレーム保存を実装するか、静止画化を明示して警告・拒否する。保持が必要な場合は当面ORIGINALを使う。

<a id="qs-010"></a>

### QS-010 — 巨大な整数設定がConfigurationErrorではなくOverflowErrorになる

**低・再現済み**。種類：数値変換限界の未処理・例外境界の不足。設定エラー診断への正規化不足。

有限値検証でmath.isfiniteを整数に直接適用する。floatで扱えない巨大なPython整数はOverflowErrorとなり、ValidationErrorだけを変換する上位処理を通過する。Cの整数オーバーフローではなく、Python整数からfloatへの変換限界である。

- 成立条件：FiniteNumberを使う設定へ10**400相当の整数を直接入力するか、YAML数値として記載すること。通常の取得先の応答からこの設定を変更できるわけではない。
- エラー・被害：ライブラリ利用者が期待するConfigurationErrorを受け取れず、設定項目を特定する診断が欠ける。未処理なら呼出元の処理が終了する。CLIには最上位の一般例外処理があり、プロセス全体の無防備なクラッシュとは区別する。
- 根拠：QS-010: validate_configで巨大整数はOverflowError。一方NaN、inf、bool、文字列、0、負数はConfigurationError。今回の再現は直接APIでありCLI終了コードは実測していない。
- 該当箇所：[configuration/models.py:28](../../../src/image_downloader/configuration/models.py#L28)、[configuration/layers.py:189](../../../src/image_downloader/configuration/layers.py#L189)、[configuration/layers.py:311](../../../src/image_downloader/configuration/layers.py#L311)
- 分類・参考観点：[CWE-1284](https://cwe.mitre.org/data/definitions/1284.html)。CERT-C INT31-Cを参考にした変換範囲の検証。
- 改善案：OverflowErrorを検証エラーへ正規化し、floatへ変換する前に表現可能範囲を検証する。必要なら設定ごとに実用的な上限を設ける。

<a id="qs-011"></a>

### QS-011 — 不正cookieデータでAttributeErrorやKeyErrorが露出する

**中・再現済み**。種類：JSON構造・必須項目の未検証・例外変換の漏れ。cookieエラー・破損復旧契約の不足。

import_fileはJSON rootの型を確認せず.getを呼ぶ。_decryptとcookie recordの復元も必須項目の辞書参照を行い、_load_unlockedはKeyError・AttributeErrorをAuthenticationErrorに変換しない。import_fileが破損した既存cookieを復旧する分岐はAuthenticationErrorだけを捕捉する。

- 成立条件：利用者がJSON配列等をcookie exportとしてimportする、または不正構造のcookie容器・復号後recordが存在すること。AES-GCMの暗号認証を破る試験ではなく、取得先が通常のSet-Cookieだけで任意の暗号化recordを作れることも示していない。
- エラー・被害：認証データエラーとして扱えず操作が失敗する。破損容器の種類によっては明示的importによる復旧経路も失敗する。暗号鍵の漏洩・暗号解読・特権昇格は確認していない。
- 根拠：QS-011: []のimportがAttributeError。QS-011-record: 架空の32byte鍵で正しくAES-GCM暗号化した[{}]をloadするとKeyError。OS credential storeは使っていない。
- 該当箇所：[storage/cookies.py:43](../../../src/image_downloader/storage/cookies.py#L43)、[storage/cookies.py:91](../../../src/image_downloader/storage/cookies.py#L91)、[storage/cookies.py:209](../../../src/image_downloader/storage/cookies.py#L209)、[storage/cookies.py:259](../../../src/image_downloader/storage/cookies.py#L259)
- 分類・参考観点：[CWE-1287](https://cwe.mitre.org/data/definitions/1287.html)、[CWE-248（ライブラリ境界）](https://cwe.mitre.org/data/definitions/248.html)。型・必須項目の検証、CERT-C ERR33-Cを参考にしたエラー処理。
- 改善案：容器・復号後recordの型、必須項目、各値の型・範囲をschemaで検証し、想定内の不正データをAuthenticationErrorへ統一する。KeyError等が実行前に出ない構造にし、既存データを消さず復旧手段を診断する。

<a id="qs-012"></a>

### QS-012 — SMTPの一部宛先拒否を通知成功として扱う

**低・再現済み**。種類：API戻り値の未確認・部分失敗の見落とし。配送結果の診断不足。

smtplib.send_messageが返す拒否宛先dictを使用しない。少なくとも1宛先が受理された部分拒否では例外にならず、通知側は成功として進む。

- 成立条件：複数宛先のうち一部がSMTPサーバーに拒否されること。全宛先拒否や接続失敗の既存例外処理とは別。
- エラー・被害：一部利用者へ通知が届かず、拒否された宛先の診断も残らない。画像保存処理の失敗を意味するものではない。
- 根拠：QS-012: fake SMTPが1件の550拒否dictを返しても_send_mailは例外なしで終了した。実メールは送信していない。
- 該当箇所：[observability/notifications.py:406](../../../src/image_downloader/observability/notifications.py#L406)
- 分類・参考観点：CWEは付けない（品質・仕様・書式上の指摘）。CERT-C ERR33-Cを参考にした戻り値確認。
- 改善案：拒否dictを確認し、完全成功・部分拒否・全拒否を区別して秘匿された診断を残す。主ダウンロードの成功とは分ける。

<a id="qs-013"></a>

### QS-013 — runtime組立て失敗後も動的プラグインmoduleが残る

**低・再現済み**。種類：初期化失敗時の後始末不足。組立て失敗時の所有資源解放不足。

composeは先にregistryを作成・importし、その後の依存組立て失敗でregistry.closeを呼ばない。完成したserviceが返らないため通常のservice.closeでも解放できない。inspectionとworkflow planの組立ても同じ形である。

- 成立条件：信頼済みプラグインがimportされた後に、cookie/keyring等の依存組立てが失敗すること。単発CLI終了時はOSがプロセス資源を回収する。
- エラー・被害：同一プロセス内の再組立てを繰り返す利用ではmodule namespaceと参照が蓄積する。単発CLIでの恒久的なOS資源漏洩や、未署名コード実行を示すものではない。
- 根拠：QS-013: _build_dependenciesの失敗を模擬すると、compose例外後にも新規plugin moduleが2件sys.modulesに残った。プローブ自身のfinallyでregistryを解放した。
- 該当箇所：[application/composer.py:54](../../../src/image_downloader/application/composer.py#L54)、[application/composer.py:59](../../../src/image_downloader/application/composer.py#L59)、[application/composer.py:64](../../../src/image_downloader/application/composer.py#L64)
- 分類・参考観点：[CWE-772](https://cwe.mitre.org/data/definitions/772.html)。初期化と解放の対称性・異常終了時のcleanup。
- 改善案：組立て途中の所有資源をExitStack等で管理し、後続段階で失敗した場合はregistryと作成済み依存を解放する。成功時だけserviceへ所有権を渡す。

<a id="qs-016"></a>

### QS-016 — 異なるプラグインIDが同じ環境変数の秘密を受け取る

**中・再現済み**。種類：秘密参照namespaceの名前衝突。異なる有効plugin IDから同じ環境変数名を生成する。

plugin IDの非英数字をすべて_へ置換するため、com.example.a-bとcom.example.a.bが同じCOM_EXAMPLE_A_Bとなる。同じreference名なら両方が同じ環境変数を読んで返し、個別のkeyringより優先する。

- 成立条件：このように衝突する2つの有効plugin IDを利用し、同じreference名を環境変数で設定すること。プラグインは既に信頼済みのPythonコードであり、悪意あるプラグイン間のOS隔離を保証する機構ではない。
- エラー・被害：別プラグインに意図しない認証トークンを渡して認証先を誤る。送信先や実装次第では外部への秘密情報送信につながり得るが、今回の再現では秘密の参照混同だけを確認した。
- 根拠：QS-016: 両IDをAppConfigが受理し、異なるRuntimeSecretsのgetが同じ架空の環境変数値を返した。OS credential storeや外部送信は使っていない。
- 該当箇所：[credentials/plugin_secrets.py:21](../../../src/image_downloader/credentials/plugin_secrets.py#L21)、[credentials/plugin_secrets.py:23](../../../src/image_downloader/credentials/plugin_secrets.py#L23)、[configuration/models.py:213](../../../src/image_downloader/configuration/models.py#L213)
- 分類・参考観点：[CWE-694](https://cwe.mitre.org/data/definitions/694.html)。識別子の一意性・秘密の参照先の整合性。
- 改善案：IDから一意な環境変数名を生成するencode方式、またはregistryでの衝突検出を導入する。変更時は旧環境変数からの移行を明示する。現行の回避策はreference名を分けるか、衝突する環境変数を使わず個別keyringを使うこと。

<a id="qs-014"></a>

### QS-014 — 79字・文書行72字というPEP 8推奨との差がある

**参考・静的根拠で確認**。種類：PEP 8の行長推奨との差分。プロジェクトは120字を採用。PEP 8との差分。

厳格な追加Ruff検査ではE501が1819箇所。コメント・docstringの72字超過も別に計測する。既存規約ではline-length=120で、本体のRuff検査は成功している。

- 成立条件：PEP 8の標準的な行長を評価基準とする場合。プロジェクト固有規約を優先する方針もPEP 8自体が認めている。
- エラー・被害：小さな画面や並列差分の可読性に影響し得る。実行エラー・情報漏洩・コード実行の根拠にはならない。
- 根拠：raw/ruff-pep8.stdoutのE501全件とstyle-occurrences.csv。72字検査は長さの機械計測であり、URL・コード例などの個別例外まで規約違反と断定しない。
- 該当箇所：全箇所はstyle-occurrences.csv
- 分類・参考観点：CWEは付けない（品質・仕様・書式上の指摘）。PEP 8 Maximum Line Length・プロジェクト内の一貫性。
- 改善案：120字の採用理由と文書行の方針を明文化する。変更を採用する場合に限り、互換性や可読性を保って折り返す。監査のための製品コード自動整形はしない。

<a id="qs-015"></a>

### QS-015 — 2件の例外クラス名にError接尾辞がない

**参考・静的根拠で確認**。種類：例外クラスの命名規約差分。公開例外APIの命名上の差分。

追加RuffのN818がUnsupportedSiteFeature、SecretNotFoundを検出した。既存の規約ではN規則を選択していない。

- 成立条件：PEP 8の例外命名推奨を厳格に適用する場合。
- エラー・被害：命名の一貫性・読み手の理解に影響し得る。例外が捕捉されないことやセキュリティ問題を意味しない。
- 根拠：N818の2件。全該当箇所をstyle-occurrences.csvにも掲載する。
- 該当箇所：[exceptions.py:53](../../../src/image_downloader/exceptions.py#L53)、[exceptions.py:65](../../../src/image_downloader/exceptions.py#L65)
- 分類・参考観点：CWEは付けない（品質・仕様・書式上の指摘）。PEP 8 Exception Names。
- 改善案：公開名の変更は互換性に影響するため、自動的にrenameせず命名方針を決める。現行名を維持する場合は意図した例外として規約に明記する。

<a id="qs-017"></a>

### QS-017 — 共有保存先の悪意あるリンク差替えに対する保証が未確認

**未評価・未確定**。種類：ローカルファイルの検証・使用間の競合候補。共有・攻撃者書込み可能なrootの追加評価候補。

FileSystemやlockはpathの事前検査と後続のopen/replaceを分けて行う。bounded read等にはfstatや再検査があるが、全操作をディレクトリhandle基準で固定していない。

- 成立条件：別の主体が保存先やstateの親を変更でき、リンク・reparse point等を検査と使用の間に差し替えられること。通常の自己所有rootとは条件が異なる。
- エラー・被害：成立すれば想定root外の読込み・書込み等の可能性があるが、今回のWindows環境では攻撃スケジュールと実害を確認していない。確定脆弱性・任意ファイル書込みとして数えない。
- 根拠：path検査とOS操作の静的確認。Windowsでsymlink作成を要する既存テスト4件はスキップ。プラグインの検証・import差替えはQS-001で別に確認済みである。
- 該当箇所：[storage/filesystem.py:102](../../../src/image_downloader/storage/filesystem.py#L102)、[storage/filesystem.py:131](../../../src/image_downloader/storage/filesystem.py#L131)、[storage/filesystem.py:165](../../../src/image_downloader/storage/filesystem.py#L165)、[storage/interprocess_lock.py:91](../../../src/image_downloader/storage/interprocess_lock.py#L91)
- 分類・参考観点：[CWE-367（候補）](https://cwe.mitre.org/data/definitions/367.html)。検証と使用の一貫性。
- 改善案：共有rootを想定する場合に限り、別主体によるディレクトリ差替えのOS別プローブを実行する。POSIXのdir_fd/O_NOFOLLOW、Windowsのhandle/reparse検証など、検査対象と操作対象を固定する設計を検討する。
