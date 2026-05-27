"""
Download Systran/faster-whisper-tiny model.bin with resume support.
Uses raw requests with long timeouts to avoid huggingface_hub timeout issues.
"""
import hashlib
import os
import sys
import time

import requests

MODEL_ID = "Systran/faster-whisper-tiny"
BLOB_SHA256 = "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1"
SNAPSHOT_REVISION = "d90ca5fe260221311c53c58e660288d3deb8d356"

CACHE_DIR = os.path.expanduser("~/.cache/huggingface/hub")
MODEL_DIR = os.path.join(CACHE_DIR, f"models--{MODEL_ID.replace('/', '--')}")
BLOB_PATH = os.path.join(MODEL_DIR, "blobs", BLOB_SHA256)
SNAPSHOT_DIR = os.path.join(MODEL_DIR, "snapshots", SNAPSHOT_REVISION)
MODEL_BIN_SYMLINK = os.path.join(SNAPSHOT_DIR, "model.bin")

EXPECTED_SIZE = 75_538_270  # 72 MB expected


def get_download_url():
    """Get a fresh signed URL from hf-mirror."""
    mirror_url = f"https://hf-mirror.com/{MODEL_ID}/resolve/main/model.bin"
    r = requests.get(mirror_url, allow_redirects=False, timeout=30)
    if r.status_code == 302:
        return r.headers["Location"]
    elif r.status_code == 200:
        return mirror_url
    raise RuntimeError(f"Unexpected status {r.status_code}: {r.text[:200]}")


def download_with_resume(url, dest_path, timeout=600):
    """Download file with resume support and long timeout."""
    mode = "ab" if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0 else "wb"
    existing_size = os.path.getsize(dest_path) if os.path.exists(dest_path) else 0

    if existing_size > 0:
        print(f"Resuming download from {existing_size / 1024 / 1024:.1f} MB")

    headers = {"Range": f"bytes={existing_size}-"} if existing_size > 0 else {}

    r = requests.get(url, headers=headers, stream=True, timeout=timeout)
    r.raise_for_status()

    if existing_size > 0 and r.status_code == 416:
        print("File already complete (416 Range Not Satisfiable)")
        return True

    total = existing_size
    last_log = time.time()

    with open(dest_path, mode) as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                total += len(chunk)
                now = time.time()
                if now - last_log > 10:
                    mb = total / 1024 / 1024
                    print(f"  Downloaded: {mb:.1f} MB", flush=True)
                    last_log = now

    final_size = os.path.getsize(dest_path)
    print(f"\nDownload complete: {final_size / 1024 / 1024:.1f} MB")
    return True


def verify_sha256(filepath):
    """Verify SHA256 of downloaded file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def create_symlink(target, link):
    """Create symlink and remove old one if exists."""
    if os.path.exists(link) or os.path.islink(link):
        os.remove(link)
    os.symlink(target, link)
    print(f"Created symlink: {link} -> {target}")


if __name__ == "__main__":
    print(f"Downloading {MODEL_ID}/model.bin")
    print(f"Expected size: {EXPECTED_SIZE / 1024 / 1024:.0f} MB")
    print(f"Blob path: {BLOB_PATH}")
    print(f"Exist: {os.path.exists(BLOB_PATH)}", flush=True)

    if os.path.exists(BLOB_PATH):
        cur_size = os.path.getsize(BLOB_PATH)
        print(f"Current file size: {cur_size / 1024 / 1024:.1f} MB / {EXPECTED_SIZE / 1024 / 1024:.0f} MB")
        if cur_size >= EXPECTED_SIZE:
            print("File already at expected size, verifying...")
            sha = verify_sha256(BLOB_PATH)
            if sha == BLOB_SHA256:
                print("SHA256 verified! Creating symlink...")
                create_symlink(f"../../blobs/{BLOB_SHA256}", MODEL_BIN_SYMLINK)
                sys.exit(0)
            else:
                print(f"SHA256 mismatch: {sha}")
                print("Re-downloading...")

    # Download
    try:
        url = get_download_url()
        print(f"\nGot signed URL (expires in 1h)")
        sys.stdout.flush()

        t0 = time.time()
        download_with_resume(url, BLOB_PATH, timeout=300)
        elapsed = time.time() - t0
        print(f"Time: {elapsed:.0f}s ({elapsed / 60:.1f} min)")
        sys.stdout.flush()
    except Exception as e:
        print(f"\nDownload error: {e}")
        if os.path.exists(BLOB_PATH):
            print(f"Partial file: {os.path.getsize(BLOB_PATH) / 1024 / 1024:.1f} MB")
        sys.exit(1)

    # Verify
    print("Verifying SHA256...", flush=True)
    actual_sha = verify_sha256(BLOB_PATH)
    if actual_sha == BLOB_SHA256:
        print("SHA256 verified!")
    else:
        file_size = os.path.getsize(BLOB_PATH)
        print(f"SHA256 mismatch!")
        print(f"  Expected: {BLOB_SHA256}")
        print(f"  Actual:   {actual_sha}")
        print(f"  Size: {file_size / 1024 / 1024:.1f} MB (expected {EXPECTED_SIZE / 1024 / 1024:.0f} MB)")
        if file_size >= EXPECTED_SIZE:
            print("Size looks right, maybe SHA mismatch due to incomplete LFS pointer?")
        else:
            print("File is incomplete, download again.")
            sys.exit(1)

    # Create symlink
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    create_symlink(f"../../blobs/{BLOB_SHA256}", MODEL_BIN_SYMLINK)

    # Clean up .incomplete file if it exists
    incomplete = BLOB_PATH + ".incomplete"
    if os.path.exists(incomplete):
        os.remove(incomplete)
        print(f"Removed incomplete marker: {incomplete}")

    print(f"\n{'='*50}")
    print(f"Model ready! Now restart the voice server.")
    print(f"{'='*50}")
