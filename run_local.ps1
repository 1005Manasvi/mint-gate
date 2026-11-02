# Starts Mint-Gate locally on Windows (development):
#   1. local Hardhat blockchain   (new window, port 8545)
#   2. deploys MintGateNFT        (writes backend/chain/deployment.json)
#   3. Flask backend + frontend   (new window, http://127.0.0.1:5000)
# Usage (from the project folder):  powershell -ExecutionPolicy Bypass -File .\run_local.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root "venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv not found - see README 'Setup'" }

# The local chain starts empty every time, so start with empty off-chain storage too.
foreach ($d in "reports", "uploads", "metadata") {
    Get-ChildItem (Join-Path $root "backend\storage\$d") -File -ErrorAction SilentlyContinue | Remove-Item -Force
}

Write-Host "[1/3] starting Hardhat node..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\Blockchain'; npx hardhat node"

# wait until the JSON-RPC port answers
$body = '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}'
for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-RestMethod -Uri http://127.0.0.1:8545 -Method Post -Body $body -ContentType "application/json" -TimeoutSec 2 | Out-Null; break }
    catch { Start-Sleep -Seconds 1 }
}

Write-Host "[2/3] deploying MintGateNFT..." -ForegroundColor Cyan
Push-Location "$root\Blockchain"
npx hardhat run scripts/deploy.js --network localhost
Pop-Location

Write-Host "[3/3] starting Mint-Gate server..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\backend'; & '$py' server.py"
Write-Host "Open http://127.0.0.1:5000 once the server prints 'Running on'." -ForegroundColor Green
