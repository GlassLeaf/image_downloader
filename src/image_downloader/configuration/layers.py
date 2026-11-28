"""Stable public entry points for format-v2 configuration resolution."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from .resolution import (
    DEFAULT_CONFIG as DEFAULT_CONFIG,
)
from .resolution import (
    AppConfig as AppConfig,
)
from .resolution import (
    ConfigurationLayer as ConfigurationLayer,
)
from .resolution import (
    LayerStatus as LayerStatus,
)
from .resolution import (
    ResolvedApplicationConfig as ResolvedApplicationConfig,
)
from .resolution import (
    _field_mark as _field_mark,
)
from .resolution import (
    _read_layer as _read_layer,
)
from .resolution import (
    _site_layers as _site_layers,
)
from .resolution import (
    _validate_layer_values as _validate_layer_values,
)
from .resolution import (
    apply_overrides as apply_overrides,
)
from .resolution import (
    load_yaml as load_yaml,
)
from .resolution import (
    validate_config as validate_config,
)


def resolve_application_config(
    path: Path | None,
    profile: str | None = None,
    site: str | None = None,
    *,
    require_config: bool = False,
    runtime_override: Mapping[str, Any] | None = None,
    source: Literal["defaults", "user", "explicit"] | None = None,
    rewrite_user_layers: bool = False,
) -> ResolvedApplicationConfig:
    """Resolve file-format v2. rewrite_user_layers is a compatibility no-op."""
    from .document_layers import resolve_documents

    return resolve_documents(
        path,
        profile,
        site,
        require_config=require_config,
        runtime_override=runtime_override,
        source=source,
    )


def load_application_config(
    path: Path,
    profile: str | None = None,
    site: str | None = None,
    *,
    require_config: bool = False,
    runtime_override: Mapping[str, Any] | None = None,
    rewrite_user_layers: bool = False,
) -> AppConfig:
    """Load layered configuration while preserving the established AppConfig return value."""
    return resolve_application_config(
        path,
        profile,
        site,
        require_config=require_config,
        runtime_override=runtime_override,
        rewrite_user_layers=rewrite_user_layers,
    ).config
