import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Pikoリポジトリの情報とブランチ、定数ファイルのパスを定義
PIKO_REPO = "crimera/piko"
PIKO_REPOSITORY = f"https://github.com/{PIKO_REPO}.git"
PIKO_BRANCH = "x-lite"
XLITE_CONSTANTS = (
    "patches/src/main/kotlin/app/crimera/patches/xlite/utils/Constants.kt"
)


@dataclass(frozen=True)
class PikoBuild:
    # Pikoのビルド結果を保持するデータクラス（コミットハッシュと対応バージョン一覧）
    commit: str
    supported_versions: frozenset[str]


def get_supported_versions(constants: str) -> frozenset[str]:
    # Constants.ktからAppTargetのバージョン文字列を抽出し、サポート対象バージョンの集合を返す
    versions = frozenset(
        re.findall(r'AppTarget\(version\s*=\s*"([^"]+)"\)', constants)
    )
    if not versions:
        raise ValueError("Could not find X-Lite compatible app versions in Piko")
    return versions


def build_piko_patches(output: str = "bins/patches.mpp") -> PikoBuild:
    # 出力先ディレクトリを作成（存在しない場合のみ）
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 一時ディレクトリにPikoをクローンしてビルド
    with tempfile.TemporaryDirectory(prefix="piko-") as temporary_directory:
        piko_directory = Path(temporary_directory) / "piko"

        # x-liteブランチを浅くクローン
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

        # サポート対象のXバージョン一覧を取得
        supported_versions = get_supported_versions(
            (piko_directory / XLITE_CONSTANTS).read_text()
        )

        # GradleでAndroid向けパッチをビルド
        subprocess.run(
            ["./gradlew", "clean", "buildAndroid"],
            cwd=piko_directory,
            env=os.environ.copy(),
            check=True,
        )

        # ビルド成果物（.mpp）を検索して出力先へコピー
        artifacts = sorted(
            (piko_directory / "patches" / "build" / "libs").glob("patches-*.mpp")
        )
        if not artifacts:
            raise FileNotFoundError("Piko did not produce a patches .mpp artifact")

        shutil.copy2(artifacts[-1], output_path)

        # ビルドに使用したPikoのコミットハッシュを取得
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=piko_directory,
            check=True,
            capture_output=True,
            text=True,
        )

    # ビルド結果（コミットハッシュと対応バージョン）を返す
    return PikoBuild(commit=commit.stdout.strip(), supported_versions=supported_versions)