# 資格情報保管庫

資格情報とCookieはデータルートごとの保管庫へ保存します。プロファイルが異なる場合、同じプラグインID・参照名も別の登録です。SMTPもプロファイルごとに登録します。`--data-root`を変えると資格情報の取得先も変わります。

## 登録・一覧・削除

```console
image-downloader credential plugin set com.example.gallery TOKEN --profile default
image-downloader credential plugin list --profile default --json
image-downloader credential plugin delete com.example.gallery TOKEN --profile default
image-downloader credential smtp set MAIL --profile default
image-downloader credential smtp list --profile default
image-downloader credential smtp delete MAIL --profile default --yes
```

`--config`、`--data-root`、`--profile`で対象を選びます。プロファイルの設定解決は既存のCLIと共通です。プラグインの設定ではlogical nameから参照名への対応だけを書きます。

```yaml
plugin_settings:
  com.example.gallery:
    secrets:
      api_token: TOKEN
notification:
  email:
    credential_reference: MAIL
```

秘密値は非表示で対話入力します。自動処理では`set --stdin`を明示します。サイト用は標準入力のUTF-8文字列全体を保存し、末尾改行も保持します。SMTP用は`{"username":"...","password":"..."}`というJSONを受け取ります。秘密値は引数・環境変数で渡せません。一覧・JSONは識別情報だけを返し、秘密値を表示するコマンドはありません。削除は対象を表示して確認し、`--yes`で確認を省略します。

`context.secrets.get(name)`は設定された参照名を毎回取得します。値をキャッシュせず、プラグインからの登録・更新・削除は許可しません。OAuth更新トークンの自動保存はありません。

## 保存・暗号化

| 保存対象 | 保存先 |
| --- | --- |
| 非秘密の保管庫識別情報・認証済みマーカー | `<data-root>/security/vault.json` |
| プラグイン資格情報・SMTPユーザー名とパスワード | `<data-root>/profiles/<profile>/credentials/credentials.enc` |
| CookieJar | `<data-root>/profiles/<profile>/cookie/cookies.v2.enc` |
| 更新中の認証済みジャーナル | `<data-root>/security/rotation.enc` |

保管庫ID・鍵IDはランダムなUUIDです。絶対パスから生成しません。256ビットのランダムなマスターキーだけをOSストアの`image-downloader.master.<vault-id>.<key-id>`、参照名`MASTER`へ保存します。同じ項目への上書きは拒否します。通常は1保管庫につき1鍵、鍵更新・中断復旧中は最大2鍵です。旧方式のOS項目には触れません。

許可するkeyringバックエンドはWindows Credential Manager、macOS Keychain、Secret Service、KWalletの具体的な実装だけです。平文・不明なバックエンドやChainerへの暗黙フォールバックは拒否します。Windowsの新規鍵はローカル永続化を使います。

保存ごとにランダムな256ビットのデータ鍵を生成し、本文をAES-256-GCMで暗号化します。データ鍵もマスターキーでAES-256-GCM暗号化し、同じファイルへ格納します。それぞれ独立した12バイトnonceを生成します。本文のAADは形式2・保管庫ID・プロファイル・用途、データ鍵のAADはさらに鍵IDを含みます。呼び出し元が期待する所属と照合するため、別プロファイルへのファイル差替えも拒否します。

平文の一時ファイルは作りません。暗号文は安全な既存ファイル基盤を通して原子的に保存し、link・reparse pointを拒否します。POSIXでは管理ディレクトリ0700・ファイル0600、Windowsでは所有ユーザー・SYSTEM・Administratorsにアクセスを限定します。同じOSユーザーとして実行される悪意あるコードから秘密を隔離する仕組みではありません。

秘密値はUTF-8で1MiB、暗号化ファイルは64MiB、バックアップ本文は256MiBが上限です。暗号文のbase64・メタデータ分もファイル上限に含みます。通常は対象プロファイルの必要なファイルだけを読みます。保管庫ロックを先に取得し、資格情報・Cookieそれぞれのファイルロックを後に取得します。鍵確認・読取り・変更・保存を保護し、ネットワーク通信中は保持しません。

## 初期化と障害

```console
image-downloader vault status --data-root C:\isolated\data --json
image-downloader vault init --data-root C:\isolated\data
```

初期化は明示的な`init`または最初の保存時だけ行います。読取り、help、doctor、inspect、計画表示、状態表示では鍵を生成・修復しません。初期化途中の状態は通常操作へ公開せず、`vault init`の再実行で完了します。

未登録と、鍵欠損、OSストアへのアクセス障害、破損、所属不一致、未知形式、上限超過、中断状態を区別します。`VaultError`は`AuthenticationError`の派生型で、固定メッセージと`vault_*`エラーコードを使います。鍵が失われた既存データを新しい鍵で上書きしません。鍵を復旧できない場合は、空のデータルートへ暗号化バックアップを復元してください。破損先へのCookie importも停止します。

## バックアップ・復元

