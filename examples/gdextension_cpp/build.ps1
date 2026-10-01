# 一键构建 GDExtension(Windows)
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File build.ps1
#   powershell -ExecutionPolicy Bypass -File build.ps1 -GodotCpp "D:\godot-cpp" -UseCMake

param(
    [string]$GodotCpp = "",
    [switch]$UseCMake,
    [switch]$Release
)

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $GodotCpp) {
    $GodotCpp = Join-Path (Split-Path -Parent $here) "godot-cpp"
}

Write-Output "godot-cpp: $GodotCpp"
if (-not (Test-Path $GodotCpp)) {
    Write-Output ""
    Write-Output "找不到 godot-cpp。请先下载对应版本(例如 Godot 4.4 用分支 4.4):"
    Write-Output "  git clone -b 4.4 https://github.com/godotengine/godot-cpp `"$GodotCpp`""
    Write-Output ""
    Write-Output "没有 git 就从 GitHub 下载 zip 解压到上面这个路径。"
    exit 1
}

New-Item -ItemType Directory -Force -Path (Join-Path $here "bin") | Out-Null

if ($UseCMake) {
    Write-Output "== 使用 CMake 构建 =="
    $cmake = Get-Command cmake -ErrorAction SilentlyContinue
    if (-not $cmake) { Write-Output "找不到 cmake，请把它加进 PATH"; exit 1 }
    $cfg = if ($Release) { "Release" } else { "Debug" }
    & cmake -B (Join-Path $here "build") -S $here -DGODOT_CPP_PATH="$GodotCpp" -DCMAKE_BUILD_TYPE=$cfg
    if ($LASTEXITCODE -ne 0) { exit 1 }
    & cmake --build (Join-Path $here "build") --config $cfg
    if ($LASTEXITCODE -ne 0) { exit 1 }
} else {
    Write-Output "== 使用 SCons 构建 =="
    $scons = Get-Command scons -ErrorAction SilentlyContinue
    if (-not $scons) {
        Write-Output "找不到 scons。装它:pip install scons"
        Write-Output "或者改用 CMake:powershell -File build.ps1 -UseCMake"
        exit 1
    }
    $target = if ($Release) { "template_release" } else { "template_debug" }
    Push-Location $here
    $env:GODOT_CPP_PATH = $GodotCpp
    & scons platform=windows target=$target -j8
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { exit 1 }
}

Write-Output ""
Write-Output "== 产物 =="
Get-ChildItem (Join-Path $here "bin") -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Output ("  {0}  ({1:N0} 字节)" -f $_.Name, $_.Length) }

Write-Output ""
Write-Output "下一步:重启 Godot 编辑器，然后在 GDScript 里使用 GKCharacter。"
