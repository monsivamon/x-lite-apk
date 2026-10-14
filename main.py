import os
import re
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

# Number of top variants to attempt per release
MAX_VARIANTS = 2


# Print a prefixed progress message for the main pipeline
def log(message: str):
    print(f"[main] {message}", flush=True)


# Return True if the version string contains neither alpha nor beta
def is_stable_version(version: str) -> bool:
    lower = version.lower()
    return "alpha" not in lower and "beta" not in lower


# Architecture priority for bundle selection: lower is better, None = not eligible
def arch_priority(architecture: str) -> int | None:
    a = (architecture or "").lower().strip()
    if "universal" in a:
        return 0
    if "arm64-v8a" in a and "armeabi-v7a" in a:
        return 1
    if "arm64-v8a" in a:
        return 2
    return None


# Build a URL/filesystem-safe slug from the min Android display text.
# 'Android 12L+' -> 'android-12L+', 'Android 9.0+' -> 'android-9.0+'
def android_slug(min_android_text: str) -> str:
    text = (min_android_text or "unknown").strip()
    text = re.sub(r"^Android\s+", "", text, flags=re.IGNORECASE)
    text = text.replace(" ", "-")
    return f"android-{text}"


# Pick the top N bundle variants:
#   1) universal > arm64-v8a + armeabi-v7a > arm64-v8a
#   2) among the same architecture, the lowest required Android version
#   Deduplicates by android_slug so we do not publish two identical Android targets.
def get_bundle_variants(variants, limit: int = MAX_VARIANTS):
    candidates = []
    for v in variants:
        if not v.is_bundle:
            continue
        prio = arch_priority(v.architecture)
        if prio is None:
            continue
        candidates.append((prio, v.min_android, v))

    if not candidates:
        return []

    candidates.sort(key=lambda item: (item[0], item[1]))

    result = []
    seen_slugs: set[str] = set()
    for _, _, v in candidates:
        slug = android_slug(v.min_android_text)
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        result.append(v)
        if len(result) >= limit:
            break
    return result


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
        selected = get_bundle_variants(variants)
        if not selected:
            log(f"No arm64 variant for {version.version}, trying next")
            continue

        for v in selected:
            log(
                f"Selected variant for {version.version}: "
                f"{v.architecture} / {v.min_android_text} / {v.dpi_text}"
            )
        return version, selected

    return None, []


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


# Write per-variant patch results to patches-applied.txt.
# builds: list of dicts with keys variant, slug, patches, statuses
def write_patches_applied(builds, previous_patches):
    sections: list[str] = []
    for build in builds:
        variant = build["variant"]
        slug = build["slug"]
        header = (
            f"# {slug} | {variant.architecture} | {variant.dpi_text or 'unknown dpi'}"
        )
        body = format_patch_list(
            build["patches"], previous_patches, build["statuses"]
        )
        sections.append(f"{header}\n{body}")
    content = "\n\n".join(sections) + "\n"
    Path(PATCHES_APPLIED_TXT).write_text(content, encoding="utf-8")
    log(
        f"Wrote {len(builds)} variant section(s) to {PATCHES_APPLIED_TXT}"
    )


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


# Format the applied variants as a markdown table for the release notes
def format_variants_info(version, variants) -> str:
    rows = "\n".join(
        f"| {version.version} | {v.architecture} | {v.min_android_text} | {v.dpi_text} |"
        for v in variants
    )
    return (
        "### Applied variants\n\n"
        "| Variant | Architecture | Minimum Version | Screen DPI |\n"
        "|---|---|---|---|\n"
        f"{rows}"
    )


