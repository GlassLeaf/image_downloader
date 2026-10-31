"""Deterministic projects and conservative, reviewable additions to existing sources."""

from __future__ import annotations

import ast
import base64
import difflib
import importlib.resources
import json
import keyword
import re
import uuid
from pathlib import Path
from typing import Any

import yaml

from ..configuration.merging import deep_merge
from ..configuration.models import _valid_plugin_id
from ..models import AdditionalFileHookPoint
from ..plugins.plugin_files import canonical_jcs
from .catalog import AUTH, CATALOG, FEATURES, select_features
from .fixture_generation import build_fixture
from .loading import SITE_HOOKS, resolve_target, workspace_for
from .output import read_document

# Each signature describes the actual public v3 hook. Only selected optional hooks are emitted.
SIGNATURES = {
    "validate_config": "def validate_config(self, config: Mapping[str, object], "
    "app_settings: Mapping[str, object]) -> None",
    "matches": "def matches(self, url: str) -> bool",
    "matches_with_config": "def matches_with_config(self, url: str, config: Mapping[str, object], "
    "app_settings: Mapping[str, object]) -> bool",
    "inspect": "async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest",
    "create_image_request": "async def create_image_request(self, image: ImageResource, "
    "context: PluginExecutionContext) "
    "-> RequestSpec | ImageFetchRequest",
    "recover_image_request": "async def recover_image_request(self, image: ImageResource, failed: RequestSpec, "
    "response: RequestResponse, context: PluginExecutionContext) "
    "-> RequestSpec | ImageFetchRequest | None",
    "auth_flow": "def auth_flow(self, context: PluginExecutionContext) -> AuthFlow | None",
    "transform_image": "async def transform_image(self, artifact: ImageArtifact, "
    "context: TransformContext) -> ImageArtifact",
    "transform": "async def transform(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact",
    "check_updates": "async def check_updates(self, url: str, context: PluginExecutionContext) -> UpdateSnapshot",
    "additional_file_hook_points": "def additional_file_hook_points(self, context: PluginExecutionContext) "
    "-> tuple[AdditionalFileHookPoint, ...]",
    "additional_files": "async def additional_files(self, hook: AdditionalFileHookContext, "
    "context: PluginExecutionContext) "
    "-> tuple[AdditionalFileSpec, ...]",
    "additional_file_received": "async def additional_file_received(self, "
    "result: AdditionalFileReceiveResult, "
    "context: PluginExecutionContext) -> None",
    "additional_file_saved": "async def additional_file_saved(self, result: AdditionalFileSaveResult, "
    "context: PluginExecutionContext) -> None",
    "output_format_values": "def output_format_values(self, context: PluginFormatContext) -> Mapping[str, str]",
    "cleanup_after_use": "async def cleanup_after_use(self) -> None",
}
ARGS = {
    "validate_config": "config, app_settings",
    "inspect": "url, context",
    "create_image_request": "image, context",
    "recover_image_request": "image, failed, response, context",
    "auth_flow": "context",
    "transform_image": "artifact, context",
    "transform": "artifact, context",
    "check_updates": "url, context",
    "additional_file_hook_points": "context",
    "additional_files": "hook, context",
    "additional_file_received": "result, context",
    "additional_file_saved": "result, context",
    "output_format_values": "context",
    "cleanup_after_use": "",
}
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR4nGP8zwACTGCSAQANHQEDgslx/wAAAABJRU5ErkJggg=="
)


