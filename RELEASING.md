# Releasing HTTP Data Bridge

HTTP Data Bridge releases are created from `main` by the **Release** GitHub Actions workflow.

## First release

The repository is initially staged at the version already present in `custom_components/http_data_bridge/manifest.json`. For the first public release, run **Actions → Release → Run workflow** and enter that exact version (currently `0.4.0`). The workflow publishes it without creating an unnecessary version-bump commit.

## Later releases

For later releases, enter the new `X.Y.Z` version in the Release workflow. Do **not** update `manifest.json` manually beforehand.

The workflow:

1. verifies the tracked version and release state;
2. rejects malformed, duplicate, or non-increasing versions;
3. refuses to publish if `main` moved while the release was being prepared;
4. updates `manifest.json` to the requested version;
5. commits the version bump to `main` when required; and
6. creates the matching `vX.Y.Z` GitHub release with generated release notes.

Normal CI runs `scripts/set_release_version.py --check` so version metadata or release-helper breakage is caught before merge.
