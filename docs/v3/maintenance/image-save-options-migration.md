# 保存条件フックへの移行

現行API番号内での破壊的変更です。API番号、マニフェストのスキーマ番号、本体・プラグインの版番号は変更しません。本体とプラグインを対応する実装へ揃えて更新してください。旧API・旧CLIの互換受付はありません。

## プラグイン

`ImageResource(save_options=...)`を削除します。画像の実形式は変換後の`ImageArtifact.data`・`content_type`・`extension`を一致させて表します。保存時の最低条件は、サイト・画像プロセッサーの任意同期フックから返します。

```python
def image_save_options(self, artifact: ImageArtifact, context: TransformContext) -> ImageSaveOptions | None:
    return ImageSaveOptions(min_width=1920, min_height=1080, quality=90, lossless=True)
```

`ImageSaveOptions.format`・`extension`は削除し、`exif`の未指定値は`None`になります。最低寸法・品質は最大値、ロスレスは一つでもTrueなら要求します。その他の明示項目はサイト→有効プロセッサー順に上書きします。フックは全変換後の同じArtifactと、自身の設定・既存のmetadataアクセス制約を持つContextを受け取ります。

最低条件だけでリサイズ・再エンコードはしません。入力画像を上回る画質を合成する処理もありません。条件未達は通常ログ・章ログの`image_save_policy_unmet`に記録し、成功扱いで保存を続けます。不正な戻り値や値の範囲は契約違反として拒否します。

コアによるエンコードはプロセッサー前から保存直前へ移動します。プロセッサーは直前の変換が返した実形式を受け取るため、特定のコア変換後形式に依存している実装を修正してください。

## 呼び出し・設定

`DownloadRequest`・`WorkflowRequest`の`force_image_format`を`image_format`へ変更します。`image_quality`も操作単位で指定できます。

```python
request = DownloadRequest(url=url, image_format="WEBP", image_quality=80)
```

CLIは`--force-image-format`を`--image-format`へ置き換えます。`--image-quality 80`はプラグインの品質下限・ロスレス要求にも優先します。`ORIGINAL`と品質指定の併用は拒否します。

設定ファイルの`output.image_format`は引き続き利用できます。受理値は`null`・`ORIGINAL`・`JPEG`・`PNG`・`WEBP`で、既定値を従来の`ORIGINAL`から`null`へ変更します。優先順位は操作引数／CLI、設定、最終Artifactの実データ形式です。既存ファイルの`ORIGINAL`は維持され、最終バイト列の保存とエンコード設定の非適用を要求します。設定が`ORIGINAL`でCLI品質だけを指定した場合は通信前に設定エラーとなるため、`--image-format JPEG`等も指定するか、設定を`null`へ変更してください。形式・品質の操作単位の指定は永続化しません。検査JSONの画像には`save_options`を出力せず、検査時に保存フックも呼びません。

署名対象ソースを変更した場合は既存手順で内容ハッシュ・署名を更新します。これらの整合性情報の更新に、バージョン値の変更を伴わせないでください。

[保存条件の仕様](../reference/configuration.md#image-save-format)、[フック契約](../reference/plugin-hooks.md#変換後の画像と保存形式)を参照してください。
