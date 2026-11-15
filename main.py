import os
import re
import shutil
from pathlib import Path
from argparse import ArgumentParser
import json
from datetime import datetime, timezone

import apkmirror
import github
from build_piko import PIKO_REPO, build_piko_patches
from build_variants import build_apks, get_xlite_patches
from constants import REPO
from download_bins import download_morphe_cli
from utils import download, panic, publish_release

PATCHES_BUNDLE_FILE = "patches-bundle.json"
PATCHES_APPLIED_TXT = "patches-applied.txt"
MORPHE_CLI = "bins/morphe-cli.jar"

MAX_VARIANTS = 2

LEGACY_RELEASE_PATTERN = re.compile(r"^(?P<app>.+)-(?P<piko>[0-9a-f]{7,40})$")
RELEASE_TAG_PATTERN = re.compile(r"^v\d+\.\d+\.\d+$")
APP_VERSION_PATTERN = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<channel>alpha|beta|prod)\.(?P<revision>\d+))?$"
)
APP_VERSION_CHANNEL_PRIORITY = {"alpha": 0, "beta": 1, "prod": 2}

def log(message: str):
    print(f"[main] {message}", flush=True)

def app_version_sort_key(version: str) -> tuple[int, int, int, int, int]:
    match = APP_VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise ValueError(f"Unsupported X app version format: {version}")

    channel = match.group("channel")
    channel_priority = APP_VERSION_CHANNEL_PRIORITY[channel] if channel is not None else 3
    revision = int(match.group("revision") or 0)
    return (
        int(match.group("major")), int(match.group("minor")), int(match.group("patch")),
        channel_priority, revision,
    )

def is_stable_version(version: str) -> bool:
    lower = version.lower()
    return "alpha" not in lower and "beta" not in lower

def arch_priority(architecture: str) -> int | None:
    a = (architecture or "").lower().strip()
    if "universal" in a: return 0
    if "arm64-v8a" in a and "armeabi-v7a" in a: return 1
    if "arm64-v8a" in a: return 2
    return None

def android_slug(min_android_text: str) -> str:
    text = (min_android_text or "unknown").strip()
    text = re.sub(r"^Android\s+", "", text, flags=re.IGNORECASE)
    text = text.replace(" ", "-").replace("+", "plus")
    return f"android-{text}"

def get_bundle_variants(variants, limit: int = MAX_VARIANTS):
    candidates = []
    for v in variants:
        if not v.is_bundle: continue
        prio = arch_priority(v.architecture)
        if prio is None: continue
        candidates.append((prio, v.min_android, v))

    if not candidates: return []

    candidates.sort(key=lambda item: (item[0], item[1]))

    result = []
    seen_slugs: set[str] = set()
    for _, _, v in candidates:
        slug = android_slug(v.min_android_text)
        if slug in seen_slugs: continue
        seen_slugs.add(slug)
        result.append(v)
        if len(result) >= limit: break
    return result

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
            log(f"Selected variant for {version.version}: {v.architecture} / {v.min_android_text} / {v.dpi_text}")
        return version, selected

    return None, []

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

def write_patches_applied(builds, previous_patches):
    sections: list[str] = []
    for build in builds:
        variant = build["variant"]
        slug = build["slug"]
        header = f"# {slug} | {variant.architecture} | {variant.dpi_text or 'unknown dpi'}"
        body = format_patch_list(build["patches"], previous_patches, build["statuses"])
        sections.append(f"{header}\n{body}")
    content = "\n\n".join(sections) + "\n"
    Path(PATCHES_APPLIED_TXT).write_text(content, encoding="utf-8")
    log(f"Wrote {len(builds)} variant section(s) to {PATCHES_APPLIED_TXT}")

def find_previous_mpp_asset(previous_release):
    return next((a for a in previous_release.assets if a.name.endswith(".mpp")), None)

def get_previous_patches_from_release(previous_release):
    if previous_release is None:
        log("No previous release (first build)")
        return None

    mpp_asset = find_previous_mpp_asset(previous_release)
    if mpp_asset is None:
        asset_names = [a.name for a in previous_release.assets]
        raise Exception(f"No .mpp asset found in previous release {previous_release.tag_name}. Assets: {asset_names}")

    log(f"Downloading previous mpp: {mpp_asset.name}")
    prev_mpp = "bins/patches-prev.mpp"
    if os.path.exists(prev_mpp): os.remove(prev_mpp)
    download(mpp_asset.browser_download_url, prev_mpp)
    log(f"Saved to {prev_mpp}")

    log("Extracting patch list from previous mpp...")
    patches = get_xlite_patches(MORPHE_CLI, prev_mpp)
    log(f"Previous patches: {len(patches)}")
    return patches

