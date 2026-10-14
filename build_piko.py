import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

PIKO_REPO = "crimera/piko"
PIKO_REPOSITORY = f"https://github.com/{PIKO_REPO}.git"
PIKO_BRANCH = "x-lite"
XLITE_CONSTANTS = (
    "patches/src/main/kotlin/app/crimera/patches/newx/utils/Constants.kt"
)


# Result of a Piko build: commit SHA and supported X versions
@dataclass(frozen=True)
class PikoBuild:
    commit: str
    supported_versions: frozenset[str]


# Print a prefixed progress message for the Piko stage
def log(message: str):
    print(f"[piko] {message}", flush=True)


# Parse X-Lite compatible app versions from Constants.kt source
def get_supported_versions(constants: str) -> frozenset[str]:
    versions = frozenset(
        re.findall(r'AppTarget\(version\s*=\s*"([^"]+)"\)', constants)
    )
    if not versions:
        raise ValueError("Could not find X-Lite compatible app versions in Piko")
    return versions


# Remove Instagram, Twitter, and legacy patches/extensions before building.
# Ported from crimera/piko-newx build_piko.py so only NewX (X-Lite) patches
# end up in the generated .mpp.
def pre_build_cleanup(piko_directory: Path) -> None:
    patches_root = piko_directory / "patches/src/main/kotlin/app/crimera/patches"
    revanced_root = piko_directory / "patches/src/main/kotlin/app/revanced"

    removed: list[str] = []

    # Non-NewX patch source packages
    for path in [
        patches_root / "instagram",
        patches_root / "twitter",
        revanced_root,
    ]:
        if path.exists():
            shutil.rmtree(path)
            removed.append(str(path.relative_to(piko_directory)))

    # Non-NewX extension modules
    for path in [
        piko_directory / "extensions/instagram",
        piko_directory / "extensions/twitter",
    ]:
        if path.exists():
            shutil.rmtree(path)
            removed.append(str(path.relative_to(piko_directory)))

    # Keep twitter/bringbacktwitter assets for Bring back twitter patch
    twitter_res = piko_directory / "patches/src/main/resources/twitter"
    if twitter_res.exists():
        for item in twitter_res.iterdir():
            if item.name != "bringbacktwitter":
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                removed.append(str(item.relative_to(piko_directory)))

    addresources_dir = piko_directory / "patches/src/main/resources/addresources"
    if addresources_dir.exists():
        for res_name in ("instagram", "twitter"):
            for matched in addresources_dir.glob(f"*/{res_name}"):
                if matched.is_dir():
                    shutil.rmtree(matched)
                    removed.append(str(matched.relative_to(piko_directory)))

    if removed:
        log(f"Removed {len(removed)} non-NewX entries:")
        for entry in removed:
            log(f"  - {entry}")
    else:
        log("pre_build_cleanup: nothing to remove")


# Clone Piko x-lite, filter to NewX patches, build, and copy the .mpp artifact
def build_piko_patches(output: str = "bins/patches.mpp") -> PikoBuild:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log(f"Output path: {output_path}")

    with tempfile.TemporaryDirectory(prefix="piko-") as temporary_directory:
        piko_directory = Path(temporary_directory) / "piko"

        log(f"Cloning {PIKO_REPO} ({PIKO_BRANCH})...")
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                PIKO_BRANCH,
                PIKO_REPOSITORY,
                str(piko_directory),
            ],
            check=True,
        )
        log("Clone complete")

        log("Reading supported versions...")
        supported_versions = get_supported_versions(
            (piko_directory / XLITE_CONSTANTS).read_text()
        )
        log(
            f"Supported versions ({len(supported_versions)}): "
            f"{sorted(supported_versions)}"
        )

        log("Cleaning non-NewX patches/extensions...")
        pre_build_cleanup(piko_directory)

        log("Running gradle clean buildAndroid (may take several minutes)...")
        subprocess.run(
            ["./gradlew", "clean", "buildAndroid"],
            cwd=piko_directory,
            env=os.environ.copy(),
            check=True,
        )
        log("Gradle build complete")

        log("Locating .mpp artifact...")
        artifacts = sorted(
            (piko_directory / "patches" / "build" / "libs").glob("patches-*.mpp")
        )
        if not artifacts:
            raise FileNotFoundError("Piko did not produce a patches .mpp artifact")
        log(f"Found: {artifacts[-1].name}")

        shutil.copy2(artifacts[-1], output_path)
        log(f"Copied to {output_path}")

        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=piko_directory,
            check=True,
            capture_output=True,
            text=True,
        )

    sha = commit.stdout.strip()
    log(f"Piko build done: {sha[:7]}")
    return PikoBuild(commit=sha, supported_versions=supported_versions)