"""Dedicated CLI: generation stays in the parent; plugin execution stays in a worker."""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from .catalog import FEATURES
from .generation import create_project, extend_project, wizard
from .loading import workspace_for
from .output import read_document


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="image-downloader-dev", description="API v3プラグインの生成・実行・デバッグ")
    result.add_argument("--json", action="store_true")
    subcommands = result.add_subparsers(dest="command", required=True)
    for name in ("init", "extend"):
        command = subcommands.add_parser(name)
        command.add_argument("target", type=Path)
        command.add_argument("--id")
        command.add_argument("--kind", choices=("site_plugin", "image_processor_plugin"))
        command.add_argument("--class", dest="class_name")
        command.add_argument("--features", help="カンマ区切り: " + ", ".join(feature.id for feature in FEATURES))
        command.add_argument("--answers", type=Path)
        command.add_argument("--non-interactive", action="store_true")
        command.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        if name == "extend":
            command.add_argument("--apply", action="store_true")
    for name in ("load", "methods", "call", "simulate", "session", "check"):
        command = subcommands.add_parser(name)
        command.add_argument("target", type=Path)
        command.add_argument("--class", dest="class_name")
        command.add_argument("--kind", choices=("site_plugin", "image_processor_plugin"))
        command.add_argument("--config", type=Path)
        command.add_argument("--app-config", type=Path)
        command.add_argument("--fixture", type=Path)
        command.add_argument("--processor", dest="processors", action="append", default=[])
        command.add_argument("--profile")
        command.add_argument("--profile-data-root", type=Path)
        command.add_argument("--binary", choices=("file", "hex", "base64"), default="file")
        command.add_argument("--timeout", type=float, default=120)
        command.add_argument("--run-root", type=Path)
        command.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        if name == "call":
            command.add_argument("method")
            command.add_argument("--input", dest="input_file", type=Path)
            command.add_argument("--args", default="[]")
            command.add_argument("--kwargs", default="{}")
            command.add_argument("--save-as", default="last")
            command.add_argument("--url")
        if name in {"simulate", "session"}:
            command.add_argument("--record", action="store_true")
            command.add_argument("--force-plugin", action="store_true")
        if name == "simulate":
            command.add_argument("url")
            command.add_argument("--mode", choices=("preview", "download", "replay"), default="preview")
        if name == "check":
            command.add_argument("--scenario", dest="scenarios", type=Path, action="append")
    sign = subcommands.add_parser("sign")
    sign.add_argument("target", type=Path)
    sign.add_argument("--key", type=Path, required=True)
    sign.add_argument("--create-key", action="store_true")
    sign.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    return result


class WorkerClient:
    def __init__(self, options: dict[str, Any], directory: Path, timeout: float) -> None:
        self.timeout = timeout
        directory.mkdir(parents=True, exist_ok=True)
        options["run_directory"] = str(directory)
        path = directory / "command.json"
        path.write_text(json.dumps(options), encoding="utf-8")
        environment = dict(os.environ)
        environment["PYTHONPATH"] = (
            str(Path(__file__).resolve().parents[2]) + os.pathsep + environment.get("PYTHONPATH", "")
        )
        environment["PYTHONIOENCODING"] = "utf-8"
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-m", "image_downloader.devtools.worker", str(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            encoding="utf-8",
            env=environment,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        self.messages: queue.Queue[str | None] = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.messages.put(line)
        self.messages.put(None)

    def receive(self) -> dict[str, Any]:
        line = self.messages.get(timeout=self.timeout)
        if line is None:
            raise RuntimeError(f"development worker exited: {self.process.poll()}")
        return dict(json.loads(line))

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        return self.receive()

    def close(self, *, interrupt: bool = False) -> list[dict[str, Any]]:
        if self.process.poll() is None:
            try:
                if interrupt:
                    self.process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
                elif self.process.stdin:
                    self.process.stdin.write('{"command":"quit"}\n')
                    self.process.stdin.flush()
                    self.process.stdin.close()
                self.process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
        self.reader.join(timeout=1)
        messages = []
        while not self.messages.empty():
            line = self.messages.get_nowait()
            if line:
                messages.append(json.loads(line))
        if self.process.stdout:
            self.process.stdout.close()
        if self.process.stdin and not self.process.stdin.closed:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        return messages


def show(value: Any, *, structured: bool) -> None:
    if not structured and isinstance(value, dict) and value.get("diff") is not None:
        print(value["diff"])
        print(json.dumps({key: item for key, item in value.items() if key != "diff"}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=None if structured else 2))


