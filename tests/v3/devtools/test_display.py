from __future__ import annotations

import asyncio
import io
import json
import logging
from pathlib import Path

import httpx
import pytest

from image_downloader.devtools.cli import WorkerClient, main, parser
from image_downloader.devtools.display import DisplayPolicy
from image_downloader.devtools.generation import create_project
from image_downloader.devtools.output import CapturedStream, RawLogHandler, RawOutput
from image_downloader.devtools.transport import FixtureTransport, TraceTransport


@pytest.mark.parametrize("source", ["event", "python"])
def test_default_info_threshold_applies_to_both_log_sources(source):
    policy = DisplayPolicy()
    event = "python_log" if source == "python" else "simulation_started"
    assert policy.matches(source, event, level=logging.INFO)
    assert policy.matches(source, event, level=logging.WARNING)
    assert not policy.matches(source, event, level=logging.DEBUG)
    assert not policy.matches("stdout", "stdout")
    assert not policy.matches("stderr", "stderr")


def test_combined_patterns_sources_and_exclusions():
    policy = DisplayPolicy(
        view="all", sources=("event", "python"), events=("http_*", "python_log"),
        excluded_events=("http_request",), loggers=(), excluded_loggers=("httpcore.*",),
    )
    assert policy.matches("event", "http_response", level=logging.DEBUG)
    assert policy.matches("python", "python_log", level=logging.INFO, logger="my_plugin.auth")
    assert not policy.matches("python", "python_log", level=logging.INFO, logger="httpcore.http11")
    assert not policy.matches("event", "http_request", level=logging.ERROR)
    assert not policy.matches("event", "HTTP_response", level=logging.ERROR)
    assert not policy.matches("event", "call_finished", level=logging.ERROR)
    assert not policy.matches("stdout", "stdout")
    assert policy.matches("event", "http_response", level=logging.ERROR, logger="httpcore.http11")


def test_explicit_options_override_all_preset_and_logger_inclusion_selects_python():
    policy = DisplayPolicy(
        view="all", level="WARNING", sources=("python",), loggers=("my_plugin.*", "other.*"),
        excluded_loggers=("my_plugin.noisy",),
    )
    assert policy.matches("python", "python_log", level=logging.WARNING, logger="my_plugin.auth")
    assert policy.matches("python", "python_log", level=logging.ERROR, logger="other.auth")
    assert not policy.matches("python", "python_log", level=logging.INFO, logger="my_plugin.auth")
    assert not policy.matches("python", "python_log", level=logging.ERROR, logger="my_plugin.noisy")
    assert not policy.matches("python", "python_log", level=logging.ERROR, logger="unrelated")
    assert not policy.matches("event", "operation_failed", level=logging.ERROR)
    assert not policy.matches("event", "operation_failed", level=logging.ERROR, logger="my_plugin.auth")
    assert not policy.matches("stdout", "stdout")


def test_streams_have_no_inferred_level_and_accept_event_patterns():
    policy = DisplayPolicy(sources=("stdout", "stderr"), level="CRITICAL", excluded_events=("stderr",))
    assert policy.matches("stdout", "stdout")
    assert not policy.matches("stderr", "stderr")
    assert not DisplayPolicy(sources=("stdout",), loggers=("*",)).matches("stdout", "stdout")


