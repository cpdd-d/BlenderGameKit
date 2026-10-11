# 一键:构建扩展 -> 干净重装 -> 在真实 Blender 里跑冒烟测试
#
# 用法:
#   pwsh -File scripts\build_and_test.ps1
#
# ── 为什么测试装到 _testrepo 而不是 user_default ─────────────────────
# 测试版和用户真正在用的版本必须分开,否则每跑一次测试就把用户装好的
# 插件覆盖成测试版。所以这里建一个临时仓库 gk_test。
#
# ── 但**用完必须注销** ────────────────────────────────────────────────
# 踩过的坑:早期版本只删了 _testrepo 目录,**没有注销仓库注册**。
# 结果 Blender 里留下一个指向死路径的仓库,插件列表里就多出一个
# "BlenderGameKit"(文件显示为 D:\BlenderGameKit\_testrepo\...)。
# 用户看到两个插件,完全分不清哪个是能用的。
# 现在无论测试成功还是失败,退出前一定会 repo-remove。

param(
    [string]$Blender = "D:\blender-4.5.5-windows-x64\blender.exe",
    [string]$Root = "D:\BlenderGameKit",
    [string]$Godot = "D:\godot\Godot_v4.7.2-stable_win64_console.exe",
    [switch]$SkipInstall,
    [switch]$SkipGodot,
    [switch]$Deep,
    [switch]$KeepTestRepo
)

$ErrorActionPreference = "Continue"
$dist = Join-Path $Root "_dist"
$repo = Join-Path $Root "_testrepo"
$pkg = Join-Path $Root "BlenderGameKit"
$zip = Join-Path $dist "blender_game_kit-0.1.0.zip"
$testRepoName = "gk_test"

function Step($text) {
    Write-Output ""
    Write-Output ("=" * 60)
    Write-Output "== $text"
    Write-Output ("=" * 60)
}

# 清理函数:注销测试仓库 + 删目录。
# 放在函数里是因为要在**多个出口**调用(正常结束、异常退出都要)。
function Remove-TestRepo {
    if ($KeepTestRepo) {
        Write-Output "（-KeepTestRepo:保留测试仓库 gk_test 便于排查）"
        return
    }
    & $Blender --command extension repo-remove $testRepoName 2>&1 | Out-Null
    if (Test-Path $repo) { Remove-Item -Recurse -Force $repo }
}

if (-not (Test-Path $Blender)) { Write-Error "找不到 Blender: $Blender"; exit 1 }

# 跑一个"旁路"Godot 验证脚本,把它输出的关键行透出来,并按标记给 ok/skip/fail。
# 三个脚本的标记各不相同(GODOT_VERIFY / PLATFORMER_VERIFY / ANIM_VERIFY),
# 所以做成通用函数,新增一层覆盖只要再加一次调用。
function Invoke-Verify {
    param([string]$Script, [string[]]$CallArgs, [string]$OkMarker)
    $log = & powershell -ExecutionPolicy Bypass -File $Script @CallArgs 2>&1 | Out-String
    $log -split "`n" | Where-Object {
        $_ -match "^(跳过|SKIP:|导入无错误|导入期报错|运行期错误|行为断言失败|脚本在 Godot|GODOT_VERIFY|PLATFORMER_VERIFY|ANIM_VERIFY|结果:|\[PASS\]|\[FAIL\])" -or
        $_ -match "^(PROBE2D|PROBE25|LANDED|CAMERA|BEFORE|AFTER|COINS_LEFT|HUD=|SAVE_EXISTS|CHECK|SPRING)"
    } | ForEach-Object { Write-Host $_.TrimEnd() }
    $status = if ($log -match $OkMarker) { "ok" }
              elseif ($log -match "跳过:找不到 Godot" -or $log -match "SKIP: godot not found") { "skip" }
              else { "fail" }
    if ($status -eq "fail") {
        # 注意：函数里 Write-Output 会变成"返回值"被调用方捕获,看不见 ——
        # 所以透传日志一律用 Write-Host。
        Write-Host "---- $([System.IO.Path]::GetFileName($Script)) 输出(尾部 40 行) ----"
        ($log -split "`n") | Where-Object { $_.Trim() -ne "" } | Select-Object -Last 40 |
            ForEach-Object { Write-Host $_ }
    }
    return @{ Status = $status; Log = $log }
}

# ---------- 1. 离线单元测试 ----------
Step "1/6 离线代码生成测试 (不需要 Blender)"
& python (Join-Path $Root "tests\test_codegen.py") 2>&1 | Select-String -Pattern "^(Ran |OK|FAILED|ERROR:|FAIL:)" | ForEach-Object { $_.Line }
if ($LASTEXITCODE -ne 0) { Write-Error "离线测试失败"; exit 1 }

# ---------- 1b. 示例完整性体检 ----------
# 第四十五轮固化:目录改名打断脚本路径(ANIM_VERIFY 那次)、示例缺 README、
# 新示例有探针却没接进门禁 —— 这三类问题都在这里判红。
Step "1b/6 示例完整性体检 (目录 / README / 重建 / 探针 / 门禁)"
& python (Join-Path $Root "scripts\check_examples.py") 2>&1 |
    Select-String -Pattern "^(示例|demo_|✗|CHECK_EXAMPLES)" | ForEach-Object { $_.Line }
if ($LASTEXITCODE -ne 0) { Write-Error "示例完整性体检失败"; exit 1 }

# ---------- 1b2. 节点手册是不是最新的 ----------
# ── 为什么这条要进守则 ──────────────────────────────────────────────
# README 里那张节点表原来是**手写**的,于是它一直是过期的:表头写"182 个"
# 而实际已经 188,新加的节点一个都没进去。手写的清单**注定会漂**。
# 现在节点手册从代码生成(`make_node_docs.py`),这条检查就是"改了节点但
# 忘了重新生成手册"的看门狗 —— 报红时跑一次那个脚本即可。
Step "1b2/6 节点手册新鲜度(docs\节点手册.md 是否与代码一致)"
$docOut = & python (Join-Path $Root "scripts\make_node_docs.py") --check 2>&1 | Out-String
$docOut -split "`n" | Where-Object { $_.Trim() -ne "" } |
    ForEach-Object { Write-Host $_.TrimEnd() }
if ($docOut -notmatch "CHECK_NODE_DOCS=OK") {
    Write-Error "节点手册过期(跑一次 python scripts\make_node_docs.py)"
    exit 1
}