```console
image-downloader vault backup C:\backups\vault.backup --data-root C:\app\data
image-downloader vault restore C:\backups\vault.backup --data-root C:\app\restored-data
```

バックアップは全プロファイルの新方式の資格情報とCookieの一貫したスナップショットです。設定、画像、ログ、旧データ、マスターキーは含みません。パスフレーズは非表示入力し、書出し時には再入力で確認します。自動処理では`--passphrase-stdin`を指定します。この入力だけは末尾の改行1個（LFまたはCRLF）を除きます。

形式1の専用バックアップを、scrypt（`n=2^17,r=8,p=1`、16バイトsalt）で導出した鍵によるAES-256-GCMで暗号化します。入力ファイルから任意のKDFパラメーターを指定できません。既存のバックアップファイルは上書きしません。保管庫の管理ファイル・資格情報・Cookieの保存パスへの出力も拒否します。パスフレーズ紛失時の復旧機能はありません。

復元先は新規または空のデータルートだけです。マージ・上書きはしません。プロファイル名を維持し、新しい保管庫ID・マスターキーで再暗号化します。復元途中は通常操作を停止します。同じバックアップ・パスフレーズ・復元先でコマンドを再実行すると完了できます。他のバックアップへの差替えは拒否します。復元後は設定を別途用意し、復元先のデータルートを指定してください。

## マスターキー更新

```console
image-downloader vault rotate-key --data-root C:\app\data
image-downloader vault rotate-key --resume --data-root C:\app\data
```

全ファイルを事前検証し、旧鍵で認証したジャーナルを保存してから新鍵を作ります。各ファイルのデータ鍵だけを保護し直し、本文の暗号文は維持します。途中では通常操作を停止し、`--resume`で再開します。ファイルの鍵IDと認証結果から完了位置を判断します。

全ファイルの検証後、新鍵で認証したジャーナルを確定し、現在の鍵IDを切り替え、旧マスターキーを削除します。削除に失敗した場合は`retirement_pending`となり、通常操作は新鍵を使えますが、次の鍵更新は拒否します。`--resume`で削除と後処理を完了してください。起動済みプロセスも保存時に現在の鍵IDを再確認します。

`--resume`はジャーナルが残る中断・後処理待ちの更新だけを再開します。正常完了後や、まだ更新を開始していない保管庫では、再開対象がないため終了コード3・`vault_no_rotation`になります。この場合、保管庫は`ready`のままで、鍵の生成・更新・削除は行いません。正常完了後に`--resume`を実行する必要はありません。

鍵更新は、漏えい済みの値や旧バックアップからの復号を取り消しません。同じ保管庫IDを持つ生コピーの複数箇所同時運用は非対応です。独立した複製にはbackup/restoreを使ってください。

## 旧方式からの切替

旧keyring資格情報・Cookie鍵・資格情報用環境変数は読みません。旧設定・旧データの移行・削除機能はありません。必要な資格情報を新CLIでプロファイルごとに登録してください。SMTPの旧`username`・`credential_service`は互換項目として設定に残りますが使用しません。旧ユーザー名だけが設定されていれば匿名認証にせず、再設定を案内します。

旧`cookie/cookies.enc`は読取り・削除・変更しません。新Cookieがなく旧ファイルがある場合は標準エラーへ固定文言で案内し、空の新セッションから開始します。Cookieを引き継ぐ場合はブラウザーから再取込するか、旧版でパスフレーズ付きexportを作り、新版で明示的に`cookie import`してください。従来のCookie export/import形式1は維持します。旧データの整理はユーザーが行います。

## 直接利用するAPI

```python
from pathlib import Path
from image_downloader.credentials import CredentialVault
from image_downloader.runtime import RuntimeSecrets
from image_downloader.storage import FileSystem
from image_downloader.storage.cookies import CookieStore

vault = CredentialVault(Path("C:/isolated/data"))
store = vault.profile("default")
secrets = RuntimeSecrets("com.example.gallery", {"api_token": "TOKEN"}, store=store)
cookies = CookieStore(FileSystem(Path("C:/isolated/data/profiles/default/cookie")), store=store)
```

`CredentialVault.profile(name)`が返す`ProfileCredentialStore`は`get_plugin(plugin_id, reference)`、`set_plugin(plugin_id, reference, value)`、`get_smtp(reference)`、`set_smtp(reference, username, password)`、`list_entries(kind)`、`delete(kind, reference, plugin_id=None)`を提供します。`set_*`は登録と更新を兼ねます。`get_smtp`の結果は`SmtpCredential`で、reprは秘密を伏せます。CLIと異なり管理APIでは秘密の取得もできます。

`CredentialVault`の`initialize()`、`status()`、`backup(path, passphrase)`、`restore(path, passphrase)`、`rotate_key(resume=False)`は同期APIです。直接構築する`RuntimeSecrets`と`CookieStore`にはキーワード専用の`store`が必須になりました。`RuntimeComposer`の利用方法とプラグインの`get(name)`契約は維持します。
