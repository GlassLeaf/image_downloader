# R04修正の保存可否・互換性への影響

確認日: 2026-10-05

比較対象は修正前のコミット`7d6116a168f68e68bf76eaa757f6c0245995517f`と、現在のR04未コミット実装。
途中のMIME許可リスト案は構文エラーがあったため、実行可能な比較基準には使用していない。
以下の「既定」は、以前の`content_type`／現在の`both`、不一致判定`accept`を意味する。
既存設定で`input_validation`を明示している場合は、その値を両版で維持して比較した。

## 確認方法

修正前のArtifactPipelineと画像処理関数をGitから取得し、現行版と同じ入力を渡した。
19種類の入力・processor出力 × 5種類の設定（既定、content_type、decode、both、both + mismatch error）
× 2種類の出力（ORIGINAL、PNG）の190組を比較した。
比較実験は画像worker関数をプロセス内で呼び、保存直前までのパイプラインの成否を確認する方法で、
ディスク保存そのものは行っていない。worker相当のPillow設定を使用した。

190組中、保存可能から拒否に変わった組合せは60、新たに保存可能になった組合せは0。
これは検証ケースの件数であり、実際の画像の失敗率を表す値ではない。
別途、実worker・実保存境界を含む`tests/v3/application/test_image_validation.py`を再実行し、49件成功した。

## 既定設定での保存可否

| 入力・処理内容 | 修正前 | 修正後 | 理由・注意点 |
|---|---|---|---|
| 正常なPNG等、許可された画像MIME | 保存 | 保存 | ORIGINALのバイト列・拡張子は維持 |
| 正常画像、Content-Typeなし／octet-stream | 保存 | 保存 | 新たに保存可能になった挙動ではなく、従来の許可を維持 |
| 正常PNGをimage/jpegと宣言 | 保存 | 保存 | acceptは維持。ORIGINALの拡張子が.jpegになる挙動も維持 |
| 空本文・画像ではない本文、ORIGINAL | 保存 | 拒否 | 空本文チェック・実体検査。形式変換では以前からデコードに失敗 |
| verifyは通るが画素読込みに失敗するJPEG、ORIGINAL | 保存 | 拒否 | 全画素を読み込む。PNG変換ではこのJPEGは以前から拒否 |
| 最初のフレームは正常、後続が破損したGIF | 保存 | 拒否 | ORIGINALだけでなく、正常な最初のフレームをPNGに変換する処理も拒否 |
| 正常なSVG、またはPillowが読めないHEIC等、ORIGINAL | 保存 | 拒否 | MIMEが許可されても実体検査に失敗。SVGは実測、HEIC等はデコーダーの有無に依存 |
| 正常画像でもimage/unknown、image/jpg、application/pdf等を宣言 | 保存 | 拒否 | content_type／bothで許可リストを適用。image/jpgは登録されていない別名。ORIGINALと形式変換の両方に影響 |
| text/html、application/json、application/xml等 | 拒否 | 拒否 | これらは以前から拒否 |
| max_image_pixels設定時に上限を超える画像 | ORIGINALでは保存可能 | 拒否 | 既定がbothになり、ORIGINALにも上限が適用。上限の既定nullは維持 |
| 最初のフレームは上限内、後続フレームだけ上限超過 | 保存可能 | 拒否 | TIFFで実測。形式変換でも後続フレームを検査 |
| processorが不正な本文・後続フレーム破損を返す | ORIGINALでは保存可能 | 拒否 | 最終実体検査を強化。後続フレーム破損はPNG変換にも影響 |
| processorが正常な画像のMIMEだけ未知MIMEへ変更 | 保存 | ORIGINALでは拒否 | PNG等への最終正規化は本文を検査し、MIME許可リストは再適用しない。その経路では保存可否は変わらない |
| processorで暗号化データを復号・不正本文を修復する構成、ORIGINAL | 保存可能 | processor実行前に拒否 | 最初の検査はsite transform後・processor前。復号はsite transformで完了させる必要がある |
| processorで未知MIMEを正常MIMEへ直す構成 | 保存可能 | processor実行前に拒否 | 最初のMIME検査で拒否される。site transformでMIMEを修正すれば後段へ進める |
| site transformで復号し、正常画像・許可MIMEにする構成 | 保存 | 保存 | 検査位置は変えていない。復号後の正常PNGが保存できることを現行テストで確認 |

