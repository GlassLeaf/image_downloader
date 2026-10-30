# 実Chrome Cookie試験：利用者が隔離環境内で実行

**Codex・CI・他のAIは実行しません。** このディレクトリはpytestの収集対象外です。
実装の自動テストは偽物のbrowser/keyringと人工ファイルだけを使用します。

## 前提

- AI未導入の別Windows端末を基準とします。VMの場合もホスト側AIからのアクセス、共有フォルダー、共有クリップボード、同期を無効にします。
- Google Chrome、Python、本体と`browser-cookies` extraの依存を**事前に**導入し、コードを持ち込んでから外部ネットワークを切断します。コードにダウンロード機能はありません。
- 普段のプロファイル、Googleアカウント、実サイトの資格情報は使用しません。ランナー自身が新規のuser-data-dirを作ります。
- 作業ディレクトリはリポジトリ・別のGit checkout・クラウド同期領域の外に置き、事前に存在しない絶対パスを指定します。
- 実行中にAIや画面共有を起動せず、端末出力をAI・CIへ転送しません。対話チェックは誤起動防止であり、OSレベルの隔離を保証しません。

必要なコードと依存は、このリポジトリと`pip install '.[browser-cookies]'`で導入する内容です。
オフライン環境へ持ち込む場合は、事前に対象Python/Windows用のwheelと本体wheelを準備します。
本体だけでなく`examples/plugin-v3-feature-lab/server.py`とこのディレクトリも持ち込んでください。

### 修正版へ更新する場合

ランナーのコピーだけでなく、隔離端末にインストールした本体も更新してください。
既存の依存が導入済みなら、新しいソース一式を持ち込み、そのルートで次を実行できます。
ビルドに必要なsetuptools等も事前に用意します。依存取得やビルド環境のダウンロードは行いません。

```powershell
python -m pip install --no-index --no-deps --no-build-isolation --force-reinstall .
```

古い本体と新しいランナーが混在すると、Chrome起動前に`FAIL LOCAL_PACKAGE_VERSION`で停止します。
`PASS LOCAL_CLEANUP`で削除済みの作業先、または新しい作業先で再実行します。

## 起動

外部ターミナルから利用者が起動します。以下のパスは隔離端末内の例です。

```powershell
python manual-tests/browser-cookies/local_check.py --real-browser-test `
  --work-dir 'C:\LocalCookieLab\run-001' `
  --chrome-executable 'C:\Program Files\Google\Chrome\Application\chrome.exe'
```

CI変数がある、非対話、明示フラグなし、Windows以外のいずれかなら拒否します。
専用領域の識別マーカーを照合し、ほかのプロファイルは指定できません。

1. 隔離・外部ネットワーク切断を確認して`ISOLATED`と入力します。
2. ランナーが専用プロファイルのChromeを起動し、127.0.0.1の模擬サイトが有効期間10分のランダムなダミーCookieを発行します。値は表示しません。発行要求の到達と期限をメモリー内で確認します。
3. ローカル画面を確認し、**専用Chromeを完全に終了**して`CLOSED`と入力します。
4. 本体CLIの`--chrome-user-data-dir`で明示した`Default/Network/Cookies`と`Local State`だけを読みます。
5. 暗号化保存→再読込み→Cookie付きループバック要求まで確認します。比較はメモリー内だけです。
6. 最後に`DELETE`と入力すると、識別マーカーを再検証して専用領域と対応する試験用keyringエントリーだけを削除します。

削除しない場合や、Chromeが動作中で削除できない場合は`FAIL CLEANUP_PENDING`です。
出力に絶対パスを含めないため、作業先は利用者が起動時に控えてください。失敗後はその領域をAIへ渡さず、ローカルで確認・削除します。

## 結果の読み方

