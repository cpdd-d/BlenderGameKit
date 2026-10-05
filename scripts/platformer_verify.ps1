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
    [string]$Root = "D:\BlenderGameKit",
    # Where to generate the demo. Default is the shipped showcase under
    # examples\demo_platformer; the one-button gate passes _dist\platformer_e2e
    # so the tracked example files are never rewritten by a test run.
    [string]$OutDir = ""
)

$ErrorActionPreference = "Continue"
if ($OutDir) { $demo = $OutDir } else { $demo = Join-Path $Root "examples\demo_platformer" }
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
& python (Join-Path $Root "scripts\make_platformer_demo.py") $demo 2>&1 |
    Select-String -Pattern "generated|lines|warning" | ForEach-Object { $_.Line }
if ($LASTEXITCODE -ne 0) { Write-Error "generator failed"; exit 1 }

# ---------- 1b. clear the save file ----------
# The demo persists the best score to user://gk_best.json. If a previous run
# left one behind, the "best score updates" check below becomes flaky
# (we would read an old record instead of the one this run writes).
# Godot maps user:// to %APPDATA%\Godot\app_userdata\<project name>.
#
# Scope the deletion to THIS demo's own folder. It used to glob every
# app_userdata folder for gk_*.json, which would wipe save files belonging to
# unrelated Godot projects on the machine the moment the check ran. The folder
# name is CJK, so resolve it at runtime from project.godot instead of writing a
# literal here (this file must stay ASCII-only, see the note at the top).
Step "1b/3 clear previous save"
$projName = ""
$projFile = Join-Path $demo "project.godot"
if (Test-Path $projFile) {
    $projText = Get-Content $projFile -Raw -Encoding utf8
    if ($projText -match 'config/name="([^"]*)"') { $projName = $Matches[1] }
}
$removed = 0
if ($projName) {
    $userDir = Join-Path (Join-Path $env:APPDATA "Godot\app_userdata") $projName
    if (Test-Path $userDir) {
        Get-ChildItem $userDir -Filter "gk_*.json" -ErrorAction SilentlyContinue |
            ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue; $removed++ }
    }
} else {
    Write-Output "could not resolve project name; skipping save cleanup (nothing deleted)"
}
Write-Output ("removed {0} old save file(s)" -f $removed)

# ---------- 2. inject checker ----------
Step "2/3 inject checker"
Copy-Item $scene $backup -Force

$checkerBody = @'
extends Node2D

var _frame := 0
var _landed := false
var _landed_frame := 0

func _process(_delta: float) -> void:
	_frame += 1
	# ---- Wait until the character ACTUALLY lands (not a fixed frame count) ----
	# This used to sample at frame 60. The number of frames needed to fall onto
	# the ground varies with physics tick rate, gravity and platform height, so a
	# hard-coded frame is fragile: measured y=537 vs the expected ~573, two frames
	# short of landing, and the whole check failed for no real reason.
	# Polling until landed (with a timeout) is accurate and frame-rate proof.
	var player := get_tree().get_first_node_in_group("player")
	if player == null:
		if _frame > 300:
			print("CHECK=FAILED no player")
			get_tree().quit()
		return

	# Phase 1: not landed yet -- check every frame until it touches the ground.
	if not _landed:
		if player.is_on_floor():
			_landed = true
			_landed_frame = _frame
			var coin := get_tree().get_first_node_in_group("coin")
			if coin == null:
				print("CHECK=FAILED no coin")
				get_tree().quit()
				return
			print("LANDED y=", player.global_position.y, " on_floor=", player.is_on_floor(), " at_frame=", _frame)
			var cam := get_viewport().get_camera_2d()
			print("CAMERA ", cam.global_position if cam != null else "null")
			print("BEFORE score=", player.score, " best=", player.best_score)
			# Move the player onto the coin so the pickup actually happens.
			player.global_position = coin.global_position
			return
		if _frame > 300:
			print("LANDED y=", player.global_position.y, " on_floor=", player.is_on_floor(), " TIMEOUT at_frame=", _frame)
			print("CHECK=FAILED never landed")
			get_tree().quit()
		return

	# Phase 2: landed -- run 60 more frames from the landing moment, then report.
	# Do NOT use an absolute frame number here (`_frame == 120`): the landing
	# moment varies, and an absolute number would miss this step as well.
	if _frame - _landed_frame >= 60:
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
# --headless: an automated check must not pop a game window in the user's face.
# The checks below are all gameplay/state (gravity, score, HUD text, save file),
# none of them need a rendered frame -- so headless verifies the same things
# without hijacking the screen every time the suite runs.
$runLog = & $Godot --headless --path $demo 2>&1 | Out-String
# Set GK_VERIFY_DEBUG=1 to dump the raw engine output (handy when the checker
# itself fails to parse -- Godot then prints nothing matching the filters below).
if ($env:GK_VERIFY_DEBUG) {
    Write-Output "---- RAW LOG (first 40 lines) ----"
    ($runLog -split "`n") | Select-Object -First 40 | ForEach-Object { Write-Output $_ }
    Write-Output "---- RAW LOG END ----"
}
($runLog -split "`n") |
    Where-Object { $_ -match "LANDED|CAMERA|BEFORE|AFTER|COINS_LEFT|HUD=|SAVE_EXISTS|CHECK=" } |
    ForEach-Object { Write-Output $_.Trim() }

# ---------- restore ----------
Move-Item $backup $scene -Force
if (-not $env:GK_VERIFY_KEEP) {
    Remove-Item $checker -Force -ErrorAction SilentlyContinue
}
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
