# 动画系统一键验证:离线测试 -> 真 Blender 建图导出 -> 真 Godot 解析
#
# 用法:
#   powershell -File scripts\verify_anim_system.ps1
#
# ── 这条链路查的是"状态机到底能不能跑",不是"代码看起来对不对" ────────
#   1. 离线回归(纯 Python 桩):生成器的字符串级断言,秒级
#   2. 真实 Blender:建图 + 导出。**必须跑**,因为桩里的节点 id 是稳定的、
#      真 Blender 里不是 —— 入口状态算错这类问题只有这里能抓出来
#   3. 真实 Godot:`--check-only` 解析生成的脚本
#   4. 反例:故意写错的脚本**必须**被第 3 步拒绝
#      (抓不到已知错误的校验器比没有校验器更危险,见 README 第 19 条)
param(
    [string]$Blender = "D:\blender-4.5.5-windows-x64\blender.exe",
    [string]$Root = "D:\BlenderGameKit"
)

$ErrorActionPreference = "Continue"
$failed = @()

# ── 无头跑 Blender **必须**带这个参数(第四十五轮补上)────────────────
# 不带的话,打开带 NPR 预览材质的 .blend 会走 GPU 预览渲染并直接
# `EXCEPTION_ACCESS_VIOLATION`(实测:关卡示例那一层整层没输出、只报
# "失败项: 关卡示例",查了半天是这里)。全脚本统一用这一个变量,别再各写各的。
$GpuFlag = @("--gpu-backend", "vulkan")

function Step($text) {
    Write-Output ""
    Write-Output ("=" * 60)
    Write-Output "== $text"
    Write-Output ("=" * 60)
}

# ---------- 1. 离线回归 ----------
Step "1/6 离线代码生成测试"
& python (Join-Path $Root "tests\test_codegen.py") 2>&1 |
    Select-String -Pattern "^(Ran |OK|FAILED|FAIL:|ERROR:)" | ForEach-Object { $_.Line }
if ($LASTEXITCODE -ne 0) { $failed += "离线测试" }

# ---------- 2. 真 Blender:建图 + 导出 ----------
Step "2/6 Blender 里建动画状态机并导出"
if (-not (Test-Path $Blender)) {
    Write-Output "跳过:找不到 Blender"
    $failed += "Blender 不存在"
} else {
    $log = & $Blender -b $GpuFlag --python (Join-Path $Root "tests\blender_anim_tree.py") 2>&1 | Out-String
    $log -split "`n" | Select-String -Pattern "^\[(PASS|FAIL|INFO)\]|^结果:|^ANIM_TREE_RESULT" |
        ForEach-Object { $_.Line }
    if ($log -notmatch "ANIM_TREE_RESULT=OK") { $failed += "Blender 动画树测试" }
}

# ---------- 3. 示例工程:重建 + 自查 ----------
# 示例是"给用户照抄"的那份东西,它自己坏了就白搭 —— 所以每轮都重建、
# 都自查(节点图有没有问题、生成结果有没有告警、有没有多出线的状态)。
Step "3/6 示例工程(examples\demo_anim_tree)重建并自查"
if (-not (Test-Path $Blender)) {
    Write-Output "跳过:找不到 Blender"
} else {
    $make = & $Blender -b $GpuFlag --python (Join-Path $Root "scripts\make_anim_example.py") 2>&1 | Out-String
    if ($make -notmatch "ANIM_EXAMPLE_DONE") {
        Write-Output "!! 示例生成失败"
        ($make -split "`n") | Select-Object -Last 15 | ForEach-Object { Write-Output "   $_" }
        $failed += "示例生成"
    }
    $check = & $Blender -b $GpuFlag --python (Join-Path $Root "scripts\check_anim_example.py") 2>&1 | Out-String
    $check -split "`n" | Select-String -Pattern "^\[(PASS|FAIL|INFO)\]|^结果:|^ANIM_EXAMPLE_CHECK" |
        ForEach-Object { $_.Line }
    if ($check -notmatch "ANIM_EXAMPLE_CHECK=OK") { $failed += "示例自查" }
}

# ---------- 4. 真 Godot 解析生成的脚本 ----------
Step "4/6 Godot 解析生成的脚本"
foreach ($gd in @((Join-Path $Root "_dist\anim_state_machine.gd"),
                  (Join-Path $Root "_dist\anim_inputs.gd"),
                  (Join-Path $Root "_dist\anim_spring_arm.gd"),
                  (Join-Path $Root "_dist\anim_camera_path.gd"),
                  (Join-Path $Root "_dist\anim_bones.gd"),
                  (Join-Path $Root "_dist\anim_input_direction.gd"),
                  (Join-Path $Root "_dist\anim_example.gd"))) {
    if (-not (Test-Path $gd)) { Write-Output "跳过(还没生成): $gd"; continue }
    & powershell -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\check_gdscript.ps1") `
        -Script $gd 2>&1 | Select-String -Pattern "GDSCRIPT_CHECK|Godot " | ForEach-Object { $_.Line }
    if ($LASTEXITCODE -ne 0) { $failed += "Godot 解析 $(Split-Path $gd -Leaf)" }
}

