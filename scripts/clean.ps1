<#
.SYNOPSIS
  Cleans project artifacts. By default only what can be regenerated.

.EXAMPLE
  .\scripts\clean.ps1            # Python caches, .pytest_cache, frontend/dist
  .\scripts\clean.ps1 -Deep      # + node_modules and .venv
  .\scripts\clean.ps1 -Data      # + data/ (DELETES sessions and images; asks first)
#>
[CmdletBinding()]
param(
  [switch]$Deep,
  [switch]$Data,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# A locked file (dev server still serving dist, antivirus, an open editor) must
# not abort the rest of the cleanup: report it and carry on.
function Remove-Target($path, $label) {
  if (-not (Test-Path $path)) { return $true }
  try {
    Remove-Item -Recurse -Force $path -ErrorAction Stop
    Write-Host "  removed  $label"
    return $true
  } catch {
    Write-Warning "  in use, skipped: $label"
    return $false
  }
}

Write-Host "Cleaning $root"

Get-ChildItem -Path $root -Include '__pycache__', '.pytest_cache' -Recurse -Directory -Force |
  Where-Object { $_.FullName -notlike '*\.venv\*' -and $_.FullName -notlike '*\node_modules\*' } |
  ForEach-Object { Remove-Target $_.FullName $_.FullName.Replace($root, '.') | Out-Null }

Remove-Target "$root\frontend\dist" "frontend\dist" | Out-Null

if ($Deep) {
  Remove-Target "$root\frontend\node_modules" "frontend\node_modules" | Out-Null
  Remove-Target "$root\.venv" ".venv" | Out-Null
}

if ($Data) {
  $dataDir = "$root\data"
  if (Test-Path $dataDir) {
    $count = (Get-ChildItem $dataDir -Recurse -File -ErrorAction SilentlyContinue).Count
    if (-not $Force) {
      Write-Warning "data\ holds $count files: the database, the sessions and every generated image. This is permanent."
      $answer = Read-Host "Type DELETE to confirm"
      if ($answer -ne 'DELETE') { Write-Host "  cancelled"; exit 1 }
    }
    Remove-Target $dataDir "data" | Out-Null
  }
}

Write-Host "Done."
