"""QS-001: execute only the source bytes accepted by the plugin policy."""

from __future__ import annotations

import base64
import hashlib
import importlib
import importlib.machinery
import importlib.util
import inspect
import json
import marshal
import os
import struct
import sys
from dataclasses import fields
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from test_plugin_v3 import make_minimal_plugin, make_plugin

from image_downloader import AppConfig, PluginError
from image_downloader.plugins import plugin_manifest, source_loader
from image_downloader.security import (
    PluginClassLoader,
    PluginDiscovery,
    PluginManifestVerifier,
    PluginRecord,
    PluginRuntime,
    canonical_jcs,
    install_plugin,
    read_manifest,
    trust_plugin,
)


def _resign(directory: Path) -> None:
    wrapper = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    manifest = wrapper["manifest"]
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    manifest["public_key"] = base64.b64encode(public).decode("ascii")
    manifest["key_id"] = hashlib.sha256(public).hexdigest()
    tree = plugin_manifest.collect_plugin_file_tree(directory)
    manifest["file_tree"] = tree
    manifest["file_tree_sha256"] = hashlib.sha256(canonical_jcs(tree)).hexdigest()
    wrapper["signature"] = base64.b64encode(private.sign(canonical_jcs(manifest))).decode("ascii")
    (directory / "manifest.json").write_text(json.dumps(wrapper), encoding="utf-8")


def _unit(root: Path, *, prefix: str = "", suffix: str = "", extra: dict[str, bytes] | None = None) -> Path:
    directory = make_plugin(root)
    entry = directory / "entry.source"
    entry.write_bytes(prefix.encode() + entry.read_bytes() + suffix.encode())
    for name, data in (extra or {}).items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    _resign(directory)
    return directory


def _cache(source: Path, kind: str) -> tuple[Path, bytes]:
    data = source.read_bytes()
    code = compile(data + b"\nraise AssertionError('unsigned cached code executed')\n", str(source), "exec")
    if kind == "timestamp":
        header = struct.pack("<III", 0, int(source.stat().st_mtime) & 0xFFFFFFFF, len(data))
    else:
        header = struct.pack("<I", 3 if kind == "checked-hash" else 1) + importlib.util.source_hash(data)
    payload = importlib.util.MAGIC_NUMBER + header + marshal.dumps(code)
    cache = Path(importlib.util.cache_from_source(str(source)))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(payload)
    return cache, payload


def _runtime(directory: Path, mode: str = "strict") -> PluginRuntime:
    root = directory.parent.parent
    if mode in {"strict", "bypass-signature"}:
        trust_plugin(root, directory, mode=mode)
    return PluginRuntime(AppConfig(), root, mode=mode)


@pytest.mark.parametrize("target", ["entry.source", "helper.py", "nested/__init__.py"])
@pytest.mark.parametrize("cache_kind", ["timestamp", "unchecked-hash", "checked-hash"])
@pytest.mark.parametrize("mode", ["strict", "warn", "bypass-catalog", "bypass-signature", "off", "bypass-all"])
def test_unsigned_caches_are_ignored_without_deleting_or_writing_cache_files(
    tmp_path: Path, target: str, cache_kind: str, mode: str
) -> None:
    directory = _unit(
        tmp_path / "plugins",
        prefix="from . import helper, nested\n",
        suffix="\nEntry.answer = helper.VALUE + nested.VALUE\n",
        extra={"helper.py": b"VALUE = 20\n", "nested/__init__.py": b"VALUE = 22\n"},
    )
    cache, payload = _cache(directory / target, cache_kind)
    original_source = (directory / target).read_bytes()
    before_files = set(directory.rglob("*"))
    runtime = _runtime(directory, mode)
    try:
        record = runtime.records["com.example.gallery"]
        plugin_class = runtime.loader.load_class(record)
        assert plugin_class.answer == 42
        assert runtime.loader.load_class(record) is plugin_class
        assert cache.read_bytes() == payload
        assert (directory / target).read_bytes() == original_source
        assert set(directory.rglob("*")) == before_files
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", ["strict", "warn", "bypass-catalog", "bypass-signature", "off", "bypass-all"])
def test_mode_binding_preserves_bypass_behavior_and_rejects_changed_verified_entry(tmp_path: Path, mode: str) -> None:
    directory = _unit(tmp_path / "plugins", suffix="\nEntry.value = 1\n")
    runtime = _runtime(directory, mode)
    try:
        record = runtime.records["com.example.gallery"]
        entry = directory / "entry.source"
        entry.write_bytes(entry.read_bytes() + b"Entry.value = 2\n")
        if mode in {"off", "bypass-all"}:
            assert runtime.loader.load_class(record).value == 2
        else:
            with pytest.raises(PluginError, match="source hash"):
                runtime.loader.load_class(record)
            assert runtime.module_namespace(record) is None
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ["modify", "add", "reload"])
def test_lazy_helpers_cannot_execute_changed_or_undeclared_source(tmp_path: Path, change: str) -> None:
    directory = _unit(
        tmp_path / "plugins",
        suffix="\ndef lazy():\n    from . import helper\n    return helper.VALUE\nEntry.lazy = staticmethod(lazy)\n",
        extra={"helper.py": b"VALUE = 1\n"} if change != "add" else {},
    )
    runtime = _runtime(directory)
    try:
        record = runtime.records["com.example.gallery"]
        plugin_class = runtime.loader.load_class(record)
        if change == "reload":
            assert plugin_class.lazy() == 1
        (directory / "helper.py").write_text("VALUE = 2\n", encoding="utf-8")
        with pytest.raises(PluginError, match="verified file tree"):
            if change == "reload":
                importlib.reload(sys.modules[f"{runtime.module_namespace(record)}.helper"])
            else:
                plugin_class.lazy()
    finally:
        runtime.close()