| 固定コード | 意味 |
| --- | --- |
| `PASS DEDICATED_BROWSER_STARTED` | 専用Chromeを起動した |
| `PASS DUMMY_COOKIE_ISSUED` | 専用サイトがCookie発行応答を返し、試験の有効期間内だった。Chrome側の保存成功はこの後に確認する |
| `PASS CORE_IMPORT` | 本体CLIが正常終了した。対象Cookieが0件の場合もこの段階は成功になる |
| `PASS ENCRYPTED_ROUND_TRIP` | 保存後の値がローカルで一致した |
| `PASS LOOPBACK_AUTHENTICATION` | ローカルの認証確認に成功した |
| `PASS LOCAL_CLEANUP` | 専用データと対応キーの後片付けが完了した |
| `UNSUPPORTED CHROME_IMPORT` | 依存ライブラリ・OS・Chrome暗号化方式のため取込みが利用できない |
| `UNSUPPORTED CHROME_ENCRYPTION` | Local StateにApp-Bound用の鍵だけがあり、このローダーで利用できない |
| `FAIL LOCAL_SOURCE_MISSING` | 明示した専用ファイルが存在しない。専用サイトの表示とChromeの完全終了を確認する |
| `FAIL LOCAL_SOURCE_UNSAFE` | リンク・再解析ポイント・ハードリンク等の安全検査が拒否した |
| `FAIL LOCAL_SOURCE_CHANGED` | 検査中に明示ファイルが変更・置換・削除された |
| `FAIL LOCAL_SOURCE_ACCESS` | 明示ファイルへのアクセスが拒否された |
| `FAIL LOCAL_SOURCE_IO` | 明示ファイルの読取り・一時スナップショットのI/Oが失敗した |
| `FAIL LOCAL_COOKIE_DATABASE` | Cookie DBのSQLiteヘッダーが不正だった |
| `FAIL LOCAL_STATE` | Local Stateの形式・サイズ・必要な従来鍵の検査が失敗した |
| `FAIL LOCAL_DEPENDENCY_INIT` | browser-cookie3のimport時に依存初期化が失敗した |
| `FAIL LOCAL_KEYRING` | 試験用保存キーへのアクセスが失敗した |
| `FAIL LOCAL_STORAGE` | 試験用Cookieの暗号化保存が失敗した |
| `FAIL LOCAL_COOKIE_DATA` | ブラウザから受け取ったCookieの属性が保存形式へ変換できなかった |
| `FAIL LOCAL_IMPORT` | 上記へ分類できない取込み失敗。取得元ファイルの不正とは断定しない |
| `FAIL LOCAL_PACKAGE_VERSION` | 修正済み本体の再インストールが必要。Chromeは起動していない |
| `FAIL LOCAL_CONFIGURATION` | 本体の引数・設定解決に失敗。Cookie読取りより前の設定を確認する |
| `FAIL LOCAL_SITE_VERSION` | 模擬サーバーが古い。更新した`examples/plugin-v3-feature-lab/server.py`も持ち込む |
| `FAIL LOCAL_COOKIE_NOT_ISSUED` | 専用サイトへのCookie発行要求を確認できなかった |
| `FAIL LOCAL_COOKIE_EXPIRED` | 専用サイトのCookie発行から10分以上経過した。新しい試験としてやり直す |
| `FAIL LOCAL_COOKIE_NOT_IMPORTED` | CLIは正常終了したが保存ファイルがない。対象Cookieが未保存・取得0件・期限切れ等の可能性がある |
| `FAIL LOCAL_COOKIE_RELOAD` | 保存ファイルの再読込み・復号に失敗した。値の照合やHTTP認証には到達していない |
| `FAIL LOCAL_COOKIE_EMPTY` | 再読込み後のCookieが0件だった。期限切れのCookieは読込み時に除去する |
| `FAIL LOCAL_COOKIE_MISSING` | 読み込めたCookieの中に試験対象の名前・ドメイン・パスの組合せがなかった |
| `FAIL LOCAL_COOKIE_MISMATCH` | 試験対象のCookieはあったが、その値が今回発行したダミー値と一致しなかった |
| `FAIL LOCAL_LOOPBACK_TRANSPORT` | Cookieの復元・照合後、ローカルHTTP要求に失敗した |
| `FAIL LOCAL_LOOPBACK_AUTHENTICATION` | ローカルHTTP応答のステータス・内容が認証成功を示さなかった |
| `FAIL LOCAL_BROWSER_CHECK` | 上記に分類できないブラウザー操作・サーバー終了等の失敗 |
| `FAIL LOCAL_SETUP` | 作業先・実行ファイル・確認入力等が条件を満たさない |
| `FAIL LOCAL_INTERACTIVE_ONLY` | 実行環境のガードが拒否した |
| `FAIL CLEANUP_PENDING` | 後片付けが未完了 |

