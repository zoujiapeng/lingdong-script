"""
Finalize faster-whisper model after download completes.
1. Verify SHA256 of the blob file
2. Create symlink in snapshots directory
3. Set up the cache properly for huggingface_hub
"""
import hashlib
import os
import sys

MODEL_ID = "Systran/faster-whisper-tiny"
BLOB_SHA256 = "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1"
SNAPSHOT_REVISION = "d90ca5fe260221311c53c58e660288d3deb8d356"
EXPECTED_SIZE = 75_538_270

CACHE_DIR = os.path.expanduser("~/.cache/huggingface/hub")
MODEL_DIR = os.path.join(CACHE_DIR, f"models--{MODEL_ID.replace('/', '--')}")
BLOB_PATH = os.path.join(MODEL_DIR, "blobs", BLOB_SHA256)
SNAPSHOT_DIR = os.path.join(MODEL_DIR, "snapshots", SNAPSHOT_REVISION)
MODEL_BIN_SYMLINK = os.path.join(SNAPSHOT_DIR, "model.bin")


def main():
    if not os.path.exists(BLOB_PATH):
        print(f"Blob not found: {BLOB_PATH}")
        sys.exit(1)

    size = os.path.getsize(BLOB_PATH)
    print(f"Blob size: {size / 1024 / 1024:.1f} MB (expected {EXPECTED_SIZE / 1024 / 1024:.0f} MB)")

    if size != EXPECTED_SIZE:
        print(f"Size mismatch! Expected {EXPECTED_SIZE}, got {size}")
        print("Download incomplete. Resuming...")
        sys.exit(1)

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

    if actual != BLOB_SHA256:
        print(f"SHA256 mismatch!")
        print(f"  Expected: {BLOB_SHA256}")
        print(f"  Actual:   {actual}")
        sys.exit(1)

    print("SHA256 verified!")

    # Create symlink
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    if os.path.exists(MODEL_BIN_SYMLINK) or os.path.islink(MODEL_BIN_SYMLINK):
        os.remove(MODEL_BIN_SYMLINK)

    # Windows: use relative path for symlink
    rel_target = os.path.join("..", "..", "blobs", BLOB_SHA256)
    os.symlink(rel_target, MODEL_BIN_SYMLINK)
    print(f"Created symlink: {MODEL_BIN_SYMLINK} -> {rel_target}")

    # Clean up incomplete files
    incomplete = BLOB_PATH + ".incomplete"
    if os.path.exists(incomplete):
        os.remove(incomplete)
        print("Removed .incomplete marker")

    # Verify with huggingface_hub
    try:
        import huggingface_hub as hf
        test_path = hf.try_to_load_from_cache(MODEL_ID, "model.bin")
        if test_path:
            print(f"huggingface_hub cache check OK: {test_path}")
        else:
            print("huggingface_hub cache check returned None (unexpected)")
    except Exception as e:
        print(f"huggingface_hub cache check error: {e}")

    print("\n" + "=" * 50)
    print("MODEL READY!")
    print("=" * 50)
    print("\nNow test the model load with:")
    print("  python -c \"from faster_whisper import WhisperModel; m = WhisperModel('Systran/faster-whisper-tiny', device='cpu', compute_type='int8')\"")
    print("\nThen restart the voice server.")


if __name__ == "__main__":
    main()
