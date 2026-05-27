"""
STT diagnostic script.
Tests each backend independently to find where the failure is.
"""
import json
import os
import subprocess
import sys
import tempfile
import wave

SAMPLE_RATE = 16000

def test_sapi_script(script_path):
    """Test that the PowerShell SAPI script executes."""
    print("\n=== Test 1: SAPI script execution ===")
    try:
        result = subprocess.run(
            ["powershell.exe", "-ExecutionPolicy", "Bypass",
             "-File", script_path, "-TimeoutSeconds", "1"],
            capture_output=True, text=True, timeout=15,
        )
        print(f"  Return code: {result.returncode}")
        print(f"  Stdout: {result.stdout.strip()}")
        print(f"  Stderr: {result.stderr.strip()}")
        print("  Result: SAPI script executed OK (no speech expected with 1s timeout)")
    except Exception as e:
        print(f"  ERROR: {e}")

def test_sapi_recognizers():
    """List available SAPI recognizers."""
    print("\n=== Test 2: Available SAPI recognizers ===")
    ps_code = """
Add-Type -AssemblyName System.Speech
$recognizers = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
if ($recognizers.Count -eq 0) {
    Write-Host "ERROR:No recognizers"
} else {
    foreach ($r in $recognizers) {
        Write-Host ("RECOGNIZER:" + $r.Id + "|" + $r.Name + "|" + $r.Culture.Name)
    }
}
"""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_code],
            capture_output=True, text=True, timeout=15,
        )
        print(f"  {result.stdout.strip()}")
        if result.stderr.strip():
            print(f"  Stderr: {result.stderr.strip()}")
    except Exception as e:
        print(f"  ERROR: {e}")

def test_record_mic(duration=3):
    """Test mic recording with sounddevice."""
    print(f"\n=== Test 3: Mic recording (sounddevice, {duration}s) ===")
    try:
        import numpy as np
        import sounddevice as sd

        # Simple recording
        print(f"  Recording for {duration} seconds...")
        audio = sd.rec(int(duration * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                       channels=1, dtype='int16')
        sd.wait()

        energy = np.sqrt(np.mean(audio.astype(np.float32) ** 2))
        print(f"  Audio shape: {audio.shape}")
        print(f"  Max value: {np.max(np.abs(audio))}")
        print(f"  RMS energy: {energy:.1f}")
        print(f"  Has audio: {energy > 500}")

        # Save to WAV
        wav_path = os.path.join(tempfile.gettempdir(), "test_stt_record.wav")
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio.tobytes())
        file_size = os.path.getsize(wav_path)
        print(f"  Saved WAV: {wav_path} ({file_size} bytes)")
        return wav_path
    except Exception as e:
        print(f"  ERROR: {e}")
        return None

def test_sapi_from_wav(wav_path, script_path):
    """Test SAPI transcription from a WAV file."""
    print(f"\n=== Test 4: SAPI transcribe from WAV ===")
    try:
        result = subprocess.run(
            ["powershell.exe", "-ExecutionPolicy", "Bypass",
             "-File", script_path, "-WavPath", wav_path],
            capture_output=True, text=True, timeout=30,
        )
        print(f"  Return code: {result.returncode}")
        print(f"  Stdout:")
        for line in result.stdout.strip().split("\n"):
            print(f"    {line.strip()}")
        if result.stderr.strip():
            print(f"  Stderr: {result.stderr.strip()}")

        # Parse result
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line.startswith("TEXT:"):
                text = line[5:]
                print(f"\n  => Recognized text: '{text}'")
                return text
            if line.startswith("ERROR:"):
                print(f"\n  => Recognition failed: {line[6:]}")
                return None
        print(f"\n  => No text recognized")
        return None
    except Exception as e:
        print(f"  ERROR: {e}")
        return None

def test_sapi_live(script_path, timeout=8):
    """Test SAPI live mic capture."""
    print(f"\n=== Test 5: SAPI live mic capture ({timeout}s) ===")
    try:
        result = subprocess.run(
            ["powershell.exe", "-ExecutionPolicy", "Bypass",
             "-File", script_path, "-TimeoutSeconds", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        print(f"  Return code: {result.returncode}")
        print(f"  Stdout:")
        for line in result.stdout.strip().split("\n"):
            print(f"    {line.strip()}")
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line.startswith("TEXT:"):
                text = line[5:]
                print(f"\n  => Recognized text: '{text}'")
                return text
        return None
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sapi_script = os.path.join(script_dir, "stt_sapi.ps1")

    print("=" * 50)
    print("STT Diagnostic Tool")
    print("=" * 50)

    # Test SAPI recognizers
    test_sapi_recognizers()

    # Test SAPI script execution
    test_sapi_script(sapi_script)

    # Test mic recording
    wav_path = test_record_mic(4)

    if wav_path and os.path.exists(wav_path):
        # Test SAPI from WAV
        test_sapi_from_wav(wav_path, sapi_script)

    # Test SAPI live
    # test_sapi_live(sapi_script, 8)  # Uncomment to test live capture

    print("\n" + "=" * 50)
    print("Done. Check results above.")
    print("=" * 50)
