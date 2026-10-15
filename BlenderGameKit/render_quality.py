"""Opt-in project defaults shared by exported levels."""
from pathlib import Path
import re


def apply_startup_quality(directory, scene):
    if getattr(getattr(scene, "gk_settings", None), "render_quality", "KEEP") != "HIGH":
        return
    values = {
        "scaling_3d/scale": "1.0",
        "anti_aliasing/quality/msaa_3d": "2",
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
