"""Resolve plugin minimum requirements into one final encoder operation."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import cast

from ..models import ImageSaveOptions
from .image_processor import ImageDescription, ImageEncodingOptions

FORMAT_MIMES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
}
_LOSSLESS_FORMATS = frozenset({"PNG", "GIF", "BMP", "TIFF"})
_AUX_FORMATS = {
    "optimize": {"JPEG", "PNG"},
    "progressive": {"JPEG"},
    "compress_level": {"PNG"},
    "exif": {"JPEG", "PNG", "WEBP", "TIFF"},
}


@dataclass(frozen=True, slots=True)
class SaveWarning:
    field: str
    requested: int | bool
    actual: int | bool | None
    reason: str


@dataclass(frozen=True, slots=True)
class ResolvedSavePlan:
    encoding: ImageEncodingOptions | None
    warnings: tuple[SaveWarning, ...]


def aggregate_save_options(values: list[ImageSaveOptions]) -> ImageSaveOptions:
    combined: dict[str, object] = {}
    for value in values:
        for item in fields(value):
            current = getattr(value, item.name)
            if current is None:
                continue
            if item.name in {"min_width", "min_height", "quality"}:
                current = max(current, combined.get(item.name, current))
            elif item.name == "lossless":
                current = bool(current or combined.get(item.name))
            combined[item.name] = current
    return ImageSaveOptions(**combined)  # type: ignore[arg-type]


def _dimension_warnings(options: ImageSaveOptions, info: ImageDescription | None) -> list[SaveWarning]:
    warnings = []
    for name in ("width", "height"):
        requested = getattr(options, "min_" + name)
        actual = getattr(info, name) if info is not None else None
        if requested is not None and (actual is None or actual < requested):
            warnings.append(
                SaveWarning(
                    "min_" + name,
                    requested,
                    actual,
                    "dimensions_unknown" if actual is None else "resolution_below_minimum",
                )
            )
    return warnings


def resolve_save_plan(
    options: ImageSaveOptions,
    info: ImageDescription | None,
    content_type: str,
    image_format: str | None,
    image_quality: int | None,
) -> ResolvedSavePlan:
    warnings = _dimension_warnings(options, info)
    if image_format == "ORIGINAL":
        for name in _AUX_FORMATS:
            requested = getattr(options, name)
            if requested is not None and not (name == "exif" and requested):
                warnings.append(SaveWarning(name, requested, None, "original_preserves_bytes"))
        return ResolvedSavePlan(None, tuple(warnings))
    declared = content_type.split(";", 1)[0].strip().lower()
    source = info.format if info is not None else next((k for k, v in FORMAT_MIMES.items() if v == declared), None)
    target = image_format or source
    auxiliary: dict[str, int | bool] = {}
    for name, formats in _AUX_FORMATS.items():
        requested = getattr(options, name)
        if requested is None:
            continue
        if target not in formats:
            warnings.append(SaveWarning(name, requested, None, "unsupported_option"))
        else:
            auxiliary[name] = requested
    quality_override = image_quality is not None and target in {"JPEG", "WEBP"}
    if image_quality is not None and not quality_override:
        warnings.append(SaveWarning("quality", image_quality, None, "unsupported_option"))
    conversion = image_format is not None and (info is None or target != source)
    aux_encode = any(name != "exif" or value is False for name, value in auxiliary.items())
    if not conversion and not quality_override and not aux_encode:
        return ResolvedSavePlan(None, tuple(warnings))
    lossless = target in _LOSSLESS_FORMATS or (target == "WEBP" and options.lossless is True and not quality_override)
    if options.lossless and not lossless and not conversion and not quality_override:
        for name, requested in auxiliary.items():
            if not (name == "exif" and requested is True):
                warnings.append(SaveWarning(name, requested, None, "preserve_lossless_requirement"))
        return ResolvedSavePlan(None, tuple(warnings))
    quality = image_quality if quality_override else None if lossless else 100 if options.lossless else options.quality
    if options.lossless and not lossless:
        warnings.append(SaveWarning("lossless", True, False, "cli_overrides_lossless"))
    if options.quality is not None and not lossless and quality is not None and quality < options.quality:
        warnings.append(SaveWarning("quality", options.quality, quality, "cli_quality_below_minimum"))
    encoding = ImageEncodingOptions(
        format=target,
        quality=quality,
        lossless=lossless if target == "WEBP" else None,
        optimize=cast(bool | None, auxiliary.get("optimize")),
        progressive=cast(bool | None, auxiliary.get("progressive")),
        compress_level=auxiliary.get("compress_level"),
        exif=bool(auxiliary.get("exif", False)),
    )
    return ResolvedSavePlan(encoding, tuple(warnings))
