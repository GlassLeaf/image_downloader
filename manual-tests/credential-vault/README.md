# Windows資格情報保管庫の隔離手動試験

人間が、専用Windowsユーザーの隔離端末・対話ターミナルで実行してください。CI・AI実行環境から実OSストアへアクセスしません。本番データルート・本番バックアップを使わず、全入力を架空の値にします。

## 初期化・権限・バックエンド

1. 空の作業ディレクトリを用意し、設定ファイルをその外側に置きます。以下の`WORK`は作業ディレクトリ、`CONFIG`は**設定ファイル自体**の絶対パスに置き換えてください。`--config`へディレクトリは指定できません。新しい設定ファイルは`py -m image_downloader config init "C:\config root\app.yaml"`などで作成します。既存の設定は上書きしません。
2. `image-downloader vault status --data-root WORK/data --config CONFIG --json`が`uninitialized`を返し、データルートもOS項目も作らないことを確認します。
3. `image-downloader vault init --data-root WORK/data --config CONFIG`を実行します。statusの保管庫ID・鍵IDを記録します。秘密は記録しません。
4. Windows資格情報マネージャーで`image-downloader.master.<記録した保管庫ID>.<記録した鍵ID>`だけが追加されたことを確認します。Windowsバックエンドのローカル永続化（`CRED_PERSIST_LOCAL_MACHINE`）を隔離したデバッガー等で確認します。秘密値を表示・保存しません。
5. `credential plugin set com.example.manual TOKEN`と`credential smtp set MAIL`を、同じ`--data-root`・`--config`で実行し、架空の値を非表示入力します。一覧に秘密値がないことと、OS項目が増えないことを確認します。
6. `icacls`で`data/security`、各`credentials`・`cookie`ディレクトリ、暗号化ファイルを確認します。所有ユーザー、SYSTEM、Administrators以外にアクセス権がないことを確認します。別の通常ユーザーから読取り・変更が拒否されることも確認します。
7. 平文・null・Chainerバックエンドを明示選択した場合、初期化・読取り・保存が拒否され、既存暗号文が変わらないことを確認します。バックエンド指定を戻して通常動作を確認します。

手順2の具体例（PowerShell、空白を含むパスも引用符で囲みます）：

```powershell
py -m image_downloader vault status --data-root "C:\working test\data" --config "C:\config root\app.yaml" --json
```

`CONFIG`の設定ファイルが存在しない場合は`configuration_missing`、設定ディレクトリを渡した場合は`configuration_not_file`でコード2になります。いずれも保管庫の初期化・OSキーへのアクセスより前に停止します。

## バックアップ・障害・更新

1. `vault backup WORK/test.backup`を、同じ`--data-root`・`--config`で実行します。確認入力を含む架空のパスフレーズを使います。
2. `vault restore WORK/test.backup --data-root WORK/restored-data --config CONFIG`を実行します。IDが変わり、プロファイル・登録一覧・Cookieが引き継がれ、元の保管庫にも引き続きアクセスできることを確認します。
3. 元のルートで`vault status --data-root WORK/data --config CONFIG --json`の`vault_id`・`key_id`を記録し、`vault rotate-key --data-root WORK/data --config CONFIG --json`を実行します。同じstatusを再実行し、保管庫IDは同じままで鍵IDが変わることを確認します。`image-downloader.master.<同じ保管庫ID>.<旧鍵ID>`が消え、同じ保管庫IDの新鍵だけが1件残ることを確認します。旧方式の項目を比較対象として用意した場合は、その項目が変わらないことも確認します。
4. デバッガーで保存境界を止めて試験プロセスを終了し、初期化は`vault init`、復元は同じbackupの`vault restore`、鍵更新は`vault rotate-key --resume`で完了することを確認します。各ケースで新しい試験ルートを使います。
5. 専用ユーザーで試験用マスター項目を手動削除し、読取り・保存・initが鍵欠損エラーになり、新キーや暗号文を作らないことを確認します。バックアップはさらに別の空ルートへ復元します。

手順2の復元先は別の保管庫ID・別のマスターキーを持ちます。元の保管庫の鍵を更新しても、復元先のマスター項目は残ります。そのため、復元先を含む資格情報ストア全体の件数は2件のままでも正常です。対象名の保管庫IDと鍵IDの両方を照合してください。

正常完了後の`vault rotate-key --resume`は、再開対象がないため終了コード3・`vault_no_rotation`になります。`vault status`が`ready`のままで、保管庫ID・現在の鍵ID・OS項目が変わらないことを確認します。`--resume`で新しい鍵更新が始まることはありません。中断再開の試験は、手順4のようにジャーナルが残る途中状態を作って実施します。

表示が変わらない場合は資格情報マネージャーを閉じて開き直し、旧対象名を再確認します。必要に応じて、人間が`cmdkey /list:image-downloader.master.<保管庫ID>.<旧鍵ID>`で該当項目の有無・種類・ユーザー名を確認します。鍵の値は表示・共有しません。同じ保管庫ID・旧鍵IDの汎用資格情報が実際に残る場合は、成功結果と合わせて不具合として記録します。無関係な項目を手動削除して成功扱いにはしません。

試験後は、記録した保管庫ID・鍵IDに一致する試験用OS項目と試験ディレクトリだけを、人間が確認して削除します。サービス名のprefixを使った一括削除は行いません。端末やユーザーをまたぐ資格情報の転送・同期は試験範囲外です。
