# 実装・ドキュメントの利用観点レビュー

確認日: 2026-10-04。対象は現在の `src/image_downloader`、v3ドキュメント、CLI、現行テスト、同梱プラグイン。履歴用のv1/v2資料を現行仕様として扱わない。実装の変更は行わない。

追記（2026-10-04）: R01・R02は利用者の指示により修正。以下の一覧・再現結果は修正前の調査記録である。
R01は最終レスポンスURLと最初のHTTP(S) base hrefによる相対URL解決・最終URLのRefererへ変更した。
R02は再エンコード時のEXIF保持指定を実装し、EXIFを削除する場合はOrientationを画素へ反映する。
現在の契約は[FAQ](../../v3/how-to/faq.md)と[ライブラリ参照](../../v3/reference/library-api.md)を参照する。
修正後の関連テスト（generic workflow、画像処理、runtime hardening、文書契約・公開API）は89件成功。
`ruff check .`、90ソースファイルのmypy、`git diff --check`も成功した。

追記（2026-10-05）: R04も修正。MIME許可リストを取り込み、既定を`both`へ変更した。
空本文は全モードで拒否し、デコード検査では全フレームの画素を読み込む。
Content-Typeなし・`application/octet-stream`は形式未指定として扱い、既定では実体検査後に保存する。
既存の明示設定は変更しない。対応形式と移行手順は
[保存前の画像検査](../../v3/reference/configuration.md#image-input-validation)を参照する。
R04修正後の最終検証は、全v3テストを4グループに分割して実行し、合計1313件成功・16件スキップ。
全90ソースファイルの構文解析、ruff、mypy、差分チェックも成功した。

通常の公開HTMLページの取得、日本語サイト、画像変換、同じ保存先への反復実行、失敗後の再実行、長期保存・自動実行を利用場面として評価した。外部の実サイトや実SMTP・ブラウザへの接続は行っていない。専用サイトプラグインによって回避できる項目も、標準の汎用HTML利用では不足として挙げる。

優先度: **P1**＝誤取得・データ消失・成功判定・初回利用に直結するため優先対応、**P2**＝対応範囲・効率・継続運用を改善、**P3**＝後続の利便性改善。本調査ではP1/P2に絞った。種類の「仕様改善」「機能追加」は現行契約の変更を伴う提案であり、実装バグと区別する。

## 一覧

| ID | 優先度・種類 | 現状・問題 | 実装／修正案 | 理由・利用への影響 | 根拠 |
| --- | --- | --- | --- | --- | --- |
| R01 | P1・不具合 | 汎用HTMLの相対URLは入力URLを基準にする。リダイレクト後のURLとHTMLの`base href`を使わない。 | `response.url`を基準にし、HTTP(S)の有効なbase要素を反映する。Refererも取得先に整合させる。 | パスの異なる転送先、CDNをbaseにするページで画像の取得先が誤る。リダイレクトとbaseの両方で再現確認。 | [builtin.py:33](../../../src/image_downloader/plugins/builtin.py#L33)、[builtin.py:73](../../../src/image_downloader/plugins/builtin.py#L73) |
| R02 | P1・不具合 | 公開DTOの`ImageSaveOptions.exif=True`がエンコーダへ渡らず、変換後のEXIFが失われる。Orientationを画素へ反映する処理もない。 | EXIF保存指定を実装し、向きの正規化・保存・削除の方針を明示する。位置情報等の扱いも設定と整合させる。 | 指定どおりに保存されず、EXIF Orientationに依存する写真は変換後に横向き等で表示される。小さなJPEGで再現確認。 | [models.py:281](../../../src/image_downloader/models.py#L281)、[image_processor.py:116](../../../src/image_downloader/media/image_processor.py#L116) |
| R03 | P1・仕様改善 | 汎用HTMLが画像0件でも1章を返すため、通常取得・workflowは成功し、workflowの完了対象になる。空章数の設定では防げない。 | 画像0件を明示した結果・警告とし、既定では未取得扱いにするか、独立した許可設定を設ける。verifyとの判定も揃える。 | ログイン画面、JS描画ページ、抽出失敗を「取得成功」と誤認し、次のupdated実行で対象から外れる。現在のテストとFAQで仕様として確認。 | [service.py:550](../../../src/image_downloader/application/service.py#L550)、[test_generic_workflow.py:128](../../../tests/v3/application/test_generic_workflow.py#L128)、[workflow_verification.py](../../../src/image_downloader/application/workflow_verification.py) |
| R04 | P1・仕様改善 | 既定のORIGINAL＋content_type検査は、`image/png`と名乗る不正なバイト列を通す。宣言MIMEだけでは破損・偽画像を検出しない。 | 対応形式のデコード検査を簡単に有効化できる推奨設定・プリセットを提供し、既定の妥当性を再評価する。SVG等は形式別に検査・非対応判定する。 | 破損ファイルも保存成功・workflow完了になる。`b'not image data'`を`image/png`として与えて検査通過を確認。全形式へPillow検査を一律適用するとORIGINALの対応範囲が狭まるため設計が必要。 | [artifact_pipeline.py:251](../../../src/image_downloader/media/artifact_pipeline.py#L251)、[app.yaml](../../../src/image_downloader/app.yaml) |
| R05 | P1・仕様改善 | 既定保存先は章番号・タイトル、ファイル名は画像index、既存ファイルはoverwrite。異なるURLでも同タイトルなら同一保存先になり得る。 | 安定した作品ID／URL由来キーで保存先を分離するか、URLと保存先の所有関係を記録して別作品の上書きを拒否する。Quick startにも安全な保存設定例を追加する。 | 同名作品やタイトルを付けないページを続けて取得すると、先の画像を置き換える。ロックは同時書込みを防ぐが作品の取り違えを防がない。`isolate_by_plugin`だけでは同一プラグイン内の衝突は残る。 | [configuration/models.py:57](../../../src/image_downloader/configuration/models.py#L57)、[output_allocator.py:140](../../../src/image_downloader/output/output_allocator.py#L140) |
| R06 | P1・仕様改善 | 状態変更操作前にユーザーYAMLの旧キー・未知キーを無表示で削除する。旧キーから新キーへの値の移行は行わない。 | 通常読込みでの削除を再評価し、明示的な移行操作、削除／変換差分の表示、バックアップを設ける。typoは具体的な診断で知らせる。 | 設定ミスに気づきにくく、旧設定の並列数・待ち時間等が既定へ戻る。元の値も失われる。README記載どおりの動作であり、実装バグではない。 | [layers.py:87](../../../src/image_downloader/configuration/layers.py#L87)、[layers.py:137](../../../src/image_downloader/configuration/layers.py#L137)、[layers.py:322](../../../src/image_downloader/configuration/layers.py#L322)、[README](../../../README.md) |
| R07 | P1・文書改善 | 「最初のダウンロード」のURLが`example.test`で、記載手順だけでは成功体験を再現できない。 | 小さな画像とHTMLを含むローカルデモと起動・取得コマンド、期待ファイルを提供する。プラグイン導入が必要な例は導入まで完結させる。 | 新規利用者がURL例をそのまま試すと失敗する。既存TODO DOC-EXT-001とも一致。外部サイトの保守責任を負わずローカルで解決可能。 | [first-download.md](../../v3/tutorials/first-download.md)、[open-documentation-todos.md](../../v3/maintenance/open-documentation-todos.md) |
| R08 | P2・機能追加 | 汎用HTMLは`img[src]`だけ。`srcset`、`picture/source`、`data-src`等を抽出しない。 | 標準のレスポンシブ画像候補の選択規則を実装し、非標準lazy属性は限定したopt-in設定にする。重複・非HTTP候補も扱う。 | 実際の画像を取り逃がし、低解像度サムネイルやplaceholderだけを取得し得る。base・data-src・srcsetを含む小さなHTMLで抽出結果を確認。JS描画対応は別設計とする。 | [builtin.py:37](../../../src/image_downloader/plugins/builtin.py#L37) |
| R09 | P2・不具合 | 汎用HTMLはHTTP charsetやmeta宣言に関係なくUTF-8固定、置換デコード。 | BOM・HTTP charset・HTMLのencoding宣言を考慮する復号処理を追加する。判定不能時の扱いを決める。 | Shift_JIS等の日本語サイトで作品名・保存先が文字化けする。Shift_JIS本文とcharset付きレスポンスで再現確認。 | [builtin.py:76](../../../src/image_downloader/plugins/builtin.py#L76) |
| R10 | P2・機能追加 | 画像URLを直接渡しても汎用HTMLがバイナリをHTMLとして扱い、画像0件になる。 | 画像レスポンスを1画像のmanifestにする経路、またはHTML専用であることを示す明確なエラーを追加する。 | 画像ダウンローダーとして一般的な「画像URLを保存」が標準では完結しない。PNGレスポンスから画像0件となることを確認。 | [builtin.py:67](../../../src/image_downloader/plugins/builtin.py#L67)、[builtin.py:73](../../../src/image_downloader/plugins/builtin.py#L73) |
| R11 | P2・効率改善 | `existing_file=skip`の判定はHTTP取得・変換後。既存ファイルを残す指定でも通信と加工は走る。 | 保存名が事前に確定する画像に限定した早期skipを設ける。応答由来のファイル名・拡張子やプラグインhookに依存するケースは現行経路に残す。 | 再実行時の帯域・CPU・サイト負荷を減らせる。画像が既に存在してもサーバー障害でfetch失敗する現状も改善できる。hookの契約変更は明示する。 | [service.py:1209](../../../src/image_downloader/application/service.py#L1209)、[service.py:1236](../../../src/image_downloader/application/service.py#L1236)、[service.py:1254](../../../src/image_downloader/application/service.py#L1254) |
| R12 | P2・仕様改善 | GIF等のアニメーションを変換すると最初のフレームだけになる。フレーム保存・duration・loopの引継ぎ指定がない。 | 対応する出力形式では全フレームを保存するか、静止画化を明示指定とする。非対応の変換には診断を出す。 | 保存形式変更だけのつもりでアニメーションを失う。2フレームGIFからWEBPへの変換が1フレームとなることを確認。ORIGINALのバイト保持は既に対応済み。 | [image_processor.py:142](../../../src/image_downloader/media/image_processor.py#L142) |
| R13 | P2・機能追加 | 成功画像の保持はworkflow呼出し内のメモリ台帳。次の起動では未完了URL単位でやり直す。 | 保存済み画像の識別・保存条件・パス・サイズ等を永続記録し、検証できた成功画像を再実行でも再利用する。 | 大量画像の途中失敗・中断で取得済み画像を繰り返す。Rangeによる1画像内の途中再開とは別機能で、まず画像単位の再開が有効。 | [workflow_retry.py:41](../../../src/image_downloader/application/workflow_retry.py#L41)、[library-api.md](../../v3/reference/library-api.md) |
| R14 | P2・堅牢性改善 | HTTP応答は最大64MiBだが全量をメモリに保持。既定で最大24画像job、画素数上限なし。画像workerではPillowの標準画素警告上限を無効化し、結果待ちのtimeoutもない。 | 推奨画素数上限、同時保持バイト数の制御、加工時間制限とworker復旧方針を設ける。必要なら一時ファイル経由の取得を検討する。 | 高解像度画像・遅い加工でメモリや待ち時間が大きくなり得る。ネットワーク同時数8は加工待ち画像のメモリ総量を制御しない。負荷試験は未実施のため具体的なピーク値は断定しない。 | [app.yaml](../../../src/image_downloader/app.yaml)、[gateway.py](../../../src/image_downloader/transport/gateway.py)、[image_processor.py:103](../../../src/image_downloader/media/image_processor.py#L103)、[image_processor.py:113](../../../src/image_downloader/media/image_processor.py#L113) |
| R15 | P2・堅牢性改善 | パス要素長制限はopt-in、指定してもPythonの文字数で切る。OS・ファイルシステムのバイト数／UTF-16単位やパス全体の制限とは一致しない。 | 対象環境に合わせた自動短縮とハッシュsuffix、パス全体の事前診断を追加する。 | 長い作品名、日本語、絵文字等で保存に失敗する。既存の`max_component_length`だけではすべての環境を保証できない。OS別の境界再現は未実施。 | [configuration/models.py:64](../../../src/image_downloader/configuration/models.py#L64)、[filesystem.py:25](../../../src/image_downloader/storage/filesystem.py#L25) |
| R16 | P2・機能追加 | genericの更新revisionは画像URL・index・IDの一覧ハッシュ。同一URLで画像本体が差し替わっても検出しない。 | 明示的な再検査モード、ETag／Last-Modified等のHTTP validatorを使う方式を選択可能にする。validatorのないサイトは限界を表示する。 | 固定URLの画像が更新されるサイトでupdated運用が更新を逃す。全画像の常時ハッシュ取得は負荷が高いため、設定可能な方式が適切。現行FAQにも制限記載あり。 | [builtin.py:79](../../../src/image_downloader/plugins/builtin.py#L79)、[faq.md](../../v3/how-to/faq.md) |
| R17 | P2・機能追加 | `verify workflow`の実ファイル検査は存在・非空等。記録ファイルを別の非空データに置換しても、デコードや内容同一性は判定しない。通常downloadの独立した画像台帳もない。 | 検証レベルを存在／decode／保存時hashに分ける。保存時の最終bytesを記録し、一覧・詳細・再取得対象の抽出に利用する。 | 長期保存の破損・置換を発見できる。「verify実装済み」と「画像の完全性保証」を混同しない表示・文書も必要。hashは保存時との一致を示し、取得元の正しさまでは保証しない。 | [workflow_verification.py:16](../../../src/image_downloader/application/workflow_verification.py#L16)、[workflow_verification.py:30](../../../src/image_downloader/application/workflow_verification.py#L30)、[development-tasks.md](../../v3/maintenance/development-tasks.md) |
| R18 | P2・文書保守 | backlogに「検証コマンド未実装」「ローカルSMTP統合未整備」が残る一方、verifyコマンドとopt-in Mailpit統合テストは存在する。全部完了でも全部未実装でもない。 | 既存verify／履歴／Mailpitの到達点と、hash台帳・TLS等の未達部分に分けてbacklogを更新する。本文の「今回」も作成日・対象作業を明示する。 | 重複開発や、既に使える機能の見落としを防ぐ。テストの存在と実行済みの範囲を別々に示す。 | [development-tasks.md:16](../../v3/maintenance/development-tasks.md#L16)、[development-tasks.md:27](../../v3/maintenance/development-tasks.md#L27)、[commands/verify.py](../../../src/image_downloader/commands/verify.py)、[test_mailpit_integration.py](../../../tests/v3/observability/test_mailpit_integration.py) |
| R19 | P2・通知判定の改善 | SMTPの`send_message()`が返す宛先別の拒否情報を無視し、一部宛先の拒否でもsenderはTrueを返す。 | 返却された拒否情報を確認し、部分配送を通知診断へ反映する。主ダウンロード結果は維持し、全員への無条件再送による重複を避ける。 | 複数宛先の一部に通知されないのに、通知成功として扱われる。拒否辞書を返すfake SMTPでTrueが返ることを確認。既存SMTP-001の設計対象を具体化する項目。 | [notifications.py:406](../../../src/image_downloader/observability/notifications.py#L406)、[development-tasks.md](../../v3/maintenance/development-tasks.md) |

## 再現確認

外部通信のないPython probeで、現行のHTML parser、mockのRequestResponse、少量のPillow生成画像、変換関数を使用した。

| ケース | 入力・期待 | 現行結果 |
| --- | --- | --- |
| リダイレクト後の相対画像 | 入力`https://example.test/old`、応答先`https://example.test/new/gallery/`、`img src="a.png"`。期待は`/new/gallery/a.png`。 | `https://example.test/a.png` |
| baseとlazy／responsive画像 | CDNのbase、`img src="b.png"`、`img data-src="c.png"`、`img srcset="d.png 1x, e.png 2x"` | 入力ページ基準の`b.png`だけ |
| HTML文字コード | `charset=shift_jis`とShift_JISのタイトル「日本語」 | `\ufffd\ufffd\ufffd{\ufffd\ufffd` |
| 画像URLへの直接応答 | 小さなPNG、`Content-Type: image/png` | 1章、画像0件 |
| 既定入力検査 | `b'not image data'`、`Content-Type: image/png` | `_validate_input`が例外なしで通過 |
| アニメーション変換 | 2フレームGIF → WEBP | 1フレーム |
| EXIF保存指定 | 4×2 JPEG、Orientation=6、`format=JPEG, exif=True` | 4×2のまま、Orientationは消失 |
| SMTP一部宛先拒否 | fake SMTPが2宛先中1宛先の550拒否辞書を返す | `EmailNotificationSender.send()`はTrue |

画像0件で通常取得・workflowが成功する点は既存テストでも明示される。P1/P2の仕様変更案は再現した不具合とは区別し、互換性・プラグインhook・対応画像形式の受入条件を決めてから実装する。

## 既存機能と対応順

設定層・profile、プラグイン署名／信頼、cookieとsecret参照、HTTP retry／同時数制御、atomic保存、出力先ロック、workflowの選択・再試行・履歴・dry-run、ログとworkflowファイルの検査、画像形式指定、ORIGINAL保存は既にある。これらを未実装の要求として再計画する必要はない。

まずR01・R02の確定不具合とR07の再現可能な導入手順を直し、R03～R06の成功判定・保存・設定変更方針を決める。次にR08～R12の対応範囲と再実行効率、R13・R17の永続台帳を進める。R14・R15は大量取得・OS差の受入試験と合わせ、R16は対象サイトの更新方式に応じて採用する。R18はバックログ更新時に併せて対応する。

## 検証記録

- 外部通信なしの再現probe: 上記8ケース実行済み。
- `python -m pytest -q`: **1240 passed, 16 skipped**（288.59秒）。テスト成功は現行仕様への整合を示すもので、上記の不足・仕様改善案を否定するものではない。
- `git diff --check`: エラーなし。
- 実サイト、実SMTP、ブラウザcookie、複数OSでの動作は本調査では未検証。
- 実装・設定・既存テストは変更していない。本レポートのみ追加。
