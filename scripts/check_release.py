"""打包发布的端到端检查(第五十二轮)。

用法:
    python scripts\\check_release.py
    python scripts\\check_release.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它验的是**真打包**这一环 —— 前面几层都停在"Godot 工程看起来对",
而"能不能打出可执行文件"只有在真 Godot 里跑一次 `--export-release` 才知道。

步骤(全程在 `_dist/release_probe` 里,不碰 examples):
  1. 建一个最小的 Godot 项目(main.tscn + project.godot);
  2. 用 `packaging.ensure_export_presets()` 生成导出预设;
  3. 探导出模板:没装就打印"跳过"并返回 0(**不判红** —— 本机没装模板
     是环境问题,不是代码问题;装了模板的机器才会真正跑到第 4 步);
  4. `godot --headless --path <项目> --import`(让资源进缓存);
  5. `godot --headless --path <项目> --export-release "Windows Desktop" <产物>`;
  6. 断言产物文件真的躺在磁盘上。

`packaging` 是纯标准库模块,所以这个脚本**不需要 Blender**。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from BlenderGameKit import packaging  # noqa: E402

OUT = os.path.join(ROOT, "_dist", "release_probe")

PROJECT_GODOT = """config_version=5

[application]

config/name="GKReleaseProbe"
run/main_scene="res://main.tscn"

[rendering]

renderer/rendering_method="forward_plus"
"""

MAIN_TSCN = """[gd_scene format=3]

[node name="Main" type="Node2D"]
"""


def build_project():
    """建一个最小可导出的 Godot 项目,返回项目目录。"""
    if os.path.isdir(OUT):
        shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "project.godot"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROJECT_GODOT)
    with open(os.path.join(OUT, "main.tscn"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(MAIN_TSCN)
    return OUT


def godot_version(exe):
    try:
        result = subprocess.run([exe, "--version"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                timeout=15)
    except Exception:
        return ""
    text = (result.stdout or "") + (result.stderr or "")
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", text)
    return match.group(1) if match else ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--godot", default=(
        r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"))
    args = parser.parse_args()

    if not os.path.isfile(args.godot):
        print(f"跳过:找不到 Godot({args.godot})")
        return 0

    project = build_project()
    output = packaging.default_output_path(project, "GKReleaseProbe")
    preset_file, created = packaging.ensure_export_presets(
        project, title="GKReleaseProbe",
        export_path=packaging.rel_output_path(project, output))
    print(f"RELEASE_PRESET created={created} path={preset_file}")

    version = godot_version(args.godot)
    ready, template_dir, note = packaging.templates_status(version)
    print(f"RELEASE_TEMPLATE ready={ready} version={version} dir={template_dir}")

    # 导入一次,确保 .godot 缓存建好(导出会读它)
    subprocess.run([args.godot, "--headless", "--path", project, "--import"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")

    # ── 无论有没有模板都**真跑一次导出** ─────────────────────────────
    # 为什么不等探测到"没装模板"就早退:真跑一次能额外证明一件事 ——
    # Godot **认得出我们生成的预设**(报错若是"找不到模板",说明预设已经
    # 被解析、预设名也对上了;若预设写坏了,它会报别的错)。所以没装模板的
    # 机器上这一步也不是白跑。
    ok, log = packaging.run_release(args.godot, project,
                                    packaging.WINDOWS_PRESET_NAME, output)
    exists = os.path.isfile(output)
    size = os.path.getsize(output) if exists else 0
    print(f"RELEASE_RUN ok={ok} exists={exists} size={size} path={output}")

    if exists:
        # embed_pck=true 时产物是单个 exe,怎么也不该小于 1KB
        if size < 1024:
            print(f"✗ 产物太小({size} 字节),多半不是真的可执行文件")
            print("CHECK_RELEASE=FAIL")
            return 1
        print("CHECK_RELEASE=OK")
        return 0

    # 没出文件:分清"模板没装(本机环境,跳过)"还是"预设/命令有问题(判红)"
    if packaging.template_missing_in_log(log):
        print(f"跳过:没装 Godot 导出模板(版本 {version});"
              f"但预设名与命令已被 Godot 接受(只差模板文件)")
        for line in [ln for ln in (note or "").splitlines() if ln.strip()][:2]:
            print("  " + line)
        return 0

    print("✗ 打包失败(报错不是『缺模板』,可能预设或命令有问题)。"
          "Godot 输出(尾部 20 行):")
    for line in [ln for ln in (log or "").splitlines() if ln.strip()][-20:]:
        print("   " + line)
    print("CHECK_RELEASE=FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