終了コードは成功0、失敗1、非対応2です。未実施・非対応を成功扱いしません。
App-Bound Encryption等で読めないときは、Chromeの保護機能の無効化、管理者権限取得、プロセス注入、別プロファイル探索を行いません。

## データを外へ出さない

Cookie値・名前、DB、`Local State`、鍵、絶対パス、生例外は出力しません。JUnit・実行ログ・結果JSON・アップロード機能は作りません。
一時スナップショットも指定した専用領域内に置きます。アプリ設定・暗号化CookieとOS keyringの試験用キーは隔離端末内だけにあります。
通常のラボ、Git、CI artifacts、クラウドドライブ、問い合わせ、AIのチャットへコピーしないでください。
結果を共有する必要があれば、利用者が**固定エラーコードだけ**を手入力します。スクリーンショット・例外全文・生成ファイルの提供は不要です。

この試験は利用者によるローカル確認です。自動テストが通っても実ブラウザ検証済みとはしません。

ランナーが生成するメイン設定は`profile.default: browser-lab`に統一します。
これがない旧ランナーは、CLIの`--profile browser-lab`を別プロファイル指定と解釈し、
未作成の`profiles/browser-lab/app.yaml`を要求してCookie読取り前に失敗していました。
旧版では設定エラーや依存初期化・保存の失敗も`FAIL LOCAL_SOURCE`へまとめていましたが、
現在は上記の固定コードへ分けています。生例外やファイルの内容を出して調べる必要はありません。

Windowsの明示取込みは、CLIを実行したスレッド上で行います。browser-cookie3がimport時に
shadowcopy→WMI経由で保持するCOM参照を、短命なワーカースレッドに残さないためです。
import時の例外・出力もローダー呼出しと同じ保護範囲へ含めます。
これは実機での取込み成功を保証するものではなく、非対応の暗号化方式は`UNSUPPORTED`のままです。

`PASS CORE_IMPORT`の直後に`FAIL LOCAL_BROWSER_CHECK`だけが出て、
`PASS ENCRYPTED_ROUND_TRIP`がなかった旧ランナーでは、保存Cookieの再読込みと値の照合の
どちらが失敗したかを区別できませんでした。取得0件と値の不一致でも同じ表示になります。
現在は発行・期限・保存ファイル・再読込み・対象Cookie・値照合を分け、上記の固定コードだけを表示します。
この切り分けには更新したランナーと模擬サーバーの両方を使ってください。

`PASS DUMMY_COOKIE_ISSUED`の後に`FAIL LOCAL_IMPORT`となる原因として、
browser-cookie3がSQLite由来の`is_secure`を整数`0/1`のまま返し、本体の`bool`検証が
拒否する不具合を人工データで再現・修正しました。ブラウザ取込み時だけ`0/1`を`False/True`へ
変換し、保存済み・エクスポートファイルの厳密な型検証は維持します。
この修正は**本体の再インストールが必要**です。ランナーだけを更新した場合は
`FAIL LOCAL_PACKAGE_VERSION`で停止します。冒頭の更新手順で新しい本体を反映してください。

## 試験後のアンインストールと持込ファイルの削除