def wizard(spec: dict[str, Any]) -> dict[str, Any]:
    kind = spec.get("kind") or (
        "image_processor_plugin" if input("画像プロセッサーですか? [y/N]: ").lower() in {"y", "yes"} else "site_plugin"
    )
    spec["kind"] = kind
    default_id = "com.example.gallery" if kind == "site_plugin" else "com.example.processor"
    spec["id"] = input(f"プラグインID [{spec.get('id', default_id)}]: ").strip() or spec.get("id", default_id)
    names: list[str] = []
    config = dict(spec.get("config", {}))
    for feature in FEATURES:
        if kind not in feature.kinds:
            continue
        default = feature.id in spec.get("features", ["host", "html", "request", "cleanup"])
        answer = input(f"{feature.label}は必要ですか? [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        if answer in {"y", "yes"} or not answer and default:
            for family in ({"html", "json"}, AUTH):
                if feature.id in family:
                    names = [name for name in names if name not in family]
            names.append(feature.id)
            for key, question in feature.questions:
                current = config.get(key, feature.defaults.get(key, ""))
                answer = input(f"{question} [{current}]: ").strip()
                if answer:
                    config[key] = (
                        [item.strip() for item in answer.split(",") if item.strip()]
                        if isinstance(current, list)
                        else answer
                    )
    spec.update(features=names, config=config)
    return spec


def normalize_spec(spec: dict[str, Any]) -> dict[str, Any]:
    spec = dict(spec)
    spec.setdefault("kind", "site_plugin")
    spec.setdefault("id", "com.example.gallery" if spec["kind"] == "site_plugin" else "com.example.processor")
    if not _valid_plugin_id(spec["id"]):
        raise ValueError("id must be a valid reverse DNS plugin ID")
    spec.setdefault("class", "GalleryPlugin" if spec["kind"] == "site_plugin" else "ImageProcessorPlugin")
    if not spec["class"].isidentifier() or keyword.iskeyword(spec["class"]):
        raise ValueError("class must be a Python identifier")
    spec["features"] = select_features(
        spec.get("features", ["html", "cleanup"] if spec["kind"] == "site_plugin" else ["transform", "cleanup"]),
        spec["kind"],
    )
    config: dict[str, Any] = {}
    for name in spec["features"]:
        config = deep_merge(config, CATALOG[name].defaults)
    config = deep_merge(config, spec.get("config", {}))
    if config.get("additional_points") == ["all"] or config.get("additional_points") == "all":
        config["additional_points"] = [point.value for point in AdditionalFileHookPoint]
    for point in config.get("additional_points", []):
        AdditionalFileHookPoint(point)
    spec["config"] = config
    spec.setdefault("sample_url", "https://" + (config.get("hosts") or ["example.test"])[0] + "/gallery")
    spec.setdefault("target", "plugin")
    return spec


def pending_features(spec: dict[str, Any]) -> dict[str, str]:
    pending = {}
    selected, config = set(spec["features"]), spec["config"]
    for feature, key, hook in (
        ("oauth", "token_url", "auth_flow"),
        ("csrf", "login_url", "auth_flow"),
        ("short-lived", "resolve_url", "create_image_request"),
    ):
        if feature in selected and not config.get(key):
            pending[hook] = f"TODO: configure {key} for {feature} and regenerate"
    if "chapters" in selected and "html" in selected and not config.get("chapter_selector"):
        pending["inspect"] = "TODO: configure chapter_selector for HTML chapters and regenerate"
    return pending


def blocks(spec: dict[str, Any]) -> dict[str, str]:
    selected = set(spec["features"])
    hooks = set(SITE_HOOKS) if spec["kind"] == "site_plugin" else {"validate_config", "transform"}
    for name in selected:
        hooks.update(CATALOG[name].hooks)
    pending = pending_features(spec)
    result = {
        "_dev_support": "    def _dev_support(self):\n"
        "        if not hasattr(self, '_generated_support'):\n"
        f"            self._generated_support = GeneratedSupport({spec['features']!r})\n"
        "        return self._generated_support\n"
    }
    for hook in SIGNATURES:
        if hook not in hooks:
            continue
        if hook in pending:
            body = f"raise NotImplementedError({pending[hook]!r})"
        elif hook == "matches":
            body = "return self._dev_support().matches(url, DEFAULT_CONFIG)"
        elif hook == "matches_with_config":
            body = "return self._dev_support().matches(url, config)"
        elif (
            hook == "recover_image_request"
            and "short-lived" not in selected
            or (hook == "auth_flow" and not AUTH & selected)
        ):
            body = "return None"
        elif hook == "transform_image" and not {"transform", "history"} & selected:
            body = "return artifact"
        else:
            target = "transform" if hook == "transform_image" else hook
            prefix = "await " if SIGNATURES[hook].startswith("async ") else ""
            body = f"return {prefix}self._dev_support().{target}({ARGS[hook]})"
        result[hook] = f"    {SIGNATURES[hook]}:\n        {body}\n"
    return {
        name: f"    # devtools:begin {name}\n{source}    # devtools:end {name}\n" for name, source in result.items()
    }


def imports_for(methods: dict[str, str]) -> str:
    names = set(
        re.findall(r"\b[A-Z][A-Za-z]+\b", "\n".join(SIGNATURES[name] for name in methods if name in SIGNATURES))
    )
    public = sorted(names - {"Mapping", "None", "DEFAULT", "CONFIG", "GeneratedSupport", "NotImplementedError", "TODO"})
    return (
        "from collections.abc import Mapping\n"
        + ("from image_downloader import (\n    " + ",\n    ".join(public) + ",\n)\n" if public else "")
        + "from ._generated_support import GeneratedSupport\n"
        + ("from ._generated_config import DEFAULT_CONFIG\n" if "matches" in methods else "")
    )


def render_project(spec: dict[str, Any]) -> dict[str, str | bytes]:
    spec = normalize_spec(spec)
    methods = blocks(spec)
    entry = '"""Generated local plugin API v3 entry.\nSee plugin-dev.yaml for selected features.\n"""\n'
    entry += "from __future__ import annotations\n\n" + import_block(methods)
    entry += f"\n\nclass {spec['class']}:\n" + "\n".join(methods.values())
    support = (
        importlib.resources.files("image_downloader.devtools")
        .joinpath("templates/support.py.txt")
        .read_text(encoding="utf-8")
    )
    metadata = {
        "id": spec["id"],
        "publisher": spec.get("publisher", ".".join(spec["id"].split(".")[:-1])),
        "version": spec.get("version", "0.1.0"),
        "kind": spec["kind"],
        "capabilities": spec["features"],
        "match_priority": spec.get("match_priority", 0),
        "entry": {"file": "plugin.py", "class": spec["class"]},
        "config_file": "plugin.yaml",
    }
    fixture = build_fixture(spec)
    spec = dict(spec)
    secrets = {
        "api-key": {"api_token": "API_TOKEN"},
        "bearer": {"access_token": "ACCESS_TOKEN"},
        "oauth": {"access_token": "ACCESS_TOKEN", "refresh_token": "REFRESH_TOKEN"},
        "csrf": {"username": "USERNAME", "password": "PASSWORD"},
    }
    spec.setdefault("secret_references", {})
    for feature in spec["features"]:
        spec["secret_references"].update(secrets.get(feature, {}))
    if "network" in spec["features"]:
        spec["application"] = deep_merge(
            {"network": {"max_attempts": 3, "global_request_interval_seconds": 0.1}}, spec.get("application", {})
        )
    tests = """from pathlib import Path
from image_downloader.devtools.cli import main


def test_generated_contract():
    project = Path(__file__).resolve().parents[1]
    assert main(["load", str(project), "--json"]) == 0
"""
    if not pending_features(spec):
        tests += (
            "\n\ndef test_generated_replay():\n"
            "    project = Path(__file__).resolve().parents[1]\n"
            '    assert main(["simulate", str(project), ' + repr(spec["sample_url"]) + ",\n"
            '                 "--mode", "replay", "--fixture", str(project / "fixtures/replay.yaml"),\n'
            '                 "--json"]) == 0\n'
        )
    return {
        "plugin/plugin.py": entry,
        "plugin/_generated_support.py": support,
        "plugin/_generated_config.py": "DEFAULT_CONFIG = " + repr(spec["config"]) + "\n",
        "plugin/plugin.yaml": yaml.safe_dump({"config": spec["config"]}, allow_unicode=True, sort_keys=False),
        "plugin/plugin-metadata.json": json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        "plugin/manifest.json": json.dumps({"manifest": metadata}, ensure_ascii=False, indent=2) + "\n",
        "plugin-dev.yaml": yaml.safe_dump(spec, allow_unicode=True, sort_keys=False),
        "fixtures/replay.yaml": yaml.safe_dump(fixture, allow_unicode=True, sort_keys=False),
        "fixtures/image.png": PNG,
        "tests/test_generated.py": tests,
        "requirements-dev.txt": "image-downloader[parser]\npytest>=8\n",
        ".gitignore": ".plugin-dev/\n__pycache__/\n.pytest_cache/\n",
        "README.md": "# "
        + spec["id"]
        + "\n\n"
        + "`python -m pip install -r requirements-dev.txt`\n\n"
        + "`image-downloader-dev load .`\n\n"
        + f"`image-downloader-dev simulate . {spec['sample_url']} --mode replay --fixture fixtures/replay.yaml`\n\n"
        + "`image-downloader-dev check .`\n\n"
        + "未実装箇所は `image-downloader-dev check .` で確認できます。\n",
    }


def create_project(directory: Path, spec: dict[str, Any]) -> dict[str, Any]:
    directory = directory.resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("generation directory must be new or empty")
    files = render_project(spec)
    for relative, content in files.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    state = generation_state(files)
    state_path = directory / ".plugin-dev/generation.json"
    state_path.parent.mkdir(exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"directory": str(directory), "files": list(files), "unimplemented": pending_features(normalize_spec(spec))}


def generation_state(files: dict[str, str | bytes]) -> dict[str, str]:
    return {name: value for name, value in files.items() if isinstance(value, str) and name != "plugin-dev.yaml"}


def auxiliary_changes(
    workspace: Path,
    files: dict[str, str | bytes],
    baseline: dict[str, str],
    candidates: dict[Path, bytes],
    conflicts: list[str],
) -> dict[Path, bytes | None]:
    paths: dict[Path, bytes | None] = {}
    for relative in ("fixtures/replay.yaml", "tests/test_generated.py", "requirements-dev.txt"):
        if relative not in baseline:
            continue
        path = workspace / relative
        raw = path.read_bytes() if path.exists() else None
        paths[path] = raw
        current = raw.decode("utf-8").replace("\r\n", "\n") if raw is not None else None
        desired = str(files[relative])
        if current is not None and current not in {baseline[relative], desired}:
            conflicts.append(relative)
        else:
            candidates[path] = desired.encode()
    return paths


def _replace_blocks(
    current: str, baseline: str, desired: str, *, class_name: str, preserve: set[str]
) -> tuple[str, list[str]]:
    pattern = re.compile(r"^    # devtools:begin (\w+)\n.*?^    # devtools:end \1\n", re.M | re.S)
    old = {match.group(1): match.group(0) for match in pattern.finditer(baseline)}
    new = {match.group(1): match.group(0) for match in pattern.finditer(desired)}
    conflicts = []
    existing = {match.group(1): match.group(0) for match in pattern.finditer(current)}
    for name, block in new.items():
        if name in existing:
            if existing[name] == block or block == old.get(name):
                continue
            if existing[name] != old.get(name):
                conflicts.append(name)
            else:
                current = current.replace(existing[name], block, 1)
        else:
            tree = ast.parse(current)
            classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name]
            if len(classes) != 1:
                conflicts.append(name)
                continue
            if any(getattr(node, "name", None) == name for node in classes[0].body):
                if name not in preserve:
                    conflicts.append(name)
                continue
            lines = current.splitlines(keepends=True)
            position = classes[0].end_lineno
            assert position is not None
            while position < len(lines) and (not lines[position].strip() or lines[position].startswith("    #")):
                position += 1
            lines.insert(position, "\n" + block)
            current = "".join(lines)
    return current, conflicts


