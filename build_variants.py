import re
import subprocess

from apkmirror import Version
from utils import patch_apk

XLITE_PATCH_NAME = re.compile(r"^Name:\s*(NewX:\s*.+?)\s*$", re.MULTILINE)


# Print a prefixed progress message for the APK stage
def log(message: str):
    print(f"[apk] {message}", flush=True)


# List NewX patch names from a .mpp via Morphe CLI
def get_xlite_patches(cli: str, patches: str) -> list[str]:
    log(f"Listing patches from {patches}...")
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
    log(f"Found {len(includes)} patches")
    return includes


# Apply NewX patches to the APK and return patch names and statuses
def build_apks(
    latest_version: Version,
    apk: str,
    piko_commit: str,
) -> tuple[list[str], dict[str, bool]]:
    patches = "bins/patches.mpp"
    cli = "bins/morphe-cli.jar"

    includes = get_xlite_patches(cli, patches)

    out_apk = f"piko-lite-v{latest_version.version}-{piko_commit[:7]}.apk"
    log(f"Patching {apk} -> {out_apk} (may take 5-10 minutes)...")

    patch_statuses = patch_apk(
        cli,
        patches,
        apk,
        includes=includes,
        excludes=[],
        out=out_apk,
        continue_on_error=True,
    )

    succeeded = sum(1 for v in patch_statuses.values() if v)
    failed = len(patch_statuses) - succeeded
    log(f"Patching complete: {succeeded} succeeded, {failed} failed")

    return includes, patch_statuses