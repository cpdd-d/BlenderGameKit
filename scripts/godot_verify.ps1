# 用 Godot 验证生成的工程:能导入、能运行、无脚本错误
#
# 用法:
#   powershell -File scripts\godot_verify.ps1
#
# 这一步是整条链路的最后一道关:
#   Blender 节点图 -> GDScript -> Godot 真实编译并运行
# 只有在 Godot 里跑通，才能说"生成的代码是对的"。

param(
    [string]$Godot = "D:\godot\Godot_v4.7.2-stable_win64_console.exe",
    [string]$Root = "D:\BlenderGameKit",
    [int]$Frames = 180
)

$ErrorActionPreference = "Continue"
$project = Join-Path $Root "examples\demo_2d"

function Step($text) {
    Write-Output ""
    Write-Output ("=" * 60)
    Write-Output "== $text"
    Write-Output ("=" * 60)
}

if (-not (Test-Path $Godot)) {
    Write-Output "跳过:找不到 Godot ($Godot)"
    exit 0
}
if (-not (Test-Path $project)) {
    Write-Output "跳过:找不到示例工程，请先运行 scripts\make_demo.py"
    exit 1
}

# ---------- 1. 重新生成示例工程 ----------
Step "1/3 生成示例工程"
& python (Join-Path $Root "scripts\make_demo.py") 2>&1 |
    Select-String -Pattern "已生成|脚本行数|告警" | ForEach-Object { $_.Line }
if ($LASTEXITCODE -ne 0) { Write-Error "生成示例失败"; exit 1 }

# ---------- 2. 导入(编译脚本) ----------
Step "2/3 Godot 导入并编译"
$importLog = & $Godot --headless --path $project --import 2>&1 | Out-String
$importErrors = $importLog -split "`n" |
    Where-Object { $_ -match "SCRIPT ERROR|Parse Error|ERROR:" } |
    Where-Object { $_ -notmatch "Unrecognized binary resource" }
if ($importErrors) {
    Write-Output "导入期报错:"
    $importErrors | Select-Object -First 15 | ForEach-Object { Write-Output "   $_" }
} else {
    Write-Output "导入无错误"
}

# ---------- 3. 真实运行若干帧 ----------
Step "3/3 Godot 运行 $Frames 帧"
$runLog = & $Godot --headless --path $project --quit-after $Frames 2>&1 | Out-String
$lines = $runLog -split "`n" | Where-Object { $_.Trim() -ne "" }
$lines | Select-Object -Last 20 | ForEach-Object { Write-Output $_ }

$runtimeErrors = $lines | Where-Object { $_ -match "SCRIPT ERROR|Parse Error|Invalid call|Nonexistent function" }
$printedHint = $runLog -match "方向键移动"

Write-Output ""
if ($runtimeErrors) {
    Write-Output "运行期错误:"
    $runtimeErrors | Select-Object -First 15 | ForEach-Object { Write-Output "   $_" }
}
if ($importErrors -or $runtimeErrors -or -not $printedHint) {
    Write-Output ""
    Write-Output "GODOT_VERIFY=FAILED"
    exit 1
}
Write-Output "脚本在 Godot 里编译并通过运行"
Write-Output "GODOT_VERIFY=OK"
exit 0
