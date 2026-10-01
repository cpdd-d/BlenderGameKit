"""生成 NPR 渲染 demo 工程,并(可选)用 Godot 渲染出图。

用法:
    python scripts/make_render_demo.py

产出 examples/demo_render/:
    project.godot, Main.tscn, capture.gd, shaders/*.gdshader

为什么用脚本生成而不是手写:Godot 工程不能引用工程目录之外的文件,
着色器又必须放在 assets/godot/shaders/ 下作为插件的素材，
所以 demo 生成时把着色器复制进来，保证两边永远一致。
"""

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHADER_SRC = os.path.join(ROOT, "BlenderGameKit", "shaders")
OUT = os.path.join(ROOT, "examples", "demo_render")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


# ---------------------------------------------------------------------------
# 每个球体一个材质:用来对比卡通着色的关键开关
# ---------------------------------------------------------------------------
# (材质名, 基础色, 主参数覆盖)
MATERIAL_VARIANTS = [
    ("Albedo01", (1.00, 0.86, 0.82), {
        "shadow_step": 0.12, "shadow_smooth": 0.02, "three_tone": 0.0,
        "specular_amount": 0.0, "rim_amount": 0.0,
    }),
    ("Albedo02_hard", (0.98, 0.78, 0.80), {
        "shadow_step": 0.18, "shadow_smooth": 0.004, "three_tone": 0.0,
        "specular_amount": 0.0, "rim_amount": 0.0,
    }),
    ("Albedo03_three_tone", (0.90, 0.80, 0.98), {
        "shadow_step": 0.22, "shadow_step_2": -0.15, "shadow_smooth": 0.02,
        "three_tone": 1.0, "specular_amount": 0.0, "rim_amount": 0.0,
    }),
    ("Albedo04_full", (0.98, 0.86, 0.84), {
        "shadow_step": 0.14, "shadow_smooth": 0.02, "three_tone": 0.0,
        "specular_amount": 0.55, "specular_size": 0.82,
        "rim_amount": 0.85, "rim_width": 0.45, "backlight_amount": 0.5,
    }),
]


def make_material(name, albedo, params, with_outline):
    lines = [
        '[gd_resource type="ShaderMaterial" load_steps=2 format=3]',
        "",
        '[ext_resource type="Shader" path="res://shaders/gk_toon.gdshader" id="1_toon"]',
    ]
    if with_outline:
        lines.append(
            '[ext_resource type="Shader" path="res://shaders/gk_outline.gdshader" id="2_outline"]'
        )
        lines.append("")
        lines.append('[sub_resource type="ShaderMaterial" id="outline_pass"]')
        lines.append('shader = ExtResource("2_outline")')
        lines.append("shader_parameter/outline_color = Color(0.05, 0.04, 0.09, 1)")
        lines.append("shader_parameter/outline_width = 0.02")
        lines.append("shader_parameter/outline_only = 1.0")
    lines.append("")
    lines.append("[resource]")
    lines.append('shader = ExtResource("1_toon")')
    lines.append(
        "shader_parameter/albedo_color = Color({:.3f}, {:.3f}, {:.3f}, 1)".format(*albedo)
    )
    lines.append("shader_parameter/albedo_strength = 1.0")
    lines.append("shader_parameter/light_map_mix = 0.0")
    lines.append("shader_parameter/lit_tint = Color(1, 0.976, 0.914, 1)")
    lines.append("shader_parameter/shadow_tint = Color(0.706, 0.627, 0.902, 1)")
    lines.append("shader_parameter/shadow_tint_2 = Color(0.478, 0.42, 0.722, 1)")
    lines.append("shader_parameter/shadow_strength = 1.0")
    lines.append("shader_parameter/rim_color = Color(0.75, 0.85, 1, 1)")
    lines.append("shader_parameter/rim_front = 0.15")
    lines.append("shader_parameter/backlight_color = Color(1, 0.75, 0.55, 1)")
    lines.append("shader_parameter/emission_amount = 0.0")
    lines.append("shader_parameter/alpha_cut = 0.5")
    lines.append("shader_parameter/eye_highlight_boost = 0.0")
    for key, value in params.items():
        lines.append(f"shader_parameter/{key} = {value}")
    if with_outline:
        lines.append('next_pass = SubResource("outline_pass")')
    return write(os.path.join(OUT, "materials", f"{name}.tres"), "\n".join(lines) + "\n")


