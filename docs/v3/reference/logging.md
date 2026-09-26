# Logging reference

<a id="logging-reference"></a>

`image_downloader.observability.logging.__all__` is stable. Logging is diagnostic only; use `DownloadResult`, exceptions, and CLI exit status as the result source of truth.

Exact constructor and callable signatures, including each `DownloadLogger` member,
are listed one per row in the [public signature index](api-signatures.md#logging-api).
This page defines their delivery and failure semantics.

| API | signature / behavior |
| --- | --- |
| `LogRecord(message, chapter_id=None, metadata={}, trusted=False)` | `formatted() -> str` returns safe display text. |
| `ChapterFailureRecord(url, response_url, path, stage, image_index, http_status, code, reason, exception_type, message, transport=None)` | immutable input for a chapter failure group. |
| `LogSink` | `async write(record) -> None`; `async close() -> None`。custom sink は concurrent write を安全に扱う。 |
| `ChapterFileSink(path=None, *, filesystem=None, relative_path=None)` | path または filesystem+relative path が必要。`async write/close` は file を直列化する。 |
| `DebugFileSink` | `ChapterFileSink` に UTC timestamp/chapter/module を追加する。 |
| `ConsoleSink(stream=None)` | `async write(record) -> None`。 |
| `DownloadLogger(sinks=None)` | safety/capture/chapter registration と、下記 record/close method を所有する。 |

`DownloadLogger.configure_safety(logging, output_root=None)`、`safe_url(value)`、`set_chapter_summary_console(enabled)`、`begin_python_log_capture(namespaces)`、`end_python_log_capture()` は synchronous configuration method である。`flush_python_log_capture()`、`core(...)`、`log(...)`、`chapter_header(...)`、`chapter_download(...)`、`chapter_save(...)`、`chapter_error_group(...)`、`chapter_done(...)`、`close_chapter(...)`、`error_detail(...)`、`debug(...)`、`close()` は **async** で await が必要である。

`core(event, *, module, chapter_id=None, url=None, path=None, method=None, status=None, bytes_count=None, count=None, plugin_id=None, attempt=None, action=None, error=None, debug=False, include_chapter=False)` は event/module allowlist に限定し、任意 raw message は受け取らない。duplicate chapter registration と duplicate capture start は `RuntimeError`。caller は close と write を race させてはならない。

`safe_log_text(value, *, maximum=4096)`、`mask_log_text(value, *, safe_query_parameters=None, safe_fragment_parameters=None)`、`safe_url(value, *, safe_query_parameters=None, safe_fragment_parameters=None)`、`safe_relative_path(value, output_root)`、`safe_exception_name(value)` は secret、URL、path、exception name を安全に整形する。これらは raw secret を保存してよい値に変換するものではない。

sink/observer/capture cleanup の failure は safe diagnostic warning として抑制され得る。主 download result、exception、exit status は変更しない。

`LogSink.write()` on the base class raises `NotImplementedError`; an application
providing a custom sink must implement both async methods and tolerate concurrent
writes. `ChapterFileSink` rejects construction without either `path` or both
`filesystem` and `relative_path` with `ValueError`. Direct sink I/O can still
raise its own filesystem/stream exception; failure isolation applies when
`DownloadLogger` delivers or closes managed sinks, where it records a safe
diagnostic warning and keeps the already determined primary result unchanged.