def test_content_pin_binds_the_tree_used_for_verification_not_a_later_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "plugins"
    directory = make_minimal_plugin(root)
    trust_plugin(root, directory, mode="bypass-signature")
    collect = plugin_manifest.collect_plugin_file_tree
    calls = []

    def collect_then_change(path):
        tree = collect(path)
        calls.append(path)
        entry = path / "entry.source"
        entry.write_bytes(entry.read_bytes() + b"\nEntry.unsigned = True\n")
        return tree

    monkeypatch.setattr(plugin_manifest, "collect_plugin_file_tree", collect_then_change)
    runtime = PluginRuntime(AppConfig(), root, mode="bypass-signature")
    try:
        record = runtime.records["com.example.minimal"]
        assert calls == [directory]
        with pytest.raises(PluginError, match="source hash"):
            runtime.loader.load_class(record)
    finally:
        runtime.close()


def test_bypass_signature_preserves_normal_signed_catalog_pins(tmp_path: Path) -> None:
    directory = _unit(tmp_path / "plugins")
    trust_plugin(directory.parent.parent, directory)
    runtime = PluginRuntime(AppConfig(), directory.parent.parent, mode="bypass-signature")
    try:
        record = runtime.records["com.example.gallery"]
        assert record.catalog is not None and not record.catalog.content_pinned
        entry = directory / "entry.source"
        entry.write_bytes(entry.read_bytes() + b"\nEntry.unsigned = True\n")
        with pytest.raises(PluginError, match="source hash"):
            runtime.loader.load_class(record)
    finally:
        runtime.close()


@pytest.mark.parametrize("target", ["entry.source", "helper.py", "nested/__init__.py"])
def test_source_replacement_after_read_compiles_only_the_checked_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    directory = _unit(
        tmp_path / "plugins",
        prefix="from . import helper, nested\n",
        suffix="\nEntry.value = helper.VALUE + nested.VALUE\n",
        extra={"helper.py": b"VALUE = 20\n", "nested/__init__.py": b"VALUE = 22\n"},
    )
    runtime = _runtime(directory)
    compile_source = source_loader._SourceLoader.source_to_code
    changed = []

    def replace_then_compile(loader, data, path, **kwargs):
        if Path(path) == directory / target:
            changed.append(path)
            Path(path).write_bytes(data + b"\nraise AssertionError('changed code executed')\n")
        return compile_source(loader, data, path, **kwargs)

    monkeypatch.setattr(source_loader._SourceLoader, "source_to_code", replace_then_compile)
    try:
        assert runtime.loader.load_class(runtime.records["com.example.gallery"]).value == 42
        assert changed == [str(directory / target)]
    finally:
        runtime.close()