def make_scene():
    """2x2 球体对比 + 一个带描边的胶囊角色。"""
    ext = [
        '[ext_resource type="Shader" path="res://shaders/gk_toon.gdshader" id="sh_toon"]',
        # sh_outline 必须在场景里也声明:sub_resource 引用它时用的是场景自己的
        # ext-resource 缓存，只在 .tres 里声明是不够的。
        '[ext_resource type="Shader" path="res://shaders/gk_outline.gdshader" id="sh_outline"]',
        '[ext_resource type="Script" path="res://capture.gd" id="sh_capture"]',
        '[ext_resource type="Script" path="res://jitter_probe.gd" id="sh_jitter"]',
        '[ext_resource type="Texture2D" path="res://art/face_albedo.png" id="tex_face"]',
        '[ext_resource type="Texture2D" path="res://art/face_sdf.png" id="tex_sdf"]',
    ]
    mats = []
    nodes = []
    # 球体排成一行放前面,方便对比四种参数
    positions = [(-1.5, 0.6, -0.9), (-0.5, 0.6, -0.9), (0.5, 0.6, -0.9), (1.5, 0.6, -0.9)]
    for index, (name, albedo, params) in enumerate(MATERIAL_VARIANTS):
        mat_id = f"m{index}"
        ext.append(
            f'[ext_resource type="ShaderMaterial" path="res://materials/{name}.tres" id="{mat_id}"]'
        )
        x, y, z = positions[index]
        nodes.append(
            f'[node name="{name}" type="MeshInstance3D" parent="."]\n'
            f"transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, {x}, {y}, {z})\n"
            f'mesh = SubResource("sphere")\n'
            f'material_override = ExtResource("{mat_id}")'
        )

    subs = [
        '[sub_resource type="SphereMesh" id="sphere"]\n'
        "radius = 0.55\n"
        "height = 1.1\n"
        "radial_segments = 48\n"
        "rings = 24",
        '[sub_resource type="CapsuleMesh" id="capsule"]\n'
        "radius = 0.35\n"
        "height = 1.6\n"
        "radial_segments = 8\n"
        "rings = 4",
        '[sub_resource type="PlaneMesh" id="floor"]\n'
        "size = Vector2(14, 14)",
        '[sub_resource type="StandardMaterial3D" id="floor_mat"]\n'
        "albedo_color = Color(0.10, 0.11, 0.16, 1)",
        # 一张正对相机的四边形当作风格化头像。用平面而不是球体是有意的:
        # 平面 UV 天然就是正面投影，正是面部阴影 SDF 需要的坐标系;
        # 球体的 UV 会在接缝处翻转，把 SDF 效果搞得一塌糊涂。
        '[sub_resource type="QuadMesh" id="face_quad"]\n'
        "size = Vector2(1.0, 1.0)",
        # 面部材质:开 face_sdf_mix，让阴影线由贴图控制而不是几何法线
        '[sub_resource type="ShaderMaterial" id="mat_face"]\n'
        'shader = ExtResource("sh_toon")\n'
        "shader_parameter/albedo_color = Color(1, 1, 1, 1)\n"
        'shader_parameter/albedo_texture = ExtResource("tex_face")\n'
        "shader_parameter/alpha_cut = 0.4\n"
        "shader_parameter/three_tone = 0.0\n"
        "shader_parameter/shadow_step = 0.1\n"
        "shader_parameter/shadow_smooth = 0.02\n"
        "shader_parameter/lit_tint = Color(1, 0.976, 0.914, 1)\n"
        "shader_parameter/shadow_tint = Color(0.706, 0.627, 0.902, 1)\n"
        "shader_parameter/specular_amount = 0.0\n"
        "shader_parameter/rim_amount = 0.0\n"
        "shader_parameter/backlight_amount = 0.0\n"
        "shader_parameter/face_sdf_mix = 1.0\n"
        'shader_parameter/face_sdf = ExtResource("tex_sdf")\n'
        "shader_parameter/face_sdf_angle_scale = 1.6\n"
        "shader_parameter/face_sdf_smooth = 0.03",
        # outline_pass 必须在引用它的 mat_hero **之前**定义:
        # Godot 解析 sub_resource 是按出现顺序登记的，先引用后定义会报
        # `_parse_sub_resource ... ERR_INVALID_PARAMETER` 然后整个场景加载失败。
        '[sub_resource type="ShaderMaterial" id="outline_pass"]\n'
        'shader = ExtResource("sh_outline")\n'
        "shader_parameter/outline_color = Color(0.05, 0.04, 0.09, 1)\n"
        "shader_parameter/outline_width = 0.02\n"
        "shader_parameter/outline_only = 1.0",
        '[sub_resource type="ShaderMaterial" id="mat_hero"]\n'
        'shader = ExtResource("sh_toon")\n'
        "shader_parameter/albedo_color = Color(0.98, 0.86, 0.84, 1)\n"
        "shader_parameter/shadow_step = 0.14\n"
        "shader_parameter/shadow_smooth = 0.02\n"
        "shader_parameter/specular_amount = 0.5\n"
        "shader_parameter/specular_size = 0.8\n"
        "shader_parameter/rim_amount = 0.9\n"
        "shader_parameter/rim_width = 0.45\n"
        "shader_parameter/backlight_amount = 0.6\n"
        'next_pass = SubResource("outline_pass")',
        '[sub_resource type="Environment" id="env"]\n'
        "background_mode = 1\n"
        "background_color = Color(0.06, 0.06, 0.09, 1)\n"
        "ambient_light_source = 1\n"
        "ambient_light_color = Color(0.22, 0.24, 0.38, 1)\n"
        # 环境光能量必须压得很低:它会把"暗部"整片洗白，卡通着色的硬边就没了
        "ambient_light_energy = 0.12\n"
        "tonemap_mode = 2",
    ]

    steps = len(ext) + len(subs) + 1
    parts = [f"[gd_scene load_steps={steps} format=3]", ""]
    parts += ext
    parts.append("")
    parts += subs
    parts.append("")
    parts.append('[node name="Root" type="Node3D"]')
    parts.append('script = ExtResource("sh_capture")')
    parts.append("")
    parts.append('[node name="WorldEnvironment" type="WorldEnvironment" parent="."]')
    parts.append('environment = SubResource("env")')
    parts.append("")
    parts.append('[node name="KeyLight" type="DirectionalLight3D" parent="."]')
    parts.append("transform = Transform3D(0.86, -0.35, 0.37, 0, 0.73, 0.68, -0.51, -0.59, 0.63, 2, 3, 2)")
    # LIGHT_COLOR 已经把 light_energy 算进去了,两个都调高就会过曝成一片白。
    # LIGHT_COLOR 已经把 light_energy 算进去了。卡通着色的明暗由 tint 控制，
    # 光强只需要把亮部推到接近 1.0:实测 0.4~0.6 是甜点区，
    # 0.9 以上亮部会整片过曝成纯白，明暗分界消失。
    parts.append("light_energy = 0.5")
    parts.append("light_color = Color(1, 0.97, 0.9, 1)")
    parts.append("shadow_enabled = true")
    parts.append("")
    parts.append('[node name="RimLight" type="DirectionalLight3D" parent="."]')
    parts.append("transform = Transform3D(-0.86, 0.35, -0.37, 0, 0.73, 0.68, 0.51, 0.59, -0.63, -2, 1, -3)")
    parts.append("light_energy = 0.16")
    parts.append("light_color = Color(0.6, 0.75, 1, 1)")
    parts.append("")
    parts.append('[node name="Camera" type="Camera3D" parent="."]')
    # 稍微抬高并后拉,把上面那张脸也收进画面
    parts.append("transform = Transform3D(1, 0, 0, 0, 0.99, 0.12, 0, -0.12, 0.99, 0, 2.6, 7.2)")
    parts.append("fov = 50.0")
    parts.append("")
    parts.append('[node name="Floor" type="MeshInstance3D" parent="."]')
    parts.append('mesh = SubResource("floor")')
    # 地面材质用 sub_resource + material_override。
    # 曾经把它写成一个独立节点 `[node name="FloorMat" type="StandardMaterial3D"]`,
    # 那是错的 —— 材质不是节点,Godot 会报
    # "Node FloorMat of type StandardMaterial3D cannot be created"。
    parts.append("material_override = SubResource(\"floor_mat\")")
    parts.append("")
    parts.append('[node name="Hero" type="MeshInstance3D" parent="."]')
    parts.append("transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1.05, 0)")
    parts.append('mesh = SubResource("capsule")')
    parts.append('material_override = SubResource("mat_hero")')
    parts.append("")
    # 手绘描边动画的验证探针:把背景刷亮(描边就成了深色像素,好统计),
    # 按环境变量决定开/关抖动,抓一串帧后自己退出。
    parts.append('[node name="JitterProbe" type="Node3D" parent="."]')
    parts.append('script = ExtResource("sh_jitter")')
    parts.append("")
    # 头像:放在胶囊上方,正对相机
    parts.append('[node name="Head" type="MeshInstance3D" parent="."]')
    parts.append("transform = Transform3D(1.15, 0, 0, 0, 1.15, 0, 0, 0, 1, 0, 2.45, 0)")
    parts.append('mesh = SubResource("face_quad")')
    parts.append('material_override = SubResource("mat_face")')
    parts.append("")
    parts += nodes
    return write(os.path.join(OUT, "Main.tscn"), "\n".join(parts) + "\n")


