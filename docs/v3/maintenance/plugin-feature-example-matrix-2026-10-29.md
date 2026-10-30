# 実装機能とプラグイン例の対応調査（2026-10-29）

資格情報に関する以下の棚卸しは旧方式の記録です。現在は環境変数・個別keyringを使用せず、
[資格情報保管庫](../reference/credential-vault.md)と更新済みの機能ラボを使います。

## 追加実装後の対応状況

以下の71行は初回棚卸し時の記録です。追加実装により、次の項目を補いました。
共通の実行・署名・正常／異常系の手順は[機能ラボ](../../../examples/plugin-v3-feature-lab/README.md)にまとめています。

| 対応ID | 追加した例・機能 | 現在の状態 |
| --- | --- | --- |
| F01–F11 | [追加ファイル](../../../examples/plugin-v3-additional-files/README.md) | 独立パッケージ、全7段階、必須データ連携、callback、cleanup |
| A01/A05/A06 | [keyring認証](../../../examples/plugin-v3-feature-lab/credential-gallery/plugin.py) | 登録・削除補助、参照、未登録、環境変数上書きの手順と人工テスト |
| A07–A09 | [Cookie認証](../../../examples/plugin-v3-feature-lab/cookie-gallery/plugin.py)・[実機手順](../../../manual-tests/browser-cookies/README.md) | 本体に取得元限定オプションを追加。実機読取りはAI／CI外の利用者試験で、未実施・非対応は別管理 |
| A10 | [追加origin](../../../examples/plugin-v3-feature-lab/origin-auth-gallery/plugin.py) | 認証許可・未許可・redirect拒否の使用例 |
| I08/O02/O04 | [出力指定](../../../examples/plugin-v3-feature-lab/output-gallery/plugin.py) | 形式・品質・圧縮・EXIF、元名、独自token、force指定との優先順位 |
| S02 | [設定付き選択](../../../examples/plugin-v3-feature-lab/configured-host-gallery/plugin.py) | origin設定、一致／不一致、不正設定 |
| H09/M02 | [短命URL](../../../examples/plugin-v3-feature-lab/short-lived-gallery/plugin.py) | 決定的な失効・再発行、画像開始順の設定 |
| W01/W04–W08/M05–M09 | [運用手順](../../../examples/plugin-v3-feature-lab/README.md) | 更新・予定・再試行・保持・履歴・検査、通知のモック検証／任意手動確認 |

実ブラウザ・OS keyringは自動テストから遮断します。Chromeの既存プロファイルや実データを調査したという意味ではありません。

2026-10-30の利用者報告では、隔離Windows端末（Python 3.11.4、Chrome 156.0）で、専用Chromeの
起動・ダミーCookie発行・本体取込み・暗号化保存後の値の一致・ループバック認証・後片付けまで
所定の全段階がPASSとなりました。これは利用者が実施した手動試験の結果であり、AI・CIの検証は
引き続き人工データとモックだけを使います。実プロファイル・Cookie・鍵・生成ファイルは共有していません。

## 初回棚卸し

現行の`src/image_downloader`、`plugin-sources`、`examples/plugin-v3-*`、関連する`tests/v3`を照合した一覧。
前の作業で追加したホスト判定用ひな型も含む。旧v1/v2とarchiveは現行v3の使用例に数えない。
本調査はソース・文書・テストコードの静的な確認であり、実在サイトへの接続、ブラウザCookieの取得、実際の資格情報ストアへのアクセス、全テストの再実行、同梱署名の再検証は行っていない。

## 判定の読み方

| 表記 | 意味 |
| --- | --- |
| 実例 | `plugin-sources`のmanifest付きパッケージに、その機能を使う処理がある。実在サービスへの対応保証ではない。 |
| 部品例 | `examples`にひな型・組み込み用コード・利用者による署名が必要な例がある。準備なしにstrictでインストールできることは意味しない。 |
| テストのみ | 配布用の例にはないが、テスト用プラグイン・フロー・DTO構築に使用例がある。文書の短い説明だけでは「実例」としない。 |
| 本体 | 本体のCLI・設定・実行基盤が提供する。専用プラグインの実装は不要。既存プラグインと組み合わせられることと、専用の使用例があることは分けて記す。 |

メソッドが存在しても、単に`None`を返す認証・回復フックや、入力をそのまま返す変換フックは、その機能の実例に数えない。
本体機能に「本体」と記した行は、機能未実装やプラグイン非対応を意味しない。

## 既存例の目録

