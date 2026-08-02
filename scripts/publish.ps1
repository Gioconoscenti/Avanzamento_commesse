#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "== 1/5 Generazione dati (Excel + pianificazione) ==" -ForegroundColor Cyan
python scripts\gen_data.py
if (-not $?) { throw "gen_data.py fallito" }

Write-Host "== 2/5 Cifratura ==" -ForegroundColor Cyan
node scripts\encrypt.js build\data.plain.json docs\data.enc.json
if (-not $?) { throw "encrypt.js fallito" }

Write-Host "== 3/5 Pulizia file temporanei ==" -ForegroundColor Cyan
Remove-Item -Recurse -Force build -ErrorAction SilentlyContinue

Write-Host "== 4/5 Guardia anti-fuga dati ==" -ForegroundColor Cyan
git add docs\index.html docs\data.enc.json
$staged = git diff --cached --name-only
$leaked = $staged | Where-Object { $_ -match '\.(xlsx|xls|csv)$' -or $_ -match 'plain' }
if ($leaked) {
    git reset
    throw "Guardia anti-fuga dati: trovati file sospetti in staging, commit interrotto: $($leaked -join ', ')"
}
if (-not $staged) {
    Write-Host "Nessuna modifica da pubblicare." -ForegroundColor Yellow
    exit 0
}

Write-Host "== 5/5 Commit e push ==" -ForegroundColor Cyan
$date = Get-Date -Format "yyyy-MM-dd HH:mm"
git commit -m "Aggiornamento dati: $date"
git push origin main

Write-Host "Pubblicato: https://gioconoscenti.github.io/Avanzamento_commesse/" -ForegroundColor Green