XLITE_COMMIT_SCOPE = re.compile(r"^[A-Za-z]+\((?:newx|x-lite)\)!?:\s", re.IGNORECASE)

def is_same_commit(a: str, b: str) -> bool:
    return a == b or a.startswith(b) or b.startswith(a)

def filter_xlite_commits(commits: list[github.GithubCommit], head_sha: str, base_sha: str) -> list[github.GithubCommit]:
    by_sha = {commit.sha: commit for commit in commits}
    first_parent: list[github.GithubCommit] = []
    current = head_sha
    while current and not is_same_commit(current, base_sha):
        commit = by_sha.get(current)
        if commit is None:
            commit = next((c for c in commits if is_same_commit(c.sha, current)), None)
        if commit is None: break
        if len(commit.parents) <= 1: first_parent.append(commit)
        current = commit.parents[0] if commit.parents else None

    first_parent_shas = {commit.sha for commit in first_parent}
    return [
        commit for commit in commits
        if commit.sha in first_parent_shas
        or (len(commit.parents) <= 1 and XLITE_COMMIT_SCOPE.match(commit.subject))
    ]

def get_piko_commits(previous_piko_commit: str | None, current_commit: str):
    if not previous_piko_commit:
        return None
    if not re.fullmatch(r"[0-9a-f]{7,40}", previous_piko_commit):
        log(f"Could not extract a valid Piko commit from {previous_piko_commit}")
        return None
    if is_same_commit(previous_piko_commit, current_commit):
        return []

    commits = github.get_commits_between(PIKO_REPO, previous_piko_commit, current_commit)
    if commits is None: return None

    selected = filter_xlite_commits(commits, current_commit, previous_piko_commit)
    log(f"Selected {len(selected)}/{len(commits)} x-lite commit(s)")
    return selected

def format_commit_list(commits):
    if not commits:
        return ""
    entries = "\n".join(
        f"- [`{c.sha[:7]}`]({c.html_url}) {c.subject}" for c in commits
    )
    return f"NewX commits since previous release:\n{entries}"

def format_variants_info(version, variants) -> str:
    rows = "\n".join(f"| {version.version} | {v.architecture} | {v.min_android_text} | {v.dpi_text} |" for v in variants)
    return (
        "### Applied variants\n\n"
        "| Variant | Architecture | Minimum Version | Screen DPI |\n"
        "|---|---|---|---|\n"
        f"{rows}"
    )

def read_release_metadata() -> dict[str, str]:
    metadata_path = Path(PATCHES_BUNDLE_FILE)
    if not metadata_path.exists(): return {}
    contents = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(contents, dict):
        raise ValueError(f"{PATCHES_BUNDLE_FILE} must contain a JSON object")
    return {k: v for k, v in contents.items() if isinstance(k, str) and isinstance(v, str)}

def get_previous_context(prev_release, metadata):
    if prev_release is None: return None, None
    app_ver = metadata.get("app_version")
    piko_commit = metadata.get("piko_commit")
    if app_ver and piko_commit: return app_ver, piko_commit
    match = LEGACY_RELEASE_PATTERN.match(prev_release.tag_name)
    if match: return match.group("app"), match.group("piko")
    return None, None

def write_patches_bundle(release_tag: str, app_version: str, piko_build, repo: str = REPO) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    patch_version = release_tag.removeprefix("v")
    mpp_filename = f"patches-{patch_version}.mpp"
    metadata = {
        "version": release_tag,
        "download_url": f"https://github.com/{repo}/releases/download/{release_tag}/{mpp_filename}",
        "created_at": now,
        "description": f"Piko x-lite patch bundle for X Lite APK ({release_tag}).",
        "app_version": app_version,
        "piko_commit": piko_build.commit,
    }
    Path(PATCHES_BUNDLE_FILE).write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

