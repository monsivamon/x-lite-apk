import json
import os
from pathlib import Path

import apkmirror
import github
from apkmirror import Variant, Version
from build_piko import PIKO_REPO, PikoBuild, build_piko_patches
from build_variants import build_apks
from constants import REPO
from download_bins import download_morphe_cli
from utils import panic, publish_release

PATCHES_LIST_ASSET = "patches-list.json"
PATCHES_MPP = "bins/patches.mpp"

# サポート対象の最新バージョンを取得する
def get_latest_version(
    versions: list[Version], supported_versions: frozenset[str] | None = None
) -> Version | None:
    for version in versions:
        if supported_versions is None or version.version in supported_versions:
            return version

# APKバンドルを選択する
def get_bundle_variant(variants: list[Variant]) -> Variant | None:
    universal_bundle = next(
        (
            variant
            for variant in variants
            if variant.is_bundle and variant.architecture == "universal"
        ),
        None,
    )
    if universal_bundle is not None:
        return universal_bundle

    return next((variant for variant in variants if variant.is_bundle), None)

# パッチ一覧をMarkdown形式に整形する
def format_patch_list(
    patches: list[str],
    previous_patches: list[str] | None,
    statuses: dict[str, bool] | None = None,
) -> str:
    known_patches = set(previous_patches or [])
    mark_new_patches = previous_patches is not None

    lines = []
    for patch in patches:
        new_mark = "**NEW** " if mark_new_patches and patch not in known_patches else ""

        if statuses is not None:
            status = "Success" if statuses.get(patch, False) else "False"
            lines.append(f"- {new_mark}{patch} — {status}")
        else:
            lines.append(f"- {new_mark}{patch}")

    return "\n".join(lines)

# パッチ一覧をJSONファイルに書き出す
def write_patches_list(patches: list[str]) -> None:
    Path(PATCHES_LIST_ASSET).write_text(
        json.dumps(patches, indent=2) + "\n",
        encoding="utf-8",
    )

# 前回リリース以降のPikoコミット一覧を取得する
def get_piko_commits(
    previous_release: github.GithubRelease | None, current_commit: str
) -> list[github.GithubCommit] | None:
    if previous_release is None:
        return None

    previous_commit = previous_release.tag_name.rsplit("-", maxsplit=1)[-1]
    if previous_commit == current_commit[:7]:
        return []

    return github.get_commits_between(PIKO_REPO, previous_commit, current_commit)

# コミットリストをMarkdown形式に整形する
def format_commit_list(commits: list[github.GithubCommit] | None) -> str:
    if not commits:
        return ""

    entries = "\n".join(
        f"- [`{commit.sha[:7]}`]({commit.html_url}) {commit.subject}"
        for commit in commits
    )
    return f"Piko commits since previous release:\n{entries}"

# APKのダウンロードからパッチ適用、リリース発行までを実行する
def process(
    latest_version: Version,
    piko_build: PikoBuild,
    previous_release: github.GithubRelease | None = None,
):
    variants: list[Variant] = apkmirror.get_variants(latest_version)

    download_link = get_bundle_variant(variants)
    if download_link is None:
        raise Exception("APK bundle not found")

    apk_path = f"big_file-{latest_version.version}.apkm"
    apkmirror.download_apk(download_link, path=apk_path)
    if not os.path.exists(apk_path):
        panic("Failed to download apkm")

    download_morphe_cli(include_prereleases=True)

    piko_commit = piko_build.commit[:7]
    release_tag = f"{latest_version.version}-{piko_commit}"
    apk_name = f"piko-lite-v{latest_version.version}-{piko_commit}.apk"

    print(f"Using Piko x-lite@{piko_commit}")

    patches, patch_statuses = build_apks(latest_version, apk_path, piko_build.commit)

    write_patches_list(patches)

    previous_patches = (
        github.get_release_asset_json(previous_release, PATCHES_LIST_ASSET)
        if previous_release is not None
        else None
    )

    patch_list = format_patch_list(patches, previous_patches, patch_statuses)

    commit_list = format_commit_list(
        get_piko_commits(previous_release, piko_build.commit)
    )
    additional_notes = commit_list
    additional_notes = f"\n\n{additional_notes}" if additional_notes else ""

    message = f"""Patches applied:
{patch_list}{additional_notes}

Piko source:
[x-lite@{piko_commit}](https://github.com/crimera/piko/commit/{piko_build.commit})
"""

    publish_release(
        release_tag,
        [apk_name, PATCHES_MPP, PATCHES_LIST_ASSET],
        message,
        release_tag,
    )

# 最新のXバージョンを確認し、必要ならビルドを開始する
def main():
    versions = apkmirror.get_versions(
        "https://www.apkmirror.com/apk/x-corp/twitter/"
    )

    piko_build = build_piko_patches()
    latest_version = get_latest_version(versions, piko_build.supported_versions)
    if latest_version is None:
        raise Exception("No X version is supported by the Piko x-lite patches")

    release_tag = f"{latest_version.version}-{piko_build.commit[:7]}"
    last_build_version: github.GithubRelease | None = github.get_last_build_version(REPO)
    if (
        last_build_version is not None
        and last_build_version.tag_name == release_tag
    ):
        print("No new compatible version found")
        return

    print(f"New compatible version found: {latest_version.version}")
    process(latest_version, piko_build, last_build_version)


if __name__ == "__main__":
    main()