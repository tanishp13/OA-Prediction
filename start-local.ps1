$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = (Get-Command py -ErrorAction SilentlyContinue).Source
}
if (-not $Python) {
    throw "Python was not found. Install Python or create .venv first."
}

$backend = Start-Process -FilePath $Python -ArgumentList "-m uvicorn edge.main:app --host 127.0.0.1 --port 8000" -WorkingDirectory $ProjectRoot -PassThru
$frontend = Start-Process -FilePath $Python -ArgumentList "-m http.server 8080 --bind 127.0.0.1" -WorkingDirectory $ProjectRoot -PassThru

Write-Host "Backend:  http://127.0.0.1:8000"
Write-Host "Frontend: http://127.0.0.1:8080/OA%20Screening.dc.html"
Write-Host "Press Ctrl+C to stop both services."

try {
    while (-not $backend.HasExited -and -not $frontend.HasExited) {
        Start-Sleep -Seconds 1
    }
}
finally {
    if (-not $backend.HasExited) { Stop-Process -Id $backend.Id -Force }
    if (-not $frontend.HasExited) { Stop-Process -Id $frontend.Id -Force }
}