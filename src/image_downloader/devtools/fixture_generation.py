"""Representative offline responses accompany each selected template feature."""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

import httpx


def put(document: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        document = document.setdefault(part, {})
    document[parts[-1]] = value


def html_node(selector: str, *, tag: str, text: str = "", attributes: dict[str, str] | None = None) -> str:
    """Build a sample for common tag/class/id/attribute/descendant selectors."""
    parts = selector.split(",", 1)[0].replace(">", " ").split()
    rendered = text
    for index, part in enumerate(reversed(parts or [tag])):
        found = re.match(r"[a-zA-Z][\w-]*", part)
        node_tag = found.group() if found else (tag if index == 0 else "div")
        attrs = dict(attributes or {}) if index == 0 else {}
        names = re.findall(r"\.([\w-]+)", part)
        if names:
            attrs["class"] = " ".join(names)
        name = re.search(r"#([\w-]+)", part)
        if name:
            attrs["id"] = name.group(1)
        for name, value in re.findall(r"\[([\w-]+)(?:=[\"']?([^\]\"']*)[\"']?)?\]", part):
            attrs[name] = value or attrs.get(name, "fixture")
        attributes_text = "".join(f' {name}="{html.escape(value, quote=True)}"' for name, value in attrs.items())
        rendered = f"<{node_tag}{attributes_text}>{rendered}</{node_tag}>"
    return rendered


def build_fixture(spec: dict[str, Any]) -> dict[str, Any]:
    selected, config = set(spec["features"]), spec["config"]
    base = config.get("endpoint") or spec["sample_url"]
    image_url = urljoin(base, "/image.png")
    response = gallery_response(selected, config)
    main: dict[str, Any] = {
        "match": {
            "url": str(httpx.Request("GET", base, params=config.get("query") or None).url),
            "method": config.get("request_method", "GET"),
        },
        "response": response,
    }
    requests: list[dict[str, Any]] = [
        main,
        {"match": {"url": image_url}, "response": {"headers": {"content-type": "image/png"}, "body_file": "image.png"}},
    ]
    if "pagination" in selected:
        add_pagination(requests, main, base, selected, config)
    if "chapters" in selected and "html" in selected and config.get("chapter_selector"):
        main["response"]["body"] = html_node(
            config["chapter_selector"], tag="a", text="Chapter", attributes={"href": "/chapter1"}
        )
        requests.append({"match": {"url": urljoin(base, "/chapter1")}, "response": gallery_response(selected, config)})
    secrets: dict[str, str] = {}
    if "api-key" in selected:
        secrets["api_token"] = "DEVELOPMENT-API-TOKEN"
    if {"bearer", "oauth"} & selected:
        secrets["access_token"] = "DEVELOPMENT-ACCESS-TOKEN"
    if "oauth" in selected:
        secrets["refresh_token"] = "DEVELOPMENT-REFRESH-TOKEN"
        if config.get("token_url"):
            successful = main.pop("response")
            main["responses"] = [{"status": 401}, successful]
            requests.append(
                {
                    "match": {"url": config["token_url"], "method": "POST"},
                    "response": {"json": {"access_token": "DEVELOPMENT-UPDATED-TOKEN"}},
                }
            )
    if "csrf" in selected:
        secrets.update(username="development-user", password="development-password")
        if config.get("login_url"):
            requests.extend(
                [
                    {
                        "match": {"url": config["login_url"]},
                        "repeat_last": True,
                        "response": {
                            "body": html_node(
                                config["csrf_selector"], tag="input", attributes={"value": "development-csrf"}
                            )
                        },
                    },
                    {
                        "match": {"url": config["login_url"], "method": "POST"},
                        "repeat_last": True,
                        "response": {"headers": {"set-cookie": "session=development; Path=/; HttpOnly"}},
                    },
                ]
            )
    if "short-lived" in selected and config.get("resolve_url"):
        image_id = "image1" if "json" in selected else image_url
        endpoint = config["resolve_url"].replace("{id}", quote(image_id, safe=""))
        requests.extend(
            [
                {
                    "match": {"url": endpoint},
                    "responses": [
                        {"json": {config.get("resolved_url_field", "url"): urljoin(base, "/expired.png")}},
                        {"json": {config.get("resolved_url_field", "url"): image_url}},
                    ],
                },
                {"match": {"url": urljoin(base, "/expired.png")}, "response": {"status": 410}},
            ]
        )
    if "additional-files" in selected and config.get("additional_url"):
        requests.append(
            {"match": {"url": config["additional_url"]}, "repeat_last": True, "response": {"json": {"fixture": True}}}
        )
    return {"secrets": secrets, "requests": requests}


def gallery_response(selected: set[str], config: dict[str, Any]) -> dict[str, Any]:
    if "json" in selected:
        image = {"id": "image1"}
        put(image, config.get("url_field", "url"), "/image.png")
        group = {"id": "chapter1", "title": "Chapter"}
        put(group, config.get("images_field", "images"), [image])
        document = dict(group)
        put(document, config.get("title_field", "title"), "Fixture gallery")
        put(document, config.get("chapters_field", "chapters"), [group])
        put(document, config.get("updates_field", "updates"), [])
        return {"json": document}
    body = html_node(config.get("title_selector", "title"), tag="title", text="Fixture gallery")
    body += html_node(
        config.get("image_selector", "img"), tag="img", attributes={config.get("image_attribute", "src"): "/image.png"}
    )
    return {"headers": {"content-type": "text/html; charset=utf-8"}, "body": body}


def add_pagination(
    requests: list[dict[str, Any]], main: dict[str, Any], base: str, selected: set[str], config: dict[str, Any]
) -> None:
    if "json" in selected:
        parsed = urlparse(base)
        query = dict(parse_qsl(parsed.query))
        parameter = (
            config.get("page_parameter", "page")
            if config.get("pagination_style") == "page"
            else (config.get("cursor_parameter", "cursor"))
        )
        query[parameter] = "2"
        next_url = urlunparse(parsed._replace(query=urlencode(query)))
        put(main["response"]["json"], config.get("next_field", "next_cursor"), "2")
        second = gallery_response(selected, config)
        put(second["json"], config.get("images_field", "images"), [])
        if "chapters" in selected:
            put(second["json"], config.get("chapters_field", "chapters"), [])
    else:
        next_url = urljoin(base, "/page2")
        main["response"]["body"] += html_node(
            config.get("next_selector", "a[rel=next]"), tag="a", attributes={"href": next_url}, text="Next"
        )
        second = gallery_response(selected, config)
    requests.append(
        {
            "match": {
                "url": str(httpx.Request("GET", next_url, params=config.get("query") or None).url),
                "method": config.get("request_method", "GET"),
            },
            "response": second,
        }
    )