| 例 | 構成・準備 | 主な内容 |
| --- | --- | --- |
| [generic-css-selector][generic] | manifest付きサイトプラグイン | CSS選択、相対URL、設定検証、Referer、vendor依存 |
| [paginated-catalog][pages] | manifest付きサイトプラグイン | HTML一覧のページ分割、作品詳細、章、重複排除 |
| [public-gallery][public] | manifest付きサイトプラグイン | HTML抽出、Origin・Referer |
| [cursor-api-gallery][cursor] | manifest付きサイトプラグイン | APIトークン参照、POST JSON、カーソル分割 |
| [csrf-login-gallery][csrf] | manifest付きサイトプラグイン | 保存されたユーザー名・パスワードの参照、CSRFフォームログイン |
| [oauth-media-api][oauth] | manifest付きサイトプラグイン | Bearer認証、refresh tokenによる更新 |
| [signed-cdn-gallery][signed] | manifest付きサイトプラグイン | 画像URLの取得直前発行、失敗時の再発行 |
| [chaptered-catalog][catalog] | manifest付きサイトプラグイン | 複数章、安定ID、更新候補・revision |
| [artifact-history-processor][history] | manifest付き画像プロセッサー | 設定検証、加工履歴の追加 |
| [resize-processor][resize] | manifest付き画像プロセッサー | Pillowで縮小、PNG出力 |
| [plugin-v3-template][template] | 通常の署名用ひな型 | 必須契約の骨格。`matches=False`、`inspect`は未実装 |
| [plugin-v3-host-check][host] | 署名なしのひな型 | ホスト完全一致、空manifest、bypass-all、doctorで確認 |
| [plugin-v3-metadata-bridge][bridge] | サイト＋プロセッサーの2例。manifest生成・署名が必要 | 画像metadata、回転、request-time data、プロセッサーへの許可付き受渡し |
| [plugin-v3-additional-files][files] | 組み込み用のコード。manifestなし | メタデータ取得・保存、受信／保存コールバック、必須データ参照、完了時サマリー、cleanup |

`plugin-sources`は10パッケージ（サイト8・プロセッサー2）。`examples/plugin-v3-*`は4ディレクトリ、5クラス分の例。
追加ファイル例は本体内部の`GenericHtmlPlugin`を継承するため、安定した公開APIだけで完結する独立パッケージの例ではない。

## 1. 選択・設定・パッケージ

| ID | 実装機能・本体の根拠 | 判定 | 使用例・不足点 |
| --- | --- | --- | --- |
| S01 | URL・ホストでの選択：`matches`（[選択実装][selection]） | 実例＋部品例 | [catalog][catalog]、[signed][signed]など。ホストだけを試す例は[host-check][host]。 |
| S02 | 設定を使う選択：`matches_with_config`（[契約][ports]） | テストのみ | [テスト用プラグイン生成][test-plugins]と[test_plugin_v3.py][test-plugin]にalternate hostの例。配布用例にはない。 |
| S03 | 優先順位・有効無効・明示／強制選択・汎用fallback（[runtime][selection]、[リクエスト][requests]） | 実例＋本体 | [paginated-catalog][pages]の優先度と[generic-css-selector][generic]の競合。[組込みHTML][builtin]はfallback用。明示／強制選択自体はCLI/API側。 |
| S04 | 作者YAML・利用者設定のマージと`validate_config`（[設定][configuration]、[runtime][selection]） | 実例 | [generic-css-selector][generic]のselector、[paginated-catalog][pages]のmax_pages、[resize][resize]の最大寸法。[plugin-sources README][sources]に上書き例。 |
| S05 | アプリ共通設定・manifest・catalogの読取り用コンテキスト（[ports][ports]） | テストのみ | 例では`context.config`の活用が中心で、由来情報を処理に使う完成例はない。[出力値テスト][test-format]等でコンテキストを参照。 |
| S06 | 署名・ファイルツリー・trust/install/revoke/uninstall・content pin（[管理][management]、[検証][verification]） | 実例＋本体 | [plugin-sources][sources]にmanifest付きパッケージと署名・install手順。失効・アンインストール・content pinの処理は本体。[検証テスト][test-plugin]に使用例。 |
| S07 | 署名なしの最小manifest、検証迂回（[manifest reader][manifest]） | 部品例 | [host-check README][host]に`bypass-all`でのinstall・doctor手順。[テスト][test-plugin]には`bypass-signature`＋content pinもある。 |

## 2. 情報取得・HTTP

