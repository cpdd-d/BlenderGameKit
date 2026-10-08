"""打包发布的端到端检查(第五十二轮)。

用法:
    python scripts\\check_release.py
    python scripts\\check_release.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它验的是**真打包**这一环 —— 前面几层都停在"Godot 工程看起来对",
而"能不能打出可执行文件"只有在真 Godot 里跑一次 `--export-release` 才知道。

步骤(全程在 `_dist/release_probe` 里,不碰 examples):
  1. 建一个最小的 Godot 项目(main.tscn + project.godot);
  2. 用 `packaging.append_missing_presets()` 一次补上**多个平台**的预设
     (第六十八轮起:Windows / Linux / Web);
  3. 逐个平台探模板:没装那个平台的模板就打印"跳过"并接着看下一个
     (**不判红** —— 本机没装模板是环境问题,不是代码问题);
  4. `godot --headless --path <项目> --import`(让资源进缓存);
  5. 逐个平台真跑 `godot --headless --path <项目> --export-release "<预设>" <产物>`;
  6. 断言每个产出的文件真的躺在磁盘上。

只验 Windows / Linux / Web 这三个**纯模板**导出:Android 还要签名
(keystore)、macOS 只能在 macOS 主机上导 —— 拿它们判红会把"本机没配签名"
误判成代码错。

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
    title = "GKReleaseProbe"

    # 第六十八轮:一次补上多个平台的预设(Windows / Linux / Web)。
    # 只验这三个 —— 它们是**纯模板**导出;Android 还要签名(keystore)、
    # macOS 只能在 macOS 上导,拿它们判红会把"本机没配签名"误判成代码错。
    probed = ["windows", "linux", "web"]
    output_paths = {key: packaging.platform_output_path(project, title, key)
                    for key in probed}
    added = packaging.append_missing_presets(
        project, probed, title=title,
        output_paths={key: packaging.rel_output_path(project, path)
                      for key, path in output_paths.items()})
    preset_file = os.path.join(project, packaging.EXPORT_PRESETS_NAME)
    print(f"RELEASE_PRESET added={added} path={preset_file}")

    version = godot_version(args.godot)
    ready, template_dir, note = packaging.templates_status(version)
    print(f"RELEASE_TEMPLATE ready={ready} version={version} dir={template_dir}")

    # 导入一次,确保 .godot 缓存建好(导出会读它)
    subprocess.run([args.godot, "--headless", "--path", project, "--import"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")

    failures = []
    successes = []
    for key in probed:
        label = packaging.platform_label(key)
        output = output_paths[key]
        # ── 没装这个平台的模板:跳过(本机环境问题,不判红)──────────────
        # 但仍然**真跑一次** —— 能额外证明 Godot 认得出我们补的那段预设
        # (报错若是"找不到模板",说明预设已被解析、预设名也对上了)。
        plat_ready, _d, plat_note = packaging.templates_status(version, key)
        ok, log = packaging.run_release(
            args.godot, project, packaging.PLATFORMS[key]["preset_name"], output)
        exists = os.path.isfile(output)
        size = os.path.getsize(output) if exists else 0
        print(f"RELEASE_RUN_{key} ok={ok} exists={exists} size={size} path={output}")
        if exists:
            if size < 1024:
                failures.append(f"{label}:产物太小({size} 字节),多半不是真的可执行文件")
            else:
                successes.append(label)
            continue
        if packaging.template_missing_in_log(log):
            print(f"跳过:{label} 没装导出模板"
                  f"({'模板已就绪' if plat_ready else '且模板缺失'})")
            for line in [ln for ln in (plat_note or "").splitlines() if ln.strip()][:1]:
                print("  " + line)
            continue
        failures.append(f"{label}:打包失败(报错不是『缺模板』)")
        for line in [ln for ln in (log or "").splitlines() if ln.strip()][-10:]:
            print("   " + line)

    if failures:
        print("✗ " + ";".join(failures))
        print("CHECK_RELEASE=FAIL")
        return 1
    if not successes:
        print(f"跳过:没装任何导出模板(版本 {version});"
              f"但预设名与命令已被 Godot 接受(只差模板文件)")
        for line in [ln for ln in (note or "").splitlines() if ln.strip()][:2]:
            print("  " + line)
        return 0
    print(f"RELEASE_OK {successes}")
    print("CHECK_RELEASE=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
