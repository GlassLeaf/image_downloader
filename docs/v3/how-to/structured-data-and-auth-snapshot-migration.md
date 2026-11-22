<a id="structured-data-and-auth-snapshot-migration"></a>

# 構造化データと認証スナップショットへの移行

API version 3と署名manifest形式は維持します。既存プラグインは無修正で使えます。
新APIを利用するプラグインは対応本体を必須とし、旧本体向けの二重実装は不要です。
正確なデータ契約は[フックリファレンス](../reference/plugin-hooks.md#structured-plugin-data)を参照してください。

## データの配置

| 決定する時点・範囲 | 保存場所 | 後段の参照先 |
| --- | --- | --- |
| inspect時の作品共通情報 | `DownloadManifest.plugin_data` | `context.plugin_data.work` |
| inspect時の章共通情報 | `Chapter.plugin_data` | `context.plugin_data.chapter` |
| inspect時の画像固有情報 | `ImageResource.plugin_data` | `context.plugin_data.image` |
| 画像要求時に確定する変換用情報 | 既存の`ImageFetchRequest.plugin_data`（文字列マッピング） | `context.transport_metadata.plugin_data`。プロセッサーへの公開は既存の許可設定に従う |
| 認証の公開結果 | `AuthLease.public_data` | `context.auth.snapshot().public_data`。実行コンテキストでのみ参照可能 |

前三者は非秘密のJSON相当データで、数値・配列・入れ子を文字列化せずに渡せます。
全プロセッサーと共有されるので、資格情報・秘密鍵・Cookie・トークンを入れないでください。
`metadata`は既存の文字列マッピングとして残り、自動移動しません。
同じキーが各階層にあっても混ざりません。自動マージや優先順位はありません。
DTO生成時にコピー・不変化され、配列はtupleになります。

## 最初に対応機能を確認する

新しい名前を直接importする前でも判定できるよう、モジュールの公開定数を参照します。
必須の`validate_config()`で確認すれば、認証・画像APIへの通信より前に停止できます。

```python
import image_downloader as plugin_api

def validate_config(self, config, app_settings):
    required = {"structured_plugin_data", "auth_snapshot"}
    if not required <= getattr(plugin_api, "PLUGIN_API_FEATURES", frozenset()):
        raise ValueError("このプラグインには構造化データ・認証スナップショット対応本体が必要です")
```

実際に使う機能だけを要求してください。署名manifestの`api_version`は3のままです。

## inspectから要求・変換への受け渡し

移行前は章共通の文字列を各画像へ複製していました。

```python
image = ImageResource(url, metadata={"placeholder": placeholder, "iv": iv, "salt": salt})
# transform_image内
placeholder = context.image_metadata["placeholder"]
iv = context.image_metadata["iv"]
```

移行後は各DTOに自然な範囲のデータを設定します。

```python
image = ImageResource(url, plugin_data={"iv": iv, "salt": salt})
chapter = Chapter(1, "第1話", images=(image,), plugin_data={"placeholder": placeholder})
manifest = DownloadManifest("作品", (chapter,), plugin_data={"work_id": 42})

# create_image_request / recover_image_request / transform_image / processor.transform
placeholder = context.plugin_data.chapter["placeholder"]
iv = context.plugin_data.image["iv"]
work_id = context.plugin_data.work["work_id"]
```

DTOのデータは再帰的に読取り専用です。更新には新DTOまたは`dataclasses.replace()`を使います。
ビューは検証済みDTOのデータを共有するため、章の長いplaceholderを画像ごとに深くコピーしません。
フックごとに適切なコンテキストが渡るので、プラグインのインスタンスに「現在の章」を保持する必要はありません。
追加ファイルと受信・保存コールバックにも対象のビューが渡ります。
manifest確定前や更新確認では空、章単位では画像階層が空です。
`PluginExecutionContext.plugin_manifest`はパッケージ情報の別名で、既存の`.manifest`と同じ値です。
作品DTOは`PluginDataView.work`の元となる`DownloadManifest`で、パッケージmanifestとは別です。

[連携サンプル](../../../examples/plugin-v3-metadata-bridge/README.md)では構造化データと要求時データの両方を使います。

## 認証結果の取得

移行前の独自の`flow.get_user_id()`や、フローとプラグインの共有変数は不要です。
フローは既存の初期化・更新の戻り値に公開情報を付けます。

```python
# AuthFlow.initialize / refresh内
return AuthLease(expires_in_seconds=3600, public_data={"user_id": user_id})

# inspectなど、PluginExecutionContextを受け取るフック内
await context.auth.ensure_ready()
snapshot = context.auth.snapshot()
user_id = snapshot.public_data["user_id"]
remaining = snapshot.remaining_seconds
```

`snapshot()`だけでは初期化・検証・更新・通信を実行しません。最新のサーバー状態の確認には
`await context.auth.ensure_ready(check=True)`を先に呼び、フロー側に`verify()`を実装してください。
初期化・再認証の成功は追加検証の成功とは扱わず、`last_verified_status`はUNKNOWNです。
無効化、期限更新が必要な状態、失敗、操作終了では`credentials_ready=False`、公開情報は空になります。
古いスナップショットは自動更新されないため、必要な時点で再取得します。

初期化・再認証の`public_data=None`は公開情報を空にします。
VALIDな`verify()`はlease省略なら期限と公開情報を維持し、leaseありなら期限を更新します。
そのleaseの`public_data=None`は公開情報を維持、マッピングは全置換、`{}`は消去します。
有効期間はコアの単調時計で管理します。期限不明は`None`、期限超過の残り時間は0です。
変換コンテキストにはライブ認証はありません。変換に必要な非秘密情報は要求時の既存経路へ明示的に載せます。
[モック認証サンプル](../../../examples/plugin-v3-auth-snapshot/README.md)でユーザーIDと再認証を確認できます。

## 開発・配布・切り戻し

1. 対応本体を先に導入します。既存の署名済みプラグインは変更せず検証します。
2. 新規生成では`--features json,chapters,structured-data`を選べます。既存の`metadata`生成機能も残ります。
3. [開発ツール](plugin-devtools.md)の単体呼び出しで、`resources.manifest`・`resources.chapter`を明示します。
   変換では`resources.image`も指定します。指定しない階層は空で、以前のinspect結果からの暗黙推定はありません。
4. fixtureによるscenario・replayで認証、回復、並行章、サイト変換とプロセッサーを確認します。
5. 新APIへ移行したパッケージだけ通常どおり再署名・再インストールします。
   未変更パッケージの再署名、一斉移行、非推奨警告はありません。
6. 旧本体へ戻す際は、新API利用プラグインを旧版へ戻してから本体を戻します。

詳細inspect JSONの`all`では非空の`plugin_data`だけ各階層へ追加します。
通常のログの要約・項目選択・保存モード・長さ制限は変更しません。
長いデータは既存の詳細出力方針に従うため、ログサイズの削減は保証しません。
操作をまたぐデータストアと認証公開情報の永続化は提供しません。