## 既存の明示設定にも生じる影響

| 設定 | 修正による影響 |
|---|---|
| content_type + accept + ORIGINAL | 既定変更の影響は受けない。ただし空本文・未知MIMEは新たに拒否。許可MIMEの非空不正本文やSVGは引き続き保存できる |
| content_type + accept + JPEG/PNG/WEBP | MIME検査の制限に加え、変換処理自体で全フレームを検査する。後続フレーム破損・上限超過は新たに拒否 |
| decode | MIME許可リストは適用しない。JPEG切り詰め・後続フレーム破損・後続フレームの上限超過を新たに拒否 |
| both | MIME許可リストの制限と、全フレームの読込み・上限検査が強化される |
| content_type_mismatch: error | 許可された画像MIMEと実体の不一致は引き続き拒否。Content-Typeなし／octet-streamを不一致とみなさない挙動は比較元コミットでも同じ |

設定ファイルの自動書換えは行わないが、明示設定が維持されることは旧挙動の完全な維持を意味しない。
設定snapshot等に明示されたcontent_typeが残っていれば既定bothへの変更は適用されない。
設定が省略されている新規環境・設定レイヤーではbothが使われる。

## 保存以外の影響

- 正常なORIGINALは再エンコードしない。JPEG・PNG・GIF・WEBP・TIFFとGIFアニメーションのバイト一致を確認した。
- 変換時の形式選択・EXIF・拡張子決定ロジックは変更していない。正常なアニメーションからPNGへ変換すると最初のフレームだけ保存する従来挙動も維持する。
- 全フレーム読込みによるCPU・メモリ負荷と待ち時間が増える。ORIGINALもデコードを行い、processorがなくても前後2回検査する。画像workerは1プロセスなので、長いアニメーション等は他画像の処理待ちにも影響する。増加量は計測していない。
- workerのLOAD_TRUNCATED_IMAGES=Falseを明示する。親プロセスでTrueにしてもworkerでは切り詰めを許容しない。通常のFalseからの動作は変更しない。
- ImageProcessorの直接利用にも全フレーム検査が適用される。format未指定の同形式バイト返却経路で、切り詰めJPEGが以前は返され、現在はImageDecodeErrorになることも実測した。
- 新たな検査失敗は画像処理の失敗として記録される。画像本体の保存・上書きを行わず、既存ファイルを保持する。workflowは成功・完了扱いにならず、一部成功ならpartialになる。
- 一部の失敗理由は変わる。例えば未知image/* + mismatch errorはImageMimeMismatchErrorより先にImageContentTypeErrorになる。空本文は全モードでImageDecodeErrorを優先する。
- 検査失敗時は後続processor、BEFORE_IMAGE_SAVE、AFTER_IMAGE_SAVEの処理に進まない。HTTP取得とAFTER_IMAGE_REQUEST等の取得直後の追加ファイル処理は検査より前なので、既に発生し得る。
- 既に保存済みのファイルや完了済みworkflowを遡って検査・削除する変更はない。今回の検査が実行される取得・処理に適用される。

## 結論

比較元コミットに対して、保存を許可する条件の拡大は確認されなかった。
意図した不正本文の排除に加え、正常画像でも未知MIME・未対応デコーダー・processorで初めて修復する構成には互換性への影響がある。
必要な対処は、site transformで画像とMIMEを正常化すること、または用途に応じて明示設定を選ぶこと。
未知MIMEの正常画像はdecode、Pillow未対応形式のORIGINAL保存はcontent_type + acceptを選択できるが、後者は本文の妥当性を保証しない。
