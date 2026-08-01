"""Minimal OCS client for the KDE Store (api.kde-look.org / pling)."""

from __future__ import annotations

import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "lol-kde (+https://github.com/wolframs/lol-kde)"
TIMEOUT = 30


class StoreError(RuntimeError):
    pass


@dataclass
class StoreItem:
    content_id: str
    name: str
    typename: str
    xdg_type: str
    author: str
    downloads: str
    type_id: str = ""
    description: str = ""     # authors hide their dependency list in here


@dataclass
class DownloadTarget:
    url: str
    filename: str
    mimetype: str


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise StoreError(f"{url}: {exc}") from exc


def _ocs(root: ET.Element) -> ET.Element:
    status = root.findtext("meta/statuscode", default="")
    if status != "100":
        message = root.findtext("meta/message", default="unknown error")
        raise StoreError(f"store returned status {status}: {message}")
    data = root.find("data")
    if data is None:
        raise StoreError("store response contained no data")
    return data


def _text(node: ET.Element, tag: str) -> str:
    return (node.findtext(tag) or "").strip()


def fetch_metadata(host: str, content_id: str) -> StoreItem:
    raw = _get(f"https://{host}/ocs/v1/content/data/{content_id}")
    data = _ocs(ET.fromstring(raw))
    content = data.find("content")
    if content is None:
        raise StoreError(f"content {content_id} not found on {host}")
    return StoreItem(
        content_id=content_id,
        name=_text(content, "name") or f"item {content_id}",
        typename=_text(content, "typename"),
        xdg_type=_text(content, "xdg_type"),
        author=_text(content, "personid"),
        downloads=_text(content, "downloads"),
        type_id=_text(content, "typeid"),
        description=_text(content, "description"),
    )


def list_downloads(host: str, content_id: str) -> list[tuple[int, str]]:
    """Every file attached to an entry, as (index, filename).

    One store entry is often several files: Layan cursors ships
    01-Layan-border-cursors, 02-Layan-cursors and 03-Layan-white-cursors.
    Fetching only the first silently installs the wrong variant.
    """
    raw = _get(f"https://{host}/ocs/v1/content/data/{content_id}")
    data = _ocs(ET.fromstring(raw))
    content = data.find("content")
    if content is None:
        return []
    files: list[tuple[int, str]] = []
    for element in content:
        if element.tag.startswith("downloadname") and (element.text or "").strip():
            suffix = element.tag[len("downloadname"):]
            if suffix.isdigit():
                files.append((int(suffix), element.text.strip()))
    return sorted(files)


def best_match(files: list[tuple[int, str]], prefer: str = "") -> int:
    """Which attached file best matches a wanted component name."""
    if not files:
        return 1
    if not prefer:
        return files[0][0]
    wanted = prefer.lower().replace("-", "").replace("_", "")
    for index, name in files:
        stem = Path(name).name.lower().replace("-", "").replace("_", "")
        if wanted in stem:
            return index
    return files[0][0]


def choose_download(host: str, content_id: str, prefer: str = "") -> int:
    """Pick the attached file that best matches a wanted component name."""
    return best_match(list_downloads(host, content_id), prefer)


def fetch_download(host: str, content_id: str, index: int = 1) -> DownloadTarget:
    """Resolve a signed download URL. Store links are time-limited."""
    raw = _get(f"https://{host}/ocs/v1/content/download/{content_id}/{index}")
    data = _ocs(ET.fromstring(raw))
    content = data.find("content")
    if content is None:
        raise StoreError(f"no download {index} for content {content_id}")
    url = _text(content, "downloadlink")
    if not url:
        raise StoreError(f"content {content_id} exposes no direct download link")
    name = _text(content, "downloadname") or Path(url.split("?")[0]).name
    return DownloadTarget(url=url, filename=name, mimetype=_text(content, "mimetype"))


def download(target: DownloadTarget, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(target.url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            destination.write_bytes(response.read())
    except urllib.error.URLError as exc:
        raise StoreError(f"download failed: {exc}") from exc
    return destination
