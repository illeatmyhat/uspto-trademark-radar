"""USPTO Open Data Portal client (absorbs the download_trtyrap.py draft).

- Product manifest: GET {API_BASE}/{shortName}
- File download:   GET {API_BASE}/files/{shortName}/{fileName} (follow redirects)
- Auth: x-api-key header.

Errors are split into two families so the update job can fail with a message
that tells a future operator what actually happened (ARCHITECTURE preflight):

- PlatformDriftError: the portal answered but not in the shape we expect
  (product gone, manifest schema changed, filenames unparseable). Human
  attention required; see RUNBOOK.
- TransientError: network/5xx/429 — retrying later is the fix. Downloads
  already retry with backoff before this is raised.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

import requests

from .config import API_BASE
from .filenames import latest_annual_set, parse_daily
from .integrity import sha256_file


@dataclass(frozen=True)
class RemoteFile:
    """One file entry from a product manifest. `size` is USPTO's official
    byte count — the only integrity metadata the manifest exposes (spiked
    2026-07-07: no checksum fields exist).
    """
    name: str
    url: str
    size: int | None


class PlatformDriftError(RuntimeError):
    """USPTO moved/renamed things. Retrying will not help."""


class TransientError(RuntimeError):
    """Network or server trouble. Retrying later should help."""


_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class OdpClient:
    def __init__(self, api_key: str, base: str = API_BASE,
                 max_attempts: int = 5):
        self.base = base.rstrip("/")
        self.max_attempts = max_attempts
        self.session = requests.Session()
        self.session.headers["x-api-key"] = api_key

    def _get(self, url: str, **kw) -> requests.Response:
        """GET with exponential backoff on 429/5xx/connection errors."""
        delay = 2.0
        for attempt in range(1, self.max_attempts + 1):
            try:
                r = self.session.get(url, timeout=kw.pop("timeout", 60), **kw)
            except requests.ConnectionError as e:
                if attempt == self.max_attempts:
                    raise TransientError(f"connection to {url} failed: {e}") from e
            else:
                if r.status_code not in _RETRYABLE_STATUS:
                    return r
                if attempt == self.max_attempts:
                    raise TransientError(
                        f"{url} kept answering HTTP {r.status_code} "
                        f"after {attempt} attempts"
                    )
                retry_after = r.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = max(delay, float(retry_after))
            time.sleep(delay)
            delay = min(delay * 2, 120)
        raise AssertionError("unreachable")

    # -- manifest -------------------------------------------------------------

    def product_manifest(self, short_name: str) -> dict:
        r = self._get(f"{self.base}/{short_name.lower()}")
        if r.status_code == 404:
            raise PlatformDriftError(
                f"product '{short_name}' not found on ODP (HTTP 404). USPTO "
                "may have renamed or moved it — locate the 'Trademark Full "
                "Text XML Data (No Images)' products by TITLE on "
                "data.uspto.gov and update config.py. See RUNBOOK §2."
            )
        if r.status_code in (401, 403):
            raise PlatformDriftError(
                f"ODP rejected the API key (HTTP {r.status_code}). Keys are "
                "personal and ID.me-tied; mint a fresh one — RUNBOOK §1."
            )
        r.raise_for_status()
        return r.json()

    def list_zip_files(self, short_name: str) -> dict[str, RemoteFile]:
        """{fileName: RemoteFile} for every .zip in the product manifest.

        The ODP JSON has nested file records whose field names have shifted
        over time, so walk the whole document for anything that looks like a
        file entry rather than trusting one schema.
        """
        manifest = self.product_manifest(short_name)
        pairs: dict[str, RemoteFile] = {}
        for name, uri, size in _find_files(manifest):
            if name.lower().endswith(".zip"):
                pairs[name] = RemoteFile(
                    name=name,
                    url=uri or f"{self.base}/files/{short_name}/{name}",
                    size=size,
                )
        if not pairs:
            raise PlatformDriftError(
                f"manifest for '{short_name}' parsed but contained no .zip "
                "entries — the ODP JSON schema likely changed. Inspect the "
                "raw manifest and update odp._find_files(). RUNBOOK §2."
            )
        return pairs

    # -- download ---------------------------------------------------------

    def download(self, url: str, dest: Path,
                 heartbeat: Callable[[], None] | None = None,
                 ) -> tuple[int, str, str | None]:
        """Stream url -> dest via .part + atomic rename. Skips (hashing the
        existing file) if dest already exists — reruns are free. Returns
        (bytes written, sha256, server ETag or None). `heartbeat` is called
        about once per 64 MiB so a job lease can be extended during multi-GB
        files.
        """
        if dest.exists() and dest.stat().st_size > 0:
            return dest.stat().st_size, sha256_file(dest), None
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        delay = 5.0
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._stream_once(url, dest, tmp, heartbeat)
            except (requests.ConnectionError, requests.HTTPError,
                    TransientError) as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status is not None and status not in _RETRYABLE_STATUS:
                    raise  # 4xx other than 429: not transient, surface it
                if attempt == self.max_attempts:
                    raise TransientError(
                        f"download of {dest.name} failed after "
                        f"{attempt} attempts: {e}"
                    ) from e
                time.sleep(delay)
                delay = min(delay * 2, 300)
        raise AssertionError("unreachable")

    def _stream_once(self, url: str, dest: Path, tmp: Path,
                     heartbeat: Callable[[], None] | None,
                     ) -> tuple[int, str, str | None]:
        with self.session.get(url, stream=True, allow_redirects=True,
                              timeout=300) as r:
            r.raise_for_status()
            expected = int(r.headers.get("Content-Length", 0))
            # ODP 302s to signed S3/CloudFront URLs; the final response
            # carries the object's ETag. USPTO's bucket uses SSE-KMS, so the
            # ETag is NEVER a plain content MD5 (learned the hard way —
            # single-part ETags failed an MD5 comparison on valid files).
            # Record it as an opaque content ID only.
            etag = r.headers.get("ETag", "").strip('"') or None
            written = 0
            sha = hashlib.sha256()
            next_beat = 64 << 20
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
                    sha.update(chunk)
                    written += len(chunk)
                    if heartbeat and written >= next_beat:
                        heartbeat()
                        next_beat += 64 << 20
        if expected and written != expected:
            tmp.unlink(missing_ok=True)
            raise TransientError(
                f"{dest.name}: got {written} bytes, server announced "
                f"{expected} — truncated transfer"
            )
        tmp.replace(dest)  # atomic: a visible zip is a complete zip
        return written, sha.hexdigest(), etag


def _find_files(obj) -> list[tuple[str, str | None, int | None]]:
    """Walk arbitrarily nested manifest JSON yielding (fileName, url, size).

    Observed 2026-07 shape: bulkDataProductBag[].productFileBag.fileDataBag[]
    with fileName/fileSize/fileDownloadURI — but keep the walk defensive.
    """
    out: list[tuple[str, str | None, int | None]] = []
    if isinstance(obj, dict):
        name = obj.get("fileName") or obj.get("fileNameText")
        uri = (obj.get("fileDownloadURI") or obj.get("fileDownloadUri")
               or obj.get("fileLocationURI"))
        size = obj.get("fileSize")
        if isinstance(name, str):
            out.append((name, uri if isinstance(uri, str) else None,
                        size if isinstance(size, int) else None))
        for v in obj.values():
            out.extend(_find_files(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_find_files(v))
    return out


def preflight(client: OdpClient, annual: str, daily: str) -> list[str]:
    """Preflight. Returns human-readable check lines; raises
    PlatformDriftError / TransientError with actionable messages on failure.
    """
    lines: list[str] = []

    annual_files = client.list_zip_files(annual)
    picked = latest_annual_set(list(annual_files))
    if picked is None:
        raise PlatformDriftError(
            f"'{annual}' manifest has zips but none match the "
            "apcSTART-END-NN.zip annual naming — naming convention drifted. "
            "Update filenames.py. RUNBOOK §2."
        )
    cutoff, parts = picked
    lines.append(
        f"{annual}: OK — {len(annual_files)} zips; latest snapshot has "
        f"{len(parts)} parts, coverage cutoff {cutoff}"
    )

    daily_files = client.list_zip_files(daily)
    parsed_daily = [d for d in (parse_daily(n) for n in daily_files) if d]
    if not parsed_daily:
        raise PlatformDriftError(
            f"'{daily}' manifest has zips but none match apcyymmdd.zip — "
            "naming convention drifted. Update filenames.py. RUNBOOK §2."
        )
    newest = max(d.transaction_dt for d in parsed_daily)
    lines.append(
        f"{daily}: OK — {len(parsed_daily)}/{len(daily_files)} zips parse as "
        f"dailies, newest transaction date {newest}"
    )
    return lines