`PASS ENCRYPTED_ROUND_TRIP`、`PASS LOOPBACK_AUTHENTICATION`、`PASS LOCAL_CLEANUP`まで
表示された試行は、保存後の値の一致・ローカル認証・後片付けが完了しています。
`PASS LOCAL_CLEANUP`は、その試行の`--work-dir`以下（専用Chromeプロファイル、Cookie DB、
Local State、一時コピー、設定、暗号化Cookie）と、対応する試験用keyringエントリーの後片付けを示します。
インストール済みPythonパッケージ、持ち込んだソースやwheelは別に撤去します。

### 試験専用の仮想環境を使った場合

試験に使ったPythonで、環境の場所をローカル表示できます。出力をAIへ渡す必要はありません。

```powershell
python -c "import sys; print(sys.executable); print(sys.prefix); print(sys.base_prefix)"
```

通常のvenvでは`sys.prefix`と`sys.base_prefix`が異なり、`sys.prefix`が仮想環境のフォルダーです。
PythonとChromeの試験プロセスを終了し、仮想環境を有効化していたターミナルでは`deactivate`します。
エクスプローラーで表示した絶対パスが試験専用venvであることを確認し、そのフォルダーを削除します。
その環境内にインストールした本体と依存パッケージをまとめて撤去でき、個別のpip uninstallは不要です。
ベースPythonのフォルダーを仮想環境と取り違えて削除しないでください。

### 既存のPythonへ追加インストールした場合

試験時と同じPythonで実行します。

```powershell
python -m pip uninstall image-downloader browser-cookie3
```

pip uninstallは依存パッケージを自動削除しません。Windowsのブラウザ読取り用の追加依存は、
今回の試験で導入し、ほかの用途で使用していないものだけを削除します。

```powershell
python -m pip show shadowcopy WMI pywin32 lz4 pycryptodomex
python -m pip uninstall shadowcopy WMI pywin32 lz4 pycryptodomex
python -m pip check
```

`pip show`の`Required-by`も確認します。上記のパッケージ同士の依存だけでなく、
試験以外のアプリやスクリプトから使用しているパッケージも残してください。
httpx、Pillow、keyring、cryptography等の本体の通常依存とその間接依存も残り得ます。
共有Pythonを導入前と同じ状態へ戻すには、導入前の一覧またはインストールレポートと照合し、
今回追加したものを特定する必要があります。現在のpip freeze全件を削除対象にしないでください。

### 持ち込んだファイルとキャッシュ

試験専用のコピーであることと絶対パスを確認し、エクスプローラーで以下を削除します。

- 持ち込んだリポジトリ／ソース一式（ランナー、模擬サーバー、build、dist、egg-info、__pycache__を含む）
- wheelhouse等に置いた本体・依存パッケージのwheel、導入用アーカイブ、試験専用インストーラー
- 持込媒体に残った同じ試験用コピー

pipキャッシュはvenvの外に置かれることもあります。Pythonを撤去する前に場所を確認できます。

```powershell
python -m pip cache dir
```

試験専用端末で、そのユーザーのpipキャッシュをすべて削除してよい場合は次を実行します。
これはほかの用途のpipキャッシュも対象にします。

```powershell
python -m pip cache purge
```

PythonやChrome本体も今回の試験のためだけに導入した場合は、Windowsのアプリの管理画面から
アンインストールします。ごみ箱へ移した持込ファイルも消す場合は、対象を確認してごみ箱を空にします。
普段のChromeプロファイルや、試験とは無関係な資格情報の削除は不要です。

## マスターキー方式への切替

資格情報とCookieは試験専用の`<work-dir>/data`に独立した保管庫を作って保存します。
OSストアにはその保管庫のマスターキーだけを登録します。旧Cookie鍵・旧プラグイン資格情報には触れません。
本番のデータルートを指定しないでください。実OSストア・ブラウザー試験は人間の隔離した手動試験だけで実行します。
資格情報の環境変数上書きと`env-names`は廃止しました。feature labの`credentials-set`・`credentials-delete`にも
`--work-dir`が必須です。補助スクリプトは新CLIへ試験用データルートを明示して呼び出します。