| ID | 実装機能・本体の根拠 | 判定 | 使用例・不足点 |
| --- | --- | --- | --- |
| H01 | HTTP(S)取得、HTMLから画像抽出・相対URL解決（[gateway][gateway]、[組込みHTML][builtin]） | 実例 | [generic-css-selector][generic]、[public-gallery][public]。組込み側には文字コード判定とbase URL対応もある。 |
| H02 | JSON API・複数章・作品／章／画像の安定ID（[DTO][models]） | 実例 | [chaptered-catalog][catalog]、[cursor-api-gallery][cursor]。 |
| H03 | HTMLページ分割・一覧から詳細取得・重複／循環対策（[inspect契約][hooks]） | 実例 | [paginated-catalog][pages]：max_pages、seen集合、作品重複排除、詳細ページ読取り。 |
| H04 | カーソルAPIを最後まで取得（[inspect契約][hooks]） | 実例 | [cursor-api-gallery][cursor]：next_cursorがなくなるまで取得し、カーソルの繰返しを拒否。 |
| H05 | header・Referer・Origin・query指定（[RequestSpec][models]） | 実例＋部品例 | [public-gallery][public]のOrigin/Referer、[generic][generic]のAccept、[metadata bridge][bridge-site]のquery、[追加ファイル][files-code]のquery。 |
| H06 | POST form・POST JSON（[RequestSpec][models]） | 実例 | formは[CSRF][csrf]と[OAuth][oauth]、JSON bodyは[cursor API][cursor]。 |
| H07 | GET等の再試行、Retry-After、POST再試行の明示許可（[gateway][gateway]） | 本体／POSTの指定例はテストのみ | 通常再試行は各例の通信に自動適用。`retry_non_idempotent=True`を指定する配布用例はなく、[runtime hardeningテスト][test-hardening]にある。 |
| H08 | タイムアウト・pool・HTTP/2・proxy・速度制限・origin/domain別並行制限・応答サイズ制限（[設定][configuration]、[gateway][gateway]） | 本体 | `context.requests.execute`と画像取得に共通適用。各制限を選んで使う専用プラグイン例はない。[hardeningテスト][test-hardening]に制限の例。 |
| H09 | 画像URLの直前解決・placeholder locator・HTTP失敗後の再発行（[画像要求処理][image-requests]） | 実例 | [signed-cdn-gallery][signed]。取得直前に発行APIを呼び、HTTPエラーで再発行する。transport例外の回復hookではない。 |

## 3. 認証・Cookie・保存されたクレデンシャル

| ID | 実装機能・本体の根拠 | 判定 | 使用例・不足点 |
| --- | --- | --- | --- |
| A01 | APIキー／トークンの参照と送信（[秘密情報解決][secrets]） | 実例 | [cursor-api-gallery][cursor]が`context.secrets.get("api_token")`を`X-Api-Key`へ設定。 |
| A02 | ユーザー名・パスワードを使うCSRFフォームログイン（[認証実行][auth]） | 実例 | [csrf-login-gallery][csrf]：ログイン画面→CSRF抽出→秘密情報参照→POST→元要求再実行。 |
| A03 | Bearerトークン付与・refresh token更新（[AuthFlow][ports]、[認証実行][auth]） | 実例 | [oauth-media-api][oauth]。更新したaccess tokenはフローのメモリー内へ保持する。OAuth認可コード／PKCE／device flowの例ではない。 |
| A04 | HTTPステータス以外の認証失敗判定・認証不要リクエスト（[認証実行][auth]） | 実例 | [CSRF][csrf]の`login-required`、[OAuth][oauth]の`expired_token`。ログイン／更新APIに`auth_required=False`。 |
| A05 | 保存されたクレデンシャルの参照：OS keyring（[provider][secrets]、[keyring読取り][keyring]） | 実例＋本体 | CSRF/cursor/OAuthが同じ`context.secrets.get`経由で参照。serviceは`image-downloader.plugin.<plugin_id>`、usernameは設定した参照名。[テスト][test-secrets]でこれらの実プラグインとkeyring解決を組み合わせる。 |
| A06 | 環境変数による秘密情報の上書き・未設定エラー（[provider][secrets]） | 実例＋本体 | 同じ3例が対象。環境変数が非空ならkeyringより優先。変数名はplugin IDと参照名をUTF-8の16進数にした形式で、単純な大文字置換ではない。[テスト][test-secrets]に具体例。 |
| A07 | Cookie認証・Set-Cookie取込み・保存Cookieの再利用（[gateway][gateway]、[CookieStore][cookies]、[service][service]） | 実例＋本体 | [CSRF][csrf]のセッション確立後、本体のjar経由で再要求。プラグインはCookieファイルを直接読まない。 |
| A08 | ブラウザからのCookie取得（[cookie CLI][cookie-cli]、[CookieStore.import_browser][cookies]） | 本体 | `cookie browser-import DOMAIN`→`browser_cookie3.load(domain_name=...)`→profile jarへマージ。`browser-cookies` extraが必要。専用のプラグイン例・ブラウザ取込みとサイトアクセスを一続きに示す例はない。[テスト][test-cookies]は依存をモックする。 |
| A09 | Cookieの暗号化保存・passphrase付きexport/import・差分マージ（[CookieStore][cookies]） | 本体 | AES-256-GCM、暗号鍵をOS keyringへ保存。プラグイン不要。[Cookieテスト][test-cookies]にround trip・競合・破損時の例。 |
| A10 | 追加originへの認証許可・cross-origin redirectの情報保護（[OriginScopedAuthFlow][ports]、[認証実行][auth]、[gateway][gateway]） | テストのみ | `allowed_origins`を指定する配布用例はない。[redirectテスト][test-redirect]、[hardeningテスト][test-hardening]、[inspectionテスト][test-inspection]にある。 |
| A11 | リクエスト単位の明示Cookie・並行要求の認証refresh共有制御（[RequestSpec][models]、[認証実行][auth]） | テストのみ＋本体 | `RequestSpec.cookies`の明示例は[redirect][test-redirect]、[inspection][test-inspection]のテスト内。refreshロック／世代管理は本体。[hardeningテスト][test-hardening]に並行例。 |

