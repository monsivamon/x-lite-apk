import re
import subprocess

from apkmirror import Version
from utils import patch_apk

# X-Liteパッチ名を抽出する正規表現
XLITE_PATCH_NAME = re.compile(r"^Name:\s*(X-Lite:\s*.+?)\s*$", re.MULTILINE)

# 常に適用するTwitter復活パッチ名
BRING_BACK_TWITTER_PATCH = "Bring back twitter"


def get_xlite_patches(cli: str, patches: str) -> list[str]:
    # Morphe CLIでパッチ一覧を取得し、X-Liteパッチ名を抽出する
    result = subprocess.run(
        [
            "java",
            "-jar",
            cli,
            "list-patches",
            "--patches",
            patches,
            "--with-descriptions=false",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    output = result.stdout + result.stderr
    # 抽出したパッチ名を重複なしでリスト化
    includes = list(dict.fromkeys(XLITE_PATCH_NAME.findall(output)))
    if not includes:
        raise RuntimeError("Morphe returned no X-Lite patches")
    return includes


def build_apks(latest_version: Version, apk: str, piko_commit: str) -> list[str]:
    # X-LiteパッチとTwitter復活パッチを適用してAPKをビルドする
    patches = "bins/patches.mpp"
    cli = "bins/morphe-cli.jar"
    includes = get_xlite_patches(cli, patches)
    includes.append(BRING_BACK_TWITTER_PATCH)

    # APKにパッチを適用し、指定パッチ数がすべて当たっているか検証しながら出力
    patch_apk(
        cli,
        patches,
        apk,
        includes=includes,
        excludes=[],
        out=f"piko-lite-v{latest_version.version}-{piko_commit[:7]}.apk",
        minimum_patches=len(includes),
    )

    return includes