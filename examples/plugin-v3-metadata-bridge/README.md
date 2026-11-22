<a id="metadata-aware-site-processor-plugin-pair"></a>

# メタデータを受け渡すサイト・画像処理プラグインの組合せ

`plugin-sources/`の署名済みソースを補う、プラグイン作成用の例です。`manifest.json`は含めていません。サンプルをインストール可能にするために秘密署名鍵をコミットしないでください。ソースを確認したうえで、後述の手順に従い、利用者が管理する鍵で各パッケージに署名してください。

`structured_plugin_data`対応本体が必要です。関連する次の2つのv3仕様を示します。

1. `site-plugin/metadata_bridge_gallery.py`は、作品IDを`DownloadManifest.plugin_data`、版情報を`Chapter.plugin_data`、`variant`と数値の`orientation`を`ImageResource.plugin_data`に設定します。要求・サイト変換・プロセッサーは`context.plugin_data.work/chapter/image`から非秘密の構造化データを読みます。このビューの共有に許可設定は不要です。
2. 同じ要求フックが、少量の非秘密データを`plugin_data`に持つ`ImageFetchRequest`を返します。`processor-plugin/metadata_bridge_processor.py`は、利用者がサイトと画像処理プラグインの組合せを明示的に許可した場合にだけ、そのデータを`TransformContext.transport_metadata`から読み取ります。

例に使うAPIは架空のものです。`GET https://metadata-bridge.example.test/api/works/{work_id}`は、`id`、`title`、`images`配列を持つJSONオブジェクトを返します。各画像は、秘密情報ではない`id`、`variant`（`full`または`preview`）、EXIF形式の`orientation`（`1`、`3`、`6`、`8`のいずれか）を持ちます。実際の画像エンドポイントは`/api/images/{image_id}/download?variant={variant}`です。

<a id="sign-and-install"></a>

## 署名とインストール

署名スクリプトがローカルパッケージをインポートできるよう、先にプロジェクトをインストールします。Ed25519の秘密鍵は、保護された場所へ保存し、このリポジトリ内には置かないでください。

```powershell
python -m pip install -e ".[dev]"
$signingKey = "C:\secure\image-downloader-plugin.pem"

python tools/sign_local_site_plugin.py --key $signingKey --create-key `
  .\examples\plugin-v3-metadata-bridge\site-plugin
python tools/sign_local_site_plugin.py --key $signingKey `
  .\examples\plugin-v3-metadata-bridge\processor-plugin

image-downloader plugin install (Resolve-Path .\examples\plugin-v3-metadata-bridge\site-plugin)
image-downloader plugin install (Resolve-Path .\examples\plugin-v3-metadata-bridge\processor-plugin)
```

`--create-key`は最初の一度だけ指定します。以後の変更では省略し、更新版をインストールする前に両方のパッケージへ再署名してください。

<a id="required-application-configuration"></a>

## 必要なアプリケーション設定

両方のパッケージを有効にし、次の組合せを永続設定の許可リストへ登録した場合にだけ、画像処理プラグインは秘匿化前の**要求時データ**（`transport_metadata.plugin_data`）を参照できます。三階層の`context.plugin_data`は許可リストによらず共有します。

```yaml
plugin_settings:
  local.image-downloader.metadata-bridge-gallery:
    enabled: true
  local.image-downloader.metadata-bridge-processor:
    enabled: true

image_processors:
  chain:
    - local.image-downloader.metadata-bridge-processor
  transport_metadata_access:
    local.image-downloader.metadata-bridge-processor:
      - local.image-downloader.metadata-bridge-gallery
```

許可リストがなくても画像処理プラグインは動作しますが、秘匿化されたスナップショットを受け取り、プロファイルを適用しなかったことを記録します。このサンプルは、`plugin_data`を画像データ、結果、イベント、ログへコピーしません。`ImageResource.metadata`と`plugin_data`は、どちらも秘密情報を含まない値として扱ってください。パスワード、Cookie、アクセストークン、署名付きURL、鍵を入れないでください。