保存されたクレデンシャルの**参照**は実装済みだが、SecretProviderの公開契約は`get`だけである。
プラグインによる資格情報の新規登録・更新・削除や、OAuth更新トークンのkeyringへの自動書戻しを示すAPI／例はない。
ブラウザCookie取得はブラウザ自動操作・ブラウザでのログイン実行ではない。

## 4. 追加ファイル・処理に必要な補助データ

| ID | 実装機能・本体の根拠 | 判定 | 使用例・不足点 |
| --- | --- | --- | --- |
| F01 | 追加ファイル機能の宣言・7段階のhook（[AdditionalFiles][additional]、[契約][hooks]） | 部品例／全段階はテストのみ | [追加ファイル例][files-code]の宣言は`BEFORE_IMAGE_REQUEST`と`AFTER_DOWNLOAD`だけ。[テスト][test-additional]は全7段階を対象。 |
| F02 | manifest取得前の補助データ取得・一時保持・章確定後の展開：`BEFORE_MANIFEST`（[AdditionalFiles][additional]） | テストのみ | [test_all_stages_raw_data_and_cleanup_order][test-additional]等。配布例・部品例にはこの段階の宣言がない。 |
| F03 | manifest後の章別追加処理：`AFTER_MANIFEST`（[AdditionalFiles][additional]） | テストのみ | [全段階テスト][test-additional]。 |
| F04 | 画像要求前にメタデータをHTTP取得し章フォルダへ保存（[AdditionalFiles][additional]） | 部品例 | [追加ファイル例][files-code]：`metadata_url`へ画像URLをqueryとして渡し、`metadata/<index>.json`へ保存。 |
| F05 | 受信コールバックから画像要求に必要な値を受渡す（[AdditionalFileReceiver][ports]） | 部品例 | [追加ファイル例][files-code]：`additional_file_received`でJSONのkeyを画像インスタンスごとに保持。欠落時は画像要求を`AuthenticationError`で失敗させる。 |
| F06 | 取得成功後の応答を使う追加処理：`AFTER_IMAGE_REQUEST`（[AdditionalFiles][additional]） | テストのみ | [並行transformへの受渡しテスト][test-additional]等。部品例では宣言していない。 |
| F07 | 変換後・保存前／保存後の追加処理：`BEFORE_IMAGE_SAVE`・`AFTER_IMAGE_SAVE`（[AdditionalFiles][additional]） | テストのみ | [全段階テスト][test-additional]。 |
| F08 | ダウンロード通常完了後にbytesからサマリーを保存：`AFTER_DOWNLOAD`（[AdditionalFiles][additional]） | 部品例 | [追加ファイル例][files-code]：各章の画像結果数を`metadata/images.json`へ保存。 |
| F09 | 保存後コールバック・skip時の既存データ参照（[AdditionalFileSaveObserver][ports]、[AdditionalFiles][additional]） | 部品例＋テスト | [追加ファイル例][files-code]がpath/dataを保持。[skipテスト][test-additional]が保存済み旧データを渡すことを確認する。 |
| F10 | 追加ファイルのパス安全性・サイズ上限・衝突方針・optional失敗の分離（[AdditionalFiles][additional]） | 本体 | [test_additional_files.py][test-additional]に不正パス、上限、コールバック失敗、画像処理継続の例。追加ファイル保存失敗だけでは主処理を必ず停止させる仕様ではない。 |
| F11 | 処理終了時のプラグイン内データの後始末：`cleanup_after_use`（[操作ライフサイクル][operations]、[契約][hooks]） | 部品例 | [追加ファイル例][files-code]が辞書をclear。同期／非同期、失敗・キャンセル時は[transport metadataテスト][test-metadata]、processor側は[processor lifecycleテスト][test-processor]。 |

