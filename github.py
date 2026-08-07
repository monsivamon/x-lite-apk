from dataclasses import dataclass
from urllib.parse import quote

import requests

from utils import github_api_headers

REQUEST_TIMEOUT_SECONDS = 30


@dataclass
class Asset:
    # リリースアセットのダウンロードURLとファイル名を保持
    browser_download_url: str
    name: str


@dataclass
class GithubRelease:
    # GitHubリリースの基本情報とアセット一覧を保持
    tag_name: str
    html_url: str
    assets: list[Asset]


@dataclass(frozen=True)
class GithubCommit:
    # コミットのSHA、URL、件名（サブジェクト）を保持
    sha: str
    html_url: str
    subject: str


def _to_github_release(release) -> GithubRelease:
    # APIレスポンスの辞書からGithubReleaseオブジェクトに変換
    assets = [
        Asset(browser_download_url=asset["browser_download_url"], name=asset["name"])
        for asset in release["assets"]
    ]

    return GithubRelease(
        tag_name=release["tag_name"], html_url=release["html_url"], assets=assets
    )


def _fetch_release(url: str) -> GithubRelease | None:
    # 指定URLからリリース情報を取得し、存在しない場合はNoneを返す
    response = requests.get(
        url,
        headers=github_api_headers(),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    if response.status_code == 404:
        return None

    response.raise_for_status()
    return _to_github_release(response.json())


def get_release_by_tag(repo_url: str, tag: str) -> GithubRelease | None:
    # タグ名をエンコードしてGitHub Releases APIから特定リリースを取得
    encoded_tag = quote(tag, safe="")
    url = f"https://api.github.com/repos/{repo_url}/releases/tags/{encoded_tag}"
    return _fetch_release(url)


def get_last_build_version(repo_url: str) -> GithubRelease | None:
    # 最新リリースを取得する（/latestエンドポイントを使用）
    url = f"https://api.github.com/repos/{repo_url}/releases/latest"
    return _fetch_release(url)


def get_commits_between(
    repo_url: str, base: str, head: str
) -> list[GithubCommit] | None:
    # ベースコミットとヘッドコミットの間の全コミットを取得してリスト化
    url = f"https://api.github.com/repos/{repo_url}/compare/{base}...{head}"
    response = requests.get(
        url,
        headers=github_api_headers(),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("GitHub compare response was not an object")

    commits = payload.get("commits", [])
    if not isinstance(commits, list):
        raise ValueError("GitHub compare response did not contain a commit list")

    result: list[GithubCommit] = []
    for commit in commits:
        # 各コミットから必要な情報だけを取り出し、件名は最初の行のみ使用
        if not isinstance(commit, dict):
            continue
        commit_details = commit.get("commit", {})
        if not isinstance(commit_details, dict):
            continue
        message = commit_details.get("message", "")
        sha = commit.get("sha")
        html_url = commit.get("html_url")
        if not isinstance(message, str):
            continue
        if not isinstance(sha, str) or not isinstance(html_url, str):
            continue

        result.append(
            GithubCommit(
                sha=sha,
                html_url=html_url,
                subject=message.partition("\n")[0],
            )
        )

    return result


def get_release_asset_json(
    release: GithubRelease, asset_name: str
) -> list[str] | None:
    # 指定リリースの指定アセットをダウンロードし、JSONから文字列リストを返す
    asset = next((asset for asset in release.assets if asset.name == asset_name), None)
    if asset is None:
        return None

    # ブラウザダウンロードURLのため、GitHubトークンは送らず、Acceptヘッダのみでリクエスト
    response = requests.get(
        asset.browser_download_url,
        headers={"Accept": "application/octet-stream"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    contents = response.json()
    if not isinstance(contents, list) or not all(
        isinstance(patch, str) for patch in contents
    ):
        raise ValueError(f"Release asset {asset_name} must contain a list of strings")

    return contents