@pytest.mark.parametrize("filename", ["entry.source", "entry.py"])
def test_entry_metadata_encoding_and_nested_resources_are_preserved(tmp_path: Path, filename: str) -> None:
    directory = _unit(
        tmp_path / "plugins",
        prefix="from .vendor import package\n",
        suffix="\nEntry.resource = package.VALUE\n",
        extra={
            "vendor/package/__init__.py": (
                b"from importlib.resources import files\n"
                b"VALUE = files(__package__).joinpath('data.txt').read_text(encoding='utf-8')\n"
            ),
            "vendor/package/data.txt": b"preserved resource",
        },
    )
    original = directory / "entry.source"
    data = b"# coding: latin-1\n" + original.read_bytes() + b"\nEntry.label = 'caf\xe9'\n"
    original.unlink()
    (directory / filename).write_bytes(data)
    wrapper = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    wrapper["manifest"]["entry"]["file"] = filename
    (directory / "manifest.json").write_text(json.dumps(wrapper), encoding="utf-8")
    _resign(directory)
    runtime = _runtime(directory)
    try:
        record = runtime.records["com.example.gallery"]
        plugin_class = runtime.loader.load_class(record)
        namespace = runtime.module_namespace(record)
        module = sys.modules[f"{namespace}.entry"]
        assert plugin_class.label == "café"
        assert plugin_class.resource == "preserved resource"
        assert module.__file__ == str(directory / filename)
        assert module.__package__ == namespace
        assert module.__spec__.origin == module.__file__
        assert hasattr(module, "__cached__") == (filename == "entry.py")
        assert sys.modules[f"{namespace}.vendor"].__path__[0] == str(directory / "vendor")
        assert not list(directory.rglob("*.pyc"))
    finally:
        runtime.close()


@pytest.mark.parametrize("kind", ["bytecode", "native"])
def test_local_sourceless_and_native_helpers_are_rejected_before_execution(tmp_path: Path, kind: str) -> None:
    if kind == "bytecode":
        code = compile("raise AssertionError('binary module executed')\n", "helper.py", "exec")
        filename = "helper.pyc"
        data = importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, 0, 0) + marshal.dumps(code)
    else:
        filename = "helper" + importlib.machinery.EXTENSION_SUFFIXES[0]
        data = b"not an executable native library"
    directory = _unit(tmp_path / "plugins", prefix="from . import helper\n", extra={filename: data})
    runtime = _runtime(directory)
    try:
        with pytest.raises(PluginError, match="bytecode and native extensions are unsupported"):
            runtime.loader.load_class(runtime.records["com.example.gallery"])
    finally:
        runtime.close()


def test_missing_local_source_is_not_delegated_to_another_meta_finder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _unit(tmp_path / "plugins", prefix="from . import missing\n")
    runtime = _runtime(directory)
    calls = []

    class UncheckedFinder:
        def find_spec(self, fullname, path, target=None):
            if fullname.endswith(".missing"):
                calls.append(fullname)
                raise AssertionError("unchecked fallback used")
            return None

    monkeypatch.setattr(sys, "meta_path", [*sys.meta_path, UncheckedFinder()])
    try:
        with pytest.raises(PluginError, match="plugin entry import failed") as error:
            runtime.loader.load_class(runtime.records["com.example.gallery"])
        assert isinstance(error.value.__cause__, ImportError)
        assert calls == []
    finally:
        runtime.close()


def test_import_failure_releases_finder_and_namespaces_even_for_base_exception(tmp_path: Path) -> None:
    directory = _unit(tmp_path / "plugins", prefix="raise SystemExit(7)\n")
    runtime = _runtime(directory)
    before_modules = set(sys.modules)
    before_namespaces = set(source_loader._NAMESPACE_FINDER._contexts)
    before_meta_path = tuple(sys.meta_path)
    try:
        with pytest.raises(SystemExit):
            runtime.loader.load_class(runtime.records["com.example.gallery"])
        assert set(source_loader._NAMESPACE_FINDER._contexts) == before_namespaces
        assert tuple(sys.meta_path) == before_meta_path
        assert not any(name.startswith("_image_downloader_plugins") for name in set(sys.modules) - before_modules)
    finally:
        runtime.close()


def test_two_runtimes_keep_independent_imports_when_one_is_unloaded(tmp_path: Path) -> None:
    directory = _unit(
        tmp_path / "plugins",
        suffix="\ndef lazy():\n    from . import helper\n    return helper.VALUE\nEntry.lazy = staticmethod(lazy)\n",
        extra={"helper.py": b"VALUE = 1\n"},
    )
    first = _runtime(directory)
    second = PluginRuntime(AppConfig(), directory.parent.parent, mode="strict")
    before_namespaces = set(source_loader._NAMESPACE_FINDER._contexts)
    before_meta_path = tuple(sys.meta_path)
    try:
        record = first.records["com.example.gallery"]
        first_class = first.loader.load_class(record)
        second_record = second.records[record.id]
        second_class = second.loader.load_class(second_record)
        first_namespace = first.module_namespace(record)
        second_namespace = second.module_namespace(second_record)
        assert first_class is not second_class
        first.loader.unload(record)
        assert first_namespace not in source_loader._NAMESPACE_FINDER._contexts
        assert second_namespace in source_loader._NAMESPACE_FINDER._contexts
        assert second_class.lazy() == 1
        assert first.loader.load_class(record).lazy() == 1
    finally:
        first.close()
        second.close()
    assert set(source_loader._NAMESPACE_FINDER._contexts) == before_namespaces
    assert tuple(sys.meta_path) == before_meta_path


