<a id="application-api-migration"></a>

# アプリケーションAPIの移行

アプリケーションのPython APIは、不変のリクエストオブジェクトを受け取る形式になりました。プラグインAPI v3、CLIコマンド、設定キー、保存データのスキーマは従来どおりです。既存データの移行は不要です。

<a id="downloads-and-other-operations"></a>

## ダウンロードとその他の操作

`service.run(url, force_image_format="PNG")`を、次の呼び出しへ置き換えてください。

```python
from image_downloader import DownloadRequest

async def download_png(service, url):
    return await service.download(DownloadRequest(url=url, force_image_format="PNG"))
```

`inspect`、`check_updates`、`plan_workflow`、`workflow`では、従来のURLとキーワード引数を、それぞれ`InspectRequest`、`UpdateCheckRequest`、`WorkflowPlanRequest`、`WorkflowRequest`へ移します。全フィールドはキーワード引数専用で、既定値は従来どおりです。リクエストの構築時に検証を行い、呼び出し側が渡した上書き設定のマッピングは、不変の値として複製します。

<a id="workflow-failures-and-cancellation"></a>

## ワークフローの失敗とキャンセル

原因例外を直接捕捉して`workflow_result`や`workflow_plan_result`を読む処理を、専用例外の`result`を読む処理へ変更してください。通常の失敗では、元の例外を`cause`と`__cause__`に保持します。

```python
from image_downloader import WorkflowCancelledError, WorkflowExecutionError, WorkflowRequest

async def workflow(service, url):
    try:
        return await service.workflow(WorkflowRequest(url=url, workflow_retry_delay=30))
    except WorkflowExecutionError as error:
        partial_result = error.result
        original_error = error.cause
        raise
    except WorkflowCancelledError as error:
        partial_result = error.result
        raise
```

計画処理には、`WorkflowPlanResult`を持つ`WorkflowPlanError`と`WorkflowPlanCancelledError`を使います。キャンセル用の2型は`asyncio.CancelledError`を継承しており、捕捉後もキャンセルを伝播させてください。再試行の期限切れは通常の`WorkflowExecutionError`として公開し、`cause`は`WorkflowRetryTimeoutError`、結果は`result.timed_out=True`かつ`result.cancelled=False`になります。履歴と専用の進捗記録を確定してから、最終結果を公開します。

<a id="internal-boundaries"></a>

## 内部の責務境界

`DownloadService`は操作の直列化と実行環境の終了処理を担当し、`SiteOperationFactory`は選択したプラグインの準備と後始末を担当します。ダウンロード・章・画像・検査・更新確認の各実行器は、それぞれの操作を受け持ちます。ワークフローの実行と結果確定には依存を明示的に渡し、サービスの非公開メソッドは呼び出しません。

検証済みCLIコマンドは、解析済みの上書き設定を直接保持します。`argparse.Namespace`には検証済み設定のキャッシュを書き込みません。共通の構文規則とエラー表示処理は、コマンド振り分け処理に依存しません。HTTP通信・認証セッション・画像要求の回復処理は別々の実装へ分離しています。プラグインカタログの書き込み、署名検証、設定の正規化、通知の整形と配送、管理ルート内に限定したストリーミング読み取りも、それぞれを担当するサブシステムへ配置しています。

現行APIの全体は、[APIシグネチャ](../reference/api-signatures.md)と[ライブラリ仕様](../reference/library-api.md)を参照してください。