# ---------- 5. 真 Godot **跑起来** ----------
# 解析通过只说明语法对。相机过渡 / 缓动 / 状态进出时这些新功能必须真跑:
# 实测踩过"局部变量跨函数用了"(解析期报)和"进入状态那段代码跑到别的
# 函数里去了"(语法完全正确,跑起来才发现进游戏什么动画都不播)。
Step "5/6 真 Godot 跑 1.5 秒(相机过渡 / 缓动 / 状态机)"
$godot = "D:\godot\Godot_v4.7.2-stable_win64_console.exe"
$probe = Join-Path $Root "_dist\anim_probe"
if (-not (Test-Path $godot)) {
    Write-Output "跳过:找不到 Godot ($godot)"
} else {
    & python (Join-Path $Root "scripts\make_anim_runtime_probe.py") 2>&1 |
        Select-String -Pattern "写入输入动作|ANIM_PROBE_READY" | ForEach-Object { $_.Line }
    $runLog = & $godot --headless --path $probe --quit-after 900 2>&1 | Out-String
    $runLines = $runLog -split "`n" | Where-Object { $_.Trim() -ne "" }
    $runLines | Where-Object { $_ -match "^\[GK\]|^PROBE" } |
        ForEach-Object { Write-Output "   $_" }
    $runErrors = $runLines | Where-Object {
        $_ -match "SCRIPT ERROR|Parse Error|Invalid call|Nonexistent" }
    if ($runErrors) {
        $runErrors | Select-Object -First 10 | ForEach-Object { Write-Output "   !! $_" }
        $failed += "真跑有脚本错误"
    }
    if ($runLog -notmatch "animation=待机 playing=true") {
        Write-Output "   !! 起始状态没有在播动画"
        $failed += "起始状态没播"
    }
    if ($runLog -notmatch "camera=特写机位") {
        Write-Output "   !! 相机过渡没生效"
        $failed += "相机过渡"
    }
    # ---- 开始口 / 结束口在**运行时**真的生效 ----
    # 探针在 0.6 秒手动进了"攻击",攻击动画 0.6 秒演完 -> 直连到结束口。
    # 1.5 秒时应当看到:状态已清空 + 已结束 = true(玩法图读的就是它)。
    if ($runLog -notmatch "anim_state=待机") {
        Write-Output "   !! 开始口没有把状态机启动起来"
        $failed += "开始口"
    }
    if ($runLog -notmatch "anim_state=攻击") {
        Write-Output "   !! 状态机没有切到攻击状态"
        $failed += "状态切换"
    }
    if ($runLog -notmatch "anim_state= done=true") {
        Write-Output "   !! 走到结束口之后没有收工(已结束 应该是 true)"
        $failed += "结束口"
    }

    # ---- 弹簧臂:相机真的被墙顶住了吗(数值验证,不靠肉眼)------------
    # 探针场景:杆子 5 米,身后 2 米一堵 1 米厚的墙(正面在 1.5 米处),
    # 碰撞半径 0.3 -> 相机应该停在 1.5 - 0.3 = **1.2 米**。
    # 读到的要是 5.0,说明碰撞检测没生效(相机会穿进墙里)。
    Step "5b/6 弹簧臂:相机被墙顶住(不穿墙)"
    $springLog = & $godot --headless --path (Join-Path $Root "_dist\spring_probe") `
        --quit-after 300 2>&1 | Out-String
    $springLog -split "`n" | Where-Object { $_ -match "^SPRING" } |
        ForEach-Object { Write-Output "   $($_.Trim())" }
    if ($springLog -notmatch "SPRING arm_wanted=5\.0 camera_distance=([0-9.]+)") {
        Write-Output "   !! 弹簧臂探针没有输出距离"
        $failed += "弹簧臂探针"
    } else {
        $distance = [double]$Matches[1]
        if ($distance -gt 4.5) {
            Write-Output ("   !! 相机退到了 {0:N2} 米 —— 碰撞检测没挡住它(穿墙了)" -f $distance)
            $failed += "弹簧臂没挡住"
        } elseif ($distance -lt 0.5) {
            Write-Output ("   !! 相机被顶到 {0:N2} 米 —— 太近了,像是把自己也当成了障碍" -f $distance)
            $failed += "弹簧臂贴脸"
        } else {
            Write-Output ("   OK:相机停在 {0:N2} 米(墙前),没穿墙" -f $distance)
        }
    }
}

