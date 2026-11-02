# Starts Mint-Gate locally on Windows (development):
#   1. Postgres + Redis           (docker compose, ports 5432 / 6379) + database migrations
#   2. local Hardhat blockchain   (new window, port 8545)
#   3. deploys contracts v2       (writes backend/chain/deployments/localhost.json)
#   4. chain indexer, analysis worker (models on the GPU), chain worker, API server (new windows)
# Usage (from the project folder):  powershell -ExecutionPolicy Bypass -File .\run_local.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root "venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv not found - see README 'Setup'" }

Write-Host "[1/4] starting Postgres + Redis..." -ForegroundColor Cyan
docker compose -f "$root\docker-compose.yml" up -d --wait
Push-Location "$root\backend"
& $py -m alembic upgrade head
Pop-Location

Write-Host "[2/4] starting Hardhat node..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\Blockchain'; npx hardhat node"

# wait until the JSON-RPC port answers
$body = '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}'
for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-RestMethod -Uri http://127.0.0.1:8545 -Method Post -Body $body -ContentType "application/json" -TimeoutSec 2 | Out-Null; break }
    catch { Start-Sleep -Seconds 1 }
}

Write-Host "[3/4] deploying MintGateRegistry + MintGateNFTV2..." -ForegroundColor Cyan
Push-Location "$root\Blockchain"
npx hardhat run scripts/deploy-v2.js --network localhost
Pop-Location

# The local chain starts empty every time: forget what was indexed from the previous one.
$envs = "`$env:CHAIN_NETWORKS='localhost'; `$env:DEFAULT_NETWORK='localhost';"
Push-Location "$root\backend"
Invoke-Expression "$envs & '$py' -m indexer --network localhost --from-deploy --once"
Pop-Location

Write-Host "[4/4] starting indexer, workers and server..." -ForegroundColor Cyan
$windows = @(
    "-m indexer --interval 3",
    "-m jobs.worker analysis --preload",
    "-m jobs.worker chain",
    "server.py"
)
foreach ($cmd in $windows) {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\backend'; $envs & '$py' $cmd"
}
Write-Host "Open http://127.0.0.1:5000 once the server prints 'Running on' (the worker loads the models first)." -ForegroundColor Green