「必要な追加ファイル」のうち、後続処理に必須かどうかはプラグイン側が判断する。
部品例のように取得した値を保持し、足りなければ後続hookを明示的に失敗させる。
画像と追加ファイルの全体を一括commit／rollbackする機能ではない。

## 5. 画像加工・メタデータ連携

| ID | 実装機能・本体の根拠 | 判定 | 使用例・不足点 |
| --- | --- | --- | --- |
| I01 | サイト固有の画像加工：`transform_image`（[契約][hooks]） | 部品例 | [metadata bridgeのサイト][bridge-site]がorientationを使って回転しPNG化。単にartifactを返す他の例は加工実例に数えない。 |
| I02 | 画像プロセッサー契約・複数段チェーン（[processor chain][chain]） | 実例 | [artifact-history][history]、[resize][resize]。[README][sources]にチェーン有効化のYAML。 |
| I03 | Pillowで縮小し、PNGとして返す（[プロセッサー契約][ports]） | 実例 | [resize-processor][resize]：max_width/max_height、source URL・image_id維持。 |
| I04 | 加工履歴の追加・非破壊なartifact置換（[ImageArtifact][models]） | 実例 | [artifact-history-processor][history]。 |
| I05 | inspect時の画像metadataを要求生成／変換へ渡す（[ImageResource・TransformContext][ports]） | 部品例 | [metadata bridge][bridge]のvariantとorientation。 |
| I06 | 要求時の`ImageFetchRequest.plugin_data`・プロセッサーへの許可付き受渡し（[DTO][models]、[chain][chain]） | 部品例 | [metadata bridge][bridge]：transport_metadata_access設定、raw/redacted分岐。[追加ファイル例][files-code]も要求時データを返す。 |
| I07 | 送信前後のrequest・最終応答URL・重複headerの参照、recovery時のplugin_data維持／置換（[transport metadata][transport-meta]） | テストのみ（一部部品例） | [metadata bridge processor][bridge-processor]は`is_redacted`と`plugin_data`だけを使う。request/header/redirect情報を実処理に使う完成例はなく、[transport metadataテスト][test-metadata]にある。 |
| I08 | 保存形式・拡張子・品質・圧縮・EXIFの画像単位指定、入力MIME／decode／画素数検査（[ImageSaveOptions][models]、[image processor][image-processor]） | テストのみ＋本体 | `ImageResource.save_options`を設定する配布用例はない。[media processingテスト][test-media]にJPEG/PNG・EXIF・形式優先度、[hardeningテスト][test-hardening]に画素上限。CLI/APIのforce形式指定は本体。 |

## 6. 更新確認・一括実行・検査

| ID | 実装機能・本体の根拠 | 判定 | 使用例・不足点 |
| --- | --- | --- | --- |
| W01 | 更新候補の完全なスナップショット提供：`check_updates`（[update executor][updates]） | 実例 | [chaptered-catalog][catalog]：`UpdateCandidate`、content_id、revision。 |
| W02 | 汎用HTMLの画像一覧からrevision生成（[組込みHTML][builtin]） | 本体内の実装例 | `core.generic-html.check_updates`が一覧を正規化してSHA-256化。外部サイトプラグインの独立した例ではない。 |
| W03 | 追加・変更・削除比較、更新状態の保存・分離（[update executor][updates]、[state][state]） | 本体 | W01の提供情報を本体が比較する。[hardeningテスト][test-hardening]・[state scopingテスト][test-update-state]に例。 |
| W04 | 更新一覧→対象URL選択→一括取得、all/updated・未完了対象の選択（[workflow][workflow]、[selection][workflow-selection]） | 本体（入力提供の実例あり） | 入力となるhookは[chaptered-catalog][catalog]と[builtin][builtin]。workflow自体を実装するプラグインは不要。[workflowテスト][test-workflow]に実行例。 |
| W05 | plan/dry-run：初回選択のみ表示、更新状態・画像を保存しない（[workflow plan][plan]） | 本体 | [workflow planテスト][test-plan]。ダウンロード／保存先の完全なシミュレーションではない。 |
| W06 | workflow再試行・期限・成功画像保持・変更画像の再取得（[workflow retry][retry]） | 本体 | [workflow retryテスト][test-retry]。専用の配布用プラグイン例はない。 |
| W07 | inspect：画像本体を取得せず要求URL／HTTP snapshotを検査（[inspection][inspection]） | 本体（既存例を利用可能） | [signed CDN][signed]や[OAuth][oauth]で要求組立てを検査できる。補助API・AuthFlow.applyは動くため、完全な無通信ではない。[inspectionテスト][test-inspection]。 |
| W08 | manifest-only検査：画像要求フックを呼ばない（[inspection][inspection]） | 本体 | `InspectRequest(resolve_image_requests=False)`／CLI `--manifest-only`。[inspectionテスト][test-inspection]に例。manifest取得の通信はあり得る。 |
| W09 | doctorでロード・設定・候補ホストの判定だけを診断（[doctor][doctor]） | 部品例＋本体 | [host-check][host]に署名なし・対応／非対応ホストの実行手順。 |

