import os
import shutil
from pathlib import Path

import apkmirror
import github
from build_piko import PIKO_REPO, build_piko_patches
from build_variants import build_apks, get_xlite_patches
from constants import REPO
from download_bins import download_morphe_cli
from utils import download, panic, publish_release

PATCHES_MPP = "bins/patches.mpp"
PATCHES_MPP_PREV = "bins/patches-prev.mpp"
PATCHES_APPLIED_TXT = "patches-applied.txt"
MORPHE_CLI = "bins/morphe-cli.jar"


# Print a prefixed progress message for the main pipeline
def log(message: str):
    print(f"[main] {message}", flush=True)


# Return True if the version string contains neither alpha nor beta
def is_stable_version(version: str) -> bool:
    lower = version.lower()
    return "alpha" not in lower and "beta" not in lower


# Pick the best arm64 bundle variant; return None when no arm64 variant exists
def get_bundle_variant(variants):
    preferred_architectures = [
        "universal",
        "arm64-v8a + armeabi-v7a",
        "arm64-v8a",
    ]
    for arch in preferred_architectures:
        match = next(
            (v for v in variants if v.is_bundle and v.architecture == arch),
            None,
        )
        if match is not None:
            return match
    return None


# Walk versions from newest to oldest until one with an arm64 bundle is found
def find_version_with_arm64_variant(versions, supported_versions):
    for version in versions:
        if supported_versions is not None and version.version not in supported_versions:
            continue
        if not is_stable_version(version.version):
            log(f"Skipping pre-release version: {version.version}")
            continue

        log(f"Checking variants for {version.version}...")
        variants = apkmirror.get_variants(version)
        variant = get_bundle_variant(variants)
        if variant is None:
            log(f"No arm64 variant for {version.version}, trying next")
            continue

        log(f"Found arm64 variant for {version.version}: {variant.architecture}")
        return version, variant

    return None, None


# Format patches as markdown, marking new ones and optional status
def format_patch_list(patches, previous_patches, statuses=None):
    known = set(previous_patches or [])
    mark_new = previous_patches is not None
    lines = []
    for patch in patches:
        new_mark = "**NEW** " if mark_new and patch not in known else ""
        if statuses is not None:
            status = "Success" if statuses.get(patch, False) else "Failure"
            lines.append(f"- {new_mark}{patch} \u2014 {status}")
        else:
            lines.append(f"- {new_mark}{patch}")
    return "\n".join(lines)


# Write the formatted patch list to patches-applied.txt
def write_patches_applied(patches, previous_patches, statuses):
    content = format_patch_list(patches, previous_patches, statuses)
    Path(PATCHES_APPLIED_TXT).write_text(content + "\n", encoding="utf-8")
    log(f"Wrote {len(patches)} entries to {PATCHES_APPLIED_TXT}")


# Return the .mpp asset from the previous release, or None
def find_previous_mpp_asset(previous_release):
    return next(
        (a for a in previous_release.assets if a.name.endswith(".mpp")),
        None,
    )


# Download the previous .mpp and extract patch names from it
def get_previous_patches_from_release(previous_release):
    if previous_release is None:
        log("No previous release (first build)")
        return None

    mpp_asset = find_previous_mpp_asset(previous_release)
    if mpp_asset is None:
        asset_names = [a.name for a in previous_release.assets]
        raise Exception(
            f"No .mpp asset found in previous release {previous_release.tag_name}. "
            f"Assets: {asset_names}"
        )

    log(f"Downloading previous mpp: {mpp_asset.name}")
    if os.path.exists(PATCHES_MPP_PREV):
        os.remove(PATCHES_MPP_PREV)
    download(mpp_asset.browser_download_url, PATCHES_MPP_PREV)
    log(f"Saved to {PATCHES_MPP_PREV}")

    log("Extracting patch list from previous mpp...")
    patches = get_xlite_patches(MORPHE_CLI, PATCHES_MPP_PREV)
    log(f"Previous patches: {len(patches)}")
    return patches


# Return Piko commits between the previous release and current commit
def get_piko_commits(previous_release, current_commit):
    if previous_release is None:
        return None
    previous_commit = previous_release.tag_name.rsplit("-", maxsplit=1)[-1]
    if previous_commit == current_commit[:7]:
        return []
    return github.get_commits_between(PIKO_REPO, previous_commit, current_commit)


