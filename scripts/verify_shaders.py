"""校验 Godot 着色器:在真实 Godot 里编译,把错误原文带行号抓出来。

用法:
    python scripts/verify_shaders.py                     # 磁盘上的 + **代码里内嵌的**着色器
    python scripts/verify_shaders.py a.gdshader b.gdshader   # 只校验指定文件

做法:临时建一个最小 Godot 工程,场景里每个着色器挂一个 ShaderMaterial。
Godot 加载场景时会真的编译着色器,语法错误原样打到 stderr。

── 为什么还要查"内嵌的"着色器(第五十一轮真机抓到)──────────────────
『屏幕_滤镜』/『屏幕_暗角』的着色器源码是**写在 Python 里的字符串**
(`codegen/services.py` 的 `_GD_*_SHADER`),不是 .gdshader 文件 ——
所以只扫 shaders/ 目录这条链路**永远看不到它们**。

后果实测过:滤镜里用的是 Godot 3 时代的 `SCREEN_TEXTURE`,而 Godot 4.7
早就删掉了,导出的材质**根本编译不过**;无头探针用的是 dummy 渲染器,
着色器编译错误不进断言,于是一直没人发现。现在这里把内嵌着色器
按正则抽出来一起编译 —— 这类"代码里写死的着色器坏掉"就挡在门禁里了。

已知坑(踩过):
  * **Godot 着色器编译器不接受注释里的非 ASCII 字符**
    (报错形如 `Tokenizer: Unknown character #22522`)，所以 .gdshader
    里只能写英文注释,中文说明放到 .md 里。
  * **Godot 4 没有内置的 `SCREEN_TEXTURE` 了** —— 读屏必须声明
    `uniform sampler2D x : hint_screen_texture;` 再 `texture(x, SCREEN_UV)`。
  * 场景 .tscn 的 load_steps 必须 >= 实际资源数，否则报
    `Parse Error: Unknown tag ...`，看起来像是着色器的问题，其实是场景坏了。
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 着色器放在插件包内部，这样能随 Blender 扩展一起打包
SHADER_DIR = os.path.join(ROOT, "BlenderGameKit", "shaders")
FIXTURE_DIR = os.path.join(ROOT, "tests")
# 内嵌着色器写在哪个文件里(纯文本正则抽取,不需要 import 插件 / 不需要 bpy)
SERVICES_PY = os.path.join(ROOT, "BlenderGameKit", "codegen", "services.py")
# 在 services.py 里形如:  _GD_VIGNETTE_SHADER = """shader_type canvas_item; ..."""
EMBEDDED_SHADER_RE = re.compile(
    r'^(_GD_[A-Z0-9_]+_SHADER)\s*=\s*"""(.*?)"""',
    re.MULTILINE | re.DOTALL)
GODOT_CANDIDATES = (
    r"D:\godot\Godot_v4.7.2-stable_win64_console.exe",
    r"D:\godot\Godot_v4.7.2-stable_win64.exe",
)

PROJECT_GODOT = """config_version=5

[application]

config/name="Shader Verify"
run/main_scene="res://main.tscn"
"""


def find_godot():
    for candidate in GODOT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("godot")


def shader_kind(path):
    """读一个 .gdshader 的 `shader_type`(决定它该怎么挂上去编译)。

    ── 为什么需要分类型(第一百三十三轮)──────────────────────────────
    天空盒着色器是 `shader_type sky` —— 它只能挂在
    `WorldEnvironment -> Environment.sky -> Sky.sky_material` 上。
    以前这里一律挂成 MeshInstance3D 的 `material_override`,天空着色器
    那样挂会被 Godot 当成"用错了地方",编译信息里出现一堆和着色器本身
    无关的报错(或者干脆不编译)—— 那这条校验对它就等于没做。
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return "spatial"
    match = re.search(r"^\s*shader_type\s+([a-z_]+)\s*;", text, re.MULTILINE)
    return match.group(1) if match else "spatial"


