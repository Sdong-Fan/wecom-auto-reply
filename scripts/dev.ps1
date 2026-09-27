# scripts/dev.ps1
# One-command dev environment startup for Windows

Write-Host "=== WeCom Auto Reply - Dev Setup ===" -ForegroundColor Cyan

if (-not (Test-Path ".env")) {
    Write-Host "Creating .env from .env.example. EDIT .env with your real keys!" -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
}

Write-Host "Starting Qdrant + Redis..." -ForegroundColor Green
docker compose up -d qdrant redis

Start-Sleep -Seconds 3

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Gateway starting on http://localhost:8000" -ForegroundColor Cyan
Write-Host "If using ngrok, run in another terminal:" -ForegroundColor Cyan
Write-Host "  ngrok http 8000" -ForegroundColor Yellow
Write-Host "============================================" -ForegroundColor Cyan

python -m uvicorn gateway.main:app --host 0.0.0.0 --port 8000 --reload
