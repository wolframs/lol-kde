"""Minimal OCS client for the KDE Store (api.kde-look.org / pling)."""

from __future__ import annotations

import http.client
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "lol-kde (+https://github.com/wolframs/lol-kde)"
TIMEOUT = 30

# `read()` buffers the whole body in RAM, and on many systems the temporary
# directory it lands in is tmpfs -- so an oversized upload costs twice its size
# in memory before anything notices. The largest legitimate theme seen here is
# a 60 MB icon set.
MAX_DOWNLOAD = 512 * (1 << 20)


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


def encode_url(url: str) -> str:
    """Percent-encode the parts of a store URL that the store did not.

    Store download links end in the uploader's original filename, spaces and
    all: `.../Gently-Nebula-Noir No Logo.jpg`. `http.client` refuses to put a
    raw space in a request line -- correctly, it would break the protocol --
    and raises `InvalidURL`, which is an HTTPException and so sails past every
    handler that expects URLError. One uploader's filename killed a nineteen
    component install with a traceback.

    Only the path and query are touched, and characters that are already
    escaped are left alone, so encoding twice is harmless.
    """
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((
        parts.scheme,
        parts.netloc,
        urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;="),
        urllib.parse.quote(parts.query, safe="/%:@!$&'()*+,;=?&"),
        parts.fragment,
    ))


def safe_filename(name: str, fallback: str) -> str:
    """One path component from a name the store chose, not us.

    `downloadname` is uploader-controlled text that gets joined onto a
    directory and written to. A value of `../../x` or an absolute path escapes
    the temporary directory the caller carefully created; `..` or `.` names the
    directory itself. Take the last component and nothing else.
    """
    cleaned = Path(name.strip()).name
    return cleaned if cleaned and cleaned not in (".", "..") else fallback


def _read(url: str) -> bytes:
    """Fetch a URL, turning every transport failure into StoreError.

    The handler list is long because this project keeps meeting exceptions that
    are not where you would expect them in the hierarchy, each one costing a
    whole multi-component install:

    - `InvalidURL` is an `HTTPException`, not a `URLError` (a filename with a
      space in it).
    - `TimeoutError` is an `OSError`, not a `URLError` -- raised by `read()`
      when a server sends headers and then stalls.
    - `Request()` itself raises `ValueError` on a malformed URL, which is why
      it is inside the `try` rather than above it.
    """
    try:
        request = urllib.request.Request(encode_url(url),
                                         headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise StoreError(f"{url}: {exc}") from exc
    except http.client.HTTPException as exc:
        raise StoreError(f"{url}: {type(exc).__name__}: {exc}") from exc
    except ValueError as exc:
        raise StoreError(f"{url}: {exc}") from exc
    except OSError as exc:
        raise StoreError(f"{url}: {exc}") from exc


def _get(url: str) -> ET.Element:
    """Fetch an OCS endpoint and parse it. Never raises anything but StoreError.

    `ET.ParseError` subclasses `SyntaxError`, so it sails past every handler in
    this project that catches `StoreError`. A store that answers with an HTML
    error page -- which happens -- would otherwise abort the run with a
    traceback reading `no element found`.
    """
    raw = _read(url)
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise StoreError(f"{url}: malformed XML from the store: {exc}") from exc


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
    data = _ocs(_get(f"https://{host}/ocs/v1/content/data/{content_id}"))
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
    data = _ocs(_get(f"https://{host}/ocs/v1/content/data/{content_id}"))
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
    data = _ocs(_get(f"https://{host}/ocs/v1/content/download/{content_id}/{index}"))
    content = data.find("content")
    if content is None:
        raise StoreError(f"no download {index} for content {content_id}")
    url = _text(content, "downloadlink")
    if not url:
        raise StoreError(f"content {content_id} exposes no direct download link")
    if urllib.parse.urlsplit(url).scheme.lower() != "https":
        # The first hop is TLS to a known API host, so a plain-http or file://
        # downloadlink means either a coerced link or a store bug. Neither is
        # worth honouring silently.
        raise StoreError(f"content {content_id} offers a non-https download "
                         f"link: {url!r}")
    name = safe_filename(_text(content, "downloadname")
                         or Path(url.split("?")[0]).name,
                         fallback=f"download-{content_id}")
    return DownloadTarget(url=url, filename=name, mimetype=_text(content, "mimetype"))


def download(target: DownloadTarget, destination: Path) -> Path:
    """Write a store download to `destination`, which the caller owns.

    Callers build `destination` as `<their directory> / target.filename`, and
    `target.filename` came off the wire. A `downloadname` of
    `../../../.config/kdeglobals` used to write straight through the caller's
    temporary directory, so it is now reduced to a single component in
    `fetch_download`, where the untrusted value enters.

    The basename is re-checked here as well, but note what this can and cannot
    promise: a traversal that has already been joined into `destination.parent`
    is indistinguishable from a directory the caller meant. **The load-bearing
    guard is `fetch_download`'s**, not this one.
    """
    destination = destination.parent / safe_filename(destination.name,
                                                     fallback="download")
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = _read(target.url)
    if len(body) > MAX_DOWNLOAD:
        raise StoreError(f"download is {len(body) // (1 << 20)} MiB, over the "
                         f"{MAX_DOWNLOAD // (1 << 20)} MiB limit")
    try:
        destination.write_bytes(body)
    except OSError as exc:
        raise StoreError(f"download failed: {exc}") from exc
    return destination
