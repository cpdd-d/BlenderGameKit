# End-to-end check for the 2D platformer demo.
#
#   powershell -File scripts/platformer_verify.ps1
#
# Answers "is the demo actually playable", not "does it compile":
#   * does the character fall (is gravity applied at all)
#   * does the ground catch it (is_on_floor)
#   * is there a camera
#   * does eating a coin raise the score, refresh the HUD, and save the record
#
# The checker script is injected into a temporary copy of Main.tscn and the
# original is restored afterwards, so the shipped demo stays clean.
#
# NOTE: this file is intentionally ASCII-only. Windows PowerShell 5.1 reads
# BOM-less UTF-8 as ANSI and mangles non-ASCII string literals, which breaks
# parsing (a documented pitfall in README.md).

param(
    [string]$Godot = "D:\godot\Godot_v4.7.2-stable_win64_console.exe",
    [string]$Root = "D:\BlenderGameKit"
)

$ErrorActionPreference = "Continue"
$demo = Join-Path $Root "examples\demo_platformer"
$scene = Join-Path $demo "Main.tscn"
$backup = Join-Path $demo "Main.tscn.gkbackup"
$checker = Join-Path $demo "_gk_check.gd"

function Step($text) {
    Write-Output ""
    Write-Output ("=" * 60)
    Write-Output "== $text"
    Write-Output ("=" * 60)
}

if (-not (Test-Path $Godot)) { Write-Output "SKIP: godot not found"; exit 0 }

# ---------- 1. regenerate ----------
Step "1/3 regenerate demo"
& python (Join-Path $Root "scripts\make_platformer_demo.py") 2>&1 |
    Select-String -Pattern "generated|lines|warning" | ForEach-Object { $_.Line }
if ($LASTEXITCODE -ne 0) { Write-Error "generator failed"; exit 1 }

# ---------- 2. inject checker ----------
Step "2/3 inject checker"
Copy-Item $scene $backup -Force

$checkerBody = @'
extends Node2D

var _frame := 0
var _moved := false

func _process(_delta: float) -> void:
	_frame += 1
	if _frame == 60 and not _moved:
		_moved = true
		var player := get_tree().get_first_node_in_group("player")
		var coin := get_tree().get_first_node_in_group("coin")
		if player == null or coin == null:
			print("CHECK=FAILED no player or coin")
			get_tree().quit()
			return
		print("LANDED y=", player.global_position.y, " on_floor=", player.is_on_floor())
		var cam := get_viewport().get_camera_2d()
		print("CAMERA ", cam.global_position if cam != null else "null")
		print("BEFORE score=", player.score, " best=", player.best_score)
		player.global_position = coin.global_position
		return
	if _frame == 120:
		var player := get_tree().get_first_node_in_group("player")
		var left := get_tree().get_nodes_in_group("coin").size()
		var hud := player.get_node_or_null("GKUI/Root/\u5206\u6570")
		print("AFTER score=", player.score, " best=", player.best_score)
		print("COINS_LEFT=", left)
		print("HUD=", hud.text if hud != null else "(missing)")
		print("SAVE_EXISTS=", FileAccess.file_exists("user://gk_best.json"))
		print("CHECK_DONE")
		get_tree().quit()
'@
# The checker holds one CJK literal (the HUD label name). Write it as UTF-8
# with a BOM so Godot reads the characters correctly.
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
[System.IO.File]::WriteAllText($checker, $checkerBody, $utf8Bom)

$text = Get-Content $scene -Raw -Encoding utf8
$text = $text -replace 'load_steps=8', 'load_steps=9'
$text = $text -replace '(\[ext_resource type="PackedScene" path="res://Coin.tscn" id="3_coin"\])', "`$1`n[ext_resource type=`"Script`" path=`"res://_gk_check.gd`" id=`"9_check`"]"
$text = $text -replace '(\[node name="Main" type="Node2D"\])(\r?\n)', "`$1`nscript = ExtResource(`"9_check`")`$2"
Set-Content $scene -Value $text -Encoding utf8 -NoNewline

# ---------- 3. run ----------
Step "3/3 run in Godot"
$runLog = & $Godot --path $demo 2>&1 | Out-String
($runLog -split "`n") |
    Where-Object { $_ -match "LANDED|CAMERA|BEFORE|AFTER|COINS_LEFT|HUD=|SAVE_EXISTS|CHECK=" } |
    ForEach-Object { Write-Output $_.Trim() }

# ---------- restore ----------
Move-Item $backup $scene -Force
Remove-Item $checker -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $demo "_gk_check.gd.uid") -Force -ErrorAction SilentlyContinue

# ---------- verdict ----------
Write-Output ""
$checks = @(
    @{ Name = "character is caught by ground (gravity works)"; Pass = ($runLog -match "on_floor=true") },
    @{ Name = "a camera exists";                               Pass = ($runLog -match "CAMERA \(") },
    @{ Name = "eating a coin adds score";                      Pass = ($runLog -match "AFTER score=10") },
    @{ Name = "best score updates";                            Pass = ($runLog -match "best=10") },
    @{ Name = "coin is consumed";                              Pass = ($runLog -match "COINS_LEFT=4") },
    @{ Name = "HUD text refreshed";                            Pass = ($runLog -match "HUD=") },
    @{ Name = "best score persisted to disk";                  Pass = ($runLog -match "SAVE_EXISTS=true") }
)
$failed = @()
foreach ($c in $checks) {
    if ($c.Pass) { Write-Output ("[PASS] " + $c.Name) }
    else { Write-Output ("[FAIL] " + $c.Name); $failed += $c.Name }
}
Write-Output ""
if ($failed.Count -gt 0) {
    Write-Output "PLATFORMER_VERIFY=FAILED"
    Write-Output ("failed: " + ($failed -join ", "))
    exit 1
}
Write-Output "PLATFORMER_VERIFY=OK"
exit 0
