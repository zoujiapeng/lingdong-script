# Kill existing processes
Get-Process electron -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.Id -Force }
Get-Process node -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*vite*" } | ForEach-Object { Stop-Process -Id $_.Id -Force }

Start-Sleep -Seconds 2

# Start Vite
$vite = Start-Process -FilePath "node" -ArgumentList ".\node_modules\vite\bin\vite.js" -WorkingDirectory "C:\Users\zou\Desktop\claude" -WindowStyle Hidden -PassThru
Write-Output "Vite started (PID: $($vite.Id))"

# Wait for Vite
Start-Sleep -Seconds 8

# Start Electron
$electron = Start-Process -FilePath "node" -ArgumentList ".\node_modules\electron\cli.js ." -WorkingDirectory "C:\Users\zou\Desktop\claude" -WindowStyle Hidden -PassThru
Write-Output "Electron started (PID: $($electron.Id))"

# Verify
Start-Sleep -Seconds 3
$count = (Get-Process electron -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Output "Electron process count: $count"
