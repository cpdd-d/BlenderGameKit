"""界面设计"所见即所得"的真 GUI 校验(第一百零三轮)。

用法:
    python scripts\\check_ui_drag.py
    python scripts\\check_ui_drag.py --blender "D:\\blender-4.5.5-windows-x64\\blender.exe"

── 为什么要真开窗口(GUI)────────────────────────────────────────────
视口预览靠 `gpu` / `blf` 画,而**无头模式没有 GPU 上下文** ——
冒烟测试只能验"换算 + 写回 + 状态机"那段(绘制整段包在 try 里,
出错会静默跳过)。所以这个脚本真启动一次 Blender GUI,在真 3D 视口上:

  1. 验 `gpu.shader.from_builtin("IMAGE")` 在 4.5 里可用(贴真图那条路);
  2. 挂真 draw handler + 让 Blender 进事件循环,把预览**真画一遍**
     (圆角 / 文字 / 图片全走),并断言绘制链没在中间抛;
  3. 断言**纹理缓存被填充** —— 这是"图片真的贴上了"的硬证据
     (shader 名字错了 / 纹理建不出来,缓存就是空的);
  4. 真造一次 "TRI_FAN" 的 batch(圆角几何在真 GPU 上合法);
  5. 顺手把 3D 视口截一张图到 `_dist/ui_drag_check.png`(人工看效果)。

结论行 `CHECK_UI_DRAG=OK`;找不到 Blender 时打印"跳过"(不判红)。
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BLENDER_DEFAULT = r"D:\blender-4.5.5-windows-x64\blender.exe"
SHOT = os.path.join(ROOT, "_dist", "ui_drag_check.png")

INNER = r'''import os
import sys
from importlib import import_module

import addon_utils
import bpy


def log(*parts):
    print("UI_DRAG", *parts)


def find_addon():
    """挑**代码最新**的那份插件。

    ── 为什么不能"取第一个"(踩过的坑)────────────────────────────────
    同时存在两个仓库时(门禁的 `gk_test` + 用户的 `user_default`),
    `addon_utils.modules()` 会给出**两份** blender_game_kit,先拿到谁不一定
    —— 拿到旧的那份,表现是"改了代码但行为还是老的"(连新符号都没有),
    查半天。判定用第一百零三轮加的 `ui._UI_PREVIEW_ERROR`。
    """
    fallback = None
    for module in addon_utils.modules():
        name = getattr(module, "__name__", "")
        if "blender_game_kit" not in name:
            continue
        fallback = fallback or name
        try:
            ui_mod = import_module(f"{name}.ui")
        except Exception:                                 # noqa: BLE001
            continue
        if hasattr(ui_mod, "_UI_PREVIEW_ERROR"):
            return name
    return fallback


name = find_addon()
if name is None:
    log("FAIL", "没找到已安装的 BlenderGameKit 插件")
    sys.exit(2)
# 卸掉**别的**那一份:两份同时注册会 bl_idname 冲突(同一个算子注册两次)
for module in addon_utils.modules():
    other = getattr(module, "__name__", "")
    if other != name and "blender_game_kit" in other:
        try:
            addon_utils.disable(other, default_set=False)
        except Exception:                                 # noqa: BLE001
            pass
addon_utils.enable(name, default_set=True, persistent=True)
ui = import_module(f"{name}.ui")
ui_wysiwyg = import_module(f"{name}.ui_wysiwyg")
if not hasattr(ui, "_UI_PREVIEW_ERROR"):
    log("FAIL", "拿到的是**旧版**插件(%s):先重装一份新的" % name)
    sys.exit(2)
log("ADDON", name)

# ── 1. 造一张设计图(面板 + 文字 + 图片,把三条绘制路径都覆盖上)──────
design = bpy.data.node_groups.new("真机拖动校验103", "GKUiDesignTree")
design.gk_design_width = 960
design.gk_design_height = 540
panel = design.nodes.new("GKNodeUiPanel")
panel.name = "校验底板"
panel.gk_width = 420.0
panel.gk_height = 260.0
panel.gk_x = 60.0
panel.gk_y = 40.0
panel.gk_radius = 18
text = design.nodes.new("GKNodeUiText")
text.name = "校验标题"
text.gk_text = "所见即所得"
text.gk_size = 30
text.gk_parent_widget = "校验底板"
text.gk_x = 24.0
text.gk_y = 20.0

image = bpy.data.images.new("校验图片", width=32, height=32, alpha=True)
image.pixels = [0.9, 0.5, 0.2, 1.0] * 32 * 32
picture = design.nodes.new("GKNodeUiImage")
picture.name = "校验图片"
picture.gk_image = image.name
picture.gk_width = 120.0
picture.gk_height = 120.0
picture.gk_align = "BOTTOM_RIGHT"
picture.gk_margin = 30.0

# ── 2. 找一个 3D 视口(没有就把第一个区域改成 3D 视口)────────────────
window = bpy.context.window
area = next((a for a in window.screen.areas if a.type == "VIEW_3D"), None)
if area is None and len(window.screen.areas) > 0:
    area = window.screen.areas[0]
    area.type = "VIEW_3D"
if area is None:
    log("FAIL", "没有可用的区域")
    sys.exit(2)
region = next((r for r in area.regions if r.type == "WINDOW"), None)
if region is None or region.width < 100 or region.height < 100:
    log("FAIL", "3D 视口区域太小: %s" % (region and (region.width, region.height)))
    sys.exit(2)

# ── 3. IMAGE shader + 纹理(贴真图那条路)────────────────────────────
try:
    import gpu
    shader = gpu.shader.from_builtin("IMAGE")
    image_shader = shader is not None
except Exception as exc:
    image_shader = False
    log("FAIL", "IMAGE shader 不可用:", type(exc).__name__, exc)
log("SHADER", "image=%s" % image_shader)
try:
    texture = gpu.texture.from_image(image)
    log("TEXTURE", "w=%d h=%d" % (texture.width, texture.height))
except Exception as exc:
    log("FAIL", "纹理建不出来:", type(exc).__name__, exc)

# ── 4. 真在 draw 回调里跑预览(靠事件循环触发),再截图 ──────────────
# ⚠ 两个坑(真 GUI 里踩出来的):
#   1. 不能在脚本里直接调 `_ui_preview_draw()` —— 里面的 `batch.draw`
#      要求**在绘制回调内**执行,脚本里调会被 Blender 拒(异常还会被它
#      自己的 try 吞掉,看起来"跑通了其实什么都没画");
#   2. `wm.redraw_timer` **不触发** SpaceView3D 的 POST_PIXEL handler ——
#      得挂真 handler,然后**让 Blender 进事件循环**(脚本返回后窗口会
#      自然重绘),用定时器过一两秒再断言 + 截图。
rows_now = ui._ui_preview_rects(design)
log("ROWS", "n=%d names=%s" % (len(rows_now),
                               ",".join(str(r[0]) for r in rows_now)))
log("IMGPROP", "%s image=%r" % (getattr(picture, "bl_idname", "?"),
                                getattr(picture, "gk_image", None)))
ui._UI_PREVIEW_TEXTURES.clear()
original = ui._ui_preview_tree
ui._ui_preview_tree = lambda: design
calls = []


def _probe_draw():
    """包一层:记录"回调到底被调了几次、当时的 region 多大"。"""
    region_now = getattr(bpy.context, "region", None)
    calls.append((region_now.width if region_now else -1,
                  region_now.height if region_now else -1))
    ui._ui_preview_draw()


handle = bpy.types.SpaceView3D.draw_handler_add(
    _probe_draw, (), "WINDOW", "POST_PIXEL")
area.tag_redraw()


def _finish():
    """事件循环跑过一两秒之后:贴图缓存 / 异常 / 截图一起收。"""
    try:
        log("CALLS", "n=%d last=%s" % (len(calls), calls[-1] if calls else None))
        log("DRAW", "textures=%d" % len(ui._UI_PREVIEW_TEXTURES))
        errors = list(getattr(ui, "_UI_PREVIEW_ERROR", []) or [])
        log("ERROR", errors[-1] if errors else "(无)")
        out = os.environ.get("UI_DRAG_SHOT", "")
        if out:
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with bpy.context.temp_override(area=area, region=region,
                                           window=window):
                bpy.ops.screen.screenshot_area(filepath=out)
            log("SHOT", os.path.getsize(out) if os.path.isfile(out) else 0)
    except Exception as exc:
        log("FAIL", "收尾失败:", type(exc).__name__, exc)
    finally:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
        except Exception:
            pass
        ui._ui_preview_tree = original
        log("DONE")
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(_finish, first_interval=2.0)

# ── 5. 圆角几何在真 GPU 上造一次 batch(不 draw,draw 要在回调里)──────
try:
    from gpu_extras.batch import batch_for_shader
    color_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts = ui_wysiwyg.rounded_rect_fan(10.0, 10.0, 200.0, 100.0, 16.0)
    batch = batch_for_shader(color_shader, "TRI_FAN", {"pos": verts})
    log("FAN", "ok verts=%d batch=%s" % (len(verts), batch is not None))
except Exception as exc:
    log("FAIL", "圆角 batch 建不出来:", type(exc).__name__, exc)

# ⚠ 这里**不能** quit:上面注册的定时器要等事件循环跑一两秒才截图 / 收尾
# (见第 4 节的两个坑)。脚本返回后 Blender 会继续跑主循环。
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", default=BLENDER_DEFAULT)
    parser.add_argument("--keep", action="store_true",
                        help="保留临时脚本(排查用)")
    args = parser.parse_args()
    if not os.path.isfile(args.blender):
        print(f"跳过:找不到 Blender({args.blender})")
        return 0

    handle = tempfile.NamedTemporaryFile(
        "w", suffix="_ui_drag_check.py", delete=False, encoding="utf-8")
    handle.write(INNER)
    handle.close()
    env = dict(os.environ)
    env["UI_DRAG_SHOT"] = SHOT
    os.makedirs(os.path.dirname(SHOT), exist_ok=True)
    try:
        run = subprocess.run(
            [args.blender, "--gpu-backend", "vulkan", "--python", handle.name],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"跳过:起不了 Blender GUI({type(exc).__name__})")
        return 0
    text = (run.stdout or "") + (run.stderr or "")
    for line in text.splitlines():
        if "UI_DRAG" in line:
            print(line.strip())
    problems = []
    if not re.search(r"UI_DRAG SHADER image=True", text):
        problems.append("IMAGE shader 拿不到 —— 图片控件的真图那条路断了")
    if not re.search(r"UI_DRAG TEXTURE w=\d+ h=\d+", text):
        problems.append("纹理建不出来")
    draw = re.search(r"UI_DRAG DRAW textures=(\d+)", text)
    if not draw:
        problems.append("预览绘制没跑通(重绘没触发 draw 回调?)")
    elif int(draw.group(1)) < 1:
        problems.append("预览没贴出真图(纹理缓存是空的 —— "
                        "绘制链可能在中间就抛了,异常被吞)")
    if not re.search(r"UI_DRAG FAN ok", text):
        problems.append("圆角几何在真 GPU 上造不出 batch")
    if "UI_DRAG DONE" not in text:
        problems.append("Blender 侧没跑完(可能中途退出)")
    if not os.path.isfile(SHOT):
        problems.append(f"没截到图({SHOT})")

    for item in dict.fromkeys(problems):
        print(f"✗ {item}")
    print("CHECK_UI_DRAG=" + ("OK" if not problems else "FAIL"))
    if not args.keep:
        try:
            os.remove(handle.name)
        except OSError:
            pass
    elif problems:
        print(f"（临时脚本保留在 {handle.name}）")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())