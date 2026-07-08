"""Bronze integrity: hashing and zip validation.

Three layers, each catching a different failure:
- Content-Length vs bytes-written (in odp.download): truncated transfers.
- Zip CRC sweep (download stage, before a job is marked done): corrupt
  archives — zip members carry CRC32, so this needs no external checksum.
- SHA-256 recorded in the ledger at download, re-verified at parse time:
  on-disk rot or tampering between download and parse (possibly years
  later), and proof that silver derives from the exact downloaded bytes.

Authenticity against USPTO (spiked 2026-07-07 against the live API):
- The product manifest publishes NO checksums — only `fileSize` per file,
  which the download stage verifies against bytes on disk.
- The file endpoint 302s to a signed S3/CloudFront URL on data.uspto.gov
  whose response carries an `ETag`. The bucket uses SSE-KMS
  (x-amz-server-side-encryption: aws:kms), so the ETag is NOT a content
  MD5 in any case — confirmed empirically 2026-07-08 when valid downloads
  failed an MD5-vs-ETag comparison. It is recorded in the ledger as an
  opaque content ID only, useful for detecting silent server-side file
  replacement via a later HEAD request (compare stored vs current ETag).
- The remaining trust anchor is TLS to *.uspto.gov on both hops.
"""

from __future__ import annotations

import hashlib
import zipfile
import zlib
from pathlib import Path


class IntegrityError(RuntimeError):
    """File content does not match what the ledger says we downloaded."""


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def assert_zip_valid(path: Path) -> None:
    """CRC-check every member. Raises IntegrityError on any corruption.
    Corruption can surface either as a testzip() CRC report or as a
    zlib/BadZipFile exception mid-decompress, depending on which bytes
    are damaged — treat both identically.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
    except (zipfile.BadZipFile, zlib.error, EOFError, OSError) as e:
        raise IntegrityError(f"{path.name}: not a valid zip ({e})") from e
    if bad is not None:
        raise IntegrityError(f"{path.name}: CRC mismatch in member '{bad}'")


def assert_sha256(path: Path, expected: str) -> None:
    actual = sha256_file(path)
    if actual != expected:
        raise IntegrityError(
            f"{path.name}: sha256 {actual} does not match the ledger's "
            f"download record {expected} — the file changed since download. "
            "Delete it and re-run the download stage."
        )
