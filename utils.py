import os
import re
import subprocess
import sys

import requests

# cloudscraperインスタンスをキャッシュするグローバル変数
_scraper = None


def github_api_headers() -> dict[str, str]:
    # GitHub APIリクエスト用のヘッダー（トークンがあれば認証付き）を生成する
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def get_scraper():
    # cloudscraperのシングルトンインスタンスを取得（初回のみ生成）
    global _scraper
    if _scraper is None:
        import cloudscraper
        _scraper = cloudscraper.create_scraper()
        _scraper.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        })
    return _scraper


def panic(message: str):
    # エラーメッセージを標準エラーに出力して終了する
    print(message, file=sys.stderr)
    exit(1)


def download(link, out, headers=None, use_scraper=False):
    # ファイルをダウンロードして保存する（既存ファイルがある場合はスキップ）
    dir_name = os.path.dirname(out)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    if os.path.exists(out):
        print(f"{out} already exists skipping download")
        return

    if use_scraper:
        print(f"Downloading with scraper: {link}")

    session = get_scraper() if use_scraper else requests

    # ストリーミングで大きなファイルをダウンロードする
    with session.get(link, stream=True, headers=headers) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)


def run_command(command: list[str]):
    # シェルコマンドを実行し、失敗時は標準出力/エラーを表示して終了する
    cmd = subprocess.run(command, capture_output=True, shell=True)

    try:
        cmd.check_returncode()
    except subprocess.CalledProcessError:
        print(cmd.stdout)
        print(cmd.stderr)
        exit(1)


def patch_apk(
    cli: str,
    patches: str,
    apk: str,
    includes: list[str] | None = None,
    excludes: list[str] | None = None,
    out: str | None = None,
    minimum_patches: int | None = None,        # 後方互換のため残すが使用しない
    continue_on_error: bool = False,
) -> dict[str, bool]:
    """
    Morphe CLIを使ってAPKにパッチを適用し、パッチごとの成否を返す。

    continue_on_error=True の場合、失敗したパッチがあっても処理を継続する。
    戻り値は、includes で指定した各パッチ名と成否 (True=成功, False=失敗) の辞書。
    """
    command = [
        "java",
        "-jar",
        cli,
        "patch",
        "-p",
        patches,
        # 再インストール不要な固定キーストアを使用
        "--keystore",
        "ks.keystore",
        "--keystore-entry-password",
        "123456789",
        "--keystore-password",
        "123456789",
        "--signer",
        "jhc",
        "--keystore-entry-alias",
        "jhc",
        # 古いXバージョンへのパッチ適用を強制
        "--force",
        # 強制互換時にデフォルトパッチを無効化
        "--exclusive",
    ]

    # 失敗しても続行するオプション
    if continue_on_error:
        command.append("--continue-on-error")

    if includes is not None:
        for i in includes:
            command.append("-e")
            command.append(i)

    if excludes is not None:
        for e in excludes:
            command.append("-d")
            command.append(e)

    if out is not None:
        command.extend(["--out", out])

    command.append(apk)

    result = subprocess.run(command, text=True, capture_output=True)
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)

    # fatal error の場合は終了
    if result.returncode != 0:
        result.check_returncode()

    # パッチごとの成否を解析
    output = result.stdout + result.stderr
    statuses: dict[str, bool] = {}

    if includes:
        # "FAILED: パッチ名" の形式を検出
        failed = set(
            re.findall(
                r"FAILED:\s*(.+?)\s*$",
                output,
                re.MULTILINE | re.IGNORECASE,
            )
        )
        for patch_name in includes:
            statuses[patch_name] = patch_name not in failed

    if out is not None and not os.path.exists(out):
        raise FileNotFoundError(f"Morphe did not create the expected output: {out}")

    return statuses


def publish_release(tag: str, files: list[str], message: str, title = ""):
    # GitHub CLIでリリースを作成し、指定のアセットをアップロードする
    key = os.environ.get("GITHUB_TOKEN")
    if key is None:
        raise Exception("GITHUB_TOKEN is not set")

    command = ["gh", "release", "create", "--latest", tag, "--notes", message, "--title", title]

    if len(files) == 0:
        raise Exception("Files should have atleast one item")

    for file in files:
        command.append(file)

    subprocess.run(command, env=os.environ.copy()).check_returncode()