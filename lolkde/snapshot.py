"""Capturing the state of a KDE desktop, and proving the capture worked.

A checkpoint is only as good as its file list. This project learned that the
expensive way: a checkpoint taken before a display-scale change backed up
`~/.local/share/kscreen/`, which is the Plasma 5 location -- still present on
upgraded systems, still non-empty, never written to. Every signal the capture
had said success. It preserved nothing.

So the manifest here is declarative data with a confidence column, and every
snapshot ends by running COVERAGE PROBES: read a fact from a live instrument,
then read the same fact back out of the bytes just written. Disagreement is
DRIFT. Absence is GAP -- and on a GAP the snapshot says where the value
actually lives, so the manifest can be fixed rather than silently trusted.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from . import kconfig, legacy, manifest as lnf, paths, resolve

# ---------------------------------------------------------------------------
# Where snapshots live

def store() -> Path:
    return Path(os.environ.get("LOL_KDE_HOME") or paths.HOME / ".lol-kde")


def snapshots_dir() -> Path:
    return store() / "snapshots"


# ---------------------------------------------------------------------------
# The manifest

CONFIG, DATA, HOME, LAYERS = "config", "data", "home", "layers"

CORE, CONTEXT, LEGACY = "core", "context", "legacy"
VERIFIED, LIKELY, UNVERIFIED = "verified", "likely", "unverified"


@dataclass(frozen=True)
class Entry:
    """One manifest line. `pattern` is a glob relative to `root`."""
    root: str
    pattern: str
    holds: str
    tier: str = CORE
    confidence: str = VERIFIED
    superseded_by: str = ""


MANIFEST: tuple[Entry, ...] = (
    # -- the cascade. Capturing only ~/.config reproduces the exact bug that
    #    paths.config_layers() exists to fix: an applied global theme writes to
    #    kdedefaults, and several pointers live ONLY there.
    Entry(CONFIG, "kdeglobals", "user layer: look-and-feel package, fonts, accent colour, the whole palette"),
    Entry(CONFIG, "kdedefaults/*", "where an applied global theme actually writes"),
    Entry(LAYERS, "*rc", "the distro's defaults layer; a release upgrade moves values you never touched"),
    Entry(LAYERS, "kdeglobals", "distro defaults for kdeglobals, often absent from ~/.config"),

    # -- appearance and window management
    Entry(CONFIG, "plasmarc", "Plasma style"),
    Entry(CONFIG, "kwinrc", "decoration, effects, Xwayland scale, tiling, virtual desktops"),
    Entry(CONFIG, "kwinrulesrc", "per-window rules; can pin opacity and confound every theming diagnosis"),
    Entry(CONFIG, "plasma-org.kde.plasma.desktop-appletsrc",
          "panel and desktop layout, and the wallpaper. The hardest thing to rebuild by hand"),
    Entry(CONFIG, "plasma-org.kde.plasma.desktop-appletsrc.bak*",
          "Plasma's own auto-backups of the above -- free prior states"),
    Entry(CONFIG, "plasmashellrc", "shell and panel state", confidence=UNVERIFIED),
    Entry(CONFIG, "ksplashrc", "splash screen"),
    Entry(CONFIG, "kscreenlockerrc", "lock screen appearance"),
    Entry(CONFIG, "kcminputrc", "cursor size; the cursor theme itself lives in kdedefaults"),
    Entry(CONFIG, "kcmfonts", "force-font-DPI. Zero bytes here; role unconfirmed", confidence=UNVERIFIED),
    Entry(CONFIG, "fontconfig/fonts.conf", "user font rules; override KDE's hinting", confidence=LIKELY),

    # -- display. The entry whose absence produced GAP.md.
    Entry(CONFIG, "kwinoutputconfig.json",
          "Plasma 6's real output store: scale, mode, position, HDR, VRR, per-output edidHash"),
    Entry(DATA, "kscreen/**", "Plasma 5 output store. Read as a migration hint, never written",
          tier=LEGACY, superseded_by="~/.config/kwinoutputconfig.json"),

    # -- Kvantum, which sits outside every KDE mechanism
    Entry(CONFIG, "Kvantum/kvantum.kvconfig", "the selected Kvantum theme; applying a global theme does not touch it"),
    Entry(CONFIG, "Kvantum/*/*.kvconfig", "reduce_window_opacity, translucent_windows, the opaque= list"),
    Entry(CONFIG, "Kvantum/*/*.kvconfig.bak", "hand-made backups of the above"),

    # -- the GTK/Qt bridge, written within milliseconds of a global theme apply
    Entry(CONFIG, "gtk-3.0/*.ini", "GTK3 theme, icons, cursor, font"),
    Entry(CONFIG, "gtk-3.0/*.css", "generated GTK3 colour bridge"),
    Entry(CONFIG, "gtk-4.0/*.ini", "GTK4 theme, icons, cursor, font"),
    Entry(CONFIG, "gtk-4.0/*.css", "generated GTK4 colour bridge"),
    Entry(CONFIG, "gtkrc", "GTK1; referenced by live GTK_RC_FILES", tier=CONTEXT),
    Entry(CONFIG, "gtkrc-2.0", "GTK2; referenced by live GTK2_RC_FILES", tier=CONTEXT),
    Entry(CONFIG, "xsettingsd/xsettingsd.conf", "the XSettings bridge; can silently disagree with KDE"),
    Entry(CONFIG, "dconf/user", "binary GVDB; GTK4 reads org.gnome.desktop.interface from here",
          confidence=UNVERIFIED),
    Entry(CONFIG, "Trolltech.conf", "Qt-side style/palette/DPI state", confidence=LIKELY),
    Entry(CONFIG, "QtProject.conf", "Qt-side state", confidence=LIKELY),

    # -- not appearance, but cheap and load-bearing
    Entry(CONFIG, "kglobalshortcutsrc", "global shortcuts", tier=CONTEXT),
    Entry(CONFIG, "kxkbrc", "keyboard layout", tier=CONTEXT),
    Entry(CONFIG, "ksmserverrc", "session management", tier=CONTEXT),
    Entry(CONFIG, "powerdevilrc", "power management", tier=CONTEXT),
    Entry(CONFIG, "kdeglobals.lolkde.bak", "any hand-made backup we find", tier=CONTEXT),
    Entry(CONFIG, "kwinrc.lolkde.bak", "any hand-made backup we find", tier=CONTEXT),
)

# Kvantum ships 150-230 KiB SVGs per theme. That is installed artwork, not
# configuration, and it never changes. Hash it, do not copy it.
NEVER_COPY = ("*.svg", "*.png")


def roots() -> dict[str, list[Path]]:
    """Manifest root name -> the directories it expands against."""
    system = [d for d in paths.config_layers() if d != paths.config_home()]
    return {
        CONFIG: [paths.config_home()],
        DATA: [paths.data_home()],
        HOME: [paths.HOME],
        LAYERS: system,
    }


def expand(entry: Entry) -> list[tuple[Path, Path]]:
    """Every (source, root) pair an entry matches right now."""
    hits: list[tuple[Path, Path]] = []
    for base in roots().get(entry.root, []):
        if not base.is_dir():
            continue
        for match in sorted(base.glob(entry.pattern)):
            if match.is_file():
                hits.append((match, base))
    return hits


# ---------------------------------------------------------------------------
# Coverage probes: prove the capture holds what it claims

@dataclass
class ProbeResult:
    probe: str
    live: str
    status: str            # ok | DRIFT | GAP | skipped
    captured: str = ""
    found_at: list[str] = field(default_factory=list)
    detail: str = ""


def _kscreen_json() -> dict:
    tool = shutil.which("kscreen-doctor")
    if not tool:
        return {}
    try:
        done = subprocess.run([tool, "-j"], capture_output=True, text=True, timeout=15)
        return json.loads(done.stdout) if done.returncode == 0 else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def _json_at(data, path: list) -> str:
    """Follow a list/dict path, returning '' rather than raising."""
    node = data
    for step in path:
        try:
            node = node[step]
        except (KeyError, IndexError, TypeError):
            return ""
    return "" if node is None else str(node)


def _ini_value(path: Path, group: str, key: str) -> str:
    return kconfig.get(path, group, key) or ""


def locate_value(value: str, limit: int = 12) -> list[str]:
    """Find where a live value actually lives, when the manifest missed it.

    Scans parseable config under ~/.config for a key whose value equals
    `value`. This is what turns a silent GAP into an actionable one: the
    snapshot reports the path it should have been capturing all along.
    """
    if not value:
        return []
    found: list[str] = []
    base = paths.config_home()
    for path in sorted(base.rglob("*")):
        if len(found) >= limit:
            break
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        rel = path.relative_to(base)
        # Skip application profile trees; they are large and never KDE config.
        if len(rel.parts) > 3:
            continue
        try:
            if path.suffix == ".json":
                data = json.loads(path.read_text())
                for trail in _walk_json(data):
                    node, where = trail
                    if str(node) == value:
                        found.append(f"~/.config/{rel}  {where}")
                        break
            else:
                parsed = kconfig.read_ini(path)
                for group in parsed.sections():
                    for key, val in parsed.items(group):
                        if val == value:
                            found.append(f"~/.config/{rel}  [{group}] {key}")
                            break
        except (OSError, ValueError, UnicodeDecodeError):
            continue
    return found


def _walk_json(node, trail: str = ""):
    if isinstance(node, dict):
        for key, val in node.items():
            yield from _walk_json(val, f"{trail}.{key}")
    elif isinstance(node, list):
        for index, val in enumerate(node):
            yield from _walk_json(val, f"{trail}[{index}]")
    else:
        yield node, trail or "."


def run_probes(files_dir: Path) -> list[ProbeResult]:
    """Read facts live, then read them back out of what we just captured."""
    results: list[ProbeResult] = []

    def check(name: str, live: str, rel: str, reader) -> None:
        if not live:
            results.append(ProbeResult(name, live, "skipped",
                                       detail="no live value to verify"))
            return
        target = files_dir / rel
        if not target.is_file():
            results.append(ProbeResult(name, live, "GAP", found_at=locate_value(live),
                                       detail=f"{rel} was not captured"))
            return
        captured = reader(target)
        if captured == live:
            results.append(ProbeResult(name, live, "ok", captured))
        elif captured:
            results.append(ProbeResult(name, live, "DRIFT", captured,
                                       detail="captured bytes disagree with the live system"))
        else:
            results.append(ProbeResult(name, live, "GAP", found_at=locate_value(live),
                                       detail=f"{rel} was captured but holds no such value"))

    # -- the seven pointers, each verified against the layer that actually wins
    live = resolve.live_settings()
    for filename, group, key, label in (
        ("kdeglobals", "KDE", "widgetStyle", "widget-style"),
        ("kdeglobals", "General", "ColorScheme", "color-scheme"),
        ("kdeglobals", "Icons", "Theme", "icons"),
        ("kcminputrc", "Mouse", "cursorTheme", "cursors"),
        ("plasmarc", "Theme", "name", "plasma-style"),
        ("ksplashrc", "KSplash", "Theme", "splash"),
        ("kwinrc", "org.kde.kdecoration2", "theme", "decoration"),
        ("kdeglobals", "KDE", "LookAndFeelPackage", "look-and-feel"),
    ):
        value = live.get((filename, group), {}).get(key, "")
        origin = kconfig.origin(filename, group, key)
        if origin is None:
            results.append(ProbeResult(label, value, "skipped", detail="not set anywhere"))
            continue
        # The winning layer is what must be captured -- not merely "the key
        # exists somewhere". cursorTheme wins from kdedefaults on this machine.
        rel = _relative_capture_path(origin)
        check(label, value, rel, lambda p, g=group, k=key: _ini_value(p, g, k))

    # -- display: the fact class that produced GAP.md
    outputs = _kscreen_json().get("outputs", [])
    for index, output in enumerate(outputs):
        if not output.get("enabled", True):
            continue
        scale = str(output.get("scale", ""))
        check(f"output.{output.get('name', index)}.scale", scale,
              "config/kwinoutputconfig.json",
              lambda p, i=index: _json_at(json.loads(p.read_text()),
                                          [0, "data", i, "scale"]))

    check("xwayland.scale",
          kconfig.read_cascade("kwinrc").get(("kwinrc", "Xwayland"), {}).get("Scale", ""),
          "config/kwinrc", lambda p: _ini_value(p, "Xwayland", "Scale"))

    # -- Kvantum, which no KDE mechanism touches
    kv = paths.config_home() / "Kvantum/kvantum.kvconfig"
    theme = _ini_value(kv, "General", "theme") if kv.is_file() else ""
    check("kvantum.theme", theme, "config/Kvantum/kvantum.kvconfig",
          lambda p: _ini_value(p, "General", "theme"))
    if theme:
        check("kvantum.reduce_window_opacity",
              _ini_value(paths.config_home() / f"Kvantum/{theme}/{theme}.kvconfig",
                         "%General", "reduce_window_opacity"),
              f"config/Kvantum/{theme}/{theme}.kvconfig",
              lambda p: _ini_value(p, "%General", "reduce_window_opacity"))

    return results


def _relative_capture_path(origin: Path) -> str:
    """Where a live config file ends up inside a snapshot's files/ tree."""
    config = paths.config_home()
    try:
        return f"config/{origin.relative_to(config)}"
    except ValueError:
        pass
    for index, layer in enumerate(d for d in paths.config_layers() if d != config):
        try:
            return f"config-dirs/{index:02d}/{origin.relative_to(layer)}"
        except ValueError:
            continue
    return f"other/{origin.name}"


