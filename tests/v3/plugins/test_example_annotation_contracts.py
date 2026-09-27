"""Annotation contracts for current v3 plugin and embedded-library examples."""

from __future__ import annotations

import ast
import re
from pathlib import Path

PYTHON_FENCE = re.compile(r"```python\s*\n(?P<source>.*?)```", re.DOTALL)

SITE_HOOKS: dict[str, tuple[bool, tuple[tuple[str, str], ...], str]] = {
    "validate_config": (False, (("config", "Mapping[str, object]"), ("app_settings", "Mapping[str, object]")), "None"),
    "matches": (False, (("url", "str"),), "bool"),
    "inspect": (True, (("url", "str"), ("context", "PluginExecutionContext")), "DownloadManifest"),
    "create_image_request": (
        True,
        (("image", "ImageResource"), ("context", "PluginExecutionContext")),
        "RequestSpec",
    ),
    "recover_image_request": (
        True,
        (
            ("image", "ImageResource"),
            ("failed", "RequestSpec"),
            ("response", "RequestResponse"),
            ("context", "PluginExecutionContext"),
        ),
        "RequestSpec | None",
    ),
    "auth_flow": (False, (("context", "PluginExecutionContext"),), "AuthFlow | None"),
    "transform_image": (
        True,
        (("artifact", "ImageArtifact"), ("context", "TransformContext")),
        "ImageArtifact",
    ),
    "check_updates": (True, (("url", "str"), ("context", "PluginExecutionContext")), "UpdateSnapshot"),
}

PROCESSOR_HOOKS: dict[str, tuple[bool, tuple[tuple[str, str], ...], str]] = {
    "transform": (True, (("artifact", "ImageArtifact"), ("context", "TransformContext")), "ImageArtifact"),
}

AUTH_FLOW_HOOKS: dict[str, tuple[bool, tuple[tuple[str, str], ...], str]] = {
    "is_auth_failure": (False, (("request", "RequestSpec"), ("response", "RequestResponse")), "bool"),
    "apply": (True, (("request", "RequestSpec"),), "RequestSpec"),
    "refresh": (True, (("failed", "RequestSpec"), ("response", "RequestResponse")), "RequestSpec | None"),
}

TUTORIAL_SIGNATURES: dict[str, tuple[tuple[tuple[str, str], ...], str]] = {
    "download": ((), "None"),
    "check_for_updates": ((("config", "AppConfig"), ("config_root", "Path")), "list[str]"),
    "download_with_override": ((("config", "AppConfig"), ("config_root", "Path")), "None"),
    "inspect_registry": ((("config", "AppConfig"), ("config_root", "Path")), "None"),
    "download_with_handling": ((("config", "AppConfig"), ("config_root", "Path")), "None"),
    "build_service": (
        (("config", "AppConfig"), ("registry", "PluginRuntime"), ("dependencies", "_RuntimeDependencies")),
        "DownloadService",
    ),
}


def _source_paths(repository_root: Path) -> tuple[Path, ...]:
    template = repository_root / "examples" / "plugin-v3-template" / "sample_plugin.py"
    source_units = tuple(
        path
        for path in sorted((repository_root / "plugin-sources").rglob("*.py"))
        if "vendor" not in path.parts
    )
    return (template, *source_units)


def _annotation(node: ast.expr | None, location: str) -> str:
    assert node is not None, f"missing annotation: {location}"
    return ast.unparse(node)


def _parameters(node: ast.FunctionDef | ast.AsyncFunctionDef, location: str) -> tuple[tuple[str, str], ...]:
    parameters = (
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
    )
    return tuple(
        (parameter.arg, _annotation(parameter.annotation, f"{location} parameter {parameter.arg}"))
        for parameter in parameters
        if parameter.arg not in {"self", "cls"}
    )


def _assert_complete_annotations(tree: ast.AST, location: str) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        definition = f"{location}:{node.lineno} {node.name}"
        _parameters(node, definition)
        if node.args.vararg is not None:
            _annotation(node.args.vararg.annotation, f"{definition} parameter *{node.args.vararg.arg}")
        if node.args.kwarg is not None:
            _annotation(node.args.kwarg.annotation, f"{definition} parameter **{node.args.kwarg.arg}")
        return_type = _annotation(node.returns, f"{definition} return")
        if node.name == "__init__":
            assert return_type == "None", definition


def _assert_contract(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    expected: tuple[bool, tuple[tuple[str, str], ...], str],
    location: str,
) -> None:
    is_async, parameters, return_type = expected
    assert isinstance(node, ast.AsyncFunctionDef) is is_async, location
    assert _parameters(node, location) == parameters, location
    assert _annotation(node.returns, f"{location} return") == return_type, location


def test_current_v3_examples_have_complete_type_annotations(repository_root: Path) -> None:
    for path in _source_paths(repository_root):
        _assert_complete_annotations(ast.parse(path.read_text(encoding="utf-8")), str(path))


def test_current_v3_plugin_hooks_match_the_protocol_contract(repository_root: Path) -> None:
    contracts = {**SITE_HOOKS, **PROCESSOR_HOOKS, **AUTH_FLOW_HOOKS}
    for path in _source_paths(repository_root):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for class_node in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
            for node in class_node.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in contracts:
                    _assert_contract(node, contracts[node.name], f"{path}:{class_node.name}.{node.name}")


def test_embedded_tutorial_examples_have_complete_type_annotations(repository_root: Path) -> None:
    tutorial = repository_root / "docs" / "v3" / "tutorials" / "embedded-download.md"
    definitions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for number, match in enumerate(PYTHON_FENCE.finditer(tutorial.read_text(encoding="utf-8")), start=1):
        tree = ast.parse(match.group("source"), filename=f"{tutorial}:{number}")
        _assert_complete_annotations(tree, f"{tutorial}:{number}")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                definitions[node.name] = node

    assert definitions.keys() == TUTORIAL_SIGNATURES.keys()
    for name, expected in TUTORIAL_SIGNATURES.items():
        node = definitions[name]
        assert _parameters(node, f"{tutorial}:{name}") == expected[0], name
        assert _annotation(node.returns, f"{tutorial}:{name} return") == expected[1], name