def process(release_tag: str, patch_version: str, piko_build, previous_release, semantic_bump: bool):
    log(f"Target Piko commit: {piko_build.commit[:7]}")

    log("Fetching X versions from APKMirror...")
    apk_versions = apkmirror.get_versions("https://www.apkmirror.com/apk/x-corp/twitter/")

    latest_x_version, download_variants = find_version_with_arm64_variant(apk_versions, piko_build.supported_versions)
    if latest_x_version is None or not download_variants:
        raise Exception("No compatible X APK with arm64 bundle found on APKMirror")

    for v in download_variants:
        log(f"Will use: {v.architecture} / {v.min_android_text} / {v.dpi_text}")

    metadata = read_release_metadata()
    prev_x_ver, prev_piko_commit = get_previous_context(previous_release, metadata)

    is_new_tag = previous_release is None or previous_release.tag_name != release_tag
    content_changed = (prev_x_ver != latest_x_version.version) or (prev_piko_commit is None or not piko_build.commit.startswith(prev_piko_commit))

    if not semantic_bump and not content_changed and not is_new_tag:
        log("No semantic or content changes found. Skipping release.")
        return

    log(f"Publishing {release_tag} for X {latest_x_version.version}")

    log("Downloading morphe-cli.jar...")
    download_morphe_cli(include_prereleases=True)

    downloaded = []
    for variant in download_variants:
        slug = android_slug(variant.min_android_text)
        apk_path = f"big_file-{latest_x_version.version}-{slug}.apkm"
        try:
            log(f"Downloading {slug} ({variant.architecture}) -> {apk_path}")
            apkmirror.download_apk(variant, path=apk_path)
            if not os.path.exists(apk_path):
                log(f"Download missing after fetch: {apk_path}")
                continue

            with open(apk_path, "rb") as f:
                magic = f.read(4)
            if magic != b"PK\x03\x04":
                log(f"  [ERROR] Downloaded file is not a valid ZIP/APKM!")
                os.remove(apk_path)
                continue
            downloaded.append((variant, slug, apk_path))
        except Exception as exc:
            log(f"Download failed for {slug}: {exc}")

    if not downloaded:
        panic("All variant downloads failed")

    log("Getting previous patch list...")
    previous_patches = get_previous_patches_from_release(previous_release)

    built = []
    for variant, slug, apk_path in downloaded:
        log(f"Patching {slug} ({variant.architecture})...")
        try:
            patches, patch_statuses = build_apks(
                latest_x_version, apk_path, piko_build.commit,
                patch_version=patch_version, suffix=f"-{slug}",
            )
            apk_name = f"piko-lite-v{latest_x_version.version}-{patch_version}-{slug}.apk"
            if not os.path.exists(apk_name):
                log(f"Patched APK missing: {apk_name}")
                continue
            built.append({
                "variant": variant, "slug": slug, "apk_name": apk_name,
                "patches": patches, "statuses": patch_statuses,
            })
            succeeded = sum(1 for v in patch_statuses.values() if v)
            log(f"Built {apk_name}: {succeeded}/{len(patches)} patches succeeded")
        except Exception as exc:
            log(f"Patching failed for {slug}: {exc}")

    if not built:
        panic("All variant patches failed")

    log("Writing patches-applied.txt...")
    write_patches_applied(built, previous_patches)

    # --- Release notes ---
    log("Fetching Piko commits since previous release...")
    commit_list = format_commit_list(
        get_piko_commits(prev_piko_commit, piko_build.commit)
    )
    additional_notes = f"\n\n{commit_list}" if commit_list else ""

    variant_info = format_variants_info(
        latest_x_version, [b["variant"] for b in built]
    )

    message = f"""{variant_info}

Patch list: see `{PATCHES_APPLIED_TXT}` asset.{additional_notes}

Piko source:
[x-lite@{piko_build.commit[:7]}](https://github.com/crimera/piko/commit/{piko_build.commit})
"""

    mpp_name = f"bins/patches-{patch_version}.mpp"
    release_assets = [b["apk_name"] for b in built] + [mpp_name, PATCHES_APPLIED_TXT]

    log(f"Publishing release {release_tag} with {len(built)} APK(s)...")
    publish_release(release_tag, release_assets, message, release_tag)

    write_patches_bundle(release_tag, latest_x_version.version, piko_build)
    log("Done.")

def main(release_tag: str, semantic_bump: bool):
    if not RELEASE_TAG_PATTERN.fullmatch(release_tag):
        raise ValueError(f"Invalid semantic release tag: {release_tag}")

    patch_version = release_tag.removeprefix("v")

    log("Building Piko patches (x-lite branch)...")
    piko_build = build_piko_patches(patch_version=patch_version)
    log(f"Piko build: {piko_build.commit[:7]}, supports {len(piko_build.supported_versions)} versions")

    previous_release = github.get_last_build_version(REPO)

    process(
        release_tag=release_tag, patch_version=patch_version, piko_build=piko_build,
        previous_release=previous_release, semantic_bump=semantic_bump,
    )

if __name__ == "__main__":
    parser = ArgumentParser(description="Piko X-Lite APK Builder")
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--semantic-bump", choices=("true", "false"), default="false")
    parser.add_argument("--changelog-file", default=None) # Kept for workflow compatibility but ignored
    args = parser.parse_args()

    main(args.release_tag, args.semantic_bump == "true")