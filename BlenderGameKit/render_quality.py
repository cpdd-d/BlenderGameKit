"""Opt-in project defaults shared by exported levels."""
from pathlib import Path
import re


def apply_startup_quality(directory, scene):
    if getattr(getattr(scene, "gk_settings", None), "render_quality", "KEEP") != "HIGH":
        return
    values = {
        "scaling_3d/scale": "1.0",
        "anti_aliasing/quality/msaa_3d": "2",
        # ── 去色带(第一百四十八轮)─────────────────────────────────
        # 卡通材质靠**大面积的平滑渐变**吃饭(灯下的墙、天花板的光晕)。
        # 8 位帧缓冲把这种渐变切成一层层同心圆环 —— 实机天花板上一圈
        # 一圈的,像贴了张等高线图。Godot 的 debanding 在抖动噪声里补
        # 1 bit,专门治这个。属于"高画质"该有的默认。
        "anti_aliasing/quality/use_debanding": "true",
        "textures/default_filters/use_nearest_mipmap_filter": "false",
        "textures/default_filters/anisotropic_filtering_level": "3",
        "lights_and_shadows/positional_shadow/atlas_size": "4096",
        "lights_and_shadows/directional_shadow/size": "4096",
        "lights_and_shadows/positional_shadow/soft_shadow_filter_quality": "3",
        "lights_and_shadows/directional_shadow/soft_shadow_filter_quality": "3",
    }
    path = Path(directory) / "project.godot"
    text = path.read_text(encoding="utf-8")
    match = re.search(r'\[rendering\]\s*\n(.*?)(?=\n\[|\Z)', text, re.S)
    body = match.group(1) if match else ""
    for key, value in values.items():
        body = re.sub(r'^' + re.escape(key) + r'\s*=.*\n?', '', body, flags=re.M)
        body += f"{key}={value}\n"
    section = "[rendering]\n\n" + body
    text = text[:match.start()] + section + text[match.end():] if match else text + "\n" + section
    path.write_text(text, encoding="utf-8")
