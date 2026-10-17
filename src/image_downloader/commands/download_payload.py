"""Pure projections of application download results for CLI output."""

from __future__ import annotations

from pathlib import Path

from ..application.additional_files import additional_file_payload
from ..exceptions import error_reason_for_code
from ..models import DownloadResult
from ..privacy.log_safety import safe_exception_name, safe_relative_path, safe_url


def download_payload(result: DownloadResult, output_root: Path) -> dict[str, object]:
    return {
        "saved": result.saved_files,
        "additional_files": additional_file_payload(result, output_root),
        "skipped": result.skipped_files,
        "failures": [
            {
                "kind": item.kind,
                "exception": safe_exception_name(item.exception_type),
                "message": error_reason_for_code(item.code) or "image failure",
                "code": item.code,
                "reason": error_reason_for_code(item.code) or "image failure",
                "output_path": safe_relative_path(item.output_path, output_root)
                if item.output_path is not None and Path(item.output_path).is_absolute()
                else item.output_path,
                "response_url": safe_url(item.response_url) if item.response_url is not None else None,
                "http_status": item.http_status,
                "transport": item.transport,
            }
            for item in result.failures
        ],
    }
