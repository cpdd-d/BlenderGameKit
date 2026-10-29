"""固定机位下比较 Blender 预览与 Godot 运行时画面（可复跑）。

为什么要有这个脚本
------------------
`docs/预览一致性验证.md` 里那些数字（0.08268 → 0.03943）是**一次性实验**
算出来的：渲染脚本、指标脚本都散在 `.audit/` 里，别人（或半年后的自己）
没法照着复跑。于是每次改 NPR / 灯光 / 后期，都只能"看起来差不多"。

这个脚本把那条链路压成两条命令：
    1) Blender 侧出图（会自动打开 .blend、套『Godot 观感』、按固定机位渲染）
    2) 任意一对图做指标（普通 Python 即可，不需要 Blender）

用法
----
    # 1) 出 Blender 侧（需要 Blender，约几十秒）
    blender -b --python scripts/check_preview_match.py -- \
        --render --out .audit/preview_match/run/blender.png

    # 2) 与 Godot 参考图比对，并写 JSON + 差异图
    python scripts/check_preview_match.py \
        --blender .audit/preview_match/run/blender.png \
        --godot .audit/preview_match/godot_reference_corrected.png \
        --out .audit/preview_match/run

    # 3) 当回归门禁用（超过阈值就非零退出）
    python scripts/check_preview_match.py --blender A.png --godot B.png \
        --max-frame 0.045 --max-hall 0.09

口径（和 docs 一致，不要改）
--------------------------
* 两张图必须**同分辨率**（默认 960×540）。缩放到同一尺寸再比会把差异磨平。
* 指标 = RGB 归一化到 0–1 后的**平均绝对差**，不是 SSIM、不是感知哈希 ——
  换算法会让历史数字全部失去可比性。
* 区域矩形沿用 2026-10-17 那次标定用的一组，别新增/改动，否则前后数据对不上。
"""

import json
import os
import shutil
import subprocess
import sys

# ---------------------------------------------------------------------------
# 区域：与 docs/预览一致性验证.md 的表格一一对应
# 格式 (x0, y0, x1, y1)，左上角为原点
# ---------------------------------------------------------------------------
HALL = (15, 200, 920, 435)          # 大厅区域（文档里"整幅画面 / 大厅区域"两行）
REGIONS = {
    "warm_wall": (20, 210, 295, 360),
    "cold_wall": (700, 210, 915, 360),
    "floor_near": (20, 360, 915, 430),
    "floor_far": (450, 305, 680, 355),
    "back_wall": (450, 210, 680, 298),
    "pillar": (365, 210, 395, 360),
    "background": (20, 10, 920, 180),
}

DEFAULT_BLEND = "examples/demo_story/恐怖闯关示例.blend"
DEFAULT_SCENE = "大堂"
DEFAULT_GODOT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"
DEFAULT_PROJECT = "examples/demo_story/恐怖闯关示例_godot"
# 文档记录的机位（Blender 坐标系）
CAMERA_LOCATION = (-38.0, -2.0, 6.0)
CAMERA_TARGET = (0.0, 0.0, 3.0)
CAMERA_LENS = 34.0
# 同一机位在 Godot 里的写法（导出时做过轴序换算，见 docs/预览一致性验证.md）
GODOT_CAMERA_LOCATION = (-38.0, 6.0, 2.0)
GODOT_CAMERA_TARGET = (0.0, 3.0, 0.0)
GODOT_CAMERA_FOV = 33.16644
RESOLUTION = (960, 540)

# 2026-10-17 标定后的实测值（docs 表格）：frame 0.03943 / hall 0.08454。
# 阈值给 ~15% 余量，只用来抓"明显变差"，不追求卡死。
DEFAULT_MAX_FRAME = 0.045
DEFAULT_MAX_HALL = 0.090


def _root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# 一、Blender 侧出图（只有 --render 分支会用到 bpy）
# ---------------------------------------------------------------------------
def render_blender(out_png, scale=0.18, blend=None, scene_name=DEFAULT_SCENE,
                   samples=64):
    import addon_utils  # noqa: PLC0415  (只有在 Blender 里才存在)
    import bpy  # noqa: PLC0415
    from mathutils import Vector  # noqa: PLC0415

    root = _root()
    blend = blend or os.path.join(root, DEFAULT_BLEND)
    bpy.ops.wm.open_mainfile(filepath=blend)

    scene = bpy.data.scenes.get(scene_name)
    if scene is None:
        raise SystemExit(f"找不到场景『{scene_name}』（有: {[s.name for s in bpy.data.scenes]}）")
    bpy.context.window.scene = scene

    # 审计相机：固定机位，不碰用户场景里原有的相机
    cam_data = bpy.data.cameras.new("Audit Camera")
    cam = bpy.data.objects.new("Audit Camera", cam_data)
    scene.collection.objects.link(cam)
    cam.location = CAMERA_LOCATION
    cam.rotation_euler = (Vector(CAMERA_TARGET) - cam.location).to_track_quat(
        "-Z", "Y").to_euler()
    cam_data.lens = CAMERA_LENS
    scene.camera = cam

    # 『Godot 观感』= 预览贴近运行时的三件套（和审计脚本当年一致）
    sys.path.insert(0, root)
    try:
        addon_utils.enable("BlenderGameKit", default_set=False, persistent=False)
    except Exception:
        pass                                              # 源码目录直跑的情况
    from BlenderGameKit import godot_look, godot_material_preview  # noqa: PLC0415

    if hasattr(scene, "gk_settings"):
        scene.gk_settings.preview_light_scale = scale
    godot_look.apply(scene)
    godot_look._dim_lights(scene, force=True)
    godot_material_preview.apply(scene)

    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x, scene.render.resolution_y = RESOLUTION
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.eevee.taa_render_samples = samples
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    scene.render.filepath = os.path.abspath(out_png)
    bpy.ops.render.render(write_still=True)
    print(f"PREVIEW_MATCH_RENDER={os.path.abspath(out_png)}")


