"""Turning a store page into something installable.

Theme authors do declare their dependencies. They write them in the description
as prose, with pling/opendesktop links, because the packaging format gave them
nowhere else to put them:

    For better experience you need this kvantum theme: Layan
    https://www.pling.com/p/1325246/
    gtk theme: Layan https://www.pling.com/p/1309214/
    Icon theme: Tela icon theme https://www.pling.com/p/1279924/

That is machine-readable enough. This module extracts those links, walks the
graph they form (which is cyclic -- dependencies link back to their parent),
and works out where each item belongs on disk.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import knsrc, paths, store

# Any store page URL, from any of the federated front-ends.
PAGE_URL = re.compile(
    r"https?://(?:www\.)?(?:pling\.com|opendesktop\.org|store\.kde\.org|"
    r"kde-look\.org|gnome-look\.org|xfce-look\.org)/p/(\d+)", re.I)

# OCS hosts to try, in order. They federate, but not every id resolves on each.
HOSTS = ("api.kde-look.org", "api.opendesktop.org", "api.pling.com")

# xdg_type -> knsrc category. The most reliable signal the store gives us.
BY_XDG_TYPE = {
    "icons": "icons",
    "cursors": "xcursor",
    "wallpapers": "wallpaper",
    "gtk3_themes": "gtk_themes",
    "gtk2_themes": "gtk_themes",
    "plasma5_desktopthemes": "plasma-themes",
    "plasma5_look_and_feel": "lookandfeel",
    "plasma5_window_decorations": "aurorae",
    "plasma_color_schemes": "colorschemes",
    "emoticons": None,
    "kvantum_themes": "KVANTUM",     # sentinel: not a KNewStuff category
}

# Numeric type ids, for entries whose xdg_type is empty (Plasma 6 categories
# were added without one).
BY_TYPE_ID = {
    "722": "lookandfeel",       # Global Themes (Plasma 6)
    "121": "lookandfeel",       # Global Themes (Plasma 5)
    "123": "KVANTUM",           # Kvantum
    "132": "icons",             # Full Icon Themes
    "135": "gtk_themes",        # GTK3/4 Themes
    "299": "wallpaper",         # Wallpapers KDE Plasma
    "104": "plasma-themes",     # Plasma Themes
    "108": "aurorae",           # Plasma Window Decorations
    "109": "colorschemes",      # Plasma Color Schemes
    "113": "ksplash",           # Plasma Splashscreens
    "107": "xcursor",           # Cursors
}

# Last-resort keyword match on the human-readable category name.
BY_KEYWORD = (
    ("kvantum", "KVANTUM"),
    ("global theme", "lookandfeel"),
    ("look and feel", "lookandfeel"),
    ("window decoration", "aurorae"),
    ("colour scheme", "colorschemes"),
    ("color scheme", "colorschemes"),
    ("splashscreen", "ksplash"),
    ("splash screen", "ksplash"),
    ("icon", "icons"),
    ("cursor", "xcursor"),
    ("wallpaper", "wallpaper"),
    ("gtk", "gtk_themes"),
    ("plasma theme", "plasma-themes"),
)


@dataclass
class Route:
    """Where an item goes, and how it gets there."""
    category: str                    # knsrc name, or "KVANTUM", or "" if unknown
    target: Path | None = None
    uncompress: str = "archive"
    kpackage_type: str = ""
    needs_root: bool = False
    known: bool = True

    @property
    def uses_kpackage(self) -> bool:
        return self.uncompress == "kpackage"


@dataclass
class Node:
    """One item in the dependency graph."""
    content_id: str
    host: str
    item: store.StoreItem | None = None
    route: Route | None = None
    depth: int = 0
    required_by: list[str] = field(default_factory=list)

    @property
    def page_url(self) -> str:
        return f"https://www.pling.com/p/{self.content_id}"


def parse_url(text: str) -> str | None:
    """Extract a content id from a store URL, or accept a bare id."""
    text = text.strip()
    if text.isdigit():
        return text
    match = PAGE_URL.search(text)
    return match.group(1) if match else None


def dependency_ids(description: str) -> list[str]:
    """Store ids linked from a description, in order of first appearance."""
    text = html.unescape(description or "")
    seen: list[str] = []
    for match in PAGE_URL.finditer(text):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


def route_for(item: store.StoreItem) -> Route:
    """Decide where a store item belongs."""
    category = (BY_XDG_TYPE.get((item.xdg_type or "").lower())
                or BY_TYPE_ID.get(item.type_id or ""))
    if category is None and (item.xdg_type or "").lower() in BY_XDG_TYPE:
        return Route(category="", known=False)
    if not category:
        name = (item.typename or "").lower()
        category = next((c for keyword, c in BY_KEYWORD if keyword in name), "")

    if category == "KVANTUM":
        # Kvantum has no KNewStuff category; it reads ~/.config/Kvantum.
        return Route(category="kvantum", target=paths.config_home() / "Kvantum",
                     uncompress="archive")
    if not category:
        return Route(category="", known=False)

    spec = knsrc.load(category)
    return Route(category=category, target=spec.target, uncompress=spec.uncompress,
                 kpackage_type=spec.kpackage_type, needs_root=spec.needs_root,
                 known=spec.found or spec.uses_kpackage)


def fetch(content_id: str, host_hint: str = "") -> tuple[store.StoreItem, str]:
    """Look an id up, trying each federated OCS host in turn."""
    hosts = ([host_hint] if host_hint else []) + [h for h in HOSTS if h != host_hint]
    last: Exception | None = None
    for host in hosts:
        try:
            return store.fetch_metadata(host, content_id), host
        except store.StoreError as exc:
            last = exc
    raise store.StoreError(f"content {content_id} not found on any known host: {last}")


def walk(root_id: str, *, max_depth: int = 1, limit: int = 24) -> list[Node]:
    """Breadth-first over the dependency graph a description implies.

    The graph is cyclic -- a theme's kvantum dependency links back to the theme
    -- so ids are recorded on enqueue rather than on visit.
    """
    root = Node(content_id=root_id, host="", depth=0)
    queue: list[Node] = [root]
    seen: set[str] = {root_id}
    out: list[Node] = []

    while queue and len(out) < limit:
        node = queue.pop(0)
        try:
            node.item, node.host = fetch(node.content_id, node.host)
        except store.StoreError:
            node.item = None
            out.append(node)
            continue
        node.route = route_for(node.item)
        out.append(node)

        if node.depth >= max_depth:
            continue
        for dep_id in dependency_ids(node.item.description):
            if dep_id in seen or len(seen) >= limit:
                continue
            seen.add(dep_id)
            queue.append(Node(content_id=dep_id, host=node.host,
                              depth=node.depth + 1,
                              required_by=[node.item.name]))
    return out
