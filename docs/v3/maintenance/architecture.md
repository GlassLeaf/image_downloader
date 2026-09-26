# Architecture and non-stable boundaries

Only package root and the six documented facades are stable. `configuration.*` resolves/validates YAML, `storage.*` owns trusted-root and interprocess safety, `transport.gateway` owns operation HTTP/auth, `plugins.*` owns discovery/verification/loading/selection, `media.*` owns artifact processing, `application.*` composes and runs operations, and `observability.*` owns diagnostics.

`RuntimeComposer` builds filesystem, state, event, notification, cookie, HTTP, image-processing, and logging dependencies. `DownloadService` coordinates their lifecycle. Direct construction of service internals and dependence on `service` implementation attributes are unsupported; `_RuntimeDependencies` is the deliberately stable advanced composition DTO.

Plugin modules are loaded into service-owned isolated namespaces. `PluginRecord` is an immutable metadata snapshot, not an imported module cache. loader/runtime/service close unloads its namespace without affecting a distinct service instance. These are maintenance explanations, not a promise that internal module paths remain import-compatible.