# ---------------------------------------------------------------------------
# 三、Godot 侧抓帧（普通 Python 即可，需要 Godot 可执行文件）
# ---------------------------------------------------------------------------
def capture_godot(project, out_png, godot_exe=None, scene="", frames=240):
    """在真 Godot 里跑工程，固定机位抓一张 PNG。

    做法：临时把一个 autoload 脚本放进工程、在 project.godot 的 [autoload]
    段加一行，跑完立刻还原。**不会留在游戏工程里** —— 结束之后工程和原来一样。
    """
    root = _root()
    godot_exe = godot_exe or DEFAULT_GODOT
    project = os.path.abspath(project or os.path.join(root, DEFAULT_PROJECT))
    if not os.path.exists(godot_exe):
        raise SystemExit(f"找不到 Godot 可执行文件: {godot_exe}（用 --godot-exe 指定）")
    if not os.path.isdir(project):
        raise SystemExit(f"找不到 Godot 工程: {project}（先在 Blender 里『导出到 Godot』一次）")

    script_src = os.path.join(root, "scripts", "gk_audit_capture.gd")
    script_dst = os.path.join(project, "gk_audit_capture.gd")
    project_file = os.path.join(project, "project.godot")
    backup = project_file + ".gk_audit.bak"

    shutil.copy2(script_src, script_dst)
    shutil.copy2(project_file, backup)
    try:
        with open(project_file, encoding="utf-8") as handle:
            text = handle.read()
        if "GkAuditCapture" not in text:
            entry = 'GkAuditCapture="*res://gk_audit_capture.gd"'
            if "[autoload]" in text:
                text = text.replace("[autoload]", "[autoload]\n\n" + entry, 1)
            else:
                text = text.rstrip("\n") + "\n\n[autoload]\n\n" + entry + "\n"
            with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)

        env = os.environ.copy()
        env["GK_AUDIT_OUT"] = os.path.abspath(out_png)
        env["GK_AUDIT_CAM_POS"] = ",".join(str(value) for value in GODOT_CAMERA_LOCATION)
        env["GK_AUDIT_CAM_TARGET"] = ",".join(str(value) for value in GODOT_CAMERA_TARGET)
        env["GK_AUDIT_FOV"] = str(GODOT_CAMERA_FOV)
        if scene:
            env["GK_AUDIT_SCENE"] = scene

        os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
        if os.path.exists(out_png):
            os.remove(out_png)

        command = [godot_exe, "--path", project,
                   "--resolution", f"{RESOLUTION[0]}x{RESOLUTION[1]}",
                   "--fixed-fps", "60", "--quit-after", str(frames)]
        print("Godot 抓帧: " + " ".join(command))
        result = subprocess.run(command, env=env, capture_output=True, text=True,
                                timeout=300)
        for line in ((result.stdout or "") + (result.stderr or "")).splitlines():
            if "GK_AUDIT" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
                print("  " + line.strip())
    finally:
        shutil.move(backup, project_file)
        if os.path.exists(script_dst):
            os.remove(script_dst)

    if not os.path.exists(out_png) or os.path.getsize(out_png) < 1024:
        raise SystemExit(f"Godot 没抓到图: {out_png}（看上面打印的报错）")
    print(f"PREVIEW_MATCH_CAPTURE={os.path.abspath(out_png)}")


# ---------------------------------------------------------------------------
# 四、指标（普通 Python 即可，numpy + Pillow）
# ---------------------------------------------------------------------------
def _load(path):
    from PIL import Image  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0