def records(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_summary_saves_details_debug_bytes_and_exception_chain_before_filtering(tmp_path):
    stream = io.StringIO()
    output = RawOutput(tmp_path, stream=stream, binary="hex")
    secret = "VISIBLE-password=" + "x" * 6000
    handler = RawLogHandler(output)
    try:
        try:
            raise ValueError("ORIGINAL-SECRET")
        except ValueError as cause:
            raise RuntimeError(secret) from cause
    except RuntimeError as error:
        with output.reporting():
            output.event("http_request", headers={"Authorization": "VISIBLE"}, body=b"\xffSECRET")
            output.event("session_opened", plugin_id="sample", config={"token": "VISIBLE"})
            handler.emit(logging.LogRecord("my_plugin", logging.DEBUG, "", 0, "DEBUG-SECRET", (), None))
            handler.emit(logging.LogRecord("my_plugin", logging.INFO, "", 0, secret, (), None))
            output.event("operation_failed", error=error, debug=True)
            output.write_stream("stdout", b"\xe3\x81")
            output.write_stream("stdout", b"\x82\x00\xff")
            output.write_stream("stderr", b"stderr-secret")
            assert output.result({"token": "VISIBLE"}) == {"token": "VISIBLE"}
    saved = records(tmp_path / "events.jsonl")
    assert saved[0]["body"]["hex"] == b"\xffSECRET".hex()
    assert Path(saved[0]["body"]["$file"]).read_bytes() == b"\xffSECRET"
    assert saved[1]["config"] == {"token": "VISIBLE"}
    assert saved[2]["level"] == "DEBUG" and saved[2]["message"] == "DEBUG-SECRET"
    assert "ORIGINAL-SECRET" in saved[4]["error"]["traceback"]
    assert (tmp_path / "stdout.log").read_bytes() == b"\xe3\x81\x82\x00\xff"
    assert (tmp_path / "stderr.log").read_bytes() == b"stderr-secret"
    shown = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [item["event"] for item in shown] == [
        "session_opened", "python_log", "operation_failed", "diagnostics_summary",
    ]
    assert "config" not in shown[0]
    assert shown[1]["message"] == secret
    assert shown[2]["error"] == {"type": "RuntimeError", "message": secret}
    assert shown[-1]["hidden_events"] == 2
    assert shown[-1]["hidden_stream_bytes"] == {"stdout": 5, "stderr": 13}
    assert shown[-1]["run_directory"] == str(tmp_path)


@pytest.mark.parametrize(("event", "values", "level"), [
    ("simulation_started", {"url": "https://example.test"}, "INFO"),
    ("simulation_finished", {}, "INFO"),
    ("runtime_download_finished", {"chapter_id": "1", "action": "image_fetch", "debug": True}, "DEBUG"),
    ("runtime_download_finished", {"debug": True}, "INFO"),
    ("call_finished", {}, "DEBUG"),
    ("runtime_request_retry", {"debug": True}, "WARNING"),
    ("runtime_workflow_retry_wait", {"debug": True}, "WARNING"),
    ("http_response", {"status": 401}, "WARNING"),
    ("http_response", {"status": 200}, "DEBUG"),
    ("http_error", {"error": httpx.ConnectError("VISIBLE")}, "WARNING"),
    ("call_failed", {"error": ValueError("VISIBLE")}, "ERROR"),
    ("runtime_operation_failed", {"debug": True}, "ERROR"),
    ("cleanup_failed", {"error": ValueError("VISIBLE")}, "ERROR"),
])
def test_event_classification_and_all_view_preserve_full_payload(tmp_path, event, values, level):
    stream = io.StringIO()
    output = RawOutput(tmp_path, stream=stream, policy=DisplayPolicy(view="all"))
    output.event(event, **values)
    saved = records(tmp_path / "events.jsonl")[0]
    assert json.loads(stream.getvalue()) == saved
    assert saved["source"] == "event" and saved["level"] == level


def test_custom_python_level_is_preserved_and_compared_numerically(tmp_path):
    stream = io.StringIO()
    output = RawOutput(tmp_path, stream=stream, policy=DisplayPolicy(level="WARNING"))
    record = logging.LogRecord("plugin", 35, "", 0, "VISIBLE", (), None)
    record.levelname = "PLUGIN_WARNING"
    RawLogHandler(output).emit(record)
    assert json.loads(stream.getvalue())["level"] == "PLUGIN_WARNING"
    assert records(tmp_path / "events.jsonl")[0]["levelno"] == 35


def test_text_and_split_binary_streams_are_not_decoded_or_filtered_by_level(tmp_path):
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="utf-8")
    output = RawOutput(tmp_path, stream=stream, policy=DisplayPolicy(sources=("stdout",), level="ERROR"))
    captured = CapturedStream(output, "stdout")
    assert captured.write("VISIBLEあ") == 8
    assert captured.buffer.write(b"\xe3\x81") == 2
    assert captured.buffer.write(b"\x82\xff") == 2
    assert captured.buffer.write(memoryview(b"MEMORY")) == 6
    captured.flush()
    expected = "VISIBLEあ".encode() + b"\xe3\x81\x82\xffMEMORY"
    assert buffer.getvalue() == expected
    assert (tmp_path / "stdout.log").read_bytes() == expected


@pytest.mark.parametrize("arguments", [
    ["--log-level", "invalid"], ["--log-source", "invalid"], ["--log-view", "invalid"],
    ["--event", ""], ["--exclude-event", " "], ["--logger", ""], ["--exclude-logger", "\t"],
])
def test_invalid_filter_does_not_create_worker_or_run_directory(tmp_path, arguments):
    root = tmp_path / "runs"
    with pytest.raises(SystemExit) as error:
        main(["load", str(tmp_path / "missing"), "--run-root", str(root), *arguments])
    assert error.value.code == 2
    assert not root.exists()


