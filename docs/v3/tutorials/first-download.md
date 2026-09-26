# Tutorial: first download

この tutorial は、固定 user configuration と一回の download を成功させる最短手順である。option の完全な適用規則は [CLI reference](../reference/cli.md)、設定値は [configuration reference](../reference/configuration.md) を使用する。

```powershell
python -m pip install .
image-downloader config path
image-downloader config init `
  --data-root "D:\Images\image-downloader" `
  --plugin-root "C:\Users\you\AppData\Local\image-downloader\plugins"
image-downloader download "https://example.test/gallery"
image-downloader doctor --json
```

`config init` は明示した absolute path を含む fixed user config を作る。`download`、update listing、cookie operation、plugin mutation も config がなければ初期 config を作成し得る。`doctor`、`config path`、`config explain`、plugin list は read-only である。

失敗を自動処理する場合は、次に [JSON CLI output](../reference/cli.md#cli-json) と [exit status](../reference/cli.md#cli-exit-status) を読む。