def main():
    if not os.path.isdir(SHADER_SRC):
        print(f"找不到着色器目录: {SHADER_SRC}")
        return 1

    os.makedirs(os.path.join(OUT, "shaders"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "materials"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "art"), exist_ok=True)

    copied = []
    for name in sorted(os.listdir(SHADER_SRC)):
        if name.endswith(".gdshader"):
            shutil.copy2(os.path.join(SHADER_SRC, name), os.path.join(OUT, "shaders", name))
            copied.append(name)

    # 贴图:先确保生成过,再复制进工程(Godot 工程不能引用目录外的文件)
    try:
        import make_face_sdf

        make_face_sdf.main()
    except Exception as exc:
        print(f"生成面部贴图失败({exc})，如果 examples/demo_render/art/ 里已有贴图可以忽略")
    for texture in ("face_albedo.png", "face_sdf.png"):
        source = os.path.join(OUT, "art", texture)
        if not os.path.isfile(source):
            print(f"缺少贴图: {source}")
            return 1

    for index, (name, albedo, params) in enumerate(MATERIAL_VARIANTS):
        make_material(name, albedo, params, with_outline=(index == 3))
    make_scene()

    print(f"已生成 NPR demo: {OUT}")
    print(f"  着色器: {', '.join(copied)}")
    print(f"  材质变体: {len(MATERIAL_VARIANTS)} 个")
    print("  场景: Main.tscn (4 个球对比 + 带描边的胶囊 + 面部 SDF 头像)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