def generation_command(args: argparse.Namespace) -> dict[str, Any]:
    spec = read_document(args.answers) if args.answers else {}
    for name in ("id", "kind", "features", "class_name"):
        value = getattr(args, name)
        if value is not None:
            spec["class" if name == "class_name" else name] = value.split(",") if name == "features" else value
    if not args.non_interactive and not args.answers and not args.features and sys.stdin.isatty():
        spec = wizard(spec)
    if args.command == "init":
        return create_project(args.target, spec)
    return extend_project(args.target, spec, apply=args.apply)


def session_loop(client: WorkerClient, structured: bool) -> None:
    print(
        "load / methods / call METHOD {args,kwargs,save_as} / simulate URL [preview|download|replay] / "
        "check / refs / reload / quit",
        file=sys.stderr,
    )
    while True:
        try:
            if sys.stdin.isatty():
                print("dev> ", end="", file=sys.stderr, flush=True)
            line = sys.stdin.readline()
            if not line:
                break
            parts = line.strip().split(maxsplit=2)
            if not parts:
                continue
            command = parts[0]
            if command in {"quit", "exit"}:
                break
            payload: dict[str, Any] = {"command": command}
            if command == "call":
                payload["method"] = parts[1]
                payload["inputs"] = json.loads(parts[2]) if len(parts) == 3 else {}
                payload["save_as"] = payload["inputs"].get("save_as", "last")
            elif command == "simulate":
                payload.update(url=parts[1], mode=parts[2] if len(parts) == 3 else "preview")
            show(client.request(payload), structured=structured)
        except (ValueError, KeyError, IndexError) as exc:
            show({"ok": False, "error": str(exc)}, structured=structured)


def execution_command(args: argparse.Namespace) -> int:
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("--timeout must be a finite positive duration")
    options = {key: str(value.resolve()) if isinstance(value, Path) else value for key, value in vars(args).items()}
    options["processors"] = [str(Path(path).resolve()) for path in args.processors]
    directory = (args.run_root or workspace_for(args.target) / ".plugin-dev/runs").resolve() / uuid.uuid4().hex
    client = WorkerClient(options, directory, args.timeout)
    reply: dict[str, Any] = {}
    interrupted = False
    timed_out = False
    try:
        ready = client.receive()
        if not ready.get("ok"):
            reply = ready
        elif args.command == "session":
            session_loop(client, args.json)
            reply = {"ok": True, "result": {"session_finished": True}}
        else:
            payload = {"command": args.command}
            if args.command == "call":
                payload.update(
                    method=args.method,
                    inputs={"args": json.loads(args.args), "kwargs": json.loads(args.kwargs)},
                    input_file=options.get("input_file"),
                    save_as=args.save_as,
                )
            elif args.command == "simulate":
                payload.update(url=args.url, mode=args.mode)
            elif args.command == "check":
                payload["scenarios"] = [str(path.resolve()) for path in args.scenarios or []]
            reply = client.request(payload)
    except KeyboardInterrupt:
        interrupted = True
        reply = {"ok": False, "error": "interrupted"}
    except queue.Empty:
        timed_out = True
        reply = {"ok": False, "error": "development command timed out"}
    finally:
        cleanup = client.close(interrupt=interrupted or timed_out)
        failures = [message for message in cleanup if not message.get("ok", False)]
        if failures and reply.get("ok"):
            reply = failures[0]
    reply["run_directory"] = str(directory)
    show(reply, structured=args.json)
    return 130 if interrupted else (0 if reply.get("ok") else int(reply.get("exit_code", 1)))


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        if args.command in {"init", "extend"}:
            result = generation_command(args)
            show(result, structured=args.json)
            return 1 if result.get("conflicts") else 0
        if args.command == "sign":
            from .signing import load_key, sign

            target = args.target.resolve()
            if (target / "plugin-dev.yaml").is_file():
                target = (target / read_document(target / "plugin-dev.yaml").get("target", "plugin")).resolve()
            if args.key.resolve().is_relative_to(target):
                raise ValueError("signing key must be outside the plugin source tree")
            sign(target, load_key(args.key.resolve(), args.create_key))
            show({"signed": str(target)}, structured=args.json)
            return 0
        return execution_command(args)
    except KeyboardInterrupt:
        return 130
    except (ValueError, TypeError, KeyError, OSError) as exc:
        show({"ok": False, "error": str(exc)}, structured=bool(getattr(locals().get("args"), "json", False)))
        return 2
    except Exception as exc:
        show({"ok": False, "error": str(exc)}, structured=bool(getattr(locals().get("args"), "json", False)))
        return 1