# ---------- 5c. 2.5D 示例(.blend -> Godot 真跑)----------
# 用户第七轮:"创建一个 blender 文件……然后创建 godot,直接运行"。
# 这一层把 examples/demo_2d_blend/2.5D示例.blend 走完整条链:
# 打开 -> 导出 -> 补一个探针场景 -> 真 Godot 里模拟按键 -> 断言
Step "5c/6 2.5D 示例:打开 .blend -> 导出 -> 真跑(模拟按键)"
$blendScript = Join-Path $Root "scripts\check_2d_blend_example.py"
$blendOut = & $Blender -b $GpuFlag --python $blendScript 2>&1 | Out-String
$blendOut -split "`n" | Select-String -Pattern "^\[(PASS|FAIL)\]|^结果:|^2D_BLEND_CHECK" |
    ForEach-Object { Write-Output "   $($_.Line.Trim())" }
if ($blendOut -notmatch "2D_BLEND_CHECK=OK") { $failed += "2.5D示例导出" }
& python (Join-Path $Root "scripts\run_2d_blend_probe.py") 2>&1 |
    Select-String -Pattern "写入输入动作|PROBE25_READY" | ForEach-Object { Write-Output "   $($_.Line.Trim())" }
$probe25 = & $Godot --headless --fixed-fps 60 --path (Join-Path $Root "_dist\demo_25d_probe") `
    --quit-after 240 2>&1 | Out-String
$probe25 -split "`n" | Where-Object { $_ -match "^PROBE25" } | ForEach-Object { Write-Output "   $($_.Trim())" }
foreach ($pair in @(@("PROBE25 moved_x=[1-9]", "按 D 没走动"),
                    @("PROBE25 jump_rise=0\.[1-9]", "跳跃没抬起来"),
                    @("PROBE25 camera=侧视机位 -> 俯瞰机位", "没切到 2.5D 视角"),
                    @("PROBE25 fov=55\.0 -> 6", "滚轮没改视野"),
                    @("PROBE25_DONE", "探针没跑完"))) {
    if ($probe25 -notmatch $pair[0]) { Write-Output "   !! $($pair[1])"; $failed += $pair[1] }
}

# ---------- 5d. 关卡示例(.blend -> 导出 -> Godot 解析)----------
# 用户第 22 轮:"先做个能玩的关卡吧"。这一层保证那个关卡**一直是好的**:
# 打开 .blend -> 检查场景/节点图/端口 -> 导出 -> 真 Godot 解析。
Step "5d/6 关卡示例:打开 .blend -> 导出 -> 解析"
$levelScript = Join-Path $Root "scripts\check_level_example.py"
$levelOut = & $Blender -b $GpuFlag --python $levelScript 2>&1 | Out-String
$levelOut -split "`n" | Select-String -Pattern "^\[(PASS|FAIL)\]|^结果:|^LEVEL_CHECK" |
    ForEach-Object { Write-Output "   $($_.Line.Trim())" }
if ($levelOut -notmatch "LEVEL_CHECK=OK") { $failed += "关卡示例" }
if (Test-Path (Join-Path $Root "_dist\level.gd")) {
    $parse = & powershell -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\check_gdscript.ps1") `
        -Script "_dist\level.gd" 2>&1 | Out-String
    Write-Output "   $($parse.Trim())"
    if ($parse -notmatch "GDSCRIPT_CHECK=OK") { $failed += "关卡脚本解析" }
}

# ---------- 6. 反例:坏脚本必须被拒绝 ----------
Step "6/6 反例:故意写错的脚本必须被 Godot 拒绝"
& powershell -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\check_gdscript.ps1") `
    -Script (Join-Path $Root "tests\bad_script_fixture.gd") 2>&1 |
    Select-String -Pattern "GDSCRIPT_CHECK" | ForEach-Object { $_.Line }
if ($LASTEXITCODE -eq 0) {
    Write-Output "!! 校验器把故意写错的脚本判成了通过 —— 它现在什么都查不出来"
    $failed += "反例没被拒绝"
}

# ---------- 汇总 ----------
Step "结果"
if ($failed.Count -eq 0) {
    Write-Output "动画系统验证全部通过"
    Write-Output "ANIM_VERIFY=OK"
    exit 0
}
Write-Output ("失败项: " + ($failed -join "、"))
Write-Output "ANIM_VERIFY=FAILED"
exit 1