def legacy_inversion() -> list[str]:
    """A dead path newer than its replacement means the manifest is stale."""
    warnings = []
    for entry in MANIFEST:
        if entry.tier != LEGACY or not entry.superseded_by:
            continue
        successor = Path(entry.superseded_by.replace("~", str(paths.HOME)))
        if not successor.exists():
            continue
        for source, _ in expand(entry):
            if source.stat().st_mtime > successor.stat().st_mtime:
                warnings.append(
                    f"{source} is NEWER than {entry.superseded_by}, which the "
                    f"manifest calls its replacement. The manifest may have the "
                    f"polarity backwards.")
    return warnings


# ---------------------------------------------------------------------------
# The mtime sweep: unknown unknowns made visible

def sweep(base: Path, budget: int = 2000) -> dict[str, dict]:
    """Record mtimes under `base`, collapsing any subtree over `budget`.

    A blocklist of application directories would be the same maintenance
    failure as the file list itself. Instead, descend everywhere and roll up
    anything too big to be configuration -- ~/.local/share/icons is 227k files
    across 1.8 GB and collapses on its own, no name-matching required.

    A single pass, pruning as soon as a top-level subtree exceeds the budget.
    Walking a collapsed subtree to completion would cost seconds and buy
    nothing: what changed inside an icon theme is an inventory question, and
    state/inventory.json answers it with identity hashes.
    """
    rows: dict[str, dict] = {}
    if not base.is_dir():
        return rows

    counted: dict[str, int] = {}
    for root, dirs, files in os.walk(base, onerror=lambda e: None):
        here = Path(root)
        relative = here.relative_to(base)
        top = relative.parts[0] if relative.parts else ""

        if top and counted.get(top, 0) > budget:
            # Already over budget for this subtree. Stop descending; the
            # directory's own mtime still moves when entries are added or
            # removed, which is the signal we actually need.
            dirs[:] = []
            continue

        for name in files:
            path = here / name
            try:
                info = path.lstat()
            except OSError:
                continue
            rows[str(path.relative_to(base))] = {
                "size": info.st_size, "mtime": round(info.st_mtime, 3)}
        if top:
            counted[top] = counted.get(top, 0) + len(files)
            if counted[top] > budget:
                # Replace the per-file rows we collected for this subtree with
                # one summary row, so the sweep stays a fixed size.
                prefix = top + "/"
                for key in [k for k in rows if k == top or k.startswith(prefix)]:
                    del rows[key]
                try:
                    stamp = (base / top).stat().st_mtime
                except OSError:
                    stamp = 0.0
                # Deliberately NOT recording how many files we saw: where the
                # budget trips depends on walk order, so that number differs
                # between two snapshots of an unchanged tree and every
                # collapsed subtree would report as changed forever.
                rows[top] = {"collapsed": True, "mtime": round(stamp, 3)}
                dirs[:] = []
    return rows