## 7. 出力・保存・安全性

| ID | 実装機能・本体の根拠 | 判定 | 使用例・不足点 |
| --- | --- | --- | --- |
| O01 | 作品・章・画像番号のファイル名／フォルダ書式（[output allocator][allocator]） | 本体（実例のDTOを利用） | [chaptered-catalog][catalog]が作品・章タイトルと番号を提供。本体が設定の書式へ展開。 |
| O02 | 元ファイル名の明示指定：`ImageResource.original_filename`（[DTO][models]、[original filename][original]） | テストのみ | [original filenameテスト][test-original]と[transport metadataテスト][test-metadata]。配布用プラグインには指定例がない。 |
| O03 | Content-Disposition／最終URL／locatorから元名を選択、`%ORIGINAL_*%`展開（[original filename][original]、[allocator][allocator]） | 本体 | [original filenameテスト][test-original]に優先度・安全化・拡張子の例。専用プラグイン不要。 |
| O04 | 独自出力トークン：`output_format_values`・`%PLUGIN[id:KEY]%`（[format values][format-values]） | テストのみ | [出力値テスト][test-format]にサイト／プロセッサー両方の例。[フック文書][hooks]に記述があるが、配布用の使用例はない。 |
| O05 | overwrite/skip/rename/error、出力先のplugin別分離（[allocator][allocator]、[download executor][download]） | 本体 | プラグイン不要。`output`設定／CLIで制御。画像と追加ファイルに適用。 |
| O06 | 安全な相対パス・原子的保存・プロセス間lock・長い名前調整（[filesystem][filesystem]、[allocator][allocator]、[path safety][path-safety]） | 本体 | 通常画像／追加ファイルに共通。[追加ファイルテスト][test-additional]等に安全性の例。 |
| O07 | 章0件／画像0件の許可・拒否（[download executor][download]、[DTO][models]） | 部品例／画像単位はテストのみ | [host-check][host-code]は`allow_empty_chapters=True`。通常downloadでは本体設定の`allow_empty_chapter_manifest=False`による拒否が優先する。`Chapter.allow_empty_images`を明示する配布用例はなく、[empty resultテスト][test-empty]と[文書][hooks]にある。 |

## 8. 運用・観測・アプリケーション機能

| ID | 実装機能・本体の根拠 | 判定 | 使用例・不足点 |
| --- | --- | --- | --- |
| M01 | profile・host別設定レイヤー、由来表示、設定作成（[configuration][configuration]、[設定参照][config-doc]） | 本体 | `config path/explain/init/profile init`。専用プラグイン不要。プラグイン固有の設定例は[README][sources]。 |
| M02 | サイト別の並行上限・画像開始順の保持（[download policy設定][configuration]、[download executor][download]） | 本体 | `plugin_settings.<id>.download_policy`。短命URL例[signed CDN][signed]自体には順序設定の同梱例がない。[hardeningテスト][test-hardening]と[認証・動的URLの手順][dynamic]に例。 |
| M03 | 画像失敗時の継続・構造化結果・キャンセル・終了処理（[download executor][download]、[service][service]） | 本体 | `continue_on_image_error`と結果DTO。プラグインからの例外は本体が規則に沿って処理する。 |
| M04 | 本体／プラグインの構造化ログ、章別レポート、秘密値のマスク（[logging][logging]、[観測の参照][logging-doc]） | 本体／pluginログ出力はテストのみ | 配布用の例に意図的な`print`／logging出力例はない。[plugin loggingテスト][test-plugin-log]に捕捉・分離の例。専用Loggerはプラグインコンテキストの公開能力ではない。 |
| M05 | デスクトップ・メール通知、成功／失敗／認証イベントの分類（[notifications][notifications]、[delivery][delivery]） | 本体 | 設定で有効化し本体が送信。専用プラグイン不要。[通知テスト][test-notifications]に例。SMTP資格情報はプラグイン用秘密情報とは別に本体がkeyringを参照する。 |
| M06 | workflow状態／履歴のlist・show・history・run・prune（[WorkflowStateService][workflow-state]） | 本体 | CLI `state workflow ...`／library API。専用プラグイン例なし。 |
| M07 | workflow全予定の保存・逐次ログ・中断時の終了記録（[workflow recording][recording]、[workflow logging][workflow-log]） | 本体 | プラグイン不要。[workflow loggingテスト][test-workflow-log]に例。 |
| M08 | 保存ログの整合性検査・workflow取得結果と出力ファイルの照合（[log verification][log-verify]、[workflow verification][workflow-verify]） | 本体 | CLI `verify logs/workflow`。[検査CLIテスト][test-verify]。workflow照合は取得証跡・存在・空ファイル等の確認であり、元画像とのバイト単位の同一性保証ではない。 |
| M09 | ライブラリ組込み：download/inspect/check_updates/plan_workflow/workflow・JSON表示（[service][service]、[requests][requests]） | 本体＋文書例 | [組込みチュートリアル][embedded]に呼出し例。plugin実装例とは別のアプリケーション側API。 |