@pytest.mark.parametrize("command", ["load", "methods", "call", "simulate", "session", "check"])
def test_shared_filter_options_and_level_normalization(command):
    positional = ["matches"] if command == "call" else (["https://example.test"] if command == "simulate" else [])
    args = parser().parse_args([
        command, ".", *positional, "--log-level", "info", "--event", "http_*", "--event", "python_log",
        "--log-source", "event", "--log-source", "python",
    ])
    policy = DisplayPolicy.from_options(vars(args))
    assert policy.level == "INFO" and policy.sources == ("event", "python")
    assert policy.events == ("http_*", "python_log")


_DEBUG_METHOD = '''
    def noisy(self):
        import logging
        import os
        import subprocess
        import sys
        print("TEXT-STDOUT-SECRET", end="")
        print("TEXT-STDERR-SECRET", end="", file=sys.stderr)
        sys.stdout.buffer.write(b"BINARY-STDOUT-SECRET")
        sys.stderr.buffer.write(b"BINARY-STDERR-SECRET")
        os.write(1, b"NATIVE-STDOUT-SECRET")
        os.write(2, b"NATIVE-STDERR-SECRET")
        subprocess.run([sys.executable, "-c", "print('CHILD-STDOUT-SECRET',end='')"], check=True)
        logger = logging.getLogger("my_plugin.auth")
        logger.debug("PYTHON-DEBUG-SECRET")
        logger.info("PYTHON-INFO-SECRET")
        return {"result_token": "RESULT-SECRET"}
'''


@pytest.mark.parametrize(("options", "stdout_visible", "stderr_visible", "info_visible", "debug_visible"), [
    ([], False, False, True, False),
    (["--log-view", "all"], True, True, True, True),
    (["--log-source", "stdout"], True, False, False, False),
    (["--log-view", "all", "--exclude-event", "stdout", "--exclude-event", "stderr"], False, False, True, True),
    (["--logger", "my_plugin.*", "--exclude-logger", "my_plugin.auth"], False, False, False, False),
])
def test_worker_filters_all_output_paths_and_keeps_json_result_and_raw_files(
    tmp_path, capfd, options, stdout_visible, stderr_visible, info_visible, debug_visible,
):
    project = tmp_path / "project"
    create_project(project, {"features": ["json", "cleanup"]})
    source = project / "plugin/plugin.py"
    source.write_text(source.read_text(encoding="utf-8") + _DEBUG_METHOD, encoding="utf-8")
    assert main(["call", str(project), "noisy", "--json", *options]) == 0
    captured = capfd.readouterr()
    reply = json.loads(captured.out)
    assert reply["result"] == {"result_token": "RESULT-SECRET"}
    run = Path(reply["run_directory"])
    for marker in ("TEXT-STDOUT-SECRET", "BINARY-STDOUT-SECRET", "NATIVE-STDOUT-SECRET", "CHILD-STDOUT-SECRET"):
        assert (marker in captured.err) is stdout_visible
        assert marker.encode() in (run / "stdout.log").read_bytes()
    for marker in ("TEXT-STDERR-SECRET", "BINARY-STDERR-SECRET", "NATIVE-STDERR-SECRET"):
        assert (marker in captured.err) is stderr_visible
        assert marker.encode() in (run / "stderr.log").read_bytes()
    assert ("PYTHON-INFO-SECRET" in captured.err) is info_visible
    assert ("PYTHON-DEBUG-SECRET" in captured.err) is debug_visible
    saved = records(run / "events.jsonl")
    assert any(item.get("message") == "PYTHON-DEBUG-SECRET" for item in saved)
    assert json.loads((run / "result.json").read_text(encoding="utf-8")) == reply["result"]
    footer = json.loads(captured.err.splitlines()[-1])
    assert footer["event"] == "diagnostics_summary"
    assert footer["hidden_stream_bytes"]["stdout"] == (0 if stdout_visible else (run / "stdout.log").stat().st_size)
    assert footer["hidden_stream_bytes"]["stderr"] == (0 if stderr_visible else (run / "stderr.log").stat().st_size)


