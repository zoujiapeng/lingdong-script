"""
Download whisper-tiny model for offline STT use.
Tries multiple strategies to complete the download in China network environment.
"""
import os
import sys
import time

MODEL_ID = "openai/whisper-tiny"

# Increase all timeouts significantly
os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "300"
os.environ["HF_HUB_ETAG_TIMEOUT"] = "120"
os.environ["TRANSFORMERS_OFFLINE"] = "0"
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HUGGINGFACE_HUB_ENDPOINT"] = "https://hf-mirror.com"


def try_transformers_download():
    """Use transformers to download and cache the model."""
    print(f"Downloading {MODEL_ID} via transformers...")
    print(f"HF_ENDPOINT={os.environ.get('HF_ENDPOINT', 'default')}")
    print(f"HF_HUB_DOWNLOAD_TIMEOUT={os.environ.get('HF_HUB_DOWNLOAD_TIMEOUT', 'default')}")
    sys.stdout.flush()

    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

    t0 = time.time()
    print("Loading processor...")
    sys.stdout.flush()
    processor = AutoProcessor.from_pretrained(MODEL_ID)
    print(f"Processor loaded in {time.time()-t0:.0f}s")

    t1 = time.time()
    print("Loading model (this downloads ~151MB if not cached)...")
    sys.stdout.flush()
    model = AutoModelForSpeechSeq2Seq.from_pretrained(MODEL_ID)
    print(f"Model loaded in {time.time()-t1:.0f}s")

    print("\n✓ Model downloaded and cached successfully!")
    print(f"  Cache dir: ~/.cache/huggingface/hub/models--openai--whisper-tiny")
    print(f"  Total time: {time.time()-t0:.0f}s")
    return True


def try_snapshot_download():
    """Fallback: use huggingface_hub snapshot_download directly."""
    print(f"\nTrying snapshot_download for {MODEL_ID}...")
    sys.stdout.flush()

    from huggingface_hub import snapshot_download

    t0 = time.time()
    path = snapshot_download(
        MODEL_ID,
        resume_download=True,
        ignore_patterns=["*.h5", "*.ot", "*.msgpack"],
    )
    print(f"Downloaded to: {path}")
    print(f"Time: {time.time()-t0:.0f}s")
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("Whisper Model Downloader")
    print("=" * 60)

    # Check current cache state
    try:
        from huggingface_hub import try_to_load_from_cache

        cached = try_to_load_from_cache(MODEL_ID, "pytorch_model.bin")
        if cached and not cached.endswith(".incomplete"):
            size = os.path.getsize(cached) if os.path.exists(cached) else 0
            print(f"\nModel weights already cached: {cached} ({size/1024/1024:.0f} MB)")
            if size > 140_000_000:  # ~151MB expected
                print("Model appears complete. Testing load...")
                try_transformers_download()
                sys.exit(0)
            else:
                print(f"File exists but may be incomplete ({size/1024/1024:.0f}MB). Re-downloading...")
        else:
            print(f"\nModel not fully cached yet.")
    except Exception as e:
        print(f"Could not check cache: {e}")

    # Strategy 1: transformers direct download
    print("\n--- Strategy 1: transformers.from_pretrained() ---")
    try:
        if try_transformers_download():
            print("\n✓ Download successful!")
            sys.exit(0)
    except Exception as e:
        print(f"  ✗ Failed: {e}")

    # Strategy 2: snapshot_download with resume
    print("\n--- Strategy 2: snapshot_download (resume) ---")
    try:
        if try_snapshot_download():
            print("\n✓ Download successful!")
            sys.exit(0)
    except Exception as e:
        print(f"  ✗ Failed: {e}")

    # Strategy 3: try without hf-mirror
    print("\n--- Strategy 3: direct HuggingFace (no mirror) ---")
    os.environ.pop("HF_ENDPOINT", None)
    os.environ.pop("HUGGINGFACE_HUB_ENDPOINT", None)
    try:
        if try_transformers_download():
            print("\n✓ Download successful!")
            sys.exit(0)
    except Exception as e:
        print(f"  ✗ Failed: {e}")

    print("\n✗ All download strategies failed.")
    print("You may need to manually download the model from a browser.")
    print("URL: https://huggingface.co/openai/whisper-tiny")
    sys.exit(1)