@pytest.mark.parametrize("minimal", [False, True])
def test_manually_constructed_records_keep_public_fields_and_default_policy(tmp_path: Path, minimal: bool) -> None:
    directory = make_minimal_plugin(tmp_path / "plugins") if minimal else _unit(tmp_path / "plugins")
    record = PluginRecord(read_manifest(directory, mode="bypass-all"), {}, None)
    assert [field.name for field in fields(record)] == ["manifest", "author_defaults", "catalog", "builtin"]
    assert list(inspect.signature(PluginRecord).parameters) == ["manifest", "author_defaults", "catalog", "builtin"]
    assert record == PluginRecord(record.manifest, {}, None)
    loader = PluginClassLoader()
    entry = directory / "entry.source"
    entry.write_bytes(entry.read_bytes() + b"\nEntry.value = 2\n")
    try:
        if minimal:
            assert loader.load_class(record).value == 2
        else:
            with pytest.raises(PluginError, match="hash"):
                loader.load_class(record)
    finally:
        loader.close()


def test_manually_constructed_content_pinned_record_rejects_changed_source(tmp_path: Path) -> None:
    directory = make_minimal_plugin(tmp_path / "plugins")
    pin = trust_plugin(directory.parent.parent, directory, mode="bypass-signature")
    record = PluginRecord(read_manifest(directory, mode="bypass-signature"), {}, pin)
    entry = directory / "entry.source"
    entry.write_bytes(entry.read_bytes() + b"\nEntry.unsigned = True\n")
    loader = PluginClassLoader()
    try:
        with pytest.raises(PluginError, match="catalog pin"):
            loader.load_class(record)
    finally:
        loader.close()


def test_strict_install_class_check_rejects_source_changed_after_stage_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from image_downloader.plugins import management

    directory = _unit(tmp_path / "source")
    verify = management.verify_signed_plugin_source
    calls = []

    def verify_then_change(manifest):
        verify(manifest)
        calls.append(manifest.directory)
        if len(calls) == 2:
            entry = manifest.directory / "entry.source"
            entry.write_bytes(entry.read_bytes() + b"\nraise AssertionError('changed stage executed')\n")

    monkeypatch.setattr(management, "verify_signed_plugin_source", verify_then_change)
    root = tmp_path / "installed" / "plugins"
    with pytest.raises(PluginError, match="hash"):
        install_plugin(root, directory)
    assert not (root / "catalog.json").exists()
    assert not (root / "site_plugins" / directory.name).exists()


def test_source_hard_links_are_rejected(tmp_path: Path) -> None:
    directory = _unit(tmp_path / "plugins")
    runtime = _runtime(directory)
    try:
        try:
            os.link(directory / "entry.source", tmp_path / "entry-link")
        except OSError:
            pytest.skip("hard links are unavailable")
        with pytest.raises(PluginError, match="source changed before read"):
            runtime.loader.load_class(runtime.records["com.example.gallery"])
    finally:
        runtime.close()


def test_source_symlinks_are_rejected_before_execution(tmp_path: Path) -> None:
    directory = _unit(tmp_path / "plugins")
    runtime = _runtime(directory)
    try:
        outside = tmp_path / "outside.source"
        outside.write_text("raise AssertionError('outside code executed')\n", encoding="utf-8")
        try:
            (tmp_path / "probe-link").symlink_to(outside)
        except OSError:
            pytest.skip("symlinks are unavailable")
        entry = directory / "entry.source"
        entry.unlink()
        entry.symlink_to(outside)
        with pytest.raises(PluginError, match="regular non-link"):
            runtime.loader.load_class(runtime.records["com.example.gallery"])
    finally:
        runtime.close()


def test_verifier_constraints_do_not_change_record_equality(tmp_path: Path) -> None:
    directory = _unit(tmp_path / "plugins")
    trust_plugin(directory.parent.parent, directory)
    discovery = PluginDiscovery(directory.parent.parent).discover()
    verified = PluginManifestVerifier(directory.parent.parent, mode="strict").verify(discovery)
    record = verified.records[0]
    assert record == PluginRecord(record.manifest, record.author_defaults, record.catalog)
