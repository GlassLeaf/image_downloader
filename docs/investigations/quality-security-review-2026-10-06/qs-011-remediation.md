# QS-011 修正確認：Cookie容器とrecordの検証

## 対象と保証

issue [#4](https://github.com/GlassLeaf/image_downloader/issues/4)。基点はQS-010を統合したmainの
`67ab96d7eaab78bf3fbd100717a9c60333a0045c`、ブランチは
`codex/fix-qs-011-cookie-data-validation`。本体の変更はstorage/cookies.pyだけである。

JSON objectを確認してから容器を読む。formatの厳密な整数型と値1、cipher、base64文字列、
nonce 12バイト、ciphertextの認証tagを含む最小16バイト、exportのkind・kdf・salt 16バイトを検証する。
復号後recordにはname・value・domainを必須とし、文字列／valueのnull、optionalフィールドの
型・既定値、expiresの有限な整数範囲、restの文字列キーとJSON値を検証する。
UTF-8で保存できない文字列や非有限JSON属性も保存前に拒否する。
属性の検証は反復処理で行い、正常な入れ子へ追加の再帰制限を設けず、循環を拒否する。

不正構造からKeyError・AttributeErrorが出ない形にし、想定内のJSON・文字コード・復号の失敗を
既存の固定メッセージのAuthenticationErrorへまとめる。処理中断や一般の実装バグを一律に捕捉しない。
鍵・Cookie値・passphraseを診断へ追加せず、filesystem safety errorも破損復旧として握り潰さない。

file importは全recordの検証が完了するまでtargetをlock・merge・保存しない。
import元不正・passphrase不一致・保存失敗では既存bytesを保持する。正常な明示importから、
JSON rootやrecordが破損した既存容器を復旧する経路は維持する。
load／browser importは破損容器を上書きせず、通常のmerge・expiry・delta・lockを維持する。

## 互換性

公開constructor・メソッド・保存形式1・暗号方式・scrypt・keyring・ロックは変更しない。
正常な現行形式は移行不要。未知のフィールドは無視し、ドメイン・パスを追加正規化しない。
従来のstr()/bool()による暗黙変換で読めた不正型は拒否する。
通常の値なしCookieのvalue=nullは従来の文字列"None"からPythonのNoneへ正しく復元する。
空文字CookieとHttpOnlyのnull、SameSiteなどのJSON属性を保持し、期限0や過去の期限は除去する。

## 検証と記録

Windows／CPython 3.11.4の既存quality-venvで検証する。通常回帰テストは
tests/v3/storage_output/test_cookie_data_validation.py。架空の鍵・Cookie・passphraseだけを使用し、
外部通信・実keyringは使わない。作業用コピーでの153件成功は事前確認であり、
正式なブランチで重点・既存Cookieテスト・公開API・v3全体を改めて実行する。

最終ローカル結果：重点183 passed、v3全体2116 passed・17 skipped（579.51秒）、
coverage 88.66%。Ruff・mypy（96 source files）・pip checkは成功。
失敗・外部期限による停止はない。skipの理由は検証JSONに記録する。

結果、正確なツール版、source/test/tool SHA-256はraw/qs-011-verification.json、実行ログは
raw/qs-011-focused.stdoutとraw/qs-011-v3.stdoutに記録する。base_commitは検証前のmain、
修正ソースはSHA-256と、この資料を追加したGitコミットで識別する。
PRの12 test job・3 package smoke、統合後mainのCIは対象SHAとrun URLをissueへ記録してから閉じる。
当時の監査資料・問題挙動プローブは変更しない。分類はCWE-1287／CWE-248。