# ---------------------------------------------------------------------------
# Capture

def new_id(label: str = "") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    suffix = uuid.uuid4().hex[:4]
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in label).strip("-")
    return f"{stamp}-{suffix}" + (f"-{slug}" if slug else "")


def _atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    """Never leave a half-file a later restore would trust."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".lolkde-")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def capture(*, message: str = "", label: str = "", reason: str = "manual",
            with_sweep: bool = True, into: Path | None = None) -> dict:
    """Take a snapshot. Returns its meta dict.

    `into` writes somewhere other than the snapshot store, which is how `diff
    --live` captures the current system: the live side goes through exactly the
    same code as the stored side, so the two can never be compared unfairly.
    """
    identifier = new_id(label)
    root = into if into is not None else snapshots_dir() / identifier
    files = root / "files"
    captured: list[dict] = []

    layer_index = {d: i for i, d in enumerate(
        d for d in paths.config_layers() if d != paths.config_home())}

    for entry in MANIFEST:
        hits = expand(entry)
        if not hits:
            captured.append({"pattern": entry.pattern, "root": entry.root,
                             "status": "absent", "tier": entry.tier,
                             "confidence": entry.confidence, "holds": entry.holds})
            continue
        for source, base in hits:
            relative = source.relative_to(base)
            if entry.root == CONFIG:
                destination = files / "config" / relative
            elif entry.root == DATA:
                destination = files / "data" / relative
            elif entry.root == LAYERS:
                destination = files / "config-dirs" / f"{layer_index.get(base, 9):02d}" / relative
            else:
                destination = files / "home" / relative

            record = {"pattern": entry.pattern, "source": str(source),
                      "path": str(destination.relative_to(files)),
                      "tier": entry.tier, "confidence": entry.confidence,
                      "holds": entry.holds}
            if entry.superseded_by:
                record["superseded_by"] = entry.superseded_by
            try:
                data = source.read_bytes()
                info = source.stat()
                record["size"] = len(data)
                record["sha256"] = _digest(data)
                record["mode"] = oct(info.st_mode & 0o777)
                record["mtime"] = round(info.st_mtime, 3)
                if any(source.match(pattern) for pattern in NEVER_COPY):
                    record["status"] = "elided"      # hashed, deliberately not copied
                else:
                    _atomic_write(destination, data, info.st_mode & 0o777)
                    record["status"] = "captured"
            except OSError as exc:
                record["status"] = "unreadable"
                record["detail"] = str(exc)
            captured.append(record)

    state = _capture_state(root)
    probes = run_probes(files)
    inversions = legacy_inversion()

    if with_sweep:
        rows = {"config": sweep(paths.config_home()),
                "data": sweep(paths.data_home())}
        _atomic_write(root / "sweep.json.gz",
                      gzip.compress(json.dumps(rows, sort_keys=True).encode()))

    meta = {
        "id": identifier,
        "created": datetime.now(timezone.utc).isoformat(),
        "message": message,
        "reason": reason,
        "schema": 1,
        "tool": _tool_version(),
        "files_captured": sum(1 for c in captured if c.get("status") == "captured"),
        "bytes": sum(c.get("size", 0) for c in captured if c.get("status") == "captured"),
        "coverage": {"ok": sum(1 for p in probes if p.status == "ok"),
                     "total": sum(1 for p in probes if p.status != "skipped"),
                     "gaps": [p.probe for p in probes if p.status == "GAP"]},
        "applied_theme": state.get("applied_theme", ""),
        "warnings": inversions,
    }

    _json(root / "meta.json", meta)
    _json(root / "manifest.json", captured)
    _json(root / "coverage.json", [asdict(p) for p in probes])
    _atomic_write(root / "README.md", _readme(meta, probes).encode())
    if into is None:
        _point_latest(identifier)
    meta["path"] = str(root)
    return meta


def _json(path: Path, data) -> None:
    _atomic_write(path, (json.dumps(data, sort_keys=True, indent=2) + "\n").encode())


def _tool_version() -> str:
    from . import __version__
    return __version__


def _point_latest(identifier: str) -> None:
    link = snapshots_dir() / "latest"
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(identifier)
    except OSError:
        pass


def _run(command: list[str], timeout: int = 20) -> str:
    tool = shutil.which(command[0])
    if not tool:
        return ""
    try:
        done = subprocess.run([tool] + command[1:], capture_output=True,
                              text=True, timeout=timeout)
        return done.stdout if done.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


ENV_KEYS = ("XDG_CONFIG_DIRS", "XDG_DATA_DIRS", "XDG_SESSION_TYPE",
            "XDG_CURRENT_DESKTOP", "KDE_SESSION_VERSION", "KDE_FULL_SESSION",
            "QT_QPA_PLATFORM", "QT_QPA_PLATFORMTHEME", "QT_STYLE_OVERRIDE",
            "QT_SCALE_FACTOR", "GDK_SCALE", "GDK_DPI_SCALE",
            "GTK_RC_FILES", "GTK2_RC_FILES", "XCURSOR_THEME", "XCURSOR_SIZE")


def _capture_state(root: Path) -> dict:
    """The interpretable half: what the system was told, and what it did."""
    state = root / "state"
    live = resolve.live_settings()
    applied = live.get(("kdeglobals", "KDE"), {}).get("LookAndFeelPackage", "")

    declared: dict = {}
    if applied:
        try:
            declared = lnf.load(applied).settings
        except (FileNotFoundError, OSError):
            declared = {}

    rows = resolve.audit(declared, live)
    _json(state / "audit.json", [
        {"label": r.label, "declared": r.declared, "live": r.live,
         "status": r.status, "note": r.note,
         "detail": r.resolution.detail if r.resolution else "",
         "path": str(r.resolution.path) if r.resolution and r.resolution.path else ""}
        for r in rows])

    _json(state / "resolved.json",
          {f"{f}/{g}": dict(v) for (f, g), v in sorted(live.items())})

    _json(state / "inventory.json", _inventory())
    _json(state / "env.json", {k: os.environ.get(k, "") for k in ENV_KEYS})
    _json(state / "versions.json", {
        "plasma": _run(["plasmashell", "--version"]).strip(),
        "kwin": _run(["kwin_wayland", "--version"]).strip(),
        "qt": _run(["qmake6", "-query", "QT_VERSION"]).strip(),
        "kernel": os.uname().release,
    })
    _json(state / "legacy.json", [
        {"kind": p.kind, "name": p.name, "path": str(p.path),
         "active": p.active, "removable": p.removable,
         "referenced_by": list(p.referenced_by)} for p in legacy.scan()])

    outputs = _kscreen_json()
    if outputs:
        _json(state / "outputs.json", outputs)

    # The only honest record of what KWin actually loaded, as opposed to what
    # it was told. Effects that refuse to load show up here and nowhere else.
    support = _run(["qdbus6", "org.kde.KWin", "/KWin", "org.kde.KWin.supportInformation"], 30)
    if support:
        _atomic_write(state / "kwin-support.txt", support.encode())

    _json(state / "conclusions.json", {
        "aurorae_provider": resolve.aurorae_provider(),
        "scaled_variant_mismatches": _scaled_mismatches(),
    })
    return {"applied_theme": applied}


def _scaled_mismatches() -> list[dict]:
    found = []
    for base in paths.data_dirs():
        themes = base / "aurorae/themes"
        if not themes.is_dir():
            continue
        for theme in sorted(themes.iterdir()):
            if not theme.is_dir():
                continue
            detail = resolve.aurorae_scale_mismatch(theme)
            if detail:
                found.append({"theme": theme.name, "detail": detail})
    return found


IDENTITY_FILES = ("metadata.json", "metadata.desktop", "index.theme",
                  "contents/defaults")

INVENTORY_KINDS = {
    "look-and-feel": ("data", "plasma/look-and-feel"),
    "plasma-style": ("data", "plasma/desktoptheme"),
    "plasmoids": ("data", "plasma/plasmoids"),
    "aurorae": ("data", "aurorae/themes"),
    "wallpapers": ("data", "wallpapers"),
    "color-schemes": ("data", "color-schemes"),
}


def _inventory() -> dict[str, list[dict]]:
    """Record what is installed. Never copy it -- icons alone are 1.8 GB."""
    out: dict[str, list[dict]] = {}
    for kind, (_root, subdir) in INVENTORY_KINDS.items():
        entries = []
        for base in paths.data_dirs():
            directory = base / subdir
            if not directory.is_dir():
                continue
            for item in sorted(directory.iterdir()):
                entries.append(_package_row(item, base))
        out[kind] = entries
    icons = []
    for directory in paths.icon_dirs():
        if not directory.is_dir():
            continue
        for item in sorted(directory.iterdir()):
            if item.is_dir():
                icons.append(_package_row(item, directory.parent))
    out["icons"] = icons
    return out


def _package_row(item: Path, base: Path) -> dict:
    row = {"name": item.name, "root": str(base)}
    if item.is_file():
        try:
            row["sha256"] = _digest(item.read_bytes())
            row["size"] = item.stat().st_size
        except OSError:
            pass
        return row
    # Bounded on purpose. A full walk of ~/.local/share/icons costs seconds
    # and an exact file count of an icon theme is not worth one. The identity
    # hash below is what actually detects a changed package.
    count = size = 0
    truncated = False
    for root, _dirs, files in os.walk(item, onerror=lambda e: None):
        for name in files:
            try:
                size += (Path(root) / name).lstat().st_size
            except OSError:
                continue
            count += 1
        if count > 2_000:
            truncated = True
            break
    row["files"], row["size"] = count, size
    if truncated:
        row["approx"] = True
    # Hash the identity files only. That is the difference between "Tela is
    # installed" and 46 MB of PNGs.
    parts = []
    for name in IDENTITY_FILES:
        candidate = item / name
        if candidate.is_file():
            try:
                parts.append(f"{name}:{_digest(candidate.read_bytes())}")
            except OSError:
                continue
    if parts:
        row["identity"] = _digest("|".join(parts).encode())
    return row


def _readme(meta: dict, probes: list[ProbeResult]) -> str:
    coverage = meta["coverage"]
    lines = [
        f"# Snapshot {meta['id']}",
        "",
        f"Taken {meta['created']} — {meta['reason']}",
        f"{meta['message'] or '(no message)'}",
        "",
        f"- applied global theme: `{meta['applied_theme'] or 'none'}`",
        f"- files captured: {meta['files_captured']} ({meta['bytes']} bytes)",
        f"- coverage: {coverage['ok']}/{coverage['total']} facts verified",
        "",
        "`files/` is a byte-exact, path-preserving copy. `state/` is the",
        "interpretable version — what was applied, what resolved, what KWin",
        "actually loaded. Both are readable without this tool.",
        "",
    ]
    gaps = [p for p in probes if p.status == "GAP"]
    if gaps:
        lines += ["## Gaps", "",
                  "This snapshot does NOT contain the following live facts.",
                  "Fix the manifest in `lolkde/snapshot.py` and re-snapshot.", ""]
        for gap in gaps:
            lines.append(f"- **{gap.probe}** = `{gap.live}` — {gap.detail}")
            for where in gap.found_at:
                lines.append(f"  - found at: `{where}`")
        lines.append("")
    for warning in meta.get("warnings", []):
        lines += [f"> {warning}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reading snapshots back

def listing() -> list[dict]:
    directory = snapshots_dir()
    if not directory.is_dir():
        return []
    found = []
    for item in sorted(directory.iterdir()):
        if item.is_symlink() or not item.is_dir():
            continue
        meta = item / "meta.json"
        if meta.is_file():
            try:
                found.append(json.loads(meta.read_text()))
            except ValueError:
                continue
    return found


def find(reference: str) -> Path | None:
    """Resolve a snapshot id, a unique prefix, or 'latest'."""
    directory = snapshots_dir()
    if not directory.is_dir():
        return None
    if reference in ("latest", ""):
        link = directory / "latest"
        if link.is_symlink():
            resolved = directory / os.readlink(link)
            return resolved if resolved.is_dir() else None
        entries = [m["id"] for m in listing()]
        return directory / entries[-1] if entries else None
    exact = directory / reference
    if exact.is_dir():
        return exact
    matches = [m["id"] for m in listing() if m["id"].startswith(reference)]
    if len(matches) == 1:
        return directory / matches[0]
    return None


def ambiguous(reference: str) -> list[str]:
    return [m["id"] for m in listing() if m["id"].startswith(reference)]


def wait_for_quiescence(seconds: float = 3.0, cap: float = 30.0) -> float:
    """Block until nothing under ~/.config has changed for `seconds`.

    KDE writes are deferred. A display-scale change on this machine wrote
    kwinrc immediately and kwinoutputconfig.json about eleven seconds later, so
    an after-snapshot taken at once misses the tail and reports a false empty
    diff.
    """
    base = paths.config_home()
    started = time.monotonic()
    last_change = time.monotonic()
    previous = _mtime_fingerprint(base)
    while time.monotonic() - started < cap:
        time.sleep(0.5)
        current = _mtime_fingerprint(base)
        if current != previous:
            previous = current
            last_change = time.monotonic()
        elif time.monotonic() - last_change >= seconds:
            break
    return time.monotonic() - started


def _mtime_fingerprint(base: Path) -> str:
    newest = []
    for path in base.glob("*"):
        try:
            newest.append(f"{path.name}:{path.stat().st_mtime}")
        except OSError:
            continue
    return _digest("|".join(sorted(newest)).encode())
