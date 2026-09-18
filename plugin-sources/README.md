# Local site plugin sources

These are signed local-plugin API v3 source units. Their runtime destination is
configured in `.runtime/config/app.yaml`; do not copy files into the runtime
root by hand. Re-sign a changed unit, then use `image-downloader plugin install`
so the catalog pin is updated atomically.

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

## Migrated v2 pattern samples

The following independent v3 units retain the v2 entry source filenames and
class names. They are fixture-only examples; their `example.test` hosts are not
real services.

| Category | Unit | Pattern |
| --- | --- | --- |
| Catalog | `chaptered-catalog` | JSON catalog, chapters, and update snapshots |
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
