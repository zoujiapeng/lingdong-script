Get-Process electron -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.Id -Force }
Start-Sleep -Seconds 2
$count = (Get-Process electron -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Output "Remaining electron processes: $count"