def changed_hooks(features: list[str], kind: str) -> set[str]:
    hooks = {hook for feature in features for hook in CATALOG[feature].hooks}
    groups = {
        "inspect": {"html", "json", "pagination", "chapters", "empty-policy", "save-options", "metadata"},
        "matches": {"host"},
        "create_image_request": {"request", "short-lived", "metadata"},
        "transform" if kind == "image_processor_plugin" else "transform_image": {"transform", "history"},
    }
    for hook, names in groups.items():
        if set(features) & names:
            hooks.add(hook)
    return hooks


def import_block(methods: dict[str, str]) -> str:
    return "# devtools:imports:begin\n" + imports_for(methods) + "# devtools:imports:end\n"


def merge_imports(current: str, baseline: str, methods: dict[str, str]) -> tuple[str, bool]:
    pattern = re.compile(r"^# devtools:imports:begin\n.*?^# devtools:imports:end\n", re.M | re.S)
    existing = pattern.search(current)
    old = pattern.search(baseline)
    desired = import_block(methods)
    if existing:
        if existing.group() != desired and (not old or existing.group() != old.group()):
            return current, True
        return current[: existing.start()] + desired + current[existing.end() :], False
    tree = ast.parse(current)
    position = 0
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            position = node.end_lineno or position
        elif isinstance(node, ast.ImportFrom) and node.module == "__future__":
            position = node.end_lineno or position
        else:
            break
    lines = current.splitlines(keepends=True)
    lines.insert(position, "\n" + desired + "\n")
    return "".join(lines), False


