# 构建插件并安装到 Blender 的默认用户仓库(装完就能直接在 Blender 里用)
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#
# 装完重启 Blender(或重新加载扩展)，然后在"节点编辑器 > 侧栏(N)"里
# 就能看到"游戏逻辑"面板。

param(
    [string]$Blender = "D:\blender-4.5.5-windows-x64\blender.exe",
    [string]$Root = "D:\BlenderGameKit"
)

$ErrorActionPreference = "Continue"
$dist = Join-Path $Root "_dist"
$pkg = Join-Path $Root "BlenderGameKit"
$zip = Join-Path $dist "blender_game_kit-0.1.0.zip"

if (-not (Test-Path $Blender)) {
    Write-Output "找不到 Blender: $Blender"
    Write-Output "请用 -Blender <路径> 指定你机器上的 blender.exe"
    exit 1
}

Write-Output "== 1/3 构建扩展 =="
New-Item -ItemType Directory -Force -Path $dist | Out-Null
& $Blender --command extension build --source-dir $pkg --output-dir $dist 2>&1 |
    Select-String -Pattern "building|complete|created|FATAL|Error" | ForEach-Object { $_.Line }
if (-not (Test-Path $zip)) { Write-Output "构建失败"; exit 1 }

Write-Output ""
Write-Output "== 2/3 安装到 user_default 仓库 =="
& $Blender --command extension install-file -r user_default -e $zip 2>&1 |
    Select-String -Pattern "Error|error|注册失败" | ForEach-Object { $_.Line }

Write-Output ""
Write-Output "== 3/3 检查 =="
$list = & $Blender --command extension list 2>&1 | Out-String
if ($list -match "blender_game_kit") {
    Write-Output "已安装: blender_game_kit"
    $list -split "`n" | Select-String -Pattern "blender_game_kit" -Context 0,3 |
        ForEach-Object { $_.Line; $_.Context.PostContext }
} else {
    Write-Output "安装结果里没找到 blender_game_kit，请检查上面的输出"
    exit 1
}

Write-Output ""
Write-Output "完成。打开 Blender -> 节点编辑器 -> 新建『游戏逻辑』节点树 -> 按 N 打开侧栏。"
