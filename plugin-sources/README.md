# Local plugin sources

These are signed local-plugin API v3 source units. Their runtime destination is
configured in `.runtime/config/app.yaml`; do not copy files into the runtime
root by hand. Re-sign a changed unit, then use `image-downloader plugin install`
so the catalog pin is updated atomically.

Each included unit uses `plugin-metadata.json` as input to the bundled signer.
It is not read by runtime; the signer transfers it into `manifest.json` together
with the generated key, file-tree, and signature fields. Change metadata, entry
source, author YAML, or helpers before running the signer again; do not edit a
generated manifest directly. The exact schema, optional-runtime status, and
file-tree rules are documented in [plugin package reference](../docs/v3/reference/plugin-package.md#plugin-package).
Hook implementation is documented in [plugin hook reference](../docs/v3/reference/plugin-hooks.md).
The bundled signer supports both `site_plugin` and `image_processor_plugin`; the unit metadata chooses the kind.

```powershell
python tools/sign_local_site_plugin.py `
  --key "$env:LOCALAPPDATA\image-downloader\keys\local-site-plugins-ed25519.pem" `
  plugin-sources/generic-css-selector plugin-sources/paginated-catalog

$env:PYTHONPATH = 'src'
python -m image_downloader plugin install "${PWD}/plugin-sources/generic-css-selector" `
  --config "${PWD}/.runtime/config/app.yaml" --plugin-root "${PWD}/.runtime/plugin-root" --yes
```

`local.image-downloader.generic-css-selector` is enabled by default and accepts
a CSS selector through persistent `plugin_settings` or an invocation override:

```powershell
image-downloader URL --plugin-config 'local.image-downloader.generic-css-selector={"selector":".viewer > img[src]"}'
```

`local.image-downloader.paginated-catalog` is a fixture-site example for
`https://catalog.example.test/collections/<slug>` and has higher match priority.
It is selected before the generic plugin for that URL shape.

## Image processor reference units

`artifact-history-processor` is the smallest executable processor contract: it
validates `config.label` and immutably appends that label to `ImageArtifact.history`.
`resize-processor` validates positive `max_width`/`max_height`, uses Pillow to
resize within those bounds, and emits a PNG while retaining the source URL and
image ID. They are not active merely because they are in this repository.

```powershell
python tools/sign_local_site_plugin.py `
  --key "$env:LOCALAPPDATA\image-downloader\keys\local-processors-ed25519.pem" `
  plugin-sources/artifact-history-processor plugin-sources/resize-processor

$env:PYTHONPATH = 'src'
python -m image_downloader plugin install "${PWD}/plugin-sources/artifact-history-processor" `
  --config "${PWD}/.runtime/config/app.yaml" --plugin-root "${PWD}/.runtime/plugin-root" --yes
python -m image_downloader plugin install "${PWD}/plugin-sources/resize-processor" `
  --config "${PWD}/.runtime/config/app.yaml" --plugin-root "${PWD}/.runtime/plugin-root" --yes
```

Enable either or both explicitly in application YAML:

```yaml
image_processors:
  chain:
    - local.image-downloader.artifact-history-processor
    - local.image-downloader.resize-processor
plugin_settings:
  local.image-downloader.artifact-history-processor:
    config: {label: processed}
  local.image-downloader.resize-processor:
    config: {max_width: 1600, max_height: 1600}
```

## Migrated v2 pattern samples

The following independent v3 units retain the v2 entry source filenames and
class names. They are fixture-only examples; their `example.test` hosts are not
real services.

| Category | Unit | Pattern |
| --- | --- | --- |
| Catalog | `chaptered-catalog` | JSON catalog, chapters, and update snapshots; completed update-provider example |
| HTML | `public-gallery` | Public HTML image discovery with Origin/Referer |
| API | `cursor-api-gallery` | Cursor pagination with an API-token secret |
| Authentication | `csrf-login-gallery` | CSRF form login with username/password secrets |
| Authentication | `oauth-media-api` | OAuth bearer-token refresh |
| CDN | `signed-cdn-gallery` | Signed URL creation and failure recovery |

Sign and install all six units with the same local signing key:

```powershell
python tools/sign_local_site_plugin.py `
  --key "$env:LOCALAPPDATA\image-downloader\keys\local-site-plugins-ed25519.pem" `
  plugin-sources/chaptered-catalog plugin-sources/public-gallery `
  plugin-sources/cursor-api-gallery plugin-sources/csrf-login-gallery `
  plugin-sources/oauth-media-api plugin-sources/signed-cdn-gallery

$env:PYTHONPATH = 'src'
python -m image_downloader plugin install "${PWD}/plugin-sources/csrf-login-gallery" `
  --config "${PWD}/.runtime/config/app.yaml" --plugin-root "${PWD}/.runtime/plugin-root" --yes
```

Authentication values remain outside the unit. Configure secret references in
`plugin_settings` (not literal credentials), for example:

```yaml
plugin_settings:
  local.image-downloader.csrf-login-gallery:
    secrets:
      username: EXAMPLE_GALLERY_USERNAME
      password: EXAMPLE_GALLERY_PASSWORD
  local.image-downloader.cursor-api-gallery:
    secrets:
      api_token: EXAMPLE_GALLERY_API_TOKEN
  local.image-downloader.oauth-media-api:
    secrets:
      access_token: EXAMPLE_OAUTH_ACCESS_TOKEN
      refresh_token: EXAMPLE_OAUTH_REFRESH_TOKEN
```