def apply_changes(workspace: Path, changes: dict[Path, bytes], before: dict[Path, bytes | None]) -> Path:
    for path, content in before.items():
        if (path.read_bytes() if path.exists() else None) != content:
            raise RuntimeError(f"file changed before apply: {path}")
    backup = workspace / ".plugin-dev/backups" / uuid.uuid4().hex
    backup.mkdir(parents=True)
    journal = [
        {"path": str(path), "backup": str(index) if before[path] is not None else None}
        for index, path in enumerate(changes)
    ]
    for index, path in enumerate(changes):
        if before[path] is not None:
            (backup / str(index)).write_bytes(before[path] or b"")
    (backup / "files.json").write_text(json.dumps(journal, indent=2), encoding="utf-8")
    applied = []
    try:
        for path, data in changes.items():
            if (path.read_bytes() if path.exists() else None) != before[path]:
                raise RuntimeError(f"file changed during apply: {path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".devtools-" + uuid.uuid4().hex)
            try:
                temporary.write_bytes(data)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            applied.append(path)
    except BaseException:
        for path in reversed(applied):
            content = before[path]
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        raise
    return backup


def extend_project(target: Path, updates: dict[str, Any], *, apply: bool = False) -> dict[str, Any]:
    workspace = workspace_for(target)
    spec_path = workspace / "plugin-dev.yaml"
    record = resolve_target(target)
    for key, expected in (("id", record.id), ("kind", record.kind), ("class", record.manifest.value["entry"]["class"])):
        if key in updates and updates[key] != expected:
            raise ValueError(f"extend cannot change plugin {key}")
    original = (
        read_document(spec_path)
        if spec_path.exists()
        else {
            "id": record.id,
            "kind": record.kind,
            "class": record.manifest.value["entry"]["class"],
            "features": [],
            "config": dict(record.author_defaults),
            "target": str(record.manifest.directory),
        }
    )
    selected = list(dict.fromkeys([*original.get("features", []), *updates.get("features", [])]))
    for family in ({"html", "json"}, AUTH):
        incoming = family.intersection(updates.get("features", []))
        if incoming:
            selected = [name for name in selected if name not in family or name in incoming]
    spec = normalize_spec(deep_merge(original, {**updates, "features": selected}))
    files = render_project(spec)
    state_path = workspace / ".plugin-dev/generation.json"
    baseline = read_document(state_path) if state_path.exists() else {}
    candidates: dict[Path, bytes] = {}
    conflicts: list[str] = []
    source = record.manifest.directory / record.manifest.value["entry"]["file"]
    config_path = record.manifest.directory / record.manifest.value.get("config_file", "plugin.yaml")
    manifest_path = record.manifest.directory / "manifest.json"
    watched = [
        source,
        config_path,
        manifest_path,
        spec_path,
        state_path,
        record.manifest.directory / "_generated_support.py",
        record.manifest.directory / "_generated_config.py",
    ]
    snapshots = {path: path.read_bytes() if path.exists() else None for path in watched}
    snapshots.update(auxiliary_changes(workspace, files, baseline, candidates, conflicts))
    generated = str(files["plugin/plugin.py"])
    current = (snapshots[source] or b"").decode("utf-8-sig").replace("\r\n", "\n")
    preserve = (SITE_HOOKS | {"transform"}) - changed_hooks(updates.get("features", []), spec["kind"])
    patched, names = _replace_blocks(
        current,
        baseline.get("plugin/plugin.py", ""),
        generated,
        class_name=spec["class"],
        preserve=preserve,
    )
    conflicts.extend(f"{source.name}:{name}" for name in names)
    patched, import_conflict = merge_imports(patched, baseline.get("plugin/plugin.py", ""), blocks(spec))
    if import_conflict:
        conflicts.append("plugin.py:imports")
    ast.parse(patched)
    candidates[source] = patched.encode()
    for name in ("_generated_support.py", "_generated_config.py"):
        path = record.manifest.directory / name
        desired = str(files["plugin/" + name])
        if path.exists() and path.read_text(encoding="utf-8") not in {baseline.get("plugin/" + name), desired}:
            conflicts.append(name)
        else:
            candidates[path] = desired.encode()
    config = read_document(config_path).get("config", {}) if config_path.exists() else {}
    merged = deep_merge(spec["config"], config)
    merged = deep_merge(merged, updates.get("config", {}))
    candidates[config_path] = yaml.safe_dump({"config": merged}, allow_unicode=True, sort_keys=False).encode()
    candidates[spec_path] = str(files["plugin-dev.yaml"]).encode()
    metadata_path = record.manifest.directory / "plugin-metadata.json"
    if metadata_path.exists():
        snapshots[metadata_path] = metadata_path.read_bytes()
        metadata = json.loads(snapshots[metadata_path] or b"{}")
        metadata["capabilities"] = list(dict.fromkeys([*metadata.get("capabilities", []), *spec["features"]]))
        candidates[metadata_path] = (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode()
    if "config_file" not in record.manifest.value:
        manifest = json.loads(canonical_jcs(record.manifest.value))
        manifest["config_file"] = config_path.name
        candidates[manifest_path] = json.dumps({"manifest": manifest}, indent=2).encode()
    candidates[state_path] = json.dumps(generation_state(files)).encode()
    changes = {path: data for path, data in candidates.items() if snapshots[path] != data}
    before = {path: snapshots[path] for path in changes}
    diff = "".join(
        "".join(
            difflib.unified_diff(
                (before[path] or b"").decode("utf-8").splitlines(keepends=True),
                content.decode("utf-8").splitlines(keepends=True),
                fromfile=str(path),
                tofile=str(path),
            )
        )
        for path, content in changes.items()
        if path != state_path
    )
    backup = None
    if apply and not conflicts:
        backup = apply_changes(workspace, changes, before)
    return {
        "diff": diff,
        "conflicts": conflicts,
        "applied": bool(apply and not conflicts),
        "backup": str(backup) if backup else None,
        "unimplemented": pending_features(spec),
        "suggestions": {
            name: blocks(spec).get(name.split(":")[-1], files.get(name, files.get("plugin/" + name, "")))
            for name in conflicts
        },
    }
