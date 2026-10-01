# 扫光验证:让主光绕角色转一圈,每个角度出一张图
#
# 面部阴影 SDF 的核心承诺是"阴影线随光角度滑动,而不是按几何法线乱跑"。
# 单张静态图证明不了这件事 —— 必须看多个角度。
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\render_shots.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\render_shots.ps1 -Step 45

param(
    [string]$Godot = "D:\godot\Godot_v4.7.2-stable_win64_console.exe",
    [string]$Root = "D:\BlenderGameKit",
    [int]$Step = 30,
    [string]$Angles = "",
    [int]$Resolution = 480
)

$ErrorActionPreference = "Continue"
$project = Join-Path $Root "examples\demo_render"
$shots = Join-Path $project "shots"

if (-not (Test-Path $Godot)) { Write-Output "跳过:找不到 Godot ($Godot)"; exit 0 }
if (-not (Test-Path $project)) { Write-Output "跳过:请先运行 scripts\make_render_demo.py"; exit 1 }

Write-Output "== 重新生成 demo =="
& python (Join-Path $Root "scripts\make_render_demo.py") 2>&1 |
    Select-String -Pattern "已生成 NPR|场景:" | ForEach-Object { $_.Line }

if (Test-Path $shots) { Remove-Item -Recurse -Force $shots }
New-Item -ItemType Directory -Force -Path $shots | Out-Null

Write-Output ""
if ($Angles -ne "") {
    Write-Output "== 扫光:指定角度 $Angles =="
} else {
    Write-Output "== 扫光:每 $Step 度一张 =="
}
$env:GK_SWEEP = "1"
$env:GK_SWEEP_STEP = "$Step"
if ($Angles -ne "") { $env:GK_SWEEP_ANGLES = $Angles }
$env:GK_SHOTS_DIR = $shots

$out = & $Godot --path $project --resolution "${Resolution}x${Resolution}" 2>&1 | Out-String
Remove-Item Env:\GK_SWEEP, Env:\GK_SWEEP_STEP, Env:\GK_SWEEP_ANGLES, Env:\GK_SHOTS_DIR -ErrorAction SilentlyContinue

$errors = $out -split "`n" | Where-Object { $_ -match "SCRIPT ERROR|Parse Error|SHADER ERROR" }
if ($errors) {
    Write-Output "运行期错误:"
    $errors | Select-Object -First 10 | ForEach-Object { Write-Output "   $_" }
}

$files = Get-ChildItem $shots -Filter "*.png" -ErrorAction SilentlyContinue | Sort-Object Name
Write-Output ""
Write-Output ("生成 {0} 张: {1}" -f $files.Count, ($files.Name -join ", "))

if ($files.Count -lt 3 -or $errors) {
    Write-Output "SWEEP=FAILED"
    exit 1
}
Write-Output "输出目录: $shots"
Write-Output "SWEEP=OK"
exit 0
