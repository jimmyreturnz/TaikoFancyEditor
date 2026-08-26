"""Check GitHub Releases for a newer Taiko Fancy Arranger and fetch it.

Everything above the "Qt layer" banner is plain stdlib so it can be tested
without a display or a network: the HTTP call is a single injectable `opener`
argument, and every network path returns None rather than raising into the
caller.

Update strategy is deliberately the non-destructive one: download, verify the
SHA-256 against the release's own SHA256SUMS.txt, extract to a *sibling*
folder, and show it in Explorer. The running portable folder is never touched,
so a failed update cannot leave the user without a working copy. A
self-replacing update would have to hand the final swap to a detached helper
because Windows locks the running .exe -- more moving parts than this app's
release cadence justifies.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)

RELEASES_API = "https://api.github.com/repos/jimmyreturnz/TaikoFancyEditor/releases/latest"
RELEASE_PAGE = "https://github.com/jimmyreturnz/TaikoFancyEditor/releases/latest"
PORTABLE_ASSET = "TaikoFancyArranger-Windows-x64.zip"
CHECKSUM_ASSET = "SHA256SUMS.txt"
# GitHub answers an API request without a User-Agent with 403.
USER_AGENT = "TaikoFancyArranger-Updater"
TIMEOUT_SECONDS = 10

SETTING_CHECK_ON_STARTUP = "updates/check_on_startup"
SETTING_SKIPPED_TAG = "updates/skipped_tag"


# --------------------------------------------------------------------------
# Version
# --------------------------------------------------------------------------

def resource_roots() -> list[Path]:
    """Trusted application roots, never the working directory (see gui.py)."""
    roots = [Path(__file__).resolve().parent]
    if hasattr(sys, "_MEIPASS"):
        roots.insert(0, Path(sys._MEIPASS).resolve())
    return roots


def local_version() -> str:
    """The VERSION file bundled next to the executable, or "" if unreadable."""
    for root in resource_roots():
        candidate = root / "VERSION"
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8").strip()
        except OSError as error:
            LOGGER.warning("Could not read %s: %s", candidate, error)
    return ""


def version_tuple(text: str) -> tuple[int, ...]:
    """Parse "v3.10.0" into (3, 10, 0). Unparseable input yields ()."""
    cleaned = str(text or "").strip().lstrip("vV").split("-")[0].split("+")[0]
    parts: list[int] = []
    for chunk in cleaned.split("."):
        digits = "".join(character for character in chunk if character.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    """Numeric comparison, so 3.10.0 beats 3.9.0 where a string compare would not."""
    right, left = version_tuple(remote), version_tuple(local)
    if not right or not left:
        # An unreadable version on either side is not grounds for prompting.
        return False
    width = max(len(right), len(left))
    return right + (0,) * (width - len(right)) > left + (0,) * (width - len(left))


# --------------------------------------------------------------------------
# Release lookup
# --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    tag: str
    version: str
    notes: str
    page_url: str
    zip_url: str
    checksums_url: str


class UpdateError(RuntimeError):
    """A download or verification step failed in a way worth showing the user."""


def _asset_url(payload: dict, name: str) -> str:
    for asset in payload.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name") == name:
            return str(asset.get("browser_download_url") or "")
    return ""


def parse_release(payload: object) -> ReleaseInfo | None:
    """Turn the API payload into a ReleaseInfo, or None if it is not usable."""
    if not isinstance(payload, dict):
        return None
    tag = str(payload.get("tag_name") or "").strip()
    if not version_tuple(tag):
        return None
    return ReleaseInfo(
        tag=tag,
        version=".".join(str(part) for part in version_tuple(tag)),
        notes=str(payload.get("body") or "").strip(),
        page_url=str(payload.get("html_url") or RELEASE_PAGE),
        zip_url=_asset_url(payload, PORTABLE_ASSET),
        checksums_url=_asset_url(payload, CHECKSUM_ASSET),
    )


def _request(url: str) -> urllib.request.Request:
    if not url.lower().startswith("https://"):
        raise UpdateError(f"Refusing a non-HTTPS update URL: {url}")
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def fetch_latest_release(opener=urllib.request.urlopen, url: str = RELEASES_API) -> ReleaseInfo | None:
    """GET the latest release. Every failure is logged and returns None.

    No network, DNS failure, HTTP 403 rate limit, truncated body, HTML error
    page instead of JSON -- an update check must never be the reason the app
    misbehaves, so all of it lands in the same quiet branch.
    """
    try:
        with opener(_request(url), timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, UpdateError) as error:
        LOGGER.info("Update check failed: %s", error)
        return None
    return parse_release(payload)


def check_for_update(current: str | None = None, opener=urllib.request.urlopen) -> ReleaseInfo | None:
    """The release to offer, or None when up to date / unreachable."""
    release = fetch_latest_release(opener)
    if release is None:
        return None
    return release if is_newer(release.tag, current if current is not None else local_version()) else None


# --------------------------------------------------------------------------
# Download and verification
# --------------------------------------------------------------------------

def expected_digest(checksums: str, filename: str) -> str | None:
    """Pull one "<hex>  <name>" line out of a SHA256SUMS.txt body."""
    for line in checksums.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[-1].strip("*") == filename and len(fields[0]) == 64:
            return fields[0].lower()
    return None


def download(url: str, destination: Path, progress=None, opener=urllib.request.urlopen) -> str:
    """Stream url into destination and return the SHA-256 hex digest of what landed."""
    digest = hashlib.sha256()
    with opener(_request(url), timeout=TIMEOUT_SECONDS) as response:
        try:
            total = int(response.headers.get("Content-Length") or 0)
        except (AttributeError, ValueError):
            total = 0
        read = 0
        with destination.open("wb") as handle:
            while True:
                chunk = response.read(262144)
                if not chunk:
                    break
                handle.write(chunk)
                digest.update(chunk)
                read += len(chunk)
                if progress is not None:
                    progress(int(read * 100 / total) if total else -1)
    return digest.hexdigest()


def verify_digest(actual: str, expected: str | None) -> bool:
    return bool(expected) and str(actual).lower() == str(expected).lower()


def application_folder() -> Path:
    """The portable folder the app is running from (repo root when run from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def free_folder(parent: Path, name: str) -> Path:
    """`parent/name`, suffixed -2, -3 ... rather than overwriting anything."""
    candidate = parent / name
    counter = 2
    while candidate.exists():
        candidate = parent / f"{name}-{counter}"
        counter += 1
    return candidate


def install_beside(release: ReleaseInfo, progress=None, opener=urllib.request.urlopen) -> Path:
    """Download, verify, and extract the release into a new sibling folder.

    Raises UpdateError before anything is written outside the temp directory if
    the ZIP does not match the digest published in the release's SHA256SUMS.txt.
    """
    if not release.zip_url or not release.checksums_url:
        raise UpdateError("The release does not carry the portable Windows assets.")

    with tempfile.TemporaryDirectory(prefix="tfa-update-") as work:
        archive = Path(work) / PORTABLE_ASSET
        actual = download(release.zip_url, archive, progress, opener)

        with opener(_request(release.checksums_url), timeout=TIMEOUT_SECONDS) as response:
            checksums = response.read().decode("utf-8", "replace")

        if not verify_digest(actual, expected_digest(checksums, PORTABLE_ASSET)):
            raise UpdateError("The download does not match the checksum published with the release.")

        target = free_folder(application_folder().parent, f"TaikoFancyArranger-{release.version}")
        target.mkdir(parents=True)
        # extractall sanitises absolute paths and .. members itself, so the
        # archive cannot reach outside target.
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(target)
    return target


# --------------------------------------------------------------------------
# Qt layer
# --------------------------------------------------------------------------

from PySide6.QtCore import QThread, QUrl, Qt, Signal  # noqa: E402
from PySide6.QtGui import QDesktopServices  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from i18n import tr  # noqa: E402


class CheckThread(QThread):
    """Runs the network check off the GUI thread so startup never waits on it.

    `reachable` is separate from the release, because "you are up to date" and
    "GitHub could not be reached" are the same None to check_for_update and
    must not be the same message to the user.
    """

    result = Signal(object, bool)  # ReleaseInfo | None, reachable

    def run(self) -> None:  # noqa: D102
        release = fetch_latest_release()
        if release is None:
            self.result.emit(None, False)
            return
        self.result.emit(release if is_newer(release.tag, local_version()) else None, True)


class DownloadThread(QThread):
    """Download + checksum + extract, off the GUI thread."""

    progressed = Signal(int)
    finished_with = Signal(object, str)  # Path | None, error message

    def __init__(self, release: ReleaseInfo, parent=None) -> None:
        super().__init__(parent)
        self._release = release

    def run(self) -> None:  # noqa: D102
        try:
            self.finished_with.emit(install_beside(self._release, self.progressed.emit), "")
        except (UpdateError, urllib.error.URLError, OSError, zipfile.BadZipFile) as error:
            LOGGER.warning("Update download failed: %s", error)
            self.finished_with.emit(None, str(error))


class UpdateDialog(QDialog):
    """Offers the new release: download it, read it online, or skip the version."""

    # QDialog.Rejected is 0 and Accepted is 1; a third code says "skip this
    # version" without a fourth signal.
    SKIPPED = 2

    def __init__(self, release: ReleaseInfo, current: str, parent=None) -> None:
        super().__init__(parent)
        self._release = release
        self._download: DownloadThread | None = None
        self.setWindowTitle(tr("MainWindow", "Update available"))
        # main() sets the app-wide window icon from application_icon(); reusing
        # it here avoids importing gui, which imports this module.
        icon = QApplication.windowIcon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.resize(520, 420)

        layout = QVBoxLayout(self)
        headline = QLabel(tr("MainWindow", "Taiko Fancy Arranger {0} is available.").format(release.version))
        headline.setStyleSheet("font-weight: 600;")
        layout.addWidget(headline)
        layout.addWidget(QLabel(tr("MainWindow", "You are running version {0}.").format(current or "?")))

        notes = QTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText(release.notes or tr("MainWindow", "No release notes were published."))
        layout.addWidget(notes, 1)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        self.skip_button = QPushButton(tr("MainWindow", "Skip This Version"))
        self.skip_button.clicked.connect(lambda: self.done(self.SKIPPED))
        buttons.addWidget(self.skip_button)
        buttons.addStretch(1)
        self.page_button = QPushButton(tr("MainWindow", "Open Release Page"))
        self.page_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(release.page_url)))
        buttons.addWidget(self.page_button)
        self.later_button = QPushButton(tr("MainWindow", "Later"))
        self.later_button.clicked.connect(self.reject)
        buttons.addWidget(self.later_button)
        self.download_button = QPushButton(tr("MainWindow", "Download Update"))
        self.download_button.setDefault(True)
        self.download_button.clicked.connect(self._start_download)
        self.download_button.setEnabled(bool(release.zip_url and release.checksums_url))
        buttons.addWidget(self.download_button)
        layout.addLayout(buttons)

    def skipped(self) -> bool:
        return self.result() == self.SKIPPED

    def reject(self) -> None:
        """Refuse Esc / the window's X while a download is in flight.

        exec() returning drops the last reference to this dialog, which would
        take the still-running DownloadThread down with it. QDialog's own
        closeEvent routes here too, so this one override covers both.
        """
        if self._download is not None and self._download.isRunning():
            return
        super().reject()

    def _start_download(self) -> None:
        self.download_button.setEnabled(False)
        self.skip_button.setEnabled(False)
        self.later_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self._download = DownloadThread(self._release, self)
        self._download.progressed.connect(self._on_progress)
        self._download.finished_with.connect(self._on_finished)
        self._download.start()

    def _on_progress(self, percent: int) -> None:
        if percent < 0:
            self.progress.setRange(0, 0)
            return
        self.progress.setRange(0, 100)
        self.progress.setValue(percent)

    def _on_finished(self, folder: object, error: str) -> None:
        self.progress.setVisible(False)
        self.skip_button.setEnabled(True)
        self.later_button.setEnabled(True)
        if folder is None:
            self.download_button.setEnabled(True)
            QMessageBox.warning(
                self,
                tr("MainWindow", "Update failed"),
                tr("MainWindow", "The update could not be installed. Your current copy was not changed.")
                + "\n\n"
                + str(error),
            )
            return
        QMessageBox.information(
            self,
            tr("MainWindow", "Update downloaded"),
            tr(
                "MainWindow",
                "The new version was extracted to:\n{0}\n\nClose Taiko Fancy Arranger and start "
                "TaikoFancyArranger.exe from that folder. Your current copy was left untouched.",
            ).format(folder),
        )
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        self.accept()