## 使用例として不足している主な部分

1. **認証の入口からの一連の手順**：ブラウザCookie取込み→サイトで利用、keyring登録→プラグインの参照、OAuth更新情報の永続化を示す独立したサンプルはない。読取り側の認証プラグインは存在する。
2. **設定付き選択・追加origin認証**：`matches_with_config`、`allowed_origins`、明示Cookie、POST再試行許可はテストに偏っている。
3. **追加ファイルの独立パッケージ化と全段階**：部品例はあるが、manifest付きの独立パッケージと、7段階すべての使用例はない。前処理で必須データを取得して検査へ渡す`BEFORE_MANIFEST`もテストのみ。
4. **保存指定**：独自出力トークン、元ファイル名の明示、画像単位の品質・圧縮・EXIF・保存形式、空画像の明示許可はテスト／文書が中心。
5. **通信メタデータの実利用**：bridge例はplugin_dataの利用を示すが、最終応答headerやredirect前後のrequestを加工判断へ使う例はない。
6. **運用機能との組合せ**：workflow再試行・通知・履歴・verify・並行制限は本体にあるが、特定サンプルを使い準備から結果確認までを通す利用手順は限定的。

## 実装済みと混同しないもの

- JavaScript実行、headless browser、CAPTCHA、対話的MFA/WebAuthn、SSE/WebSocket、multipart／streaming request bodyは[現行プラグイン契約][hooks]の対象外。
- ブラウザCookie取得は、ブラウザ内での認証フロー実行やページ描画を提供しない。
- OAuth例はBearer＋refresh token更新であり、OAuth全方式のサポートを意味しない。
- 追加ファイル保存は任意処理で、必須データ不足を後続hookで拒否する責任はプラグインにある。
- チェーンの例は縮小／回転／加工履歴／データ受渡しであり、透かし・復号・タイル再構築等の完成実装例があるという意味ではない。

