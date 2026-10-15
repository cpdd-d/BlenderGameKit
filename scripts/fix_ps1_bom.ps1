# 修复 PS 脚本的 UTF-8 BOM(自愈入口)。
#
# 用法:
#   pwsh -File scripts\fix_ps1_bom.ps1
#   # PowerShell 本身跑不起来时,用等价的 Python 版:
#   python scripts\fix_ps1_bom.py
#
# ── 为什么要有这个脚本(第 124 轮:同一个坑踩了三次)──────────────────
# Windows PowerShell 5.1 读**无 BOM** 的文件时按系统 ANSI 代码页(这台机器
# 是 GBK)解码。`build_and_test.ps1` 里有大量中文注释 —— 按 GBK 解会让某些
# "引号字节"落到错位置,解析器直接报 "意外的标记",整个门禁跑不起来。
# 而 BOM 会被**任何一次普通文本编辑**抹掉(保存成无 BOM 的 UTF-8),
# 所以留一条随时能跑的自愈路径,比"记得别用错编辑器"可靠。

$ErrorActionPreference = "Stop"
$targets = @(
    (Join-Path $PSScriptRoot "build_and_test.ps1")
)
$bom = [byte[]](0xEF, 0xBB, 0xBF)

foreach ($path in $targets) {
    if (-not (Test-Path $path)) {
        Write-Output "跳过(不存在): $path"
        continue
    }
    $bytes = [System.IO.File]::ReadAllBytes($path)
    $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and
               $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    if ($hasBom) {
        Write-Output "已有 BOM: $path"
        continue
    }
    # 无 BOM -> 按 UTF-8 读出来,再带 BOM 写回(行尾统一 CRLF)
    $text = [System.Text.Encoding]::UTF8.GetString($bytes)
    $text = $text.Replace("`r`n", "`n").Replace("`n", "`r`n")
    $utf8Bom = New-Object System.Text.UTF8Encoding($true)
    [System.IO.File]::WriteAllText($path, $text, $utf8Bom)
    Write-Output "已补上 BOM: $path"
}

# 补完立刻验一遍能不能解析(能解析才说明真的修好了)
$target = $targets[0]
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    $target, [ref]$null, [ref]$errors) | Out-Null
if ($errors -and $errors.Count) {
    Write-Output ("解析仍有 {0} 条错误 —— 先看第一条:{1}" -f
                  $errors.Count, $errors[0].Message)
    exit 1
}
Write-Output "解析通过: build_and_test.ps1"
exit 0
