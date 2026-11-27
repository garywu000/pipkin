# Builds Pipkin for Windows (x64) and packages a ready-to-run zip.
# Run from the repository root or from inside windows\:
#   powershell -ExecutionPolicy Bypass -File windows\build-windows.ps1

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$outDir = Join-Path $scriptDir "dist\Pipkin"
$zipPath = Join-Path $scriptDir "dist\Pipkin-windows-x64.zip"

Write-Host "Building self-contained Pipkin Windows shell (.NET 8 + WebView2)..."
if (Test-Path $outDir) { Remove-Item -Recurse -Force $outDir }
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }

dotnet publish (Join-Path $scriptDir "PipkinWindows.csproj") `
    -c Release `
    -r win-x64 `
    --self-contained true `
    -o $outDir

Write-Host "Bundling Python backend and web UI..."
foreach ($file in @("server.py", "agent.py", "artifacts.py", "platform_os.py", "skills.py", "scheduler.py", "updater.py", "github_connector.py", "github_workflows.py", "drive_connector.py", "speech.py")) {
    Copy-Item (Join-Path $repoRoot $file) -Destination $outDir -Force
}
Copy-Item (Join-Path $repoRoot "dist") -Destination (Join-Path $outDir "dist") -Recurse -Force

# The speech engine for dictation (built by CI from pinned whisper.cpp source).
$engine = Join-Path $scriptDir "whisper-cli.exe"
if (Test-Path $engine) {
    Copy-Item $engine -Destination $outDir -Force
    Copy-Item (Join-Path $scriptDir "whisper.cpp-LICENSE.txt") -Destination $outDir -Force
} else {
    Write-Warning "windows\whisper-cli.exe not found: this package will not include dictation."
}

Write-Host "Creating $zipPath..."
Compress-Archive -Path $outDir -DestinationPath $zipPath -Force
Write-Host "Done! Output: $zipPath"
