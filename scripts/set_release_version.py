#!/usr/bin/env python3
"""Validate and stage an HTTP Data Bridge release version.

Normal development keeps committed version metadata at the most recent published
release. The release workflow takes the next version once, uses this script to
stage every tracked version field, commits the bump, and creates the matching tag
and GitHub release.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


@dataclass(frozen=True)
class VersionSpec:
    """One version-bearing file and the patterns used to read/update it."""

    label: str
    path: str
    extract_pattern: str
    replace_pattern: str
    flags: int = 0


SPECS = (
    VersionSpec(
        "Home Assistant integration",
        "custom_components/http_data_bridge/manifest.json",
        r'^  "version": "([^"]+)"[,]?$',
        r'(^  "version": ")[^"]+("[,]?$)',
        re.MULTILINE,
    ),
)


def _match_once(root: Path, spec: VersionSpec) -> tuple[str, str]:
    path = root / spec.path
    text = path.read_text(encoding="utf-8")
    matches = list(re.finditer(spec.extract_pattern, text, spec.flags))
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {spec.label} version in {spec.path}; "
            f"found {len(matches)}"
        )
    return text, matches[0].group(1)


def read_versions(root: Path = ROOT) -> dict[str, str]:
    """Return all tracked release versions, failing on ambiguous file shapes."""
    return {spec.label: _match_once(root, spec)[1] for spec in SPECS}


def current_version(root: Path = ROOT) -> str:
    """Return the common tracked version, failing if fields disagree."""
    versions = read_versions(root)
    unique = set(versions.values())
    if len(unique) != 1:
        raise RuntimeError(f"Release versions are inconsistent: {versions}")
    version = next(iter(unique))
    if not SEMVER.fullmatch(version):
        raise RuntimeError(f"Tracked release version is not X.Y.Z: {version!r}")
    return version


def set_release_version(version: str, root: Path = ROOT) -> None:
    """Set every tracked release-version field to ``version``."""
    if not SEMVER.fullmatch(version):
        raise ValueError(f"Release version must use X.Y.Z format, got {version!r}")

    current_version(root)

    for spec in SPECS:
        path = root / spec.path
        text = path.read_text(encoding="utf-8")

        def replacement(match: re.Match[str]) -> str:
            return f"{match.group(1)}{version}{match.group(2)}"

        updated, count = re.subn(
            spec.replace_pattern,
            replacement,
            text,
            count=1,
            flags=spec.flags,
        )
        if count != 1:
            raise RuntimeError(
                f"Expected exactly one writable {spec.label} version in {spec.path}; "
                f"found {count}"
            )
        path.write_text(updated, encoding="utf-8")

    staged = current_version(root)
    if staged != version:
        raise RuntimeError(f"Version staging produced {staged!r}, expected {version!r}")


def main() -> None:
    """Run the release-version command-line helper."""
    parser = argparse.ArgumentParser()
    parser.add_argument("version", nargs="?", help="target X.Y.Z release version")
    parser.add_argument(
        "--check",
        action="store_true",
        help="only verify that all tracked version fields agree",
    )
    args = parser.parse_args()

    if args.check:
        if args.version is not None:
            parser.error("--check does not accept a target version")
        print(current_version())  # noqa: T201 - CLI output consumed by release workflow
        return
    if args.version is None:
        parser.error("a target version is required unless --check is used")

    before = current_version()
    set_release_version(args.version)
    print(  # noqa: T201 - explicit CLI status output
        f"Staged release version {before} -> {args.version}"
    )


if __name__ == "__main__":
    main()