def compare(blender_png, godot_png):
    """返回指标字典。分辨率不一致直接报错（缩放会磨平差异）。"""
    import numpy as np  # noqa: PLC0415

    a = _load(blender_png)
    b = _load(godot_png)
    if a.shape != b.shape:
        raise SystemExit(
            f"分辨率不一致：{blender_png}={a.shape[1]}x{a.shape[0]}，"
            f"{godot_png}={b.shape[1]}x{b.shape[0]}。"
            "两张图必须同机位同分辨率，缩放到同尺寸会磨平差异。")

    diff = np.abs(a - b)
    mask = np.zeros(diff.shape[:2], dtype=bool)
    x0, y0, x1, y1 = HALL
    mask[y0:y1, x0:x1] = True

    result = {
        "blender": os.path.abspath(blender_png),
        "godot": os.path.abspath(godot_png),
        "size": [int(a.shape[1]), int(a.shape[0])],
        "frame_rgb_mae": float(diff.mean()),
        "hall_rgb_mae": float(diff[mask].mean()),
        "regions": {name: float(diff[cy0:cy1, cx0:cx1].mean())
                    for name, (cx0, cy0, cx1, cy1) in REGIONS.items()},
        "probes": {
            "background_rgb": a[50, 480].tolist(),
            "godot_background_rgb": b[50, 480].tolist(),
            "hall_mean_rgb": a[mask].mean(axis=0).tolist(),
            "godot_hall_mean_rgb": b[mask].mean(axis=0).tolist(),
        },
    }
    return result, diff


def write_diff_image(path, diff, gain=6.0):
    from PIL import Image  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    amplified = np.clip(diff * gain, 0.0, 1.0)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    Image.fromarray((amplified * 255).astype(np.uint8)).save(path)


def _print_report(result, max_frame, max_hall):
    print("=" * 60)
    print("Blender 预览 vs Godot 运行时（平均绝对差，越小越接近）")
    print("=" * 60)
    print(f"  整幅画面  {result['frame_rgb_mae']:.5f}")
    print(f"  大厅区域  {result['hall_rgb_mae']:.5f}")
    print("  分区：")
    for name, value in result["regions"].items():
        print(f"    {name:12s} {value:.5f}")

    failed = []
    if max_frame is not None and result["frame_rgb_mae"] > max_frame:
        failed.append(f"整幅画面 {result['frame_rgb_mae']:.5f} > {max_frame}")
    if max_hall is not None and result["hall_rgb_mae"] > max_hall:
        failed.append(f"大厅区域 {result['hall_rgb_mae']:.5f} > {max_hall}")
    if failed:
        print("")
        print("PREVIEW_MATCH=FAILED")
        for item in failed:
            print(f"  超阈值: {item}")
    else:
        print("")
        print("PREVIEW_MATCH=OK")
    return not failed


def main(argv):
    args = argv[argv.index("--") + 1:] if "--" in argv else argv[1:]

    def take(flag, default=None):
        if flag in args:
            index = args.index(flag)
            return args[index + 1] if index + 1 < len(args) else default
        return default

    out_dir = take("--out")
    project = take("--project")
    godot_exe = take("--godot-exe")
    blender_png = take("--blender")
    godot_png = take("--godot")

    if "--full" in args:
        out_dir = out_dir or ".audit/preview_match/run"
        blender_png = os.path.join(out_dir, "blender.png")
        godot_png = os.path.join(out_dir, "godot.png")
        render_blender(blender_png, scale=float(take("--scale", 0.18)),
                       blend=take("--blend"),
                       scene_name=take("--scene", DEFAULT_SCENE))
        capture_godot(project, godot_png, godot_exe=godot_exe,
                      scene=take("--godot-scene", ""))
    elif "--render" in args:
        scale = float(take("--scale", 0.18))
        png = take("--png")
        if not png:
            if out_dir and out_dir.lower().endswith(".png"):
                png = out_dir
            else:
                png = os.path.join(out_dir or ".audit/preview_match/run",
                                   "blender.png")
        render_blender(png, scale=scale, blend=take("--blend"),
                       scene_name=take("--scene", DEFAULT_SCENE))
        return 0
    elif "--capture" in args:
        png = take("--png")
        if not png:
            if out_dir and out_dir.lower().endswith(".png"):
                png = out_dir
            else:
                png = os.path.join(out_dir or ".audit/preview_match/run",
                                   "godot.png")
        capture_godot(project, png, godot_exe=godot_exe,
                      scene=take("--godot-scene", ""))
        return 0

    if not blender_png or not godot_png:
        print(__doc__)
        return 2

    result, diff = compare(blender_png, godot_png)
    out_dir = out_dir or os.path.dirname(os.path.abspath(blender_png))
    metrics_path = os.path.join(out_dir, "preview_match.json")
    os.makedirs(out_dir, exist_ok=True)
    with open(metrics_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    write_diff_image(os.path.join(out_dir, "diff.png"), diff)
    print(f"指标已写到 {metrics_path}（差异图: {os.path.join(out_dir, 'diff.png')}）")

    max_frame = take("--max-frame")
    max_hall = take("--max-hall")
    max_frame = DEFAULT_MAX_FRAME if (max_frame is None and "--check" in args) else (
        float(max_frame) if max_frame is not None else None)
    max_hall = DEFAULT_MAX_HALL if (max_hall is None and "--check" in args) else (
        float(max_hall) if max_hall is not None else None)
    return 0 if _print_report(result, max_frame, max_hall) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
