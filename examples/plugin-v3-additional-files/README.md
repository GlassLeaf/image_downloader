<a id="additional-file-callbacks"></a>

# 追加ファイルのコールバック

`plugin.py`は、組み込みの汎用HTMLサイトを基にした、注釈付きのフックの例です。パッケージ化したサイトプラグインへ各メソッドを組み込んでください。このディレクトリ自体は、署名済みのインストール可能な単位ではありません。

サイト固有の`metadata_url`に、文字列の`key`を持つJSONを返すHTTP(S)エンドポイントを設定します。各画像要求の前に、コアがJSONを取得して受信コールバックを呼びます。プラグインは操作内の画像インスタンスごとに鍵を保持し、`create_image_request()`と`ImageFetchRequest.plugin_data`で使用します。コアは受信したJSONのバイト列をそのまま章内の`metadata/<image-index>.json`へ保存します。保存後のコールバックは、実際に保存されたデータ、またはskip時の既存データを受け取ります。通常のダウンロード完了後には、各章に`metadata/images.json`の概要を作成します。

この例は、受け取ったメタデータJSONを永続化します。ローカル保存してよい内容だけを使用してください。保存すべきでない認証情報には、通常の要求・認証フックを使います。[フックの仕様](../../docs/v3/reference/plugin-hooks.md#optional-additional-files)を参照してください。