[generic]: ../../../plugin-sources/generic-css-selector/plugin.py
[pages]: ../../../plugin-sources/paginated-catalog/plugin.py
[public]: ../../../plugin-sources/public-gallery/public_gallery.py
[cursor]: ../../../plugin-sources/cursor-api-gallery/cursor_api_gallery.py
[csrf]: ../../../plugin-sources/csrf-login-gallery/csrf_login_gallery.py
[oauth]: ../../../plugin-sources/oauth-media-api/oauth_media_api.py
[signed]: ../../../plugin-sources/signed-cdn-gallery/signed_cdn_gallery.py
[catalog]: ../../../plugin-sources/chaptered-catalog/chaptered_catalog.py
[history]: ../../../plugin-sources/artifact-history-processor/artifact_history_processor.py
[resize]: ../../../plugin-sources/resize-processor/resize_processor.py
[sources]: ../../../plugin-sources/README.md
[template]: ../../../examples/plugin-v3-template/README.md
[host]: ../../../examples/plugin-v3-host-check/README.md
[host-code]: ../../../examples/plugin-v3-host-check/plugin.py
[bridge]: ../../../examples/plugin-v3-metadata-bridge/README.md
[bridge-site]: ../../../examples/plugin-v3-metadata-bridge/site-plugin/metadata_bridge_gallery.py
[bridge-processor]: ../../../examples/plugin-v3-metadata-bridge/processor-plugin/metadata_bridge_processor.py
[files]: ../../../examples/plugin-v3-additional-files/README.md
[files-code]: ../../../examples/plugin-v3-additional-files/plugin.py
[selection]: ../../../src/image_downloader/plugins/runtime.py
[ports]: ../../../src/image_downloader/ports.py
[models]: ../../../src/image_downloader/models.py
[requests]: ../../../src/image_downloader/application/requests.py
[configuration]: ../../../src/image_downloader/configuration/models.py
[management]: ../../../src/image_downloader/plugins/management.py
[verification]: ../../../src/image_downloader/plugins/plugin_verification.py
[manifest]: ../../../src/image_downloader/plugins/plugin_manifest.py
[gateway]: ../../../src/image_downloader/transport/gateway.py
[builtin]: ../../../src/image_downloader/plugins/builtin.py
[image-requests]: ../../../src/image_downloader/transport/image_requests.py
[secrets]: ../../../src/image_downloader/credentials/plugin_secrets.py
[keyring]: ../../../src/image_downloader/credentials/keyring.py
[auth]: ../../../src/image_downloader/transport/operation.py
[cookies]: ../../../src/image_downloader/storage/cookies.py
[cookie-cli]: ../../../src/image_downloader/commands/cookie.py
[service]: ../../../src/image_downloader/application/service.py
[additional]: ../../../src/image_downloader/application/additional_files.py
[operations]: ../../../src/image_downloader/application/site_operation.py
[chain]: ../../../src/image_downloader/media/processor_chain.py
[transport-meta]: ../../../src/image_downloader/transport/metadata.py
[image-processor]: ../../../src/image_downloader/media/image_processor.py
[updates]: ../../../src/image_downloader/application/update_executor.py
[state]: ../../../src/image_downloader/storage/state.py
[workflow]: ../../../src/image_downloader/application/workflow.py
[workflow-selection]: ../../../src/image_downloader/application/workflow_selection.py
[plan]: ../../../src/image_downloader/application/workflow_plan.py
[retry]: ../../../src/image_downloader/application/workflow_retry.py
[inspection]: ../../../src/image_downloader/application/inspection.py
[doctor]: ../../../src/image_downloader/commands/doctor.py
[allocator]: ../../../src/image_downloader/output/output_allocator.py
[original]: ../../../src/image_downloader/output/original_filename.py
[format-values]: ../../../src/image_downloader/plugins/format_values.py
[download]: ../../../src/image_downloader/application/download_executor.py
[filesystem]: ../../../src/image_downloader/storage/filesystem.py
[path-safety]: ../../../src/image_downloader/storage/path_safety.py
[logging]: ../../../src/image_downloader/observability/logging.py
[notifications]: ../../../src/image_downloader/observability/notifications.py
[delivery]: ../../../src/image_downloader/observability/notification_delivery.py
[workflow-state]: ../../../src/image_downloader/application/workflow_state.py
[recording]: ../../../src/image_downloader/application/workflow_recording.py
[workflow-log]: ../../../src/image_downloader/storage/workflow_logging.py
[log-verify]: ../../../src/image_downloader/application/log_verification.py
[workflow-verify]: ../../../src/image_downloader/application/workflow_verification.py
[test-plugins]: ../../../tests/v3/support/plugins.py
[test-plugin]: ../../../tests/v3/plugins/test_plugin_v3.py
[test-format]: ../../../tests/v3/plugins/test_output_format_values.py
[test-hardening]: ../../../tests/v3/application/test_runtime_hardening.py
[test-secrets]: ../../../tests/v3/credentials/test_plugin_secrets.py
[test-cookies]: ../../../tests/v3/storage_output/test_cookies.py
[test-redirect]: ../../../tests/v3/transport/test_redirect_safety.py
[test-inspection]: ../../../tests/v3/application/test_inspection.py
[test-additional]: ../../../tests/v3/application/test_additional_files.py
[test-metadata]: ../../../tests/v3/plugins/test_site_operation_transport_metadata.py
[test-processor]: ../../../tests/v3/plugins/test_processor_lifecycle.py
[test-media]: ../../../tests/v3/application/test_media_processing.py
[test-update-state]: ../../../tests/v3/storage_output/test_update_state_scoping.py
[test-workflow]: ../../../tests/v3/application/test_workflow.py
[test-plan]: ../../../tests/v3/application/test_workflow_plan.py
[test-retry]: ../../../tests/v3/application/test_workflow_retry.py
[test-original]: ../../../tests/v3/storage_output/test_original_filename.py
[test-empty]: ../../../tests/v3/application/test_empty_result_policy.py
[test-plugin-log]: ../../../tests/v3/observability/test_plugin_logging.py
[test-notifications]: ../../../tests/v3/observability/test_notifications_v3.py
[test-workflow-log]: ../../../tests/v3/application/test_workflow_logging.py
[test-verify]: ../../../tests/v3/cli/test_verify_cli.py
[hooks]: ../reference/plugin-hooks.md
[config-doc]: ../reference/configuration.md
[logging-doc]: ../reference/logging.md
[dynamic]: ../how-to/dynamic-urls-and-auth.md
[embedded]: ../tutorials/embedded-download.md
