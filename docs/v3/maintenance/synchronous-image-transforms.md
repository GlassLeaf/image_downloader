# ベータv3：同期画像変換への移行

API番号は3を維持するが、画像変換の契約を改訂した。旧async変換は読み込み時に拒否する。
本体とプラグインは対応する版へ揃えて更新する。新しい同期変換プラグインを旧本体で動かす互換性も保証しない。

```python
def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
    return artifact

# 画像プロセッサーも同じ同期契約
def transform(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
    return artifact
```

変換内部にawaitがない場合は `async def` を `def` へ変更する。
変換からcoroutineやFutureを返しても契約違反になる。コアが画像処理を専用ワーカーへ投入するため、
画像処理をスレッドへ移すだけの目的で、プラグイン内に別のThreadPoolExecutorを用意する必要はない。

<a id="async-dependencies"></a>

## 変換内部で非同期関数が必要な場合

現行の変換フック内には直接 `result = await func()` と書けない。
非同期版フックの追加は判断保留であり、`transform_image_async` 等を実装してもコアは呼び出さない。
コア側の呼び出しが非同期であることは、プラグインの同期変換フック内でawaitできることを意味しない。
現在の契約の正本は[同期変換の制限](../reference/plugin-hooks.md#synchronous-image-transforms)である。

ネットワーク・秘密情報の取得はinspect/request/auth側で行う。
非秘密で安定した画像補助情報は `ImageResource.metadata`、画像要求時に得た変換専用の値は
`ImageFetchRequest.plugin_data` で渡す。鍵・token等を通常のmetadataやログへ置かない。
別フックのcontextを保持して変換から通信する方法は、このcapability分離に反する。

非同期専用ライブラリをどうしても変換内で使う場合、ワーカー内の `asyncio.run(func())` で
呼び出し内に閉じた処理を実行することはできる。ただし、次の点をプラグイン側で満たす必要がある。

- コアはワーカーに実行中のイベントループを用意しない。`get_running_loop()` や
  `create_task()` がそのまま使えると仮定しない。
- `asyncio.run()` は呼び出しごとにループを作成・終了する。本体や前回の変換のループに属する
  Task/Future/client等を持ち込まず、必要な非同期リソースはその呼び出し内で作成・解放する。
  同じスレッドで既にイベントループが動いている場合は `asyncio.run()` を呼べない。
- 独自の `wait()` に包んでも、イベントループの所有・寿命・スレッド安全性の制約は変わらない。
  コアは同期フックが返したawaitableを代わりに実行しない。
- 待機中も変換ワーカーの実行枠を保持する。非同期待機だけを理由に同時画像数が増えるわけではない。
  ライブラリのタイムアウト・終了処理を管理し、未回収のtaskや独自スレッドを残さない。
- サービス所有のTransformExecutorをワーカー側の別ループから再利用しない。同Executorは
  所有ループ以外からの利用を拒否する。同じ実行枠への再投入を待つ設計も避ける。

このアダプターで本体のnetwork/secret capabilityを利用できるようになるわけではない。
`asyncio.run()` のループ管理については[Pythonの仕様](https://docs.python.org/3/library/asyncio-runner.html#running-an-asyncio-program)も参照する。

## 共有状態と終了処理

同じ操作の同じインスタンスへ、サイト変換・プロセッサー変換とも複数スレッドから呼ばれ得る。
画像・バッファ・変換テーブルはローカル変数とし、共有キャッシュはロックで保護した不変値にする。
requestフックはイベントループで動作するので、それらと変換の間の共有状態にも注意する。
スレッド間でasyncioのEvent/Lockを直接操作せず、必要ならloop.call_soon_threadsafeを使用する。
共有状態に使うロックの保持中に、別ループの処理や同じ変換Executorの完了を待たない。
`transform_workers: 1` にしてもrequestフックとの並行動作は残るため、共有状態の保護は必要である。

```yaml
media:
  transform_workers: 2  # 整数1〜4、サービス内でサイトと全processorが共有
  max_image_pixels: null  # 既定は維持。メモリ管理が必要な環境では明示設定する
```

Executorへの投入前に実行枠を取得し、実行終了まで保持する。待機中の呼出しはキャンセルできる。
投入済みの変換はキャンセル後も終了まで回収し、その前にcleanup・名前空間解放を行わない。
スレッドは強制停止しないので、停止した変換があれば操作やサービスの終了待ちも継続し得る。
同時数の上限は総メモリ量の保証ではない。署名検証やサンドボックスの代わりにもならない。
ワーカー内の `asyncio.run()` の処理にも同じ完了待ちが適用される。
呼び出し側のキャンセルは、ワーカー内のコルーチンへ自動伝播せず、実行中のスレッドも強制停止しない。

## コアを直接組み込む場合

高度な依存注入には `image_downloader.media.TransformExecutor(max_workers=2)` を渡す。
`_RuntimeDependencies.transform_executor` としてサービスへ所有権を移し、通常はサービスがcloseする。
直接使用する場合は `async with TransformExecutor(...) as executor` と
`await executor.run(lambda: synchronous_callback(...))` を使う。同じサービスの実行loopで使用する。

## 画像検証と署名の移行

ORIGINAL経路では最初の画像検証を維持し、バイト列オブジェクトとContent-Typeが同じなら
最後の同一検証を省く。変更された画像は再検証する。history等だけが変わった場合の不要な
再エンコードも省く。全フレーム・MIME・設定された画素数の検証は省略しない。

同梱ソースと生成テンプレートも同期化した。変更した署名済みソースは再署名が必要である。
artifact-history-processorとresize-processorの署名を、既存のローカルサイト用鍵へ統一したため、
この2個は鍵IDも変更される。移行前の設定・カタログを控え、通常のplugin install手順で再インストールし、
署名鍵とcontent pinの更新を確認する。生成済みmanifestを手で修正したり、検証を迂回して代替しない。
この変更は既存の実インストール先やユーザーの信頼カタログを自動変更しない。
