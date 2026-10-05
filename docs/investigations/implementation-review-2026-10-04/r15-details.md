# R15 パス長対策と境界検証

## 変更と互換性

通常の名前と`max_component_length`による従来の短縮結果を維持し、保存先のOS要素上限を超える名前だけさらに短縮する。WindowsではUTF-16単位数、POSIXではファイルシステムのエンコーディングによるバイト数を使用する。上限は最寄りの既存親から取得し、取得不能・不定なら255を使用する。

追加短縮は`先頭_ハッシュ8桁`で、最後の拡張子と重複改名の連番を保持する。明示設定による旧短縮で拡張子が落ちるケースは今回の対象外。`safe_component()`の公開動作、設定キー・既定値、例外型・終了コード、画像検査とプラグイン順序、manifest/revision計算は変更しない。

原子的保存の一時名を`.<最終名>.<ランダム8文字>`から`.id-<ランダム8文字>`へ変更した。Cookie/stateなど共通の保存処理にも適用されるが、最終ファイル名・保存内容と原子的置換を維持する。失敗時は既存ファイルを保持する。

保存root、profile、plugin指定の追加ファイル相対パス、state名は自動短縮しない。長さ超過は既存`storage_error`の定型messageで通知し、code/reason/JSON項目は維持する。Windowsで長いパスの作成が親の存在にもかかわらず`FileNotFoundError`になるケースは可能性として診断する。任意の例外文字列・絶対パスを診断messageへ公開しない。

## Windows実測

環境: 2026-10-05、Windows、CドライブのNTFS、Python 3.11.4、`LongPathsEnabled=1`、要素上限255。ASCII名を使用した。長さは`C:\`からの絶対パスのUTF-16単位数で、終端NULと`\\?\`接頭辞を除く。

| 読み書き方法 | 修正前 | 修正後 |
| --- | ---: | ---: |
| 通常絶対パスの直接作成・読取り・上書き | 32,739 | 32,739 |
| 拡張長パスの直接作成・読取り・上書き | 32,739 | 32,739 |
| `FileSystem.write_bytes_atomic()`とコア読取り | 32,729 | 32,739 |
| `atomic_write()`とコア読取り | 32,729（旧一時名を再現） | 32,739 |

親長32,520・32,600・32,680、通常／拡張長パス、短いFileSystem rootから深い相対パスを渡す構成で確認する。修正後は32,729・32,730・32,738・32,739で作成・読取り・上書きとバイト一致が成功し、32,740で失敗する。境界値は今回のドライブ・Python・パス構成での実測値であり、製品の固定上限や全環境での保証値にしない。

上限超過は`FileNotFoundError(errno=2)`を返すことがある。さらに長い入力ではPythonの`ValueError: ... path/src/dst too long for Windows`が発生する。通常の不存在判定やその他のValueErrorまで長さ超過とみなさない。

## 再実行

Windowsのリポジトリルートで実行する。専用`.runtime/r15-boundary-*`配下に深いディレクトリとファイルを作成するため、通常のユニットテストからは自動実行しない。

```powershell
Set-Location C:\Users\pc01\source\repos\image-downloader
.\.runtime\quality-venv\Scripts\python.exe docs/investigations/implementation-review-2026-10-04/r15-path-boundary-probe.py --output .runtime/r15-boundary-after.json
.\.runtime\quality-venv\Scripts\python.exe docs/investigations/implementation-review-2026-10-04/r15-path-boundary-probe.py --legacy-temp --output .runtime/r15-boundary-legacy.json
```

`--legacy-temp`は旧一時名の作り方だけを再現する。旧製品全体に切り替える機能ではない。プローブは直接I/Oの境界を再測定し、現在の原子的保存が同じ境界まで到達すること、旧一時名では10単位手前になることをassertする。通常／拡張長、コアの保存と汎用atomic_write、読取り・上書きの完全一致、境界直前と超過を確認する。想定外の結果・清掃失敗は非ゼロ終了。生成物は専用root配下に限定し、ファイル削除後にディレクトリを深い順から反復で削除する。

## 対処と対象外

パス全体が長すぎる場合は、通常downloadの`--output-dir`を短い絶対パスへ変更する。Cookie/state/logも対象なら短い`storage.data_root`へ既存profileデータを手動移行し、`config explain`で確認する。WindowsではOS設定と実行Pythonの長いパス対応を確認する。コアは保存root・OS設定を自動変更しない。

短い一時名も収まらないほど親が長い場合は原子的保存できない。別rootへ一時ファイルを作る、非原子的に書く、親を自動改名する対応は行わない。要素の短縮結果はOSによって異なり得る。既存ファイル・workflow stateは自動移行せず、正常なORIGINALの保存バイト列を変更しない。

## 最終検証結果

- 全v3テスト: **1,415 passed、16 skipped**（489.18秒）。旧「既定で無制限」のテストは、通常名の維持・OS超過時だけの短縮・明示設定による旧短縮結果の維持を検証する形へ更新した。
- ruff、構文チェック、公開API・文書契約: 成功。mypyはWindows・Linux・macOS指定のそれぞれで92 source filesを確認して成功。
- 上記境界プローブは通常／拡張長パス、3種の親長、直接I/O・コア保存・汎用atomic_writeの18組合せで実行し、旧一時名の方式と修正後の差を再現した。生成物の清掃も成功した。
- NTFSの最終名245・246・255文字で原子的保存が成功し、256文字の明示パスは診断付きで拒否した。長い章名の追加短縮が成功し、旧`max_component_length`による拡張子なしの短縮名は維持した。
- 長い絵文字タイトルのmock workflowでinspectのタイトル・revision、ORIGINAL保存のバイト一致、通常download・updated/all workflow、完了状態を確認した。