def test_filtering_does_not_affect_http_recording_or_binary_sidecars(tmp_path):
    fixture = tmp_path / "fixture.yaml"
    fixture.write_text(json.dumps({"requests": [{
        "match": {"url": "https://example.test/data"},
        "response": {"status": 401, "headers": {"set-cookie": "session=VISIBLE"}, "body": "BODY-SECRET"},
    }]}), encoding="utf-8")
    streams = []
    for index, policy in enumerate((DisplayPolicy(level="CRITICAL"), DisplayPolicy(view="all"))):
        stream = io.StringIO()
        output = RawOutput(tmp_path / str(index), stream=stream, policy=policy)

        async def request(output=output):
            async with httpx.AsyncClient(transport=TraceTransport(FixtureTransport(fixture), output, record=True)) as c:
                response = await c.get("https://example.test/data", headers={"Authorization": "Bearer VISIBLE"})
                assert response.status_code == 401 and response.content == b"BODY-SECRET"

        asyncio.run(request())
        saved = records(output.directory / "events.jsonl")
        assert Path(saved[-1]["body"]["$file"]).read_bytes() == b"BODY-SECRET"
        streams.append(stream.getvalue())
    assert streams[0] == "" and "Bearer VISIBLE" in streams[1]
    assert (tmp_path / "0/recording.yaml").read_bytes() == (tmp_path / "1/recording.yaml").read_bytes()


def test_session_reload_retains_filter_and_cumulative_hidden_counts(tmp_path, capfd, monkeypatch):
    project = tmp_path / "project"
    create_project(project, {"features": ["json", "cleanup"]})
    source = project / "plugin/plugin.py"
    source.write_text(source.read_text(encoding="utf-8") + _DEBUG_METHOD, encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO("call noisy\nreload\ncall noisy\nquit\n"))
    assert main(["session", str(project), "--json", "--logger", "my_plugin.*"]) == 0
    captured = capfd.readouterr()
    replies = [json.loads(line) for line in captured.out.splitlines()]
    assert [reply["result"] for reply in replies[:-1]] == [
        {"result_token": "RESULT-SECRET"}, {"reloaded": True}, {"result_token": "RESULT-SECRET"},
    ]
    assert captured.err.count("PYTHON-INFO-SECRET") == 2
    assert "PYTHON-DEBUG-SECRET" not in captured.err and "TEXT-STDOUT-SECRET" not in captured.err
    run = Path(replies[-1]["run_directory"])
    assert (run / "stdout.log").read_bytes().count(b"TEXT-STDOUT-SECRET") == 2
    assert json.loads(captured.err.splitlines()[-1])["hidden_stream_bytes"]["stdout"] == (
        run / "stdout.log"
    ).stat().st_size


def test_excluded_failure_and_cleanup_events_do_not_hide_final_error(tmp_path, capfd):
    project = tmp_path / "project"
    create_project(project, {"features": ["cleanup"]})
    source = project / "plugin/plugin.py"
    source.write_text(source.read_text(encoding="utf-8").replace(
        "return await self._dev_support().cleanup_after_use()", 'raise ValueError("CLEANUP-SECRET")',
    ), encoding="utf-8")
    assert main(["load", str(project), "--json", "--exclude-event", "*"]) == 1
    captured = capfd.readouterr()
    reply = json.loads(captured.out)
    assert not reply["ok"] and "CLEANUP-SECRET" in reply["error"]["traceback"]
    assert "CLEANUP-SECRET" not in captured.err
    assert any(event["event"] == "cleanup_failed" for event in records(Path(reply["run_directory"]) / "events.jsonl"))


def test_interrupt_keeps_final_exit_code_and_cleanup_with_all_events_hidden(tmp_path, capfd, monkeypatch):
    project = tmp_path / "project"
    create_project(project, {"features": ["cleanup"]})
    source = project / "plugin/plugin.py"
    source.write_text(source.read_text(encoding="utf-8") + '''
    async def cleanup_after_use(self):
        from pathlib import Path
        Path(__file__).with_suffix(".cleanup").write_text("closed", encoding="utf-8")
''', encoding="utf-8")
    original = WorkerClient.request

    def interrupt(client, payload):
        assert original(client, payload)["ok"]
        raise KeyboardInterrupt

    monkeypatch.setattr(WorkerClient, "request", interrupt)
    assert main(["load", str(project), "--json", "--exclude-event", "*"]) == 130
    captured = capfd.readouterr()
    reply = json.loads(captured.out)
    assert reply["error"] == "interrupted" and Path(reply["run_directory"]).is_dir()
    assert source.with_suffix(".cleanup").read_text(encoding="utf-8") == "closed"
