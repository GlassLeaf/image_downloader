"""One isolated process owns an operation or a persistent development session."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, TextIO

from .inputs import DevelopmentInputError
from .loading import describe_methods
from .output import CapturedStream, RawLogHandler, RawOutput, capture_native_streams, read_document
from .runtime import DevelopmentSession


async def dispatch(session: DevelopmentSession, request: dict[str, Any]) -> Any:
    command = request["command"]
    if command == "load":
        return {
            "plugin_id": session.record.id,
            "kind": session.record.kind,
            "class": type(session.ensure_instance()).__name__,
            "methods": describe_methods(session.instance),
        }
    if command == "methods":
        return describe_methods(session.ensure_instance())
    if command == "call":
        inputs = request.get("inputs", {})
        root = session.workspace
        if request.get("input_file"):
            path = Path(request["input_file"]).resolve()
            inputs = read_document(path)
            root = path.parent
        return await session.call(request["method"], inputs, root=root, save_as=request.get("save_as", "last"))
    if command == "simulate":
        return await session.simulate(request["url"], request.get("mode", "preview"))
    if command == "check":
        if request.get("scenarios"):
            session.settings["scenarios"] = request["scenarios"]
        return await session.check()
    if command == "refs":
        return session.refs
    raise ValueError(f"unknown session command: {command}")


async def run(options: dict[str, Any], channel: TextIO, diagnostics: TextIO) -> None:
    output = RawOutput(Path(options["run_directory"]), binary=options.get("binary", "file"), stream=diagnostics)
    handler = RawLogHandler(output)
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    session: DevelopmentSession | None = None

    def reply(value: Any) -> None:
        channel.write(json.dumps(value, ensure_ascii=False) + "\n")
        channel.flush()

    with (
        capture_native_streams(output, diagnostics),
        redirect_stdout(CapturedStream(output, "stdout", diagnostics)),
        redirect_stderr(CapturedStream(output, "stderr", diagnostics)),
    ):
        try:
            session = DevelopmentSession(options, output)
            await session.open()
            reply({"ok": True, "ready": True})
            while True:
                line = await asyncio.to_thread(sys.stdin.readline)
                if not line:
                    break
                request = json.loads(line)
                if request["command"] == "quit":
                    break
                if request["command"] == "reload":
                    await session.close()
                    session = DevelopmentSession(options, output)
                    await session.open()
                    reply({"ok": True, "result": {"reloaded": True}})
                    continue
                try:
                    value = await dispatch(session, request)
                    valid = not isinstance(value, dict) or value.get("valid", True)
                    if hasattr(value, "failures"):
                        valid = valid and not value.failures
                    valid = valid and not any(
                        item.status == "failed" for item in getattr(value, "additional_files", ())
                    )
                    reply({"ok": bool(valid), "result": output.result(value)})
                except Exception as exc:
                    output.event("operation_failed", error=exc)
                    reply(
                        {
                            "ok": False,
                            "exit_code": 2 if isinstance(exc, DevelopmentInputError) else 1,
                            "error": output.result(exc),
                        }
                    )
        except BaseException as exc:
            output.event("worker_failed", error=exc)
            reply(
                {
                    "ok": False,
                    "exit_code": 2 if isinstance(exc, DevelopmentInputError) else 1,
                    "error": output.result(exc),
                }
            )
        finally:
            try:
                if session is not None:
                    await session.close()
            except BaseException as exc:
                output.event("worker_cleanup_failed", error=exc)
                reply({"ok": False, "error": output.result(exc)})
            root.removeHandler(handler)


def main() -> None:
    if hasattr(signal, "SIGBREAK"):

        def interrupt(signum: int, frame: Any) -> None:
            raise KeyboardInterrupt

        signal.signal(signal.SIGBREAK, interrupt)
    with (
        os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1) as channel,
        os.fdopen(os.dup(2), "w", encoding="utf-8", buffering=1) as diagnostics,
    ):
        asyncio.run(run(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")), channel, diagnostics))


if __name__ == "__main__":
    main()