# Format Piko commits as a markdown bullet list
def format_commit_list(commits):
    if not commits:
        return ""
    entries = "\n".join(
        f"- [`{c.sha[:7]}`]({c.html_url}) {c.subject}" for c in commits
    )
    return f"Piko commits since previous release:\n{entries}"


# Run the full build: fetch APK, patch, and publish the release
def process(latest_version, piko_build, download_variant, previous_release=None):
    log(f"Target X version: {latest_version.version}")
    log(f"Piko commit: {piko_build.commit[:7]}")
    log(f"Selected variant: {download_variant.architecture}")

    apk_path = f"big_file-{latest_version.version}.apkm"
    log(f"Downloading APK bundle: {apk_path}")
    apkmirror.download_apk(download_variant, path=apk_path)
    if not os.path.exists(apk_path):
        panic("Failed to download apkm")
    log(f"Download complete: {apk_path}")

    log("Downloading morphe-cli.jar...")
    download_morphe_cli(include_prereleases=True)
    log("morphe-cli.jar ready")

    piko_commit = piko_build.commit[:7]
    release_tag = f"{latest_version.version}-{piko_commit}"
    apk_name = f"piko-lite-v{latest_version.version}-{piko_commit}.apk"
    mpp_name = f"patches-{piko_commit}.mpp"

    log("Getting previous patch list...")
    previous_patches = get_previous_patches_from_release(previous_release)

    log(f"Using Piko x-lite@{piko_commit}")
    log("Applying patches (this may take several minutes)...")
    patches, patch_statuses = build_apks(latest_version, apk_path, piko_build.commit)
    succeeded = sum(1 for v in patch_statuses.values() if v)
    log(f"Patches applied: {succeeded}/{len(patches)} succeeded")

    if previous_patches is not None:
        new_count = len([p for p in patches if p not in set(previous_patches)])
        log(f"New patches since last release: {new_count}")

    log(f"Writing {PATCHES_APPLIED_TXT}...")
    write_patches_applied(patches, previous_patches, patch_statuses)

    log("Fetching Piko commits since previous release...")
    commit_list = format_commit_list(
        get_piko_commits(previous_release, piko_build.commit)
    )
    additional_notes = f"\n\n{commit_list}" if commit_list else ""

    message = f"""Patch list: see `{PATCHES_APPLIED_TXT}` asset.{additional_notes}

Piko source:
[x-lite@{piko_commit}](https://github.com/crimera/piko/commit/{piko_build.commit})
"""

    log(f"Copying mpp to {mpp_name}...")
    shutil.copy2(PATCHES_MPP, mpp_name)

    log(f"Publishing release {release_tag}...")
    publish_release(
        release_tag,
        [apk_name, mpp_name, PATCHES_APPLIED_TXT],
        message,
        release_tag,
    )
    log("Done.")


# Entry point: find a version with an arm64 bundle and trigger the build
def main():
    log("Fetching X versions from APKMirror...")
    versions = apkmirror.get_versions(
        "https://www.apkmirror.com/apk/x-corp/twitter/"
    )
    log(f"Found {len(versions)} versions")

    log("Building Piko patches (x-lite branch)...")
    piko_build = build_piko_patches()
    log(
        f"Piko build: {piko_build.commit[:7]}, "
        f"supports {len(piko_build.supported_versions)} versions"
    )

    log("Searching for a version with an arm64 bundle variant...")
    latest_version, download_variant = find_version_with_arm64_variant(
        versions, piko_build.supported_versions
    )
    if latest_version is None:
        raise Exception("No X version with an arm64 bundle variant found")
    log(f"Selected: {latest_version.version} ({download_variant.architecture})")

    release_tag = f"{latest_version.version}-{piko_build.commit[:7]}"
    log(f"Checking last release against {release_tag}...")
    last_build_version = github.get_last_build_version(REPO)
    if (
        last_build_version is not None
        and last_build_version.tag_name == release_tag
    ):
        log("No new compatible version found")
        return

    log(f"New compatible version found: {latest_version.version}")
    process(latest_version, piko_build, download_variant, last_build_version)


if __name__ == "__main__":
    main()