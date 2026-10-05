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
    [int]$Frames = 180,
    # 生成到哪个目录。默认 examples\demo_2d(可展示的示例工程);
    # 一键门禁(build_and_test.ps1)会传 _dist\godot_e2e,把产物隔离在
    # gitignore 里,不弄脏已入库的示例。
    [string]$OutDir = ""
)

$ErrorActionPreference = "Continue"
if ($OutDir) { $project = $OutDir } else { $project = Join-Path $Root "examples\demo_2d" }

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
# 指定了 -OutDir 就是临时产物目录,下面第 1 步会重新生成它,
# 不需要(也不该)预先存在。只有默认的展示工程才要求已生成过。
if (-not $OutDir -and -not (Test-Path $project)) {
    Write-Output "跳过:找不到示例工程，请先运行 scripts\make_demo.py"
    exit 1
}

# ---------- 1. 重新生成示例工程 ----------
Step "1/4 生成示例工程"
& python (Join-Path $Root "scripts\make_demo.py") $project 2>&1 |
    Select-String -Pattern "已生成|脚本行数|告警" | ForEach-Object { $_.Line }
if ($LASTEXITCODE -ne 0) { Write-Error "生成示例失败"; exit 1 }

# ---------- 2. 导入(编译脚本) ----------
Step "2/4 Godot 导入并编译"
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
Step "3/4 Godot 运行 $Frames 帧"
# ── --fixed-fps 是必须的(踩过的坑)──────────────────────────────
# 无头模式下帧跑得飞快,而物理帧是**按真实时间**推进的 —— 同一份
# "--quit-after 180" 在别人机器上可能只推进 0.3 秒,探针一个时间点都到不了
# (实测:180 帧什么都不打印,300 帧全对)。加上 --fixed-fps 之后
# 180 帧 = 固定 3 秒,和机器快慢无关。
$runLog = & $Godot --headless --fixed-fps 60 --path $project --quit-after $Frames 2>&1 | Out-String
$lines = $runLog -split "`n" | Where-Object { $_.Trim() -ne "" }
$lines | Select-Object -Last 20 | ForEach-Object { Write-Output $_ }

$runtimeErrors = $lines | Where-Object { $_ -match "SCRIPT ERROR|Parse Error|Invalid call|Nonexistent function" }
# ── 运行标记改用探针的结束行(踩过的坑)────────────────────────────
# 原来这里匹配的是节点图里那句操作提示("方向键移动…"),结果提示文案一改
# (改成 "AD 移动")断言就全挂 —— 而真正的问题是"标记选错了对象"。
# 探针的 PROBE2D_DONE 是**行为验证**自己的产物,文案怎么改都不受影响。
$printedHint = $runLog -match "PROBE2D_DONE"

# ---------- 4. 行为断言:节点图真的产生了行为吗 ----------
# 用户 2026-10-03:"用节点图来进行游戏设计……我现在验证不了的节点"。
# 探针在真 Godot 里**模拟按键**(Input.action_press),再把这些量读回来 ——
# 不是"能跑就行",而是"按了右键真的往右走了、跳真的抬起来了、加分真的加了"。
Step "4/4 行为断言(模拟按键)"
$probeLines = $lines | Where-Object { $_ -match "^PROBE2D" }
$probeLines | ForEach-Object { Write-Output "   $_" }
$failed = @()
function Assert-Probe([string]$pattern, [string]$label) {
    if ($runLog -notmatch $pattern) { $script:failed += $label }
}
Assert-Probe "PROBE2D moved_x=[1-9]"        "按 D 没有往右走"
Assert-Probe "PROBE2D jump_rise=[1-9]"      "按空格没有跳起来"
Assert-Probe "PROBE2D score=0\.0 -> [1-9]" "攻击没有加分"
Assert-Probe "PROBE2D bullets=[1-9]"        "没有生成子弹"
Assert-Probe "PROBE2D label=[0-9]"          "界面文字没更新"
Assert-Probe "PROBE2D camera=Camera2D"      "相机跟随没找到相机"
Assert-Probe "PROBE2D_DONE"                 "探针没跑完"

Write-Output ""
if ($runtimeErrors) {
    Write-Output "运行期错误:"
    $runtimeErrors | Select-Object -First 15 | ForEach-Object { Write-Output "   $_" }
}
if ($failed.Count -gt 0) {
    Write-Output ("行为断言失败: " + ($failed -join "、"))
}
if ($importErrors -or $runtimeErrors -or -not $printedHint -or $failed.Count -gt 0) {
    Write-Output ""
    Write-Output "GODOT_VERIFY=FAILED"
    exit 1
}
Write-Output "脚本在 Godot 里编译并通过运行"
Write-Output "GODOT_VERIFY=OK"
exit 0
