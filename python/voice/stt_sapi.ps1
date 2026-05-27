param(
    [string]$WavPath = "",
    [int]$TimeoutSeconds = 10
)

Add-Type -AssemblyName System.Speech
Add-Type -AssemblyName System.Collections

$recognizers = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
if ($recognizers.Count -eq 0) {
    Write-Host "ERROR:No speech recognizers installed"
    exit 1
}

# Pick recognizer: prefer zh-CN, fallback to first
$recId = $null
$recName = ""
foreach ($r in $recognizers) {
    Write-Host "DIAG:Available recognizer: $($r.Id) Culture=$($r.Culture.Name)" | Out-Null
    if (-not $recId -or $r.Culture.Name -eq "zh-CN") {
        $recId = $r.Id
        $recName = $r.Culture.Name
    }
}

Write-Host "DIAG:Using recognizer: $recId ($recName)"

function RecognizeWithGrammar($recognizer) {
    # Try DictationGrammar with explicit topic
    $grammar = New-Object System.Speech.Recognition.DictationGrammar("grammar:dictation/default")
    $grammar.Name = "Dictation"
    $recognizer.LoadGrammar($grammar)

    if ($WavPath -ne "" -and (Test-Path $WavPath)) {
        $fileInfo = Get-Item $WavPath
        Write-Host "DIAG:Reading WAV: $($fileInfo.Length) bytes"
        $recognizer.SetInputToWaveFile($WavPath)
        Write-Host "DIAG:SetInputToWaveFile OK"
        $result = $recognizer.Recognize()
    } else {
        $recognizer.SetInputToDefaultAudioDevice()
        Write-Host "DIAG:Listening for speech ($TimeoutSeconds seconds)..."
        $result = $recognizer.Recognize([TimeSpan]::FromSeconds($TimeoutSeconds))
    }

    return $result
}

try {
    # === Approach 1: Standard DictationGrammar ===
    Write-Host "DIAG:--- Approach 1: DictationGrammar ---"
    $rec1 = New-Object System.Speech.Recognition.SpeechRecognitionEngine($recId)
    $result1 = RecognizeWithGrammar $rec1

    if ($result1 -ne $null) {
        $text = $result1.Text.Trim()
        Write-Host "TEXT:$text"
        Write-Host "CONFIDENCE:$($result1.Confidence)"
        Write-Host "DIAG:Approach 1 SUCCEEDED"
        $rec1.Dispose()
        exit 0
    }
    $rec1.Dispose()

    # === Approach 2: Try English recognizer if Chinese failed ===
    $engId = $null
    foreach ($r in $recognizers) {
        if ($r.Culture.Name -eq "en-US") { $engId = $r.Id; break }
    }
    if ($engId -and $engId -ne $recId) {
        Write-Host "DIAG:--- Approach 2: English recognizer ---"
        $rec2 = New-Object System.Speech.Recognition.SpeechRecognitionEngine($engId)
        $grammar2 = New-Object System.Speech.Recognition.DictationGrammar
        $rec2.LoadGrammar($grammar2)

        if ($WavPath -ne "" -and (Test-Path $WavPath)) {
            $rec2.SetInputToWaveFile($WavPath)
            $result2 = $rec2.Recognize()
        } else {
            $rec2.SetInputToDefaultAudioDevice()
            $result2 = $rec2.Recognize([TimeSpan]::FromSeconds($TimeoutSeconds))
        }

        if ($result2 -ne $null) {
            $text = $result2.Text.Trim()
            Write-Host "TEXT:$text"
            Write-Host "CONFIDENCE:$($result2.Confidence)"
            Write-Host "DIAG:Approach 2 SUCCEEDED"
            $rec2.Dispose()
            exit 0
        }
        $rec2.Dispose()
    }

    # All approaches failed
    Write-Host "TEXT:"
    Write-Host "CONFIDENCE:0"
    if ($WavPath -ne "" -and (Test-Path $WavPath)) {
        Write-Host "DIAG:WAV file exists but no recognition result from any approach"
    } else {
        Write-Host "DIAG:No speech detected via live mic"
    }

} catch {
    Write-Host "ERROR:$($_.Exception.Message)"
    exit 1
}
