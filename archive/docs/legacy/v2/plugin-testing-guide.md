# プラグイン受入テストガイド v2（履歴資料）

> 現行の v3 受入項目、fixture 方針、手動移行は [v3 テストと移行](../../../../docs/v3/testing-and-migration.md) を参照してください。

実サイト、実Cookie、token、限定URLを使わず、固定fixtureとHTTPモックで検証します。

| 対象 | 必須ケース |
|---|---|
| 選択 | `matches()`、descriptor priority、catalog `selection_priority`、同順位拒否 |
| inspection | manifest、空manifest、metadata、複数Chapter、画像index順 |
| request | Referer、Origin、cookie、JSON/form、multipart／SSE／WebSocket拒否 |
| 認証 | 401/403、HTTP 200の認証失敗、同時refresh集約、`apply()`再構築 |
| 署名URL | 非認証4xx/5xx、回復一回、None／再失敗 |
| artifact | content type、decode、MIME不一致、site transform、processor順・画像別instance、最終形式 |
| 出力 | overwrite／skip／rename／error、既存章、index順log、masking、cancel、fail-fast |
| 更新 | full snapshot、URL fallback key、added／changed／removed、原子的state保存 |
| 配布 | sidecar、JCS／Ed25519、key ID、能力、revocation、tree hash、strict／warn／off、doctor |

リリース前には`pytest --cov --cov-branch`、Ruff、Mypy、`image-downloader doctor --json`を実行します。分岐カバレッジの最低基準は75%です。

## fixtureの原則

- 実siteへ接続せず`httpx.MockTransport`または`RequestPort` fakeを使う。
- HTML、JSON、画像、認証失敗bodyはrepository内の固定bytesにする。
- token、Cookie、署名queryは明らかなtest専用値を使い、mask後の出力だけをassertする。
- 時刻、nonce、署名URL更新は注入可能なfixture値に固定する。
- testごとに一時profile rootを使い、別testのCookie、state、outputを共有しない。

## contract testの構成

### classとcontext

1. entry point payloadがclassならloadされる。
2. instance/factory、引数必須classはfailed diagnosticになる。
3. operationを2回実行し、site plugin instanceが異なることを確認する。
4. 画像2件を処理し、processor instanceが画像ごとに異なることを確認する。
5. contextの公開能力が`config`、`secrets`、`requests.execute`に限定されることを確認する。

### request順序

HTTP handlerの観測列を保存し、次を順序までassertします。

```text
initial apply -> transport attempts -> auth refresh -> apply -> transport
-> non-auth HTTP failure -> recover once -> apply -> transport
```

429/500/502/503/504とtransport exceptionだけがtransport retry対象で、`max_retries`件を超えないことを確認します。401/403とHTTP 200 login fixtureは`max_auth_retries`側を確認します。

### 結果とlog

- `ChapterResult.outcomes`はmanifest宣言順。
- `log.log`のdownload/saveはindex昇順、同じindexは宣言順。
- FAILED outcomeはpathなし、failureにkind・例外class名・mask済みmessageだけを持つ。
- skipはfailureなしの正常outcomeで、全件skipのCLI終了codeは0。
- 章末でfailure kind別のsummaryと`done`が追記される。

### security negative fixture

正常wheel相当fixtureから1項目だけを変更し、原因を分離します。

- sidecar欠落・複数・top-level不正
- manifest field不足・余分field・schema/api version・未知capability
- distribution、publisher、key ID、manifest digest、tree digest不一致
- Ed25519 signature不正、public key不正、revoked=true
- file追加・削除・bytes改変
- catalog絶対path、`..`、symlink、reparse point、directory

各caseでentry pointの`load()`が検証前に呼ばれていないこともassertしてください。

## 完了判定command

```powershell
python -m pytest -q --cov=image_downloader --cov-branch
python -m ruff check .
python -m ruff format --check .
python -m mypy src/image_downloader
image-downloader doctor --json
```

CIではcoverage総合値だけでなくbranch coverageが75%以上であること、doctor JSONの各`plugins[].loaded`/`warning`も検査します。
