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

GRADLE_JVMARGS = "-Xmx4g -XX:MaxMetaspaceSize=1g -Dfile.encoding=UTF-8"

@dataclass(frozen=True)
class PikoBuild:
    commit: str
    supported_versions: frozenset[str]

def log(message: str):
    print(f"[piko] {message}", flush=True)

def get_supported_versions(constants: str) -> frozenset[str]:
    versions = frozenset(
        re.findall(r'AppTarget\(version\s*=\s*"([^"]+)"\)', constants)
    )
    if not versions:
        raise ValueError("Could not find X-Lite compatible app versions in Piko")
    return versions

def pre_build_cleanup(piko_directory: Path) -> None:
    patches_root = piko_directory / "patches/src/main/kotlin/app/crimera/patches"
    morphe_patches_root = piko_directory / "patches/src/main/kotlin/app/morphe/patches"
    revanced_root = piko_directory / "patches/src/main/kotlin/app/revanced"

    removed: list[str] = []

    # 1. Remove non-NewX patch sources (supports both crimera and morphe structures)
    for path in [
        patches_root / "instagram",
        patches_root / "twitter",
        morphe_patches_root / "instagram",
        morphe_patches_root / "twitter",
        revanced_root,
    ]:
        if path.exists():
            shutil.rmtree(path)
            removed.append(str(path.relative_to(piko_directory)))

    # 2. Remove common patches that depend on Instagram
    # DisablePlayStoreUpdates.kt references COMPATIBILITY_INSTAGRAM, so it must be removed
    dependent_common_patches = [
        morphe_patches_root / "all/misc/updates/DisablePlayStoreUpdates.kt",
    ]
    for path in dependent_common_patches:
        if path.exists():
            path.unlink()
            removed.append(str(path.relative_to(piko_directory)))

    # 3. Remove non-NewX extension modules
    for path in [
        piko_directory / "extensions/instagram",
        piko_directory / "extensions/twitter",
    ]:
        if path.exists():
            shutil.rmtree(path)
            removed.append(str(path.relative_to(piko_directory)))

    # 4. Clean up Twitter resources (keep bringbacktwitter)
    twitter_res = piko_directory / "patches/src/main/resources/twitter"
    if twitter_res.exists():
        for item in twitter_res.iterdir():
            if item.name != "bringbacktwitter":
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                removed.append(str(item.relative_to(piko_directory)))

    # 5. Clean up addresources
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

def configure_gradle_memory(piko_directory: Path) -> None:
    gradle_properties = piko_directory / "gradle.properties"
    existing = (
        gradle_properties.read_text(encoding="utf-8")
        if gradle_properties.exists()
        else ""
    )

    kept_lines: list[str] = [
        line
        for line in existing.splitlines()
        if not line.strip().startswith("org.gradle.jvmargs")
    ]

    kept_lines.append(f"org.gradle.jvmargs={GRADLE_JVMARGS}")
    gradle_properties.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")
    log(f"Set org.gradle.jvmargs={GRADLE_JVMARGS}")

def set_project_version(piko_directory: Path, version: str) -> None:
    properties_path = piko_directory / "gradle.properties"
    lines = properties_path.read_text(encoding="utf-8").splitlines(keepends=True)
    version_indexes = [
        index for index, line in enumerate(lines)
        if re.match(r"^\s*version\s*=", line)
    ]
    if len(version_indexes) != 1:
        raise ValueError("Piko gradle.properties must contain exactly one version")

    lines[version_indexes[0]] = f"version = {version}\n"
    properties_path.write_text("".join(lines), encoding="utf-8")
    log(f"Set project version to {version}")

def build_piko_patches(patch_version: str, output: str = None) -> PikoBuild:
    if output is None:
        output = f"bins/patches-{patch_version}.mpp"
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log(f"Output path: {output_path}")

    if patch_version.startswith("v"):
        raise ValueError("patch_version must not include the leading v")

    with tempfile.TemporaryDirectory(prefix="piko-") as temporary_directory:
        piko_directory = Path(temporary_directory) / "piko"

        log(f"Cloning {PIKO_REPO} ({PIKO_BRANCH})...")
        subprocess.run(
            [
                "git", "clone", "--depth", "1", "--branch", PIKO_BRANCH,
                PIKO_REPOSITORY, str(piko_directory),
            ],
            check=True,
        )
        log("Clone complete")

        log("Reading supported versions...")
        supported_versions = get_supported_versions(
            (piko_directory / XLITE_CONSTANTS).read_text()
        )
        log(f"Supported versions ({len(supported_versions)}): {sorted(supported_versions)}")

        log("Cleaning non-NewX patches/extensions...")
        pre_build_cleanup(piko_directory)

        log("Configuring Gradle JVM memory...")
        configure_gradle_memory(piko_directory)

        log(f"Setting project version to {patch_version}...")
        set_project_version(piko_directory, patch_version)

        log("Running gradle clean buildAndroid (may take several minutes)...")
        subprocess.run(
            ["./gradlew", "clean", "buildAndroid"],
            cwd=piko_directory,
            env=os.environ.copy(),
            check=True,
        )
        log("Gradle build complete")

        log("Locating .mpp artifact...")
        artifact = piko_directory / "patches" / "build" / "libs" / f"patches-{patch_version}.mpp"
        if not artifact.is_file():
            raise FileNotFoundError(f"Piko did not produce the expected artifact: {artifact.name}")
        log(f"Found: {artifact.name}")

        shutil.copy2(artifact, output_path)
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