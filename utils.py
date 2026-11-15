import os
import re
import subprocess
import sys
import tempfile

import requests

_scraper = None

# Build headers for GitHub API requests, adding auth if available
def github_api_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

# Hybrid scraper:
#   Strategy 1: APKUpdater UA (APKMirror-permitted)
#   Strategy 2: cloudscraper stealth mode (enable_stealth=True)
#   Strategy 3: curl_cffi Chrome TLS impersonation fallback on 403
class _HybridSession:
    def __init__(self) -> None:
        import cloudscraper

        try:
            self._primary = cloudscraper.create_scraper(enable_stealth=True)
        except TypeError:
            # Older cloudscraper without stealth kwarg
            self._primary = cloudscraper.create_scraper()
        self._primary.headers.update({"User-Agent": "APKUpdater-v3.0.3"})
        self._fallback = None
        self.headers = self._primary.headers
        print("[scraper] primary: cloudscraper (stealth + APKUpdater UA)", flush=True)

    def _get_fallback(self):
        if self._fallback is None:
            from curl_cffi import requests as curl_requests
            self._fallback = curl_requests.Session(impersonate="chrome124")
            print("[scraper] fallback: curl_cffi (Chrome 124 full impersonation)", flush=True)
        return self._fallback

    def get(self, *args, **kwargs):
        r = self._primary.get(*args, **kwargs)
        if r.status_code == 403:
            try:
                r.close()
            except Exception:
                pass
            print("[scraper] primary returned 403, retrying with curl_cffi...", flush=True)
            r = self._get_fallback().get(*args, **kwargs)
        return r

# Return the singleton hybrid scraper instance
def get_scraper():
    global _scraper
    if _scraper is None:
        _scraper = _HybridSession()
    return _scraper

# Print an error message to stderr and exit with code 1
def panic(message: str):
    print(message, file=sys.stderr)
    exit(1)

# Download a file from link to out, optionally via cloudscraper
def download(link, out, headers=None, use_scraper=False):
    dir_name = os.path.dirname(out)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    if os.path.exists(out):
        print(f"{out} already exists skipping download")
        return

    if use_scraper:
        print(f"Downloading with scraper: {link}", flush=True)

    session = get_scraper() if use_scraper else requests

    with session.get(link, stream=True, headers=headers, timeout=30) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

# Run a shell command and exit on failure
def run_command(command: list[str]):
    cmd = subprocess.run(command, capture_output=True, shell=True)

    try:
        cmd.check_returncode()
    except subprocess.CalledProcessError:
        print(cmd.stdout)
        print(cmd.stderr)
        exit(1)

# Apply patches via Morphe CLI and return per-patch success status
def patch_apk(
    cli: str,
    patches: str,
    apk: str,
    includes: list[str] | None = None,
    excludes: list[str] | None = None,
    out: str | None = None,
    minimum_patches: int | None = None,
    continue_on_error: bool = False,
) -> dict[str, bool]:
    command = [
        "java", "-jar", cli, "patch", "-p", patches,
        "--keystore", "ks.keystore",
        "--keystore-entry-password", "123456789",
        "--keystore-password", "123456789",
        "--signer", "jhc",
        "--keystore-entry-alias", "jhc",
        "--force", "--exclusive",
    ]

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

    command.append(os.path.abspath(apk))

    result = subprocess.run(command, text=True, capture_output=True)
    print(result.stdout, end="", flush=True)
    print(result.stderr, end="", file=sys.stderr, flush=True)

    if result.returncode != 0:
        result.check_returncode()

    output = result.stdout + result.stderr
    statuses: dict[str, bool] = {}

    if includes:
        failed = set(
            re.findall(r"FAILED:\s*(.+?)\s*$", output, re.MULTILINE | re.IGNORECASE)
        )
        for patch_name in includes:
            statuses[patch_name] = patch_name not in failed

    if out is not None and not os.path.exists(out):
        raise FileNotFoundError(f"Morphe did not create the expected output: {out}")

    return statuses

# Create a GitHub release via gh CLI and upload assets
def publish_release(tag: str, files: list[str], message: str, title=""):
    key = os.environ.get("GITHUB_TOKEN")
    if key is None:
        raise Exception("GITHUB_TOKEN is not set")

    ref = os.environ.get("GITHUB_REF", "")
    is_prerelease = ref != "refs/heads/master"

    command = ["gh", "release", "create", tag, "--title", title]
    if is_prerelease:
        command.append("--prerelease")
        print(f"Non-master ref ({ref}) -> creating prerelease")
    else:
        command.append("--latest")

    if len(files) == 0:
        raise Exception("Files should have atleast one item")

    # Write notes to a temp file to avoid "Argument list too long" errors
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
        f.write(message)
        notes_file = f.name

    command.extend(["--notes-file", notes_file])

    for file in files:
        command.append(file)

    try:
        subprocess.run(command, env=os.environ.copy()).check_returncode()
    finally:
        os.unlink(notes_file)