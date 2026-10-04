# R09: HTML文字コードの詳細調査

確認日: 2026-10-05。対象コミット: `b4725c7337f417cda521194dd76057ee576883b4`。
以下の原因・実測は修正前コミットの調査記録である。

追記: R09を修正した。現行仕様は[FAQ](../../v3/how-to/faq.md#generic-html-encoding)を参照。
処理はGenericHtmlPlugin本体だけに適用し、継承した別クラスを含む独自プラグインは従来の復号経路を維持する。
BOM最優先、正常な非ASCII UTF-8優先、HTTP charset、先頭1,024バイトのmeta、UTF-8 fallbackの順を採用した。
UTF-8優先はブラウザとの完全一致を意図しない。webencodingsは全利用者のインストール依存になる。
修正後のタイトル・保存先・URL・ID・revisionは変わり得るが、既存のファイル・状態は自動移行しない。
新しい復号処理の呼出しを禁止し、独自プラグインのlibrary・CLIと、オフラインコマンドの既存テストで範囲を検証する。

## 問題の根拠と前後のコード

修正前の汎用HTMLプラグインでは以下の処理になっていた。
[builtin.py](../../../src/image_downloader/plugins/builtin.py)の92–97行は以下のとおり。

```python
92: async def _load_manifest(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
93:     response = await context.requests.execute(RequestSpec(url))
94:     parser = _ImageParser(response.url)
95:     parser.feed(response.body.decode("utf-8", errors="replace"))
96:     parser.close()
97:     return DownloadManifest(parser.title, (Chapter(1, parser.title, images=tuple(parser.images)),))
```

- 93行で取得するresponseは、headersとbodyを別々に持つRequestResponse。
- 94行はR01で修正した最終URLの採用。文字コードの判定はしない。
- 95行はHTTPのcharset、HTMLのmeta、BOMを調べずUTF-8として復号する。
- errors="replace"は失敗した箇所をU+FFFD（�）に置換して続行する。自動文字コード判定ではない。
- 97行で、誤復号したタイトル・画像情報をそのままmanifestにする。

HTTP層も文字コードの復号はしていない。
[gateway.py](../../../src/image_downloader/transport/gateway.py)の412–423行では、
`response.aiter_bytes()`の結果をbytearrayへ追加し、最終的に以下を返す。

```python
body.extend(chunk)
return RequestResponse(str(response.url), response.status_code, dict(response.headers), bytes(body))
```

[models.py](../../../src/image_downloader/models.py)の65–73行も、この契約を明示している。

```python
@dataclass(frozen=True, slots=True)
class RequestResponse:
    url: str
    status: int
    headers: Mapping[str, str]
    body: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", freeze_mapping(self.headers))
```

つまりcharset情報がHTTP層で失われるのではなく、保持されたheadersを汎用プラグインが使用していない。
HTTPXの`.text`による文字列化もこの経路では使用していない。
HTMLParser.feedが受け取るのは既に復号されたstrであり、metaを見つけても元のbytesから復号し直してはくれない。
[Python HTMLParser仕様](https://docs.python.org/3/library/html.parser.html#html.parser.HTMLParser.feed)、
[Python codecsのreplace仕様](https://docs.python.org/3/library/codecs.html#error-handlers)。

HTMLのブラウザ向け仕様では、BOM、HTTP等のtransportによる文字コード指定、HTMLバイト列の宣言走査が
判定に使われる。現行プラグインにブラウザの完全実装が必要という意味ではないが、
UTF-8固定では宣言どおりに読むことができない根拠になる。
[WHATWG HTMLの文字コード判定](https://html.spec.whatwg.org/multipage/parsing.html#determining-the-character-encoding)。

## 再現例1: 正しいHTTP charsetがあっても文字化け

HTTPヘッダー:

```http
Content-Type: text/html; charset=shift_jis
```

本文（以下をShift_JISで符号化）:

```html
<title>日本語</title><img src="a.png">
```

「日本語」のバイト列は`93 fa 96 7b 8c ea`。
Shift_JISとして読むと日本語だが、UTF-8 + replaceでは`\ufffd\ufffd\ufffd{\ufffd\ufffd`、表示は`���{��`になる。
例えば`7b`はUTF-8ではASCIIの`{`と解釈される。
置換後の文字列から元の日本語には戻せない。ただしRequestResponse.body自体を変更する処理ではない。

実workflowをMockTransportで実行した結果:

- status: success、画像1枚保存、failuresなし。
- 保存先末尾: `downloads/0001_���{��_���{��/0001.png`。
- 同じparser.titleを作品名と章名の両方へ設定するため、既定のディレクトリ名では文字化けが2回現れる。
- 出力値はoutput_allocator.pyの281–282行でmanifest.title・chapter.titleから取得される。
- filesystem.pyのsafe_nameは危険なパス文字を除去するが、文字コード誤解釈は修復しない。

この例ではHTMLタグと画像URLがASCIIなので抽出・取得に成功する。
R04の画像本体検査も正常PNGには成功するため、HTMLの文字化けを検知できない。

## 再現例2: 非ASCII画像URLが別のリクエストになる

同じHTTP charsetで、以下をShift_JISとして返す。

```html
<title>日本語</title><img src="日本語.png" data-image-id="日本語">
```

builtin.pyの54–62行では、HTMLParserが返したsrcとdata-image-idをそのままImageResourceへ設定する。

```python
if tag.lower() == "img" and values.get("src"):
    self._images.append(
        ImageResource(
            values["src"],
            len(self._images) + 1,
            referer=self.source_url,
            image_id=values.get("data-image-id") or None,
        )
    )
```

その後69行のurljoinで絶対URLにし、110–111行のcreate_image_requestでそのURLを使用する。
誤復号は既に終わっているため、urljoinは修復できない。

- 抽出URL: `https://example.test/gallery/���{��.png`
- HTTPXが送るURL: `https://example.test/gallery/%EF%BF%BD%EF%BF%BD%EF%BF%BD%7B%EF%BF%BD%EF%BF%BD.png`
- MockTransportでは正常な`日本語.png`を公開していても別パスへのリクエストになるため404。
- 実workflowの結果はpartial、保存0枚、http_status_error。

これは具体的にそのパスだけ存在するmockでの実測。実サイトで必ず404になると断定するものではない。
URIを事前にpercent-encodeしsrcがASCIIになっているページでは、この属性の文字化けは起こらない。
base hrefの非ASCII部分にも同じ誤復号が及ぶため、全画像の基準パスが壊れる可能性がある。

## 再現例3: meta宣言、別文字コード、BOM

9種類の入力を現行GenericHtmlPlugin.inspectへ渡して確認した。

| 入力 | 期待するタイトル | 現行結果 |
|---|---|---|
| UTF-8 + charset=utf-8 | 日本語 | 正常 |
| Shift_JIS + HTTP charset | 日本語 | `\ufffd\ufffd\ufffd{\ufffd\ufffd` |
| Shift_JIS + meta charset、HTTPはtext/htmlのみ | 日本語 | HTTP宣言例と同じ文字化け |
| EUC-JP + meta http-equiv | 日本語 | `\ufffd\ufffd\ufffd\u0738\ufffd`。一部は別のUnicode文字へ誤変換 |
| CP932 + charset=windows-31j | 髙﨑① | `\ufffd\ufffd\ud547@` |
| ISO-2022-JP + HTTP charset | 日本語 | `\u001b$BF\u007cK\\8l\u001b(B`。エスケープシーケンスが残る |
| UTF-16 + BOM + HTTP charset | 日本語 | title=download、画像0件 |
| UTF-8 + BOM | 日本語 | この例のタイトル・画像は正常。UTF-8 BOMがあれば必ず壊れるわけではない |
| Shift_JIS、タイトルはASCIIの数値文字参照 | 日本語 | 正常。文字参照がparserで展開される |

UTF-16のASCIIタグ部分はbytesで`3c 00 74 00 ...`等となり、UTF-8復号後にもNULが残るため、
通常のtitle・imgタグとして認識できない。BOMを使用して文字コードを切り替える処理はない。
実workflowでも、画像0件・保存0件・failuresなしでsuccessになった。
これは文字コード問題と、R03の画像0件を許容する仕様が組み合わさった結果。

ISO-2022-JPはASCII範囲のバイトを使うため、UTF-8復号が置換エラーなしで通っても日本語には戻らない。
画像URLに残ったESC（U+001B）についてはHTTPXのURL生成がInvalidURLを返すことも確認した。
したがって「UTF-8のstrict復号が成功すれば正しい文字コード」とは判断できない。

## 実サイトでの確認: 青空文庫

2026-10-05に、[夏目漱石『吾輩は猫である』の公開HTML](https://www.aozora.gr.jp/cards/000148/files/789_14547.html)
をHTTPXでGETし、取得したbytesを現行GenericHtmlPluginへ渡した。画像の実ダウンロードは行っていない。

- HTTP status: 200。
- Content-Type: text/html（この応答にはcharsetなし）。
- 冒頭にXML宣言のencoding=Shift_JIS、および以下のmeta宣言がある。

```html
<meta http-equiv="Content-Type" content="text/html;charset=Shift_JIS" />
```

- Shift_JISとして復号したtitle: `夏目漱石 吾輩は猫である`。
- 現行コードのtitle: `\ufffd\u0116\u069f\ufffd\ufffd\ufffd \ufffd\ufffdy\ufffd\u0354L\ufffd\u0142\ufffd\ufffd\ufffd`。
- 画像抽出数は両方36。抽出した画像URLの一覧は両方一致した。

この実例で確認されたのはタイトルの文字化けであり、画像URLの破損ではない。
サイト全体が取得不能という主張ではなく、宣言を持つ実際のHTMLで問題が発生している実証。
XML宣言だけの解釈を要求する例でもない。同じ文書にHTMLのmeta宣言がある。

## 更新検知と影響範囲

builtin.pyの99–108行は_load_manifestを再利用し、url/index/image_idのJSONからrevisionを計算する。
日本語data-image-idがある同一内容のHTMLをShift_JIS版とUTF-8版で返すと、現行revisionは異なることを確認した。
画像一覧の意味が同じでも、符号化の変更を画像一覧の更新と判断し得る。
一方、srcがASCIIでimage_idなしなら、タイトルだけの文字化けはrevisionへ含まれず、両版のrevisionは一致した。

問題の確定範囲はcore.generic-html。RequestResponseはbytesとheadersをプラグインへ渡すため、
独自プラグインが適切に復号すれば回避できる。全プラグインが同じ問題を持つわけではない。
例えばgeneric-css-selector/plugin.pyの43–47行はBeautifulSoupへbytesを渡しており、同じUTF-8固定処理ではない。
一方、public-gallery、csrf-login-gallery、paginated-catalogの同梱ソースにはUTF-8固定復号が見られる。
これらについてはソース上の類似箇所の確認に留まり、実サイトへの適用は検証していない。
修正前にも修正後にも、汎用HTMLプラグインの文字コード指定設定はない。

## 再現手順

[r09-probe.py](r09-probe.py)をリポジトリ直下から実行する。

```powershell
$env:PYTHONPATH = 'src'
python docs/investigations/implementation-review-2026-10-04/r09-probe.py
```

既定は外部通信なし。fixture9種類、revision比較2種類、実保存を伴うmock workflow3種類を実行する。
現在は修正後のコードを実行する。修正前のUTF-8固定処理を再現するには`--legacy`を追加する。
ファイルは一時ディレクトリへ保存し、終了時に削除する。
実サイトを再確認する場合だけ同じコマンドへ`--live`を付ける。サイト応答は将来変更され得る。

## 修正後の検証結果

- 全v3テスト: **1,384 passed, 16 skipped**（382.15秒）。
- 復号・manifest・revision・実保存・継承互換の専用テスト: 41件成功。
- 独自プラグインのlibrary／CLI・fallback境界: 25件成功。自動・明示・強制選択で新復号ヘルパーを禁止し、
  本文・ヘッダー、manifest、保存先・保存bytes、独自revisionと未更新時の選択を確認した。
- オフラインCLIの既存テストは、doctor・config・plugin・cookie・state・verifyの実handler内で
  ヘルパーを禁止するautouse fixtureを適用して成功。helpも禁止状態で5件成功した。
- 外部通信なしのprobe: 9種類すべてで期待したタイトルを取得。日本語URLとUTF-16のworkflowも保存成功。
- ruff、mypy（91ソース）、構文解析（91ソース）、pip check、git diff --check: 成功。
- sdist／wheelビルド（no-isolation）と、wheelインストール後のrelease smoke（offline）: 成功。
  wheel内のhtml_encoding.py・webencodings依存メタデータと、wheelからのShift_JIS復号も確認した。
- 製品変更は汎用HTMLの呼出し部分・非公開復号モジュール・必須依存だけ。
  HTTP層、DTO、共通invoker、プラグイン選択、その他のコマンド実装は変更していない。