def present_update(release: ReleaseInfo, settings, parent=None) -> None:
    """Show the offer and remember a skipped tag so it does not nag every launch."""
    dialog = UpdateDialog(release, local_version(), parent)
    dialog.exec()
    if dialog.skipped():
        settings.set_value(SETTING_SKIPPED_TAG, release.tag)
        settings.sync()


def _alive(widget) -> bool:
    """False once Qt has deleted the C++ side of a widget out from under us."""
    try:
        widget.isVisible()
    except RuntimeError:
        return False
    return True


def check_now(parent, settings) -> CheckThread:
    """Manual check: always reports a result, and ignores a previously skipped tag.

    The thread is parented to the application, not to `parent`: the Settings
    dialog that starts the check can be closed while the request is in flight,
    and Qt destroys a still-running QThread along with its parent.
    """
    parent.setCursor(Qt.BusyCursor)

    def finished(release: object, reachable: bool) -> None:
        owner = parent if _alive(parent) else QApplication.activeWindow()
        if owner is parent:
            parent.unsetCursor()
        if isinstance(release, ReleaseInfo):
            present_update(release, settings, owner)
        elif reachable:
            QMessageBox.information(
                owner,
                tr("MainWindow", "Check for updates"),
                tr("MainWindow", "You are running the latest version ({0}).").format(local_version() or "?"),
            )
        else:
            QMessageBox.information(
                owner,
                tr("MainWindow", "Check for updates"),
                tr("MainWindow", "Could not reach GitHub to check for updates. Please try again later."),
            )

    thread = CheckThread(QApplication.instance())
    thread.result.connect(finished)
    thread.start()
    return thread
