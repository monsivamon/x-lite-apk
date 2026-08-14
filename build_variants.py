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


def build_apks(
    latest_version: Version,
    apk: str,
    piko_commit: str,
) -> tuple[list[str], dict[str, bool]]:
    """
    X-LiteパッチとTwitter復活パッチを適用してAPKをビルドする。

    戻り値:
        - 適用対象パッチ名のリスト
        - 各パッチの成否を表す dict（True = 成功, False = 失敗）
    """
    patches = "bins/patches.mpp"
    cli = "bins/morphe-cli.jar"

    includes = get_xlite_patches(cli, patches)
    includes.append(BRING_BACK_TWITTER_PATCH)

    # パッチを適用する
    # 一部パッチが失敗しても続行し、成否を返す
    patch_statuses = patch_apk(
        cli,
        patches,
        apk,
        includes=includes,
        excludes=[],
        out=f"piko-lite-v{latest_version.version}-{piko_commit[:7]}.apk",
        continue_on_error=True,
    )

    return includes, patch_statuses