<a id="additional-file-callbacks"></a>

# 追加ファイルのコールバック

`plugin.py`は公開v3 APIだけを使う、独立したサイトプラグインです。最小の署名なしmanifest、作者設定、署名用メタデータを同梱しています。
共通の模擬サーバー、専用作業先へのコピー、署名なし／利用者署名によるstrict実行は[ローカル機能ラボ](../plugin-v3-feature-lab/README.md)を参照してください。

全7段階を宣言します。manifest前に公開補助設定を取得し、画像要求前には非秘密のvariantを取得して画像インスタンスごとに保持します。
`create_image_request()`がvariantを要求ヘッダーと`ImageFetchRequest.plugin_data`へ渡します。補助設定／variantが欠ける場合は、後続フックで明示的に失敗させます。

| 段階 | 保存・利用する内容 |
| --- | --- |
| BEFORE_MANIFEST | bootstrapを取得、inspectがlayoutを必要とする。確定後に各章へ保存 |
| AFTER_MANIFEST | 章の公開識別情報 |
| BEFORE_IMAGE_REQUEST | 画像別variantを取得・保存、要求に利用 |
| AFTER_IMAGE_REQUEST | 成功応答ステータス |
| BEFORE_IMAGE_SAVE | 変換後の形式・バイト数 |
| AFTER_IMAGE_SAVE | saved/skipped等の結果 |
| AFTER_DOWNLOAD | 章の画像数とcallback集計 |

各章の`metadata`へ保存します。保存後のコールバックには、書き込んだbytes、またはskip時の既存bytesが渡ります。
`cleanup_after_use()`が操作内の保持データをクリアします。複数章・並行画像は画像インスタンスと安定IDで分離します。
inspectionは追加hookを呼ばないため、この例のinspectは必要なbootstrapがない状態で明示的に失敗します。更新確認hookは未実装です。

Cookie・資格情報・鍵を保存する例ではありません。認証には通常の要求・認証フックを使います。
追加ファイルのoptional失敗と、後続処理に必要な情報不足を区別するための例です。
[フックの仕様](../../docs/v3/reference/plugin-hooks.md#optional-additional-files)を参照してください。
