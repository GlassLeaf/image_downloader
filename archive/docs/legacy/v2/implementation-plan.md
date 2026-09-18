# v2（0.3.0）完全移行仕様（履歴資料）

> この文書は v2 の過去仕様です。現行 v3 の configuration、directory plugin、catalog、runtime API は [docs/v3](../../../../docs/v3/README.md) を正本とします。

この文書は実装と同期するv2の正本仕様です。正規importは`image_downloader` rootであり、v1 API、adapter、旧entry pointは存在しません。

## 公開APIとモデル

- `RuntimeComposer(AppConfig, config_base_dir).compose()`が`DownloadService`を生成する。serviceはasync context managerで、安定実行APIは`run(url)`、`check_updates(url)`、`close()`だけ。
- `AppConfig`はstrictかつdeep-frozenのPydantic model、公開DTOは`frozen=True, slots=True` dataclass。
- `DownloadManifest`はtitle、chapters、任意のcontent_id／author／access／revision、`Mapping[str, str]` metadataを持つ。ID・番号・indexの意味的検証はcoreで行わない。
- `allow_empty_manifest`既定値はfalse。trueなら空manifestは成功し、番号0・空subtitleの出力先と`log.log`を作成する。
- `DownloadResult`は順序付きのchapter/image outcomeを返す。画像失敗は`FailureKind`、例外型、mask済みmessageを持ち、skipは正常outcome。

## Plugin、通信、artifact

- entry point groupは`image_downloader.plugins`と`image_downloader.image_processors`。どちらも引数なしclassだけを受け付ける。
- `SitePlugin`はdescriptor、matches、inspect、create/recover image request、auth_flow、transform_imageを実装する。instanceは公開operationごとに新規作成する。
- URL競合はdescriptor priority、catalogの必須整数`selection_priority`の降順で選び、同順位は`PluginError`。内蔵generic HTML pluginは外部pluginが一致しない時だけfallbackとなる。
- `PluginExecutionContext`は読み取り専用config、secrets、requestsのみ。`RequestPort`は`await execute(RequestSpec)`のみで、files/multipart、WebSocket、SSEは非対応。
- `AuthFlow`はoperationごとに一つ。gatewayは輸送retry→auth回復→非認証4xx/5xxの画像request回復の順に処理する。`max_retries`は初回を含む総試行回数で、署名request回復は一度だけ。
- 章内画像も並列実行する。continue対象は通常の取得・処理・個別保存だけであり、認証、設定、契約、保存先安全性違反は全体停止。外部cancelは実行中jobをcancelして一時ファイルを掃除する。
- pipelineはsite transform→core正規化→設定順processor chain→最終正規化・検証。processor instanceは画像jobごとに生成し、`ImageSaveOptions`が最終形式を決める。
- `media.input_validation`既定値はcontent_type、decode/bothも選択可能。既定では明白なHTML・JSON・textを拒否し、明示image MIMEとPillow検出MIMEの不一致は`media.content_type_mismatch`（accept/error）で扱う。

## 出力、更新、CLI

- `OutputAllocator`はservice内・公開operation単位のメモリ予約と非同期lockだけで衝突を制御する。章directoryは再利用する。
- overwrite/skip/error/renameは画像ファイル単位。renameは画像名だけに接尾辞を付け、既存`log.log`へ要約を追記する。
- 並列download/saveはindex順streaming bufferで`log.log`とConsoleへ同じ内容を出す。失敗詳細は章終了時にまとめ、debug logはmask済み構造化情報を記録する。
- update providerは完全snapshotを返す。state keyは`(plugin_id, content_id)`、ID欠落時はURL。削除は`UpdateChange(kind="removed")`で返しstateから除去する。
- `--list-updated-urls`は追加・変更URLだけを標準出力に出し、削除数は集計表示する。通常終了コードは、失敗なし0、成功/skipと失敗の混在5、失敗のみ1。

## 信頼と配布

- sidecarは`<dist>.dist-info/image_downloader_plugin.json`の一つのJSONで、manifestとbase64 Ed25519 signatureを持つ。
- manifest必須項目はschema_version、id、distribution、publisher、api_version、key_id、capabilities、file_tree、file_tree_sha256。RFC 8785 JCS UTF-8 bytesを署名する。
- treeはsidecarとRECORD以外の配布物のPOSIX path→SHA-256 map、tree digestはそのJCS bytesのSHA-256。
- catalogはidentity、公開鍵、許可capability、manifest/tree digest、selection_priority、revocationをpinし、config基準の相対regular fileだけを受け付ける。
- strictはcatalog必須・検証前import禁止、warnは構造的に有効なv2 pluginを警告付きで読込み、offはcatalog/暗号検証を省略する。sidecarなし/v1 pluginは全モードでimport前に拒否する。
- development profileだけで`--debug-allow-unverified-plugins`はstrictをそのrunに限りoff相当にする。strict catalog不備は終了2。個別pluginの検証不一致はfailed diagnosticとしてそのpluginをskipし、実行時にplugin選択・契約エラーとなった場合は終了4。warnのdoctor警告は0。

## 実装上の精密な境界

- 空manifestの判定は`DownloadManifest.chapters == ()`。画像0件のChapterは通常章としてdirectoryと`log.log`を作る。
- `DownloadResult`のchapter/outcomeはmanifest宣言順を維持する。並列章logのdownload/save行だけは画像`index`順（同値は宣言順）にstreamする。
- `ImageSaveOptions.exif`は公開予約fieldであり、0.3.0のcore保存処理はEXIFコピーへ使用しない。
- `off`はcatalogと暗号検証だけを省略する。v2 sidecarの存在・単一性・構造・api version・capability確認は全modeでimport前に行う。
- 個別entry pointの読込・署名・契約失敗はregistry diagnosticへ記録してskipする。catalog file自体の欠落・path安全性・schema不正はstrict compositionを停止する。

## 実装照合の補足

- 6種類のsite例は`examples/plugins/`の未登録v2 source、`examples/fixtures/`の固定fixture、`tests/v2/test_site_plugin_examples.py`の受入testとして実装済みです。
- notification deliveryはfrozen tupleの`methods`とrouteを処理し、desktop/emailのfake transport testで検証済みです。run途中で例外になった場合も、対応する失敗eventを収集してflushします。
- v1 source/APIは現行packageから削除済みで、`archive/docs/legacy/v1/`には削除境界を示すREADMEだけがあります。利用可能な一次履歴がない限り旧APIを推測で復元しません。

配布作者向けのsidecar手順は[plugin-package-template.md](plugin-package-template.md)、利用方法は[user-guide.md](user-guide.md)を参照してください。
