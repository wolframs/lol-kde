"""Installing store content into the directories KDE expects.

Extraction deliberately ignores the declared Uncompress mode in one respect:
whether an archive has a single top-level directory is *detected* rather than
trusted, because uploads routinely disagree with their category's declaration.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import knsrc, store
from .manifest import Dependency


@dataclass
class InstallResult:
    dependency: Dependency
    item: store.StoreItem | None
    status: str          # installed | skipped | failed
    detail: str
    destination: Path | None = None


def is_archive(path: Path) -> bool:
    """Is this actually a container, or just a file someone uploaded?

    The knsrc's `uncompress=` setting says what a category *usually* ships,
    and store entries disagree with it in practice: Nostrum's colour scheme
    downloads as a bare `Nostrum.colors`, and the installer refused it with
    "unrecognised archive format" while every other dependency succeeded.
    The bytes on disk are the authority, not the category's expectation.
    """
    return zipfile.is_zipfile(path) or tarfile.is_tarfile(path)


def _safe_extract(archive: Path, into: Path) -> Path:
    """Unpack an archive, refusing entries that escape the destination."""
    into.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                resolved = (into / member).resolve()
                if not resolved.is_relative_to(into.resolve()):
                    raise ValueError(f"archive escapes destination: {member}")
            zf.extractall(into)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            # filter="data" strips absolute paths, symlink escapes and specials.
            tf.extractall(into, filter="data")
    else:
        raise ValueError(f"unrecognised archive format: {archive.name}")
    return into


def _single_root(directory: Path) -> Path | None:
    """Return the sole top-level directory of an extracted archive, if any."""
    entries = [e for e in directory.iterdir() if not e.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return None


def _payloads(source: Path) -> list[Path] | None:
    """Split an extracted archive into the package(s) it actually contains.

    One archive is not always one package: icon themes routinely ship several
    variants side by side (Tela, Tela-dark, Tela-light), and wrapping those in
    a folder named after the store entry puts every one of them at the wrong
    path. But an archive whose top level has real files is a single package
    with subdirectories, and must not be split.

    Returns the directories to install, or None to install `source` whole.
    """
    entries = [e for e in source.iterdir() if not e.name.startswith(".")]
    directories = [e for e in entries if e.is_dir()]
    files = [e for e in entries if e.is_file()]

    if len(directories) == 1 and not files:
        return [directories[0]]
    if len(directories) > 1 and not files:
        return directories
    return None


def _install_tree(source: Path, target: Path, name: str, force: bool) -> list[Path]:
    """Move an extracted payload into its final home. Returns destinations."""
    target.mkdir(parents=True, exist_ok=True)
    payloads = _payloads(source)
    if payloads is None:
        payloads, names = [source], [name]
    else:
        names = [p.name for p in payloads]

    destinations: list[Path] = []
    for payload, payload_name in zip(payloads, names):
        destination = target / payload_name
        if destination.exists():
            if not force:
                raise FileExistsError(destination)
            shutil.rmtree(destination)
        shutil.move(str(payload), str(destination))
        destinations.append(destination)
    return destinations


def _kpackage_install(archive: Path, package_type: str, force: bool) -> str:
    tool = shutil.which("kpackagetool6") or shutil.which("kpackagetool5")
    if not tool:
        raise RuntimeError("kpackagetool6 not found; cannot install KPackage content")
    action = "--upgrade" if force else "--install"
    command = [tool]
    if package_type:
        command += ["--type", package_type]
    command += [action, str(archive)]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        # --install fails on an already-present package; treat that as success.
        if "already exists" in message.lower():
            return "already installed"
        raise RuntimeError(message or f"{tool} exited {completed.returncode}")
    return (completed.stdout or "installed").strip().splitlines()[-1]


def place_archive(archive: Path, route, name: str, force: bool) -> tuple[str, str, Path | None]:
    """Unpack a downloaded archive into wherever the route says it belongs.

    Shared by the manifest-driven installer and the store-page resolver, so
    both put things in exactly the same places.
    """
    if route.uses_kpackage:
        message = _kpackage_install(archive, route.kpackage_type, force)
        # kpackagetool6 reports "Successfully installed <path>"; that path is
        # the only reliable source of the installed package's directory name,
        # which is rarely the store's display name and is not discoverable by
        # diffing when the package was already present.
        found = re.search(r"(/\S+/)\s*$", message.strip())
        destination = Path(found.group(1)) if found else None
        return "installed", message, destination

    if route.target is None:
        return "failed", "no install target for this content type", None

    # Either the category says these arrive uncompressed, or this particular
    # download simply is not an archive whatever the category claims.
    bare = not is_archive(archive)
    if route.uncompress == "never" or bare:
        route.target.mkdir(parents=True, exist_ok=True)
        destination = route.target / archive.name
        if destination.exists() and not force:
            return "skipped", f"already present at {destination}", destination
        shutil.copy2(archive, destination)
        detail = ("" if not bare or route.uncompress == "never"
                  else f"not an archive; installed {archive.name} as-is")
        return "installed", detail, destination

    extracted = _safe_extract(archive, archive.parent / "unpacked")
    try:
        destinations = _install_tree(extracted, route.target, name, force)
    except FileExistsError as exc:
        return "skipped", f"already installed at {exc.args[0]} (use --force)", None
    detail = ("" if len(destinations) == 1
              else f"{len(destinations)} packages: "
                   + ", ".join(d.name for d in destinations))
    return "installed", detail, destinations[0]


def install_from_store(node, *, force: bool = False, dry_run: bool = False):
    """Download and install one node of a store dependency graph."""
    item, route = node.item, node.route
    if item is None:
        return "failed", "could not be looked up on any store host", None
    if route is None or not route.known or (route.target is None and not route.uses_kpackage):
        return "skipped", f"unrecognised content type {item.typename!r}", None
    if route.needs_root:
        return "skipped", "installs system-wide and needs root", None
    if dry_run:
        where = route.target or "via kpackagetool6"
        return "skipped", f"would install to {where}", None

    try:
        target = store.fetch_download(node.host, node.content_id)
    except store.StoreError as exc:
        return "failed", str(exc), None

    with tempfile.TemporaryDirectory(prefix="lol-kde-") as tmp:
        try:
            archive = store.download(target, Path(tmp) / target.filename)
            return place_archive(archive, route, item.name, force)
        except store.StoreError as exc:
            return "failed", str(exc), None
        except (OSError, ValueError, RuntimeError) as exc:
            return "failed", str(exc), None


def install_dependency(
    dependency: Dependency,
    *,
    force: bool = False,
    dry_run: bool = False,
    prefer: str = "",
) -> InstallResult:
    spec = knsrc.load(dependency.knsrc)

    if spec.needs_root:
        return InstallResult(
            dependency, None, "skipped",
            f"{dependency.knsrc} installs system-wide and needs root; "
            f"install manually from {dependency.store_url}",
        )
    if not spec.found and not spec.uses_kpackage:
        return InstallResult(
            dependency, None, "skipped",
            f"no install target known for category {dependency.knsrc!r}",
        )

    try:
        item = store.fetch_metadata(dependency.host, dependency.content_id)
    except store.StoreError as exc:
        return InstallResult(dependency, None, "failed", str(exc))

    if dry_run:
        where = spec.target or "via kpackagetool6"
        return InstallResult(dependency, item, "skipped", f"would install to {where}")

    try:
        index = store.choose_download(dependency.host, dependency.content_id, prefer)
        target = store.fetch_download(dependency.host, dependency.content_id, index)
    except store.StoreError as exc:
        return InstallResult(dependency, item, "failed", str(exc))

    with tempfile.TemporaryDirectory(prefix="lol-kde-") as tmp:
        tmpdir = Path(tmp)
        try:
            archive = store.download(target, tmpdir / target.filename)
        except store.StoreError as exc:
            return InstallResult(dependency, item, "failed", str(exc))

        # Delegate rather than repeat. This block used to be its own copy of
        # place_archive, and the copies drifted: a fix for a bare (non-archive)
        # download landed in one path only, so `install <theme>` went on
        # failing with "unrecognised archive format" while `please <url>`
        # worked. A knsrc spec exposes the same target/uncompress/kpackage
        # attributes a store route does, so one function can serve both.
        try:
            status, detail, destination = place_archive(
                archive, spec, item.name, force)
            return InstallResult(dependency, item, status, detail, destination)
        except (OSError, ValueError, RuntimeError) as exc:
            return InstallResult(dependency, item, "failed", str(exc))
