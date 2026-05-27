"""
Robust model downloader with NO read timeout.
Handles slow/unstable connections by using urllib (which doesn't have timeout by default).
"""
import hashlib
import os
import sys
import time
import urllib.request
import urllib.error

MODEL_ID = "Systran/faster-whisper-tiny"
BLOB_SHA256 = "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1"
SNAPSHOT_REVISION = "d90ca5fe260221311c53c58e660288d3deb8d356"
EXPECTED_SIZE = 75_538_270

CACHE_DIR = os.path.expanduser("~/.cache/huggingface/hub")
MODEL_DIR = os.path.join(CACHE_DIR, f"models--{MODEL_ID.replace('/', '--')}")
BLOB_PATH = os.path.join(MODEL_DIR, "blobs", BLOB_SHA256)
SNAPSHOT_DIR = os.path.join(MODEL_DIR, "snapshots", SNAPSHOT_REVISION)
MODEL_BIN_SYMLINK = os.path.join(SNAPSHOT_DIR, "model.bin")


def get_signed_url():
    """Get fresh signed URL from hf-mirror."""
    req = urllib.request.Request(
        f"https://hf-mirror.com/{MODEL_ID}/resolve/main/model.bin",
        method="HEAD",
    )
    resp = urllib.request.urlopen(req, timeout=30)
    # Get the final URL after redirects
    return resp.geturl()


def main():
    print(f"Downloading {MODEL_ID}/model.bin")
    print(f"Expected: {EXPECTED_SIZE / 1024 / 1024:.0f} MB")
    print(f"Blob: {BLOB_PATH}")
    sys.stdout.flush()

    # Get download URL
    url = get_signed_url()
    print(f"Got URL (CloudFront, supports resume)")
    sys.stdout.flush()

    # Check existing
    existing_size = os.path.getsize(BLOB_PATH) if os.path.exists(BLOB_PATH) else 0
    if existing_size > 0:
        print(f"Existing: {existing_size / 1024 / 1024:.1f} MB ({existing_size / EXPECTED_SIZE * 100:.1f}%)")
    sys.stdout.flush()

    # Download with NO read timeout
    resume_header = {f"Range": f"bytes={existing_size}-"} if existing_size > 0 else {}
    req = urllib.request.Request(url, headers=resume_header)

    print(f"Starting download (no timeout)...")
    t0 = time.time()
    last_log = t0
    total = existing_size

    try:
        with urllib.request.urlopen(req, timeout=None) as resp:
            mode = "ab" if existing_size > 0 else "wb"
            chunk_size = 8192
            with open(BLOB_PATH, mode) as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
                    now = time.time()
                    if now - last_log >= 15:
                        mb = total / 1024 / 1024
                        pct = total / EXPECTED_SIZE * 100
                        elapsed = now - t0
                        rate = (total - existing_size) / 1024 / 1024 / (elapsed / 60) if elapsed > 0 else 0
                        print(f"  {mb:.1f} MB ({pct:.1f}%) — {rate:.1f} MB/min")
                        sys.stdout.flush()
                        last_log = now

        elapsed = time.time() - t0
        final_size = os.path.getsize(BLOB_PATH)
        print(f"\nDownload complete: {final_size / 1024 / 1024:.1f} MB in {elapsed:.0f}s")

        if final_size < EXPECTED_SIZE:
            print(f"File smaller than expected ({final_size} < {EXPECTED_SIZE}), needs resume")
            sys.exit(1)

    except Exception as e:
        current = os.path.getsize(BLOB_PATH) if os.path.exists(BLOB_PATH) else 0
        print(f"\nError at {current / 1024 / 1024:.1f} MB: {e}")
        print(f"Partial file saved, resume by running again")
        sys.exit(1)

    # Verify SHA256
    print("Verifying SHA256...")
    sys.stdout.flush()
    h = hashlib.sha256()
    with open(BLOB_PATH, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    actual = h.hexdigest()

    if actual == BLOB_SHA256:
        print("SHA256 verified!")
    else:
        print(f"SHA256 mismatch: expected {BLOB_SHA256}, got {actual}")
        sys.exit(1)

    # Create symlink
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    if os.path.exists(MODEL_BIN_SYMLINK) or os.path.islink(MODEL_BIN_SYMLINK):
        os.remove(MODEL_BIN_SYMLINK)
    os.symlink(f"../../blobs/{BLOB_SHA256}", MODEL_BIN_SYMLINK)
    print(f"Created symlink: model.bin -> blob")

    # Clean up incomplete marker
    incomplete = BLOB_PATH + ".incomplete"
    if os.path.exists(incomplete):
        os.remove(incomplete)
        print("Removed .incomplete marker")

    print(f"\nModel ready! Restart the voice server to use it.")


if __name__ == "__main__":
    main()
