param(
    [string]$Python = 'py',
    [switch]$SkipInstall,
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskVenv = Join-Path $taskRoot 'tools\python'
$taskPython = Join-Path $taskVenv 'Scripts\python.exe'
$taskParser = Join-Path $taskRoot 'tools\kuro_mdl_tool\kuro_mdl_to_basic_gltf.py'

if (-not (Test-Path -LiteralPath $taskParser -PathType Leaf)) {
    Write-Host 'Fetching the pinned model parser submodule...'
    & git -C $taskRoot submodule update --init --recursive
    if ($LASTEXITCODE -ne 0) { throw 'Submodule setup failed. Check Git installation and network access.' }
}

if (-not (Test-Path -LiteralPath $taskPython -PathType Leaf)) {
    $taskPythonArgs = @()
    if ($Python -eq 'py') { $taskPythonArgs += '-3.11' }
    & $Python @taskPythonArgs -c 'import sys; assert sys.version_info[:2] == (3, 11), "Use Python 3.11 for the pinned dependencies."'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 is required. Use -Python with the path to python.exe.' }
    & $Python @taskPythonArgs -m venv $taskVenv
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
}
& $taskPython -c 'import sys; assert sys.version_info[:2] == (3, 11), "Use Python 3.11 for the pinned dependencies."'
if ($LASTEXITCODE -ne 0) { throw 'The existing tools/python environment must use Python 3.11.' }

if (-not $SkipInstall) {
    & $taskPython -m pip install --no-cache-dir --disable-pip-version-check -r (Join-Path $taskRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}

$taskConfig = Join-Path $taskRoot 'fdk_toolkit\profiles.json'
if (-not (Test-Path -LiteralPath $taskConfig)) {
    Copy-Item -LiteralPath (Join-Path $taskRoot 'fdk_toolkit\profiles.example.json') -Destination $taskConfig
}

if (-not $SkipBuild) {
    & (Join-Path $taskRoot 'fdk_toolkit\tool\build_gui.ps1')
    & $taskPython (Join-Path $taskRoot 'fdk_toolkit\blender_addon\package_addon.py')
    if ($LASTEXITCODE -ne 0) { throw 'Blender add-on packaging failed.' }
}
Write-Host 'Ready. Configure your game and Blender paths in fdk_toolkit/profiles.json or the GUI.'
Write-Host 'GUI: fdk_toolkit/FDK_Asset_Tool.exe; add-on: fdk_toolkit/FDK_Blender_Addon.zip'