def make_scene(shader_names, kinds=None):
    """建一个引用所有待测着色器的场景。

    load_steps = ext_resource 数 + sub_resource 数 + 1

    `kinds` 形如 {文件名: shader_type};`sky` 的那几个改挂到 WorldEnvironment
    的天空上(见 `shader_kind` 的说明),其余的照旧当网格材质。
    """
    kinds = kinds or {}
    ext_lines = []
    sub_lines = []
    node_lines = []
    sky_index = 0
    for index, name in enumerate(shader_names):
        ext_lines.append(
            f'[ext_resource type="Shader" path="res://shaders/{name}" id="s{index}"]'
        )
        if kinds.get(name) == "sky":
            mat_id = f"skymat{sky_index}"
            sky_id = f"gksky{sky_index}"
            env_id = f"gkenv{sky_index}"
            sky_index += 1
            sub_lines.append(
                f'[sub_resource type="ShaderMaterial" id="{mat_id}"]\n'
                f'shader = ExtResource("s{index}")'
            )
            sub_lines.append(
                f'[sub_resource type="Sky" id="{sky_id}"]\n'
                f'sky_material = SubResource("{mat_id}")'
            )
            # background_mode = 2 是 Godot 的 BG_SKY(真机核对过的枚举值)
            sub_lines.append(
                f'[sub_resource type="Environment" id="{env_id}"]\n'
                f'background_mode = 2\n'
                f'sky = SubResource("{sky_id}")'
            )
            node_lines.append(
                f'[node name="SkyWorld{sky_index}" type="WorldEnvironment" parent="."]\n'
                f'environment = SubResource("{env_id}")'
            )
            continue
        sub_lines.append(
            f'[sub_resource type="ShaderMaterial" id="m{index}"]\n'
            f'shader = ExtResource("s{index}")'
        )
        node_lines.append(
            f'[node name="M{index}" type="MeshInstance3D" parent="."]\n'
            f'mesh = SubResource("box")\n'
            f'material_override = SubResource("m{index}")'
        )

    sub_lines.append('[sub_resource type="BoxMesh" id="box"]')
    steps = len(ext_lines) + len(sub_lines) + 1

    parts = [f"[gd_scene load_steps={steps} format=3]", ""]
    parts += ext_lines
    parts.append("")
    parts += sub_lines
    parts.append('')
    parts.append('[node name="Root" type="Node3D"]')
    parts.append('')
    parts.append('[node name="Camera" type="Camera3D" parent="."]')
    parts.append("transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 4)")
    parts.append('')
    parts.append('[node name="Light" type="DirectionalLight3D" parent="."]')
    parts.append("transform = Transform3D(1, 0, 0, 0, 0.7, 0.7, 0, -0.7, 0.7, 1, 3, 3)")
    parts.append("light_energy = 1.2")
    parts.append('')
    parts += node_lines
    return "\n".join(parts) + "\n"


def collect_embedded():
    """从 codegen/services.py 里抽出内嵌着色器。返回 [(显示名, 源码)]。

    ⚠ 名字必须**只含 ASCII 且有 .gdshader 后缀** —— 下面会用它当临时文件名。
    """
    if not os.path.isfile(SERVICES_PY):
        return []
    with open(SERVICES_PY, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    found = []
    for match in EMBEDDED_SHADER_RE.finditer(text):
        const, code = match.group(1), match.group(2)
        # 常量名里带的是大写下划线,转成小写当文件名(embedded_xxx.gdshader)
        stem = const.lower()
        found.append((f"embedded_{stem}.gdshader", code))
    return found


def collect_targets(argv):
    if argv:
        return [os.path.abspath(a) for a in argv]
    targets = []
    if os.path.isdir(SHADER_DIR):
        targets += [
            os.path.join(SHADER_DIR, name)
            for name in sorted(os.listdir(SHADER_DIR))
            if name.endswith(".gdshader")
        ]
    return targets


def verify(argv):
    godot = find_godot()
    if godot is None:
        print("跳过:找不到 Godot")
        return 0

    targets = collect_targets(argv)
    # 显式点名文件时只查那一个(方便排查);默认把内嵌着色器也一起查
    embedded = [] if argv else collect_embedded()
    if not targets and not embedded:
        print("没有可校验的着色器")
        return 1

    missing = [t for t in targets if not os.path.isfile(t)]
    if missing:
        print(f"文件不存在: {missing}")
        return 1

    work = tempfile.mkdtemp(prefix="gk_shader_verify_")
    try:
        os.makedirs(os.path.join(work, "shaders"), exist_ok=True)
        with open(os.path.join(work, "project.godot"), "w", encoding="utf-8", newline="\n") as h:
            h.write(PROJECT_GODOT)

        names = []
        kinds = {}
        for target in targets:
            name = os.path.basename(target)
            shutil.copy2(target, os.path.join(work, "shaders", name))
            names.append(name)
            kinds[name] = shader_kind(target)
        for name, code in embedded:
            with open(os.path.join(work, "shaders", name), "w",
                      encoding="utf-8", newline="\n") as h:
                h.write(code)
            names.append(name)
            kinds[name] = "canvas_item"
        if embedded:
            print("内嵌着色器(写在 codegen/services.py 里):"
                  + "、".join(n for n, _ in embedded))

        with open(os.path.join(work, "main.tscn"), "w", encoding="utf-8", newline="\n") as h:
            h.write(make_scene(names, kinds))
        sky_names = [n for n in names if kinds.get(n) == "sky"]
        if sky_names:
            print("天空着色器(挂到 WorldEnvironment 的天空上):"
                  + "、".join(sky_names))

        proc = subprocess.run(
            [godot, "--headless", "--path", work, "--import"],
            capture_output=True, text=True, timeout=240,
            encoding="utf-8", errors="replace",
        )
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")

        problems = []
        for line in output.splitlines():
            text = line.strip()
            if not text or text.startswith("Godot Engine v"):
                continue
            if ("SHADER ERROR" in text or "Shader compilation failed" in text
                    or text.startswith("ERROR:") or "Parse Error" in text):
                problems.append(text)

        print(f"校验 {len(names)} 个着色器: {', '.join(names)}")
        print(f"Godot: {os.path.basename(godot)}")
        if problems:
            print(f"\n发现 {len(problems)} 条问题:")
            for item in problems[:40]:
                print(f"  {item}")
        else:
            print("全部编译通过,无错误")

        if os.environ.get("GK_SHADER_RAW"):
            print("\n===== Godot 原始输出 =====")
            print(output.strip())
            print("===== 结束 =====")

        return 1 if problems else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(verify(sys.argv[1:]))
