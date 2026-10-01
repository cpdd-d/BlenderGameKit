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
    [switch]$SkipInstall,
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

# ---------- 1. 离线单元测试 ----------
Step "1/5 离线代码生成测试 (不需要 Blender)"
& python (Join-Path $Root "tests\test_codegen.py") 2>&1 | Select-String -Pattern "^(Ran |OK|FAILED|ERROR:|FAIL:)" | ForEach-Object { $_.Line }
if ($LASTEXITCODE -ne 0) { Write-Error "离线测试失败"; exit 1 }

# ---------- 2. 构建扩展 ----------
Step "2/5 构建 Blender 扩展"
New-Item -ItemType Directory -Force -Path $dist | Out-Null
& $Blender --command extension build --source-dir $pkg --output-dir $dist 2>&1 |
    Select-String -Pattern "building|complete|created|FATAL|Error" | ForEach-Object { $_.Line }
if (-not (Test-Path $zip)) { Write-Error "构建失败，没有生成 zip"; exit 1 }
Write-Output ("扩展包: {0} ({1:N0} 字节)" -f $zip, (Get-Item $zip).Length)

# ---------- 3. 干净重装 ----------
if (-not $SkipInstall) {
    Step "3/5 重装到临时测试仓库(不会动 user_default)"
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
    Step "3/5 跳过重装"
}

# ---------- 4. 冒烟测试 ----------
Step "4/5 Blender 冒烟测试"
# 注意:不能加 --factory-startup，否则 Blender 看不到用户扩展仓库
$smoke = Join-Path $Root "tests\blender_smoke.py"
$out = & $Blender -b --python $smoke 2>&1
$text = $out | Out-String
$text -split "`n" | Select-String -Pattern "^\[(PASS|FAIL)\]|^结果:|^失败项:|^SMOKE_RESULT" |
    ForEach-Object { $_.Line }
$smokeOk = $text -match "SMOKE_RESULT=OK"

# ---------- 5. 汇总 ----------
Step "5/5 结果"
Remove-TestRepo
if ($smokeOk) {
    Write-Output "全部通过"
    Write-Output "（测试仓库已注销,Blender 里不会留下多余的插件条目）"
    exit 0
} else {
    Write-Output "冒烟测试未通过"
    Write-Output "--- 失败详情(尾部 60 行) ---"
    ($text -split "`n") | Select-Object -Last 60 | ForEach-Object { Write-Output $_ }
    exit 1
}
