$ErrorActionPreference = 'Stop'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
    throw 'The Windows .NET Framework 4.x C# compiler is missing. Install or enable .NET Framework 4.x first.'
}
$taskOutput = Split-Path $PSScriptRoot -Parent
& $compiler /nologo /target:winexe /platform:x64 /optimize+ "/out:$taskOutput\FDK_Asset_Tool.exe" "/win32manifest:$PSScriptRoot\AssetExporterGui.manifest" /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll "$PSScriptRoot\AssetExporterGui.cs"
if ($LASTEXITCODE -ne 0) { throw 'GUI build failed.' }
Write-Output "Built $taskOutput\FDK_Asset_Tool.exe"