# Run the full build: fetch APKs, patch each surviving variant, and publish
def process(latest_version, piko_build, download_variants, previous_release=None):
    log(f"Target X version: {latest_version.version}")
    log(f"Piko commit: {piko_build.commit[:7]}")
    log(f"Variant count: {len(download_variants)}")

    # --- Download phase: gather all successfully fetched APKs ---
    downloaded: list[tuple[object, str, str]] = []
    for variant in download_variants:
        slug = android_slug(variant.min_android_text)
        apk_path = f"big_file-{latest_version.version}-{slug}.apkm"
        try:
            log(f"Downloading {slug} ({variant.architecture}) -> {apk_path}")
            apkmirror.download_apk(variant, path=apk_path)
            if not os.path.exists(apk_path):
                log(f"Download missing after fetch: {apk_path}")
                continue
            log(f"Download complete: {apk_path}")
            downloaded.append((variant, slug, apk_path))
        except Exception as exc:
            log(f"Download failed for {slug}: {exc}")

    if not downloaded:
        panic("All variant downloads failed")

    log(f"Downloaded {len(downloaded)}/{len(download_variants)} variant(s)")

    # --- Prepare shared tooling and previous patch list ---
    log("Downloading morphe-cli.jar...")
    download_morphe_cli(include_prereleases=True)
    log("morphe-cli.jar ready")

    piko_commit = piko_build.commit[:7]
    release_tag = f"{latest_version.version}-{piko_commit}"
    mpp_name = f"patches-{piko_commit}.mpp"

    log("Getting previous patch list...")
    previous_patches = get_previous_patches_from_release(previous_release)

    # --- Patch phase: patch each downloaded APK independently ---
    built: list[dict] = []
    for variant, slug, apk_path in downloaded:
        log(f"Patching {slug} ({variant.architecture})...")
        try:
            patches, patch_statuses = build_apks(
                latest_version,
                apk_path,
                piko_build.commit,
                suffix=f"-{slug}",
            )
            apk_name = f"piko-lite-v{latest_version.version}-{piko_commit}-{slug}.apk"
            if not os.path.exists(apk_name):
                log(f"Patched APK missing: {apk_name}")
                continue
            built.append(
                {
                    "variant": variant,
                    "slug": slug,
                    "apk_name": apk_name,
                    "patches": patches,
                    "statuses": patch_statuses,
                }
            )
            succeeded = sum(1 for v in patch_statuses.values() if v)
            log(f"Built {apk_name}: {succeeded}/{len(patches)} patches succeeded")
        except Exception as exc:
            log(f"Patching failed for {slug}: {exc}")

    if not built:
        panic("All variant patches failed")

    log(f"Built {len(built)}/{len(downloaded)} patched APK(s)")

    # --- Patch list (all successfully built variants) ---
    log(f"Writing {PATCHES_APPLIED_TXT}...")
    write_patches_applied(built, previous_patches)

    # --- Release notes ---
    log("Fetching Piko commits since previous release...")
    commit_list = format_commit_list(
        get_piko_commits(previous_release, piko_build.commit)
    )
    additional_notes = f"\n\n{commit_list}" if commit_list else ""

    variant_info = format_variants_info(
        latest_version, [b["variant"] for b in built]
    )

    message = f"""{variant_info}

Patch list: see `{PATCHES_APPLIED_TXT}` asset.{additional_notes}

Piko source:
[x-lite@{piko_commit}](https://github.com/crimera/piko/commit/{piko_build.commit})
"""

    log(f"Copying mpp to {mpp_name}...")
    shutil.copy2(PATCHES_MPP, mpp_name)

    release_assets = [b["apk_name"] for b in built] + [mpp_name, PATCHES_APPLIED_TXT]
    log(f"Publishing release {release_tag} with {len(built)} APK(s)...")
    publish_release(
        release_tag,
        release_assets,
        message,
        release_tag,
    )
    log("Done.")


# Entry point: find a version with arm64 bundles and trigger the build
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

    log("Searching for a version with arm64 bundle variants...")
    latest_version, download_variants = find_version_with_arm64_variant(
        versions, piko_build.supported_versions
    )
    if latest_version is None or not download_variants:
        raise Exception("No X version with an arm64 bundle variant found")
    for v in download_variants:
        log(f"Will use: {v.architecture} / {v.min_android_text} / {v.dpi_text}")

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
    process(latest_version, piko_build, download_variants, last_build_version)


if __name__ == "__main__":
    main()