# ---------- 1c. 着色器编译校验 ----------
# ── 为什么接进门禁(第五十一轮真机抓到)──────────────────────────────
# 『屏幕_滤镜』的着色器源码是**写在 Python 里的字符串**,不是 .gdshader
# 文件 —— 只扫 shaders/ 目录那条链路看不到它。结果它一直在用 Godot 3 时代
# 的 `SCREEN_TEXTURE`(Godot 4.7 已删),导出的材质**根本编译不过**,
# 而无人发现:无头探针是 dummy 渲染器,着色器编译错误不进断言。
# 现在把"磁盘上的 + 代码里内嵌的"着色器都交给真 Godot 编译一遍。
# ⚠ 这一步**发现着色器编译错误会真报错**(不是提醒),因为那类错的症状是
#   "游戏里什么都没发生",靠肉眼极难查。
Step "1c/6 着色器编译校验(真 Godot 编译)"
$shaderOut = & python (Join-Path $Root "scripts\verify_shaders.py") 2>&1 | Out-String
$shaderOut -split "`n" | Where-Object { $_.Trim() -ne "" } |
    ForEach-Object { Write-Host $_.TrimEnd() }
if ($shaderOut -match "跳过:找不到 Godot") {
    Write-Output "（本机没装 Godot,着色器编译校验跳过）"
} elseif ($LASTEXITCODE -ne 0) {
    Write-Error "着色器编译校验失败"
    exit 1
}

# ---------- 2. 构建扩展 ----------
Step "2/6 构建 Blender 扩展"
New-Item -ItemType Directory -Force -Path $dist | Out-Null
& $Blender --command extension build --source-dir $pkg --output-dir $dist 2>&1 |
    Select-String -Pattern "building|complete|created|FATAL|Error" | ForEach-Object { $_.Line }
if (-not (Test-Path $zip)) { Write-Error "构建失败，没有生成 zip"; exit 1 }
Write-Output ("扩展包: {0} ({1:N0} 字节)" -f $zip, (Get-Item $zip).Length)

# ---------- 3. 干净重装 ----------
if (-not $SkipInstall) {
    Step "3/6 重装到临时测试仓库(不会动 user_default)"
    Remove-TestRepo
    & $Blender --command extension repo-add $testRepoName --name "GK Test" --directory $repo 2>&1 |
        Select-String -Pattern "Error|error" | ForEach-Object { $_.Line }
    $log = & $Blender --command extension install-file -r $testRepoName -e $zip 2>&1
    # 安装期会顺带加载插件，把注册错误挑出来
    $errors = $log | Select-String -Pattern "注册失败|挂载失败|Traceback|RuntimeError|AttributeError" |
        ForEach-Object { $_.Line }
    if ($errors) {
        Write-Output "!! 安装/加载期发现问题:"
        $errors | Select-Object -First 10 | ForEach-Object { Write-Output "   $_" }
    } else {
        Write-Output "安装期无注册错误"
    }
} else {
    Step "3/6 跳过重装"
}

# ---------- 4. 冒烟测试 ----------
Step "4/6 Blender 冒烟测试"
# 注意:不能加 --factory-startup，否则 Blender 看不到用户扩展仓库
$smoke = Join-Path $Root "tests\blender_smoke.py"
$out = & $Blender -b --python $smoke 2>&1
$text = $out | Out-String
$text -split "`n" | Select-String -Pattern "^\[(PASS|FAIL)\]|^结果:|^失败项:|^SMOKE_RESULT" |
    ForEach-Object { $_.Line }
$smokeOk = $text -match "SMOKE_RESULT=OK"

# ---------- 4b. 侧栏面板 draw() ----------
# ── 为什么要单列这一关(第五十五轮真机抓到)────────────────────────
# 侧栏瘦身时把 `_draw_other_trees_box(layout)` 误写成需要 context —— 冒烟
# 全绿,**但侧栏整块画不出来**(draw() 直接抛异常,面板变空)。
# 原因:冒烟测的是"算子在不在、逻辑对不对",而 `draw()` 只在**真画一次**
# 的时候才会执行。这一关就是真调一次 `GK_PT_Main.draw()`,专抓这类错。
$panelsScript = Join-Path $Root "tests\blender_panels.py"
if (Test-Path $panelsScript) {
    Step "4b/6 侧栏面板 draw()"
    $panelOut = & $Blender -b --python $panelsScript 2>&1
    $panelText = $panelOut | Out-String
    $panelText -split "`n" | Select-String -Pattern "^\[(PASS|FAIL)\]|^结果:|^失败项:" |
        ForEach-Object { $_.Line }
    # 脚本失败时退出码是 1(它以 `sys.exit(0 if ... else 1)` 收尾)
    if ($LASTEXITCODE -ne 0) {
        # 这一关失败 = 侧栏可能整个画不出来,比冒烟里的逻辑错更严重
        $smokeOk = $false
    }
} else {
    Step "4b/6 跳过侧栏面板 draw()(没有 tests\blender_panels.py)"
}

# ---------- 5. Godot 端到端 ----------
# ── 为什么要单列这一关 ──────────────────────────────────────────────
# 前面 1~4 步都停在"Blender 里看起来对"。可生成物**是给 Godot 吃的** ——
# 基类和 .tscn 节点类型不一致、InputMap 少登记一个动作、资源缓存是旧的,
# 这些都只在 Godot 里才炸(历史反复踩过)。所以最后一关必须真跑 Godot。
# 默认两层(都离线生成,产物写在 _dist,不碰已入库的 examples):
#   5a  2D 示例    节点图 -> GDScript -> 导入编译 -> 真跑 -> 模拟按键行为断言
#   5b  平台跳跃   重力/落地、相机、吃金币加分、HUD、存档落盘
# 加 -Deep 再跑 5c 动画系统六层验证(要真 Blender,耗时长)。
$godotOk = $true
$godotSkipped = $false
$godotRan = @()
if ($SkipGodot) {
    Step "5/6 跳过 Godot 端到端(-SkipGodot)"
} else {
    Step "5/6 Godot 端到端(2D 示例 + 平台跳跃)"
    # 5a. 2D 示例:节点图 -> GDScript -> Godot 导入编译 -> 真跑 -> 行为断言
    $r = Invoke-Verify -Script (Join-Path $Root "scripts\godot_verify.ps1") `
        -CallArgs @("-Root", $Root, "-Godot", $Godot, "-OutDir", (Join-Path $dist "godot_e2e")) `
        -OkMarker "GODOT_VERIFY=OK"
    switch ($r.Status) {
        "ok"    { $godotRan += "2D示例" }
        "skip"  { $godotSkipped = $true }
        default { $godotOk = $false }
    }

    # 5b. 平台跳跃:重力/落地、相机、吃金币加分、HUD、存档落盘
    if (-not $godotSkipped) {
        Write-Output ""
        $r = Invoke-Verify -Script (Join-Path $Root "scripts\platformer_verify.ps1") `
            -CallArgs @("-Root", $Root, "-Godot", $Godot, "-OutDir", (Join-Path $dist "platformer_e2e")) `
            -OkMarker "PLATFORMER_VERIFY=OK"
        switch ($r.Status) {
            "ok"    { $godotRan += "平台跳跃" }
            "skip"  { $godotSkipped = $true }
            default { $godotOk = $false }
        }
    }

    # 5c. 动画系统深层验证(仅 -Deep):6 层,含真 Blender 建图 + 真 Godot 跑探针,
    #     耗时长且会重建 examples\demo_anim_tree,所以不放进默认门禁。
    if ($Deep -and -not $godotSkipped) {
        Write-Output ""
        if (-not (Test-Path $Blender)) {
            Write-Output "跳过动画深层验证:找不到 Blender"
        } else {
            Step "5c 动画系统深层验证(-Deep)"
            $r = Invoke-Verify -Script (Join-Path $Root "scripts\verify_anim_system.ps1") `
                -CallArgs @("-Root", $Root, "-Blender", $Blender) `
                -OkMarker "ANIM_VERIFY=OK"
            switch ($r.Status) {
                "ok"    { $godotRan += "动画系统" }
                default { $godotOk = $false }
            }
        }
    }

    # 5d. 曲线运镜 + 时间轴示例:先用真 Blender **重建**示例(导出产物会跟着
    #     最新代码走),再用真 Godot 跑探针断言"现象"。
    #     ── 为什么要重建(否则这一步是假绿)────────────────────────────
    #     只检查仓库里那份已导出的工程的话,插件代码改了、示例没重导,
    #     这一关照样绿 —— 等于没测。重建 + 跑探针才算端到端。
    if (-not $godotSkipped) {
        Write-Output ""
        $demoDir = Join-Path $Root "examples\demo_curve"
        $maker = Join-Path $Root "scripts\make_curve_demo.py"
        if (-not (Test-Path $Blender)) {
            Write-Output "跳过曲线示例:找不到 Blender(示例要先重建才能真跑)"
        } elseif (-not (Test-Path $demoDir)) {
            Write-Output "跳过曲线示例:还没有 examples\demo_curve"
        } else {
            $buildLog = & $Blender -b --python $maker 2>&1 | Out-String
            if ($buildLog -notmatch "已保存") {
                Write-Host "---- 重建示例失败,尾部 20 行 ----"
                ($buildLog -split "`n") | Where-Object { $_.Trim() -ne "" } |
                    Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
                $godotOk = $false
            } else {
                $checker = Join-Path $Root "scripts\check_curve_demo.py"
                $log = & python $checker --godot $Godot 2>&1 | Out-String
                $log -split "`n" | Where-Object { $_ -match "^(DEMO|CHECK_CURVE_DEMO|跳过|✗)" } |
                    ForEach-Object { Write-Host $_.TrimEnd() }
                if ($log -match "CHECK_CURVE_DEMO=OK") {
                    $godotRan += "曲线示例"
                } elseif ($log -match "跳过") {
                    Write-Output "（曲线示例跳过）"
                } else {
                    $godotOk = $false
                }
            }
        }
    }

    # 5e. 小游戏示例(第二十四轮):同样"先重建再跑探针"。
    #     这一关测的是**整条链路**:角色移动 / 触发区拾取 / 粒子 / 时间轴巡逻 /
    #     HUD / 受伤 / 胜负 / 按 R 重开 —— 真 Godot 里把这一关玩一遍。
    if (-not $godotSkipped) {
        Write-Output ""
        $gameDir = Join-Path $Root "examples\demo_game"
        $gameMaker = Join-Path $Root "scripts\make_game_demo.py"
        if (-not (Test-Path $Blender)) {
            Write-Output "跳过小游戏示例:找不到 Blender(示例要先重建才能真跑)"
        } elseif (-not (Test-Path $gameDir)) {
            Write-Output "跳过小游戏示例:还没有 examples\demo_game"
        } else {
            $gameLog = & $Blender -b --python $gameMaker 2>&1 | Out-String
            if ($gameLog -notmatch "已保存") {
                Write-Host "---- 重建小游戏示例失败,尾部 20 行 ----"
                ($gameLog -split "`n") | Where-Object { $_.Trim() -ne "" } |
                    Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
                $godotOk = $false
            } else {
                $gameChecker = Join-Path $Root "scripts\check_game_demo.py"
                $gameOut = & python $gameChecker --godot $Godot 2>&1 | Out-String
                $gameOut -split "`n" | Where-Object { $_ -match "^(DEMO|CHECK_GAME_DEMO|跳过|✗)" } |
                    ForEach-Object { Write-Host $_.TrimEnd() }
                if ($gameOut -match "CHECK_GAME_DEMO=OK") {
                    $godotRan += "小游戏示例"
                } elseif ($gameOut -match "跳过") {
                    Write-Output "（小游戏示例跳过）"
                } else {
                    $godotOk = $false
                }
            }
        }

        # 5f. 物理测试场(第二十八轮):斜坡 / 台阶 / 对角方块 / 推箱子 /
        #     多碰撞体 / 窄缝 / 薄墙 —— 真 Godot 里把角色一个个工位走一遍。
        Write-Output ""
        $physDir = Join-Path $Root "examples\demo_physics"
        $physMaker = Join-Path $Root "scripts\make_physics_demo.py"
        if (-not (Test-Path $Blender)) {
            Write-Output "跳过物理测试场:找不到 Blender(示例要先重建才能真跑)"
        } elseif (-not (Test-Path $physDir)) {
            Write-Output "跳过物理测试场:还没有 examples\demo_physics"
        } else {
            $physLog = & $Blender -b --python $physMaker 2>&1 | Out-String
            if ($physLog -notmatch "已保存") {
                Write-Host "---- 重建物理测试场失败,尾部 20 行 ----"
                ($physLog -split "`n") | Where-Object { $_.Trim() -ne "" } |
                    Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
                $godotOk = $false
            } else {
                $physChecker = Join-Path $Root "scripts\check_physics_demo.py"
                $physOut = & python $physChecker --godot $Godot 2>&1 | Out-String
                $physOut -split "`n" | Where-Object { $_ -match "^(PHYS|CHECK_PHYSICS_DEMO|跳过|✗)" } |
                    ForEach-Object { Write-Host $_.TrimEnd() }
                if ($physOut -match "CHECK_PHYSICS_DEMO=OK") {
                    $godotRan += "物理测试场"
                } elseif ($physOut -match "跳过") {
                    Write-Output "（物理测试场跳过）"
                } else {
                    $godotOk = $false
                }
            }
        }

        # 5g. 2D 寻路示例(第三十六轮:寻路第二版)—— 俯视 2D 里『移动_寻路』
        #     真绕开障碍追人。这一层验的是 2D 导出的整条链路:
        #     NavigationRegion2D + NavigationPolygon 子资源 + 环境脚本的 2D 烘焙
        #     (碰撞体当**障碍**、先按包围盒算外框)+ 生成脚本的 2D 路径查询。
        Write-Output ""
        $nav2dDir = Join-Path $Root "examples\demo_nav2d"
        $nav2dMaker = Join-Path $Root "scripts\make_nav2d_demo.py"
        if (-not (Test-Path $Blender)) {
            Write-Output "跳过 2D 寻路示例:找不到 Blender(示例要先重建才能真跑)"
        } elseif (-not (Test-Path $nav2dDir)) {
            Write-Output "跳过 2D 寻路示例:还没有 examples\demo_nav2d"
        } else {
            $nav2dLog = & $Blender -b --python $nav2dMaker 2>&1 | Out-String
            if ($nav2dLog -notmatch "已保存") {
                Write-Host "---- 重建 2D 寻路示例失败,尾部 20 行 ----"
                ($nav2dLog -split "`n") | Where-Object { $_.Trim() -ne "" } |
                    Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
                $godotOk = $false
            } else {
                $nav2dChecker = Join-Path $Root "scripts\check_nav2d_demo.py"
                $nav2dOut = & python $nav2dChecker --godot $Godot 2>&1 | Out-String
                $nav2dOut -split "`n" | Where-Object { $_ -match "^(NAV2D|CHECK_NAV2D_DEMO|跳过|✗)" } |
                    ForEach-Object { Write-Host $_.TrimEnd() }
                if ($nav2dOut -match "CHECK_NAV2D_DEMO=OK") {
                    $godotRan += "2D寻路示例"
                } elseif ($nav2dOut -match "跳过") {
                    Write-Output "（2D 寻路示例跳过）"
                } else {
                    $godotOk = $false
                }
            }
        }
    }

    # 5h. 界面示例(第三十七轮:UI 设计)—— 图片 / 面板 / 对齐 / 设置 /
    #     对话框 / 暂停菜单都在这条链上。这一层验的是界面这套的整条链路:
    #     控件建出来 + 图片落盘并加载 + 父控件(显隐跟着走)+ 锚点对齐
    #     + 运行时改控件 + 对话框逐字/点按 + Esc 暂停/按钮解冻。
    Write-Output ""
    $uiDir = Join-Path $Root "examples\demo_ui"
    $uiMaker = Join-Path $Root "scripts\make_ui_demo.py"
    if (-not (Test-Path $Blender)) {
        Write-Output "跳过界面示例:找不到 Blender(示例要先重建才能真跑)"
    } elseif (-not (Test-Path $uiDir)) {
        Write-Output "跳过界面示例:还没有 examples\demo_ui"
    } else {
        $uiLog = & $Blender -b --python $uiMaker 2>&1 | Out-String
        if ($uiLog -notmatch "已保存") {
            Write-Host "---- 重建界面示例失败,尾部 20 行 ----"
            ($uiLog -split "`n") | Where-Object { $_.Trim() -ne "" } |
                Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
            $godotOk = $false
        } else {
            $uiChecker = Join-Path $Root "scripts\check_ui_demo.py"
            $uiOut = & python $uiChecker --godot $Godot 2>&1 | Out-String
            $uiOut -split "`n" | Where-Object { $_ -match "^(UI_|CHECK_UI_DEMO|跳过|✗|art/)" } |
                ForEach-Object { Write-Host $_.TrimEnd() }
            if ($uiOut -match "CHECK_UI_DEMO=OK") {
                $godotRan += "界面示例"
            } elseif ($uiOut -match "跳过") {
                Write-Output "（界面示例跳过）"
            } else {
                $godotOk = $false
            }
        }
    }

    # 5i. AI 示例(第四十一轮:游戏AI)—— 一个会巡逻、会追、追丢了自己回去的敌人。
    #     这一层验的是 AI 图这套的整条链路:状态机真的在跑(位置在变)、
    #     『看见目标』切追击(而且真的追近了)、『丢失目标』回巡逻、
    #     以及生成物里的行为 / 距离条件 / 巡逻折返。
    Write-Output ""
    $aiDir = Join-Path $Root "examples\demo_ai"
    $aiMaker = Join-Path $Root "scripts\make_ai_demo.py"
    if (-not (Test-Path $Blender)) {
        Write-Output "跳过 AI 示例:找不到 Blender(示例要先重建才能真跑)"
    } elseif (-not (Test-Path $aiDir)) {
        Write-Output "跳过 AI 示例:还没有 examples\demo_ai"
    } else {
        $aiLog = & $Blender -b --python $aiMaker 2>&1 | Out-String
        if ($aiLog -notmatch "已保存") {
            Write-Host "---- 重建 AI 示例失败,尾部 20 行 ----"
            ($aiLog -split "`n") | Where-Object { $_.Trim() -ne "" } |
                Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
            $godotOk = $false
        } else {
            $aiChecker = Join-Path $Root "scripts\check_ai_demo.py"
            $aiOut = & python $aiChecker --godot $Godot 2>&1 | Out-String
            $aiOut -split "`n" | Where-Object { $_ -match "^(AI_|CHECK_AI_DEMO|跳过|✗)" } |
                ForEach-Object { Write-Host $_.TrimEnd() }
            if ($aiOut -match "CHECK_AI_DEMO=OK") {
                $godotRan += "AI示例"
            } elseif ($aiOut -match "跳过") {
                Write-Output "（AI 示例跳过）"
            } else {
                $godotOk = $false
            }
        }
    }

    # 5j. 资产示例(第四十三轮:游戏资产)—— 资产库 + 跨场景 + 音频管线。
    #     这一层验的是:三棵树共用同一个 glb(一个模型导一次)、
    #     音频从资产库复制进 audio/ 并能在真 Godot 里 load 出来、
    #     图片 / 曲线资产都在产物里、第二关的树是跨场景摆过来的。
    Write-Output ""
    $assetDir = Join-Path $Root "examples\demo_assets"
    $assetMaker = Join-Path $Root "scripts\make_assets_demo.py"
    if (-not (Test-Path $Blender)) {
        Write-Output "跳过资产示例:找不到 Blender(示例要先重建才能真跑)"
    } elseif (-not (Test-Path $assetDir)) {
        Write-Output "跳过资产示例:还没有 examples\demo_assets"
    } else {
        $assetLog = & $Blender -b --gpu-backend vulkan --python $assetMaker 2>&1 | Out-String
        if ($assetLog -notmatch "已保存") {
            Write-Host "---- 重建资产示例失败,尾部 20 行 ----"
            ($assetLog -split "`n") | Where-Object { $_.Trim() -ne "" } |
                Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
            $godotOk = $false
        } else {
            $assetChecker = Join-Path $Root "scripts\check_assets_demo.py"
            $assetOut = & python $assetChecker --godot $Godot 2>&1 | Out-String
            $assetOut -split "`n" | Where-Object { $_ -match "^(ASSET_|CHECK_ASSETS_DEMO|跳过|✗)" } |
                ForEach-Object { Write-Host $_.TrimEnd() }
            if ($assetOut -match "CHECK_ASSETS_DEMO=OK") {
                $godotRan += "资产示例"
            } elseif ($assetOut -match "跳过") {
                Write-Output "（资产示例跳过）"
            } else {
                $godotOk = $false
            }
        }
    }

    # 5k. 关卡示例(多场景:关卡1 / 关卡2 各一张 .tscn)—— 第四十五轮接进门禁。
    #     它是"一关一张逻辑图 + 多场景导出"的能力展示,可它一直只在
    #     `-Deep` 那一层被跑过(verify_anim_system.ps1);这一层是默认门禁。
    #     自查脚本自己会导出一份工程并驱动真 Godot 跑(电梯 / 开车 / 切关卡)。
    Write-Output ""
    $levelMaker = Join-Path $Root "scripts\make_demo_level.py"
    $levelChecker = Join-Path $Root "scripts\check_level_example.py"
    if (-not (Test-Path $Blender)) {
        Write-Output "跳过关卡示例:找不到 Blender(示例要先重建才能真跑)"
    } else {
        $levelLog = & $Blender -b --gpu-backend vulkan --python $levelMaker 2>&1 | Out-String
        if ($levelLog -notmatch "已保存") {
            Write-Host "---- 重建关卡示例失败,尾部 20 行 ----"
            ($levelLog -split "`n") | Where-Object { $_.Trim() -ne "" } |
                Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
            $godotOk = $false
        } else {
            # 自查脚本里的运行时探针按 GK_GODOT 找引擎(没装就自动跳过探针)
            $env:GK_GODOT = $Godot
            $levelOut = & $Blender -b --gpu-backend vulkan --python $levelChecker 2>&1 | Out-String
            Remove-Item Env:\GK_GODOT -ErrorAction SilentlyContinue
            $levelOut -split "`n" | Where-Object { $_ -match "^(LEVEL|结果:|\[INFO\]|✗|\[FAIL\])" } |
                ForEach-Object { Write-Host $_.TrimEnd() }
            if ($levelOut -match "LEVEL_CHECK=OK") {
                $godotRan += "关卡示例"
            } else {
                $godotOk = $false
            }
        }
    }

    # 5l. 灯光示例(第四十八轮:阶段①灯光扩展)—— 三盏灯(平行光 / 点光 /
    #     聚光)的类型与参数、手电筒每帧贴相机、数字键开关灯、
    #     卡通材质按"影响最大的亮灯"重画明暗(全关 = 平涂)。
    Write-Output ""
    $lightDir = Join-Path $Root "examples\demo_lights"
    $lightMaker = Join-Path $Root "scripts\make_lights_demo.py"
    if (-not (Test-Path $Blender)) {
        Write-Output "跳过灯光示例:找不到 Blender(示例要先重建才能真跑)"
    } elseif (-not (Test-Path $lightDir)) {
        Write-Output "跳过灯光示例:还没有 examples\demo_lights"
    } else {
        $lightLog = & $Blender -b --python $lightMaker 2>&1 | Out-String
        if ($lightLog -notmatch "已保存") {
            Write-Host "---- 重建灯光示例失败,尾部 20 行 ----"
            ($lightLog -split "`n") | Where-Object { $_.Trim() -ne "" } |
                Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
            $godotOk = $false
        } else {
            $lightChecker = Join-Path $Root "scripts\check_lights_demo.py"
            $lightOut = & python $lightChecker --godot $Godot 2>&1 | Out-String
            $lightOut -split "`n" | Where-Object { $_ -match "^(LIGHTS|CHECK_LIGHTS_DEMO|跳过|✗)" } |
                ForEach-Object { Write-Host $_.TrimEnd() }
            if ($lightOut -match "CHECK_LIGHTS_DEMO=OK") {
                $godotRan += "灯光示例"
            } elseif ($lightOut -match "跳过") {
                Write-Output "（灯光示例跳过）"
            } else {
                $godotOk = $false
            }
        }
    }

    # 5m. 环境示例(第四十九轮:阶段②环境与后处理)—— 白天 / 幽暗两关对比。
    #     这一层验的是 WorldEnvironment 这套的整条链路:场景设置 -> .tscn 里的
    #     Environment 子资源 -> 真 Godot 里读回来的 背景 / 环境光 / 雾 /
    #     泛光 / 色调 五项,而且两关要**真的拉开差距**。
    Write-Output ""
    $envDir = Join-Path $Root "examples\demo_env"
    $envMaker = Join-Path $Root "scripts\make_env_demo.py"
    if (-not (Test-Path $Blender)) {
        Write-Output "跳过环境示例:找不到 Blender(示例要先重建才能真跑)"
    } elseif (-not (Test-Path $envDir)) {
        Write-Output "跳过环境示例:还没有 examples\demo_env"
    } else {
        $envLog = & $Blender -b --python $envMaker 2>&1 | Out-String
        if ($envLog -notmatch "已保存") {
            Write-Host "---- 重建环境示例失败,尾部 20 行 ----"
            ($envLog -split "`n") | Where-Object { $_.Trim() -ne "" } |
                Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
            $godotOk = $false
        } else {
            $envChecker = Join-Path $Root "scripts\check_env_demo.py"
            $envOut = & python $envChecker --godot $Godot 2>&1 | Out-String
            $envOut -split "`n" | Where-Object { $_ -match "^(ENV_|CHECK_ENV_DEMO|跳过|✗)" } |
                ForEach-Object { Write-Host $_.TrimEnd() }
            if ($envOut -match "CHECK_ENV_DEMO=OK") {
                $godotRan += "环境示例"
            } elseif ($envOut -match "跳过") {
                Write-Output "（环境示例跳过）"
            } else {
                $godotOk = $false
            }
        }
    }

    # 5n. 打包发布(第五十二轮:补全流程最后一环)—— 真 Godot 跑一次
    #     `--export-release`,断言磁盘上真的多出一个可执行文件。
    #     ── 没装导出模板时**跳过、不判红** ──────────────────────────────
    #     模板装没装是本机环境的事,和插件代码无关;装了模板的机器才会走到
    #     真打包那一步,那一步才是这一层的判据。脚本自己会打印"跳过"。
    Write-Output ""
    $releaseChecker = Join-Path $Root "scripts\check_release.py"
    if (-not (Test-Path $releaseChecker)) {
        Write-Output "跳过打包发布:还没有 scripts\check_release.py"
    } else {
        $releaseOut = & python $releaseChecker --godot $Godot 2>&1 | Out-String
        $releaseOut -split "`n" | Where-Object { $_ -match "^(RELEASE_|CHECK_RELEASE|跳过|✗)" } |
            ForEach-Object { Write-Host $_.TrimEnd() }
        if ($releaseOut -match "CHECK_RELEASE=OK") {
            $godotRan += "打包发布"
        } elseif ($releaseOut -match "跳过") {
            Write-Output "（打包发布跳过:本机没装 Godot 导出模板）"
        } else {
            $godotOk = $false
        }
    }

    # 5o. 恐怖示例(第五十九轮:阶段④收尾)—— 氛围套件 + 听觉 + 结局一条龙。
    #     这一层验的是:AI 巡逻、『氛围_紧张度』随距离升降 + 心跳联动、
    #     3 张纸条触发计数、走过惊吓区**相机真在震 + Master 真被压低**、
    #     门口弹结局选择、按 2 之后『结局』= 2 且真的落盘。
    Write-Output ""
    $horrorDir = Join-Path $Root "examples\demo_horror"
    $horrorMaker = Join-Path $Root "scripts\make_horror_demo.py"
    if (-not (Test-Path $Blender)) {
        Write-Output "跳过恐怖示例:找不到 Blender(示例要先重建才能真跑)"
    } elseif (-not (Test-Path $horrorDir)) {
        Write-Output "跳过恐怖示例:还没有 examples\demo_horror"
    } else {
        $horrorLog = & $Blender -b --python $horrorMaker 2>&1 | Out-String
        if ($horrorLog -notmatch "已保存") {
            Write-Host "---- 重建恐怖示例失败,尾部 20 行 ----"
            ($horrorLog -split "`n") | Where-Object { $_.Trim() -ne "" } |
                Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
            $godotOk = $false
        } else {
            $horrorChecker = Join-Path $Root "scripts\check_horror_demo.py"
            $horrorOut = & python $horrorChecker --godot $Godot 2>&1 | Out-String
            $horrorOut -split "`n" | Where-Object { $_ -match "^(HORROR_|CHECK_HORROR_DEMO|跳过|✗)" } |
                ForEach-Object { Write-Host $_.TrimEnd() }
            if ($horrorOut -match "CHECK_HORROR_DEMO=OK") {
                $godotRan += "恐怖示例"
            } elseif ($horrorOut -match "跳过") {
                Write-Output "（恐怖示例跳过）"
            } else {
                $godotOk = $false
            }
        }
    }

# 5p. 动画混合树示例(第六十一轮)—— 走↔跑连续过渡。
#     这一层验的是 AnimationTree + BlendSpace1D 这套:真的建出树、混合位置
#     0/1 时高度差得开、0.5 时落在中间(是"混"不是"二选一")、按 1/2 改参数。
    Write-Output ""
    $blendDir = Join-Path $Root "examples\demo_blend"
    $blendMaker = Join-Path $Root "scripts\make_blend_demo.py"
    if (-not (Test-Path $Blender)) {
        Write-Output "跳过混合树示例:找不到 Blender(示例要先重建才能真跑)"
    } elseif (-not (Test-Path $blendDir)) {
        Write-Output "跳过混合树示例:还没有 examples\demo_blend"
    } else {
        $blendLog = & $Blender -b --python $blendMaker 2>&1 | Out-String
        if ($blendLog -notmatch "已保存") {
            Write-Host "---- 重建混合树示例失败,尾部 20 行 ----"
            ($blendLog -split "`n") | Where-Object { $_.Trim() -ne "" } |
                Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
            $godotOk = $false
        } else {
            $blendChecker = Join-Path $Root "scripts\check_blend_demo.py"
            $blendOut = & python $blendChecker --godot $Godot 2>&1 | Out-String
            $blendOut -split "`n" | Where-Object { $_ -match "^(BLEND_|CHECK_BLEND_DEMO|跳过|✗)" } |
                ForEach-Object { Write-Host $_.TrimEnd() }
            if ($blendOut -match "CHECK_BLEND_DEMO=OK") {
                $godotRan += "混合树示例"
            } elseif ($blendOut -match "跳过") {
                Write-Output "（混合树示例跳过）"
            } else {
                $godotOk = $false
            }
        }
    }

# 5q. AI 绕墙追击示例(第六十三轮)—— AI 的移动行为直接走导航网格。
#     这一层验的是"寻路整合进 AI":场景勾了『寻路』之后,纯 AI 驱动的敌人
#     (图里一个『移动_寻路』都没有)会**绕过墙**追到人 —— 量三个数:
#     导航网格烤出来了(polys>0)、它横着走出 2.8 米以上(around,绕墙证据)、
#     最后贴到玩家 1.5 米以内(arrived)。
    Write-Output ""
    $aiNavDir = Join-Path $Root "examples\demo_ai_nav"
    $aiNavMaker = Join-Path $Root "scripts\make_ai_nav_demo.py"
    if (-not (Test-Path $Blender)) {
        Write-Output "跳过 AI 绕墙示例:找不到 Blender(示例要先重建才能真跑)"
    } elseif (-not (Test-Path $aiNavDir)) {
        Write-Output "跳过 AI 绕墙示例:还没有 examples\demo_ai_nav"
    } else {
        $aiNavLog = & $Blender -b --python $aiNavMaker 2>&1 | Out-String
        if ($aiNavLog -notmatch "已保存") {
            Write-Host "---- 重建 AI 绕墙示例失败,尾部 20 行 ----"
            ($aiNavLog -split "`n") | Where-Object { $_.Trim() -ne "" } |
                Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
            $godotOk = $false
        } else {
            $aiNavChecker = Join-Path $Root "scripts\check_ai_nav_demo.py"
            $aiNavOut = & python $aiNavChecker --godot $Godot 2>&1 | Out-String
            $aiNavOut -split "`n" | Where-Object { $_ -match "^(AINAV_|CHECK_AI_NAV_DEMO|跳过|✗)" } |
                ForEach-Object { Write-Host $_.TrimEnd() }
            if ($aiNavOut -match "CHECK_AI_NAV_DEMO=OK") {
                $godotRan += "AI绕墙示例"
            } elseif ($aiNavOut -match "跳过") {
                Write-Output "（AI 绕墙示例跳过）"
            } else {
                $godotOk = $false
            }
        }
    }

    # 5r. 关卡预设模板(第八十七轮:I 组 24)——『一键建能跑的一关』。
    #     Blender 里建 2D / 3D / 2.5D 三关 + 真导出(逐场景维度、各关 glb
    #     互不串),再用真 Godot 无头跑 3D 关:角色落地 / 按 D 走 /
    #     按 C 切相机 —— "跑得起来"这句话要有真机证据。
    Write-Output ""
    $lvTplChecker = Join-Path $Root "scripts\check_level_templates.py"
    if (-not (Test-Path $Blender)) {
        Write-Output "跳过关卡模板自查:找不到 Blender(它要在 Blender 里建关)"
    } elseif (-not (Test-Path $lvTplChecker)) {
        Write-Output "跳过关卡模板自查:没有 scripts\check_level_templates.py"
    } else {
        # 探针自己按 GK_GODOT / 默认路径找 Godot —— 把门禁用的那台传进去
        $env:GK_GODOT = $Godot
        $lvTplOut = & $Blender -b --python $lvTplChecker 2>&1 | Out-String
        $lvTplOut -split "`n" | Where-Object {
            $_ -match "^(\[PASS\]|\[FAIL\]|结果:|失败项:|CHECK_LEVEL_TEMPLATES|LEVELS_|跳过)"
        } | ForEach-Object { Write-Host $_.TrimEnd() }
        if ($lvTplOut -match "CHECK_LEVEL_TEMPLATES=OK") {
            $godotRan += "关卡模板"
        } elseif ($lvTplOut -match "找不到 Godot") {
            Write-Output "（关卡模板自查:运行时探针跳过 —— 只算了导出那段）"
            $godotRan += "关卡模板(导出段)"
        } else {
            $godotOk = $false
        }
    }

    # 5s. NPR 渲染验证(第九十一轮:I 组 32)—— `demo_render` 的**渲染结果**
    #     进守则。这一层要真 GPU 出图(`--headless` 是 dummy 渲染器,
    #     什么都画不出来),所以会**开一个真窗口**跑 4 个角度的扫光。
    #     断言的是结果而不是"文件存在":每张不黑、明暗随光角度变
    #     (面部阴影 SDF 在滑动)、四张两两字节不同(灯真的转了)。
    #     机器没有 GPU / 开不出窗口时探针自己打印"跳过",**不判红**
    #     —— 这一层是加分项,不能因为环境没显卡把守则整片弄红。
    Write-Output ""
    $renderChecker = Join-Path $Root "scripts\check_render_demo.py"
    if ($godotSkipped) {
        Write-Output "跳过渲染验证:本机找不到 Godot"
    } elseif (-not (Test-Path $renderChecker)) {
        Write-Output "跳过渲染验证:没有 scripts\check_render_demo.py"
    } else {
        # 探针自己按 GK_GODOT / 默认路径找 Godot —— 把门禁用的那台传进去
        $env:GK_GODOT = $Godot
        $renderOut = & python $renderChecker 2>&1 | Out-String
        $renderOut -split "`n" | Where-Object {
            $_ -match "^(\[渲染验证\]|CHECK_RENDER_DEMO|跳过)"
        } | ForEach-Object { Write-Host $_.TrimEnd() }
        if ($renderOut -match "CHECK_RENDER_DEMO=OK") {
            $godotRan += "NPR 渲染"
        } elseif ($renderOut -match "跳过") {
            Write-Output "（渲染验证跳过:这台机器没有可用 GPU / 开不出窗口）"
        } else {
            $godotOk = $false
        }
    }

    # 5t. 网络联机示例(第一百轮:房间 + 自定义消息)—— **同机起两个 Godot
    #     进程真连一次**:一个开房、一个加入,互相收发消息再断开。
    #     ── 为什么必须两个进程 ────────────────────────────────────────
    #     Godot 一个 SceneTree 只有一个 multiplayer_peer —— "开房"和"加入"
    #     只能在两个进程里。探针把示例场景**复制一份、只换脚本**当"对端"
    #     (RPC 按节点路径找对端,节点结构必须逐字节一致,不然收不到消息)。
    Write-Output ""
    $netDir = Join-Path $Root "examples\demo_net"
    $netMaker = Join-Path $Root "scripts\make_net_demo.py"
    if ($godotSkipped) {
        Write-Output "跳过联机示例:本机找不到 Godot(要两个进程真连)"
    } elseif (-not (Test-Path $Blender)) {
        Write-Output "跳过联机示例:找不到 Blender(示例要先重建才能真跑)"
    } elseif (-not (Test-Path $netDir)) {
        Write-Output "跳过联机示例:还没有 examples\demo_net"
    } else {
        $netLog = & $Blender -b --python $netMaker 2>&1 | Out-String
        if ($netLog -notmatch "已保存") {
            Write-Host "---- 重建联机示例失败,尾部 20 行 ----"
            ($netLog -split "`n") | Where-Object { $_.Trim() -ne "" } |
                Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
            $godotOk = $false
        } else {
            $netChecker = Join-Path $Root "scripts\check_net_demo.py"
            $netOut = & python $netChecker --godot $Godot 2>&1 | Out-String
            $netOut -split "`n" | Where-Object {
                $_ -match "^(----|\[PASS\]|\[FAIL\]|CHECK_NET_DEMO|跳过|✗)"
            } | ForEach-Object { Write-Host $_.TrimEnd() }
            if ($netOut -match "CHECK_NET_DEMO=OK") {
                $godotRan += "联机示例"
            } elseif ($netOut -match "跳过") {
                Write-Output "（联机示例跳过）"
            } else {
                $godotOk = $false
            }
        }
    }

    # 5u. 集合层级示例(第一百零二轮:集合 -> Godot 节点分组)—— 同样
    #     "先重建再跑探针":真 Blender 里摆几个集合(含嵌套 / 撞名 / 空),
    #     导出后真 Godot 里用**路径**取节点(`房间A/A_墙`)—— 证明 Blender
    #     的集合真的成了场景树分组,而且碰撞 / 材质 / 走路都没被弄坏。
    Write-Output ""
    $groupsDir = Join-Path $Root "examples\demo_groups"
    $groupsMaker = Join-Path $Root "scripts\make_groups_demo.py"
    if ($godotSkipped) {
        Write-Output "跳过集合层级示例:本机找不到 Godot"
    } elseif (-not (Test-Path $Blender)) {
        Write-Output "跳过集合层级示例:找不到 Blender(示例要先重建才能真跑)"
    } elseif (-not (Test-Path $groupsDir)) {
        Write-Output "跳过集合层级示例:还没有 examples\demo_groups"
    } else {
        $groupsLog = & $Blender -b --python $groupsMaker 2>&1 | Out-String
        if ($groupsLog -notmatch "已保存") {
            Write-Host "---- 重建集合层级示例失败,尾部 20 行 ----"
            ($groupsLog -split "`n") | Where-Object { $_.Trim() -ne "" } |
                Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
            $godotOk = $false
        } else {
            $groupsChecker = Join-Path $Root "scripts\check_groups_demo.py"
            $groupsOut = & python $groupsChecker --godot $Godot 2>&1 | Out-String
            $groupsOut -split "`n" | Where-Object {
                $_ -match "^(GROUPS_|CHECK_GROUPS_DEMO|跳过|✗)"
            } | ForEach-Object { Write-Host $_.TrimEnd() }
            if ($groupsOut -match "CHECK_GROUPS_DEMO=OK") {
                $godotRan += "集合层级示例"
            } elseif ($groupsOut -match "跳过") {
                Write-Output "（集合层级示例跳过）"
            } else {
                $godotOk = $false
            }
        }
    }

    # 5v. 界面"所见即所得"的真 GUI 校验(第一百零三轮)—— 真开一次 Blender
    #     窗口,在真 3D 视口上把界面预览画一遍(无头模式没有 GPU 上下文,
    #     圆角 / 贴真图这条链只能这样验),并截图到 _dist 供人工看。
    #     ⚠ 会闪一下窗口;没装 Blender / 起不了窗口时自动跳过。
    Write-Output ""
    $dragChecker = Join-Path $Root "scripts\check_ui_drag.py"
    if (-not (Test-Path $dragChecker)) {
        Write-Output "跳过界面真机校验:还没有 scripts\check_ui_drag.py"
    } else {
        $dragOut = & python $dragChecker --blender $Blender 2>&1 | Out-String
        $dragOut -split "`n" | Where-Object {
            $_ -match "^(UI_DRAG|CHECK_UI_DRAG|跳过|✗)"
        } | ForEach-Object { Write-Host $_.TrimEnd() }
        if ($dragOut -match "CHECK_UI_DRAG=OK") {
            $godotRan += "界面真机校验"
        } elseif ($dragOut -match "跳过") {
            Write-Output "（界面真机校验跳过）"
        } else {
            $godotOk = $false
        }
    }

    # 5w. 剧情闯关示例(第一百零四轮:3D 恐怖闯关,第一/三人称可切换)——
    #     三关(大堂 / 走廊 / 结局)各一张 .tscn。这一层验的是:章节卡 + HUD、
    #     **C 切第一人称 / V 切第三人称**、拾钥匙、F 手电(亮 + 掉电)、大门链、
    #     走廊 AI(巡逻 / 躲藏 / 看见就追 + 心跳惊吓)、结局选择 + 存档落盘。
    #     ⚠ 关底切关卡那句探测不了(change_scene 会把探针换掉),改用
    #     **出码文本**验 —— 这就是和 5o 那层"全靠真 Godot 行为"的区别。
    #     (5p..5v 已被别的示例占用,故按顺序追加在末尾。)
    Write-Output ""
    $storyDir = Join-Path $Root "examples\demo_story"
    $storyMaker = Join-Path $Root "scripts\make_story_demo.py"
    if (-not (Test-Path $Blender)) {
        Write-Output "跳过剧情闯关示例:找不到 Blender(示例要先重建才能真跑)"
    } elseif (-not (Test-Path $storyDir)) {
        Write-Output "跳过剧情闯关示例:还没有 examples\demo_story"
    } else {
        $storyLog = & $Blender -b --python $storyMaker 2>&1 | Out-String
        if ($storyLog -notmatch "已保存") {
            Write-Host "---- 重建剧情闯关示例失败,尾部 20 行 ----"
            ($storyLog -split "`n") | Where-Object { $_.Trim() -ne "" } |
                Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
            $godotOk = $false
        } else {
            $storyChecker = Join-Path $Root "scripts\check_story_demo.py"
            $storyOut = & python $storyChecker --godot $Godot 2>&1 | Out-String
            $storyOut -split "`n" | Where-Object {
                $_ -match "^(STORY_|CHECK_STORY_DEMO|跳过|✗)"
            } | ForEach-Object { Write-Host $_.TrimEnd() }
            if ($storyOut -match "CHECK_STORY_DEMO=OK") {
                $godotRan += "剧情闯关示例"
            } elseif ($storyOut -match "跳过") {
                Write-Output "（剧情闯关示例跳过）"
            } else {
                $godotOk = $false
            }
        }
    }

    if ($godotSkipped) {
        Write-Output "（本机没装 Godot,端到端这一关跳过;装了 Godot 才能算真正跑通）"
    } elseif ($godotOk) {
        Write-Output ("Godot 端到端通过: " + ($godotRan -join " / "))
    }
}

# ---------- 6. 汇总 ----------
Step "6/6 结果"
Remove-TestRepo
if ($smokeOk -and $godotOk) {
    if ($godotSkipped) {
        Write-Output "全部通过（Godot 端到端未跑:本机找不到 Godot）"
    } else {
        Write-Output ("全部通过（含 Godot 端到端: " + ($godotRan -join " / ") + "）")
    }
    Write-Output "（测试仓库已注销,Blender 里不会留下多余的插件条目）"
    exit 0
} else {
    if (-not $smokeOk) {
        Write-Output "冒烟测试未通过"
        Write-Output "--- 失败详情(尾部 60 行) ---"
        ($text -split "`n") | Select-Object -Last 60 | ForEach-Object { Write-Output $_ }
    }
    if (-not $godotOk) {
        Write-Output "Godot 端到端未通过（上方已打印失败脚本的尾部输出）"
    }
    exit 1
}
