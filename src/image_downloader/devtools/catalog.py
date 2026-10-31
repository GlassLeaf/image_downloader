"""One feature catalogue drives both the wizard and deterministic generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Feature:
    id: str
    label: str
    hooks: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ("site_plugin",)
    defaults: dict[str, Any] = field(default_factory=dict)
    questions: tuple[tuple[str, str], ...] = ()


BOTH = ("site_plugin", "image_processor_plugin")
FEATURES = (
    Feature(
        "host",
        "URL・ホスト判定",
        defaults={"hosts": ["example.test"], "path_prefix": "/"},
        questions=(("hosts", "対応ホスト（カンマ区切り）"), ("path_prefix", "URLパスの接頭辞")),
    ),
    Feature("configured-host", "設定によるホスト判定", ("matches_with_config",), ("host",)),
    Feature(
        "html",
        "HTML・CSSセレクターによる抽出",
        defaults={"title_selector": "title", "image_selector": "img", "image_attribute": "src"},
        questions=(("title_selector", "タイトルのCSSセレクター"), ("image_selector", "画像のCSSセレクター")),
    ),
    Feature(
        "json",
        "JSON APIからの抽出",
        defaults={
            "endpoint": "",
            "images_field": "images",
            "url_field": "url",
            "title_field": "title",
            "id_field": "id",
            "request_method": "GET",
        },
        questions=(
            ("endpoint", "API URL（空なら入力URL）"),
            ("images_field", "画像配列の項目パス"),
            ("url_field", "画像URLの項目パス"),
        ),
    ),
    Feature(
        "pagination",
        "ページ番号・カーソル・次ページ",
        defaults={
            "max_pages": 50,
            "next_selector": "a[rel=next]",
            "next_field": "next_cursor",
            "page_parameter": "page",
            "cursor_parameter": "cursor",
        },
    ),
    Feature(
        "chapters",
        "複数章と安定ID",
        defaults={"chapters_field": "chapters", "chapter_selector": "", "chapter_id_field": "id"},
        questions=(("chapter_selector", "章へのリンクのCSSセレクター（HTML用）"),),
    ),
    Feature("api-key", "APIキー認証", ("auth_flow",), defaults={"api_key_header": "X-Api-Key"}),
    Feature("bearer", "Bearer認証", ("auth_flow",)),
    Feature(
        "oauth",
        "OAuthトークン更新",
        ("auth_flow",),
        defaults={"token_url": ""},
        questions=(("token_url", "トークン更新URL"),),
    ),
    Feature(
        "csrf",
        "CSRFフォームログイン",
        ("auth_flow",),
        defaults={
            "login_url": "",
            "csrf_selector": "input[name=csrf]",
            "csrf_field": "csrf",
            "username_field": "username",
            "password_field": "password",
        },
        questions=(("login_url", "ログインURL"),),
    ),
    Feature("cookies", "Cookie利用"),
    Feature(
        "origins",
        "追加originへの認証許可",
        defaults={"allowed_origins": []},
        questions=(("allowed_origins", "認証を許可する追加origin（カンマ区切り）"),),
    ),
    Feature(
        "request",
        "ヘッダー・Referer・query・form・JSON",
        defaults={"headers": {}, "query": {}, "form": {}, "request_json": None, "referer": ""},
    ),
    Feature(
        "short-lived",
        "取得直前のURL発行と再発行",
        ("recover_image_request",),
        defaults={"resolve_url": "", "resolved_url_field": "url"},
        questions=(("resolve_url", "URL発行API（{id}を置換）"),),
    ),
    Feature(
        "transform",
        "画像の縮小・形式変換",
        kinds=BOTH,
        defaults={"max_width": 1600, "max_height": 1600, "transform_format": "PNG"},
    ),
    Feature("save-options", "画像保存形式・品質・元ファイル名", defaults={"save_options": {"format": "ORIGINAL"}}),
    Feature("metadata", "画像metadata・request-time data", kinds=BOTH),
    Feature(
        "updates",
        "更新スナップショット",
        ("check_updates",),
        defaults={"update_url": "", "updates_field": "updates", "update_selector": "a[href]"},
    ),
    Feature(
        "additional-files",
        "追加ファイル・7段階・コールバック",
        ("additional_file_hook_points", "additional_files", "additional_file_received", "additional_file_saved"),
        defaults={"additional_points": ["after_download"], "additional_url": ""},
        questions=(
            ("additional_points", "実行段階（カンマ区切り、全段階はall）"),
            ("additional_url", "追加ファイルURL（空ならメタデータJSON）"),
        ),
    ),
    Feature(
        "output-values",
        "独自出力トークン",
        ("output_format_values",),
        kinds=BOTH,
        defaults={"output_values": {"AUTHOR": "example"}},
    ),
    Feature("history", "加工履歴", kinds=BOTH),
    Feature("cleanup", "後始末", ("cleanup_after_use",), kinds=BOTH),
    Feature(
        "empty-policy",
        "空のmanifest・空章の許可",
        defaults={"allow_empty_chapters": False, "allow_empty_images": False},
    ),
    Feature("network", "本体の速度制限・再試行設定", kinds=BOTH),
)
CATALOG = {feature.id: feature for feature in FEATURES}
AUTH = {"api-key", "bearer", "oauth", "csrf"}


def select_features(names: list[str], kind: str) -> list[str]:
    selected = set(names)
    unknown = selected - CATALOG.keys()
    if unknown:
        raise ValueError(f"unknown features: {sorted(unknown)}")
    if len(AUTH & selected) > 1:
        raise ValueError("choose one authentication strategy: api-key, bearer, oauth, csrf")
    if {"html", "json"} <= selected:
        raise ValueError("choose either html or json extraction")
    if kind == "site_plugin":
        selected.add("host")
        if not {"html", "json"} & selected:
            selected.add("html")
    for name in tuple(selected):
        selected.update(CATALOG[name].requires)
    invalid = [name for name in selected if kind not in CATALOG[name].kinds]
    if invalid:
        raise ValueError(f"features unavailable for {kind}: {sorted(invalid)}")
    return [feature.id for feature in FEATURES if feature.id in selected]
