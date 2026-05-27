Add-Type -AssemblyName System.Speech

$recognizers = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
Write-Host "Available recognizers:"
foreach ($r in $recognizers) {
    Write-Host "  Id=$($r.Id), Name=$($r.Name), Culture=$($r.Culture)"
}

# Use the Chinese recognizer
$recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine($recognizers[0].Id)
Write-Host "Using: $($recognizer.RecognizerInfo.Name) ($($recognizer.RecognizerInfo.Culture))"

# Dictation grammar
$grammar = New-Object System.Speech.Recognition.DictationGrammar
$recognizer.LoadGrammar($grammar)

Write-Host "Please speak now (recording for 10 seconds)..."
$recognizer.SetInputToDefaultAudioDevice()

try {
    $result = $recognizer.Recognize([TimeSpan]::FromSeconds(10))
    if ($result -ne $null) {
        Write-Host "TEXT:$($result.Text)"
        Write-Host "CONFIDENCE:$($result.Confidence)"
    } else {
        Write-Host "TEXT:"
        Write-Host "CONFIDENCE:0"
    }
} catch {
    Write-Host "ERROR:$($_.Exception.Message)"
} finally {
    $recognizer.Dispose()
}
