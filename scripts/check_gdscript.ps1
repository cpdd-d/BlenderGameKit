# 把一份生成的 GDScript 交给真实 Godot 解析一遍(编译校验)
#
# 用法:
#   powershell -File scripts\check_gdscript.ps1 -Script _dist\anim_state_machine.gd
#
# ── 为什么需要它 ────────────────────────────────────────────────────
# 离线测试查的是"生成器写出了我以为的字符串",这一层查的是
# **Godot 到底认不认**。两者差得很远:
#   * `if a == "x" and:` 这种半截条件,字符串检查照样能过
#   * `match` 的分支缩进、`var x := ...` 的类型推断,只有真解析才知道
# `--check-only` 只解析不运行,秒级返回。
param(
    [Parameter(Mandatory = $true)][string]$Script,
    [string]$Godot = "D:\godot\Godot_v4.7.2-stable_win64_console.exe"
)

$ErrorActionPreference = "Continue"

if (-not (Test-Path $Godot)) {
    Write-Output "跳过:找不到 Godot ($Godot)"
    exit 0
}
if (-not (Test-Path $Script)) {
    Write-Output "找不到要检查的脚本: $Script"
    exit 1
}

# Godot 的 --script 只认 res:// 路径,所以搭一个临时工程把文件放进去
$tmp = Join-Path $env:TEMP ("gk_gd_check_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
@"
config_version=5

[application]
config/name="gk_check"
"@ | Set-Content -Path (Join-Path $tmp "project.godot") -Encoding utf8
Copy-Item $Script (Join-Path $tmp "check.gd") -Force

$log = & $Godot --headless --path $tmp --check-only --script res://check.gd 2>&1 | Out-String
Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue

$errors = $log -split "`n" | Where-Object {
    $_ -match "SCRIPT ERROR|Parse Error|ERROR:|error at"
}
if ($errors) {
    Write-Output "Godot 拒绝了这个脚本:"
    $errors | Select-Object -First 20 | ForEach-Object { Write-Output "   $_" }
    Write-Output ""
    Write-Output "GDSCRIPT_CHECK=FAILED"
    exit 1
}
Write-Output "Godot 解析通过: $Script"
Write-Output "GDSCRIPT_CHECK=OK"
exit 0
