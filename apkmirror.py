from dataclasses import dataclass
import re
from typing import cast
from bs4 import BeautifulSoup, Tag
from utils import download, get_scraper


# Holds an APK version string and its detail page URL
@dataclass
class Version:
    version: str
    link: str


# Holds APK variant info: bundle flag, download link, architecture,
# parsed minimum Android version, its raw display text, and screen DPI text
@dataclass
class Variant:
    is_bundle: bool
    link: str
    architecture: str
    min_android: tuple[int, int] = (999, 0)
    min_android_text: str = ""
    dpi_text: str = ""


# Holds app name and link (currently unused placeholder)
@dataclass
class App:
    name: str
    link: str


# Raised when a target element is not found during scraping
class FailedToFindElement(Exception):
    def __init__(self, message=None) -> None:
        self.message = (
            f"Failed to find element{' ' + message if message is not None else ''}"
        )
        super().__init__(self.message)


# Raised when page fetching fails
class FailedToFetch(Exception):
    def __init__(self, url=None) -> None:
        self.message = f"Failed to fetch{' ' + url if url is not None else ''}"
        super().__init__(self.message)


# Parse 'Android 12L+' / 'Android 9.0+' / 'Android 10+' into (major, minor).
# Unknown or 'Varies with device' -> (999, 0) so it sorts last.
def parse_min_android(text: str | None) -> tuple[int, int]:
    if not text:
        return (999, 0)
    match = re.search(r"Android\s+(\d+)(?:\.(\d+))?\s*(L)?", text, re.IGNORECASE)
    if not match:
        return (999, 0)
    major = int(match.group(1))
    minor = int(match.group(2)) if match.group(2) else 0
    if match.group(3) and match.group(3).upper() == "L":
        minor = max(minor, 1)
    return (major, minor)


# Extract version list from the given APKMirror page
def get_versions(url: str) -> list[Version]:
    response = get_scraper().get(url)
    if response.status_code != 200:
        raise FailedToFetch(f"{url}: {response.status_code}")

    bs4 = BeautifulSoup(response.text, "html.parser")
    versions = bs4.find("div", attrs={"class": "listWidget"})

    out: list[Version] = []
    if versions is not None:
        for versionRow in cast(Tag, versions).findChildren("div", recursive=False)[1:]:
            if versionRow is None:
                print(f"{versionRow} is None")
                continue

            version = versionRow.find("span", {"class": "infoSlide-value"})
            if version is None:
                continue

            version = version.string.strip()
            link = f"https://www.apkmirror.com/{versionRow.find('a')['href']}"
            out.append(Version(version=version, link=link))

    return out


# Follow download link from variant page and save APK file
def download_apk(variant: Variant, path: str = "big_file.apkm"):
    url = variant.link

    response = get_scraper().get(url)

    if response.status_code != 200:
        raise FailedToFetch(url)

    response_body = BeautifulSoup(response.content, "html.parser")

    downloadButton = response_body.find("a", {"class": "downloadButton"})
    if downloadButton is None:
        raise FailedToFindElement("Download button")

    download_page_link = (
        f"https://www.apkmirror.com/{cast(Tag, downloadButton).attrs['href']}"
    )

    download_page = get_scraper().get(download_page_link)
    if download_page.status_code != 200:
        raise FailedToFetch(download_page_link)

    download_page_body = BeautifulSoup(download_page.content, "html.parser")

    direct_link = download_page_body.find("a", {"rel": "nofollow"})
    if direct_link is None:
        raise FailedToFindElement("download link")

    direct_link_href = cast(Tag, direct_link).attrs["href"]
    direct_link_url = f"https://www.apkmirror.com/{direct_link_href}"
    print(f"Direct link: {direct_link_url}")

    download(
        direct_link_url,
        path,
        use_scraper=True,
        headers={"Referer": download_page_link},
    )


# Fetch available variants from the version-specific page
def get_variants(version: Version) -> list[Variant]:
    url = version.link
    variants_page = get_scraper().get(url)
    if variants_page is None:
        raise FailedToFetch(url)

    variants_page_body = BeautifulSoup(variants_page.content, "html.parser")

    variants_table = variants_page_body.find("div", {"class": "table"})
    if variants_table is None:
        raise FailedToFindElement("variants table")

    variants_table_rows = cast(Tag, variants_table).findChildren(
        "div", recursive=False
    )[1:]

    variants: list[Variant] = []
    for variant_row in variants_table_rows:
        cells = variant_row.findChildren(
            "div", {"class": "table-cell"}, recursive=False
        )
        if len(cells) < 3:
            print("Could not find enough cells")
            continue

        is_bundle_tag = variant_row.find("span", {"class": "apkm-badge"})
        is_bundle = False
        if is_bundle_tag is None:
            print("Failed to find apk-badge")
        else:
            is_bundle = is_bundle_tag.string.strip() == "BUNDLE"

        architecture: str = cells[1].get_text(strip=True)
        min_android_text: str = cells[2].get_text(strip=True)
        min_android = parse_min_android(min_android_text)
        dpi_text: str = cells[3].get_text(strip=True) if len(cells) > 3 else ""

        link_element = variant_row.find("a", {"class": "accent_color"})
        if link_element is None:
            print("Failed to find the link element")
            continue

        link: str = f"https://www.apkmirror.com{link_element.attrs['href']}"
        variants.append(
            Variant(
                is_bundle=is_bundle,
                link=link,
                architecture=architecture,
                min_android=min_android,
                min_android_text=min_android_text,
                dpi_text=dpi_text,
            )
        )

    print(variants)
    return variants