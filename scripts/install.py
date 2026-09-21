"""Installs everything a fresh clone needs beyond the Python dependencies.

gitleaks is a Go binary and isn't published to PyPI, so uv.lock can't pin it.
Installing it through a package manager leaves every machine on a different
version, so the secret scan behaves differently per developer and in CI. This
module downloads one pinned release, verifies it against the checksums file
published with that release, and extracts it into .tools/ inside the repo.

Bump GITLEAKS_VERSION to move everyone at once. The next install overwrites
whatever is already there.

Run with: uv run install
"""

import hashlib
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import click
from loguru import logger
from pydantic import BaseModel

GITLEAKS_VERSION = "8.30.1"
GITLEAKS_RELEASES = "https://github.com/gitleaks/gitleaks/releases/download"

PROJECT_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_DIR / ".tools"


class PlatformTarget(BaseModel):
    """The release asset and binary name for one operating system."""

    asset_suffix: str
    archive_extension: str
    binary_name: str


# Keyed by the values platform.system() and platform.machine() return, both
# lowercased. gitleaks names its 64-bit assets "x64" rather than "amd64".
PLATFORM_TARGETS: dict[tuple[str, str], PlatformTarget] = {
    ("darwin", "arm64"): PlatformTarget(
        asset_suffix="darwin_arm64", archive_extension="tar.gz", binary_name="gitleaks"
    ),
    ("darwin", "x86_64"): PlatformTarget(
        asset_suffix="darwin_x64", archive_extension="tar.gz", binary_name="gitleaks"
    ),
    ("linux", "aarch64"): PlatformTarget(
        asset_suffix="linux_arm64", archive_extension="tar.gz", binary_name="gitleaks"
    ),
    ("linux", "arm64"): PlatformTarget(
        asset_suffix="linux_arm64", archive_extension="tar.gz", binary_name="gitleaks"
    ),
    ("linux", "x86_64"): PlatformTarget(
        asset_suffix="linux_x64", archive_extension="tar.gz", binary_name="gitleaks"
    ),
    ("windows", "arm64"): PlatformTarget(
        asset_suffix="windows_arm64",
        archive_extension="zip",
        binary_name="gitleaks.exe",
    ),
    ("windows", "amd64"): PlatformTarget(
        asset_suffix="windows_x64", archive_extension="zip", binary_name="gitleaks.exe"
    ),
    ("windows", "x86_64"): PlatformTarget(
        asset_suffix="windows_x64", archive_extension="zip", binary_name="gitleaks.exe"
    ),
}


def _platform_target() -> PlatformTarget:
    """Return the release asset details for the host, or fail with what's supported."""
    key = (platform.system().lower(), platform.machine().lower())
    target = PLATFORM_TARGETS.get(key)
    if not target:
        supported = ", ".join(f"{s}/{m}" for s, m in sorted(PLATFORM_TARGETS))
        raise click.ClickException(
            f"No pinned gitleaks build for {key[0]}/{key[1]}. Supported: {supported}"
        )
    return target


def gitleaks_binary() -> Path:
    """Path to the pinned gitleaks binary. build.py calls this."""
    return TOOLS_DIR / _platform_target().binary_name


class ToolRequest(BaseModel):
    """One pinned binary to install."""

    name: str
    version: str
    archive_name: str
    archive_url: str
    checksums_url: str
    binary_name: str
    is_zip: bool


class ToolResponse(BaseModel):
    """Where the binary landed, and whether this run replaced it."""

    name: str
    version: str
    path: Path
    replaced: bool


def _gitleaks_request() -> ToolRequest:
    target = _platform_target()
    archive_name = (
        f"gitleaks_{GITLEAKS_VERSION}_{target.asset_suffix}.{target.archive_extension}"
    )
    base = f"{GITLEAKS_RELEASES}/v{GITLEAKS_VERSION}"
    return ToolRequest(
        name="gitleaks",
        version=GITLEAKS_VERSION,
        archive_name=archive_name,
        archive_url=f"{base}/{archive_name}",
        checksums_url=f"{base}/gitleaks_{GITLEAKS_VERSION}_checksums.txt",
        binary_name=target.binary_name,
        is_zip=target.archive_extension == "zip",
    )


class ToolInstaller:
    """Downloads a pinned binary, verifies its checksum, and extracts it."""

    def __init__(self, tools_dir: Path) -> None:
        self.tools_dir = tools_dir

    def install(self, request: ToolRequest) -> ToolResponse:
        """Download and extract the pinned binary, overwriting any existing copy."""
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        target = self.tools_dir / request.binary_name
        replaced = target.exists()

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / request.archive_name

            logger.info(f"Downloading {request.name} {request.version}")
            archive.write_bytes(self._fetch(request.archive_url))

            expected = self._published_digest(request)
            actual = hashlib.sha256(archive.read_bytes()).hexdigest()
            if actual != expected:
                raise click.ClickException(
                    f"Checksum mismatch for {request.archive_name}.\n"
                    f"  expected {expected}\n"
                    f"  actual   {actual}\n"
                    "Refusing to install a binary that doesn't match the published checksum."
                )
            logger.info(f"Checksum verified ({actual[:16]}...)")

            self._extract(archive, request, target)

        if platform.system().lower() != "windows":
            target.chmod(
                target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )

        (self.tools_dir / f".{request.name}-version").write_text(f"{request.version}\n")
        return ToolResponse(
            name=request.name, version=request.version, path=target, replaced=replaced
        )

    def _extract(self, archive: Path, request: ToolRequest, target: Path) -> None:
        """Pull the single binary out of the archive."""
        if request.is_zip:
            with zipfile.ZipFile(archive) as zf:
                target.write_bytes(zf.read(request.binary_name))
            return

        with tarfile.open(archive) as tar:
            member = tar.extractfile(request.binary_name)
            if not member:
                raise click.ClickException(
                    f"{request.binary_name} missing from {request.archive_name}"
                )
            target.write_bytes(member.read())

    def _published_digest(self, request: ToolRequest) -> str:
        """Read the expected sha256 from the checksums file for this release."""
        text = self._fetch(request.checksums_url).decode()
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == request.archive_name:
                return parts[0]
        raise click.ClickException(
            f"{request.archive_name} isn't listed in the published checksums file"
        )

    def _fetch(self, url: str) -> bytes:
        if not url.startswith("https://"):
            raise click.ClickException(f"Refusing to fetch over a non-HTTPS URL: {url}")
        with urllib.request.urlopen(url) as response:
            return response.read()


class NodeInstaller:
    """Installs the Node dependencies, which is Prettier and nothing else."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir

    def install(self) -> bool:
        """Run npm ci. Returns False when npm isn't on PATH."""
        if not shutil.which("npm"):
            logger.warning(
                "npm not found, skipping Prettier. Install Node 20 or later."
            )
            return False

        logger.info("Installing Node dependencies")
        result = subprocess.run(["npm", "ci", "--silent"], cwd=self.project_dir)
        if result.returncode != 0:
            raise click.ClickException("npm ci failed")
        return True


@click.command()
def main() -> None:
    """Install the Node dependencies and the pinned binaries uv can't manage."""
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    NodeInstaller(PROJECT_DIR).install()

    response = ToolInstaller(TOOLS_DIR).install(_gitleaks_request())
    verb = "Replaced with" if response.replaced else "Installed"
    logger.success(f"{verb} {response.name} {response.version} at {response.path}")


if __name__ == "__main__":
    main()
