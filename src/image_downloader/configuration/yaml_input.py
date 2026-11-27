"""Safe YAML loading that rejects explicit duplicate keys before merge expansion."""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode


class DuplicateYamlKeyError(yaml.YAMLError):
    def __init__(self, node: Any) -> None:
        self.problem_mark = node.start_mark
        super().__init__("YAML mapping contains a duplicate explicit key")


class UniqueKeySafeLoader(yaml.SafeLoader):
    def __init__(self, stream: str | bytes) -> None:
        self._checked_mappings: set[int] = set()
        super().__init__(stream)

    def flatten_mapping(self, node: MappingNode) -> None:
        if id(node) not in self._checked_mappings:
            self._checked_mappings.add(id(node))
            seen: set[Hashable] = set()
            for key_node, _value in node.value:
                key = (
                    ("yaml-merge-key",)
                    if key_node.tag == "tag:yaml.org,2002:merge"
                    else self.construct_object(key_node)
                )
                if not isinstance(key, Hashable):
                    raise ConstructorError(
                        problem="YAML mapping key must be hashable", problem_mark=key_node.start_mark
                    )
                if key in seen:
                    raise DuplicateYamlKeyError(key_node)
                seen.add(key)
        super().flatten_mapping(node)


def safe_load_unique(source: str | bytes) -> Any:
    return yaml.load(source, Loader=UniqueKeySafeLoader)
