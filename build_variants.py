import re
import subprocess

from apkmirror import Version
from utils import patch_apk

XLITE_PATCH_NAME = re.compile(r"^Name:\s*(X-Lite:\s*.+?)\s*$", re.MULTILINE)
BRING_BACK_TWITTER_PATCH = "Bring back twitter"

# Morphe CLIからX-Liteパッチ名一覧を取得する
def get_xlite_patches(cli: str, patches: str) -> list[str]:
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
    includes = list(dict.fromkeys(XLITE_PATCH_NAME.findall(output)))
    if not includes:
        raise RuntimeError("Morphe returned no X-Lite patches")
    return includes

# APKに対してX-LiteパッチとBring back twitterを適用し、成否を返す
def build_apks(
    latest_version: Version,
    apk: str,
    piko_commit: str,
) -> tuple[list[str], dict[str, bool]]:
    patches = "bins/patches.mpp"
    cli = "bins/morphe-cli.jar"

    includes = get_xlite_patches(cli, patches)
    includes.append(BRING_BACK_TWITTER_PATCH)

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