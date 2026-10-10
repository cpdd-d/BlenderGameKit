"""示例完整性体检(第四十五轮固化):目录 / README / 重建 / 探针 / 门禁,一处不齐就红。

用法:
    python scripts\\check_examples.py

── 为什么要固化它(这一轮真踩到的)────────────────────────────────
第四十四轮加完新内容后做"完整性体检",发现:

  * 两个旧示例目录被改名成 `demo_anim_tree——旧，需重写）` 这种名字,
    可 `make_anim_example.py` / `check_anim_example.py` /
    `verify_anim_system.ps1` 还按老名字找 —— **深层验证直接失败**
    (ANIM_VERIFY=FAILED:"找不到示例工程")。改名看着无害,其实打断了链路。
  * `demo_nav2d` / `demo_physics` 没有 README(别人打开不知道在看什么)。
  * `demo_level`(关卡示例)有重建脚本、有自查,却**没进门禁** ——
    多场景导出这条能力一直没有回归保护。

这三类问题都会被下面几条规则抓住:
  1. 每个 `examples/*` 目录都要有 `README.md`;
  2. 有 `.blend` 的示例要有**重建脚本**(能随时重新生成)和**探针**(能自动验);
  3. 探针必须**被门禁引用**(或者在白名单里写明理由,比如只在 `-Deep` 跑);
  4. 脚本里写死的 `examples/<目录>` 路径必须**真的存在**(防改名)。
"""

import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXAMPLES = os.path.join(ROOT, "examples")
SCRIPTS = os.path.join(ROOT, "scripts")
GATE = os.path.join(SCRIPTS, "build_and_test.ps1")

# ── 白名单:说明"为什么它可以不满足默认规则" ────────────────────────
# Godot 侧示例:没有 .blend(纯手工 Godot 工程),验证走 *.ps1
GODOT_ONLY = {
    "demo_2d": "Godot 侧 2D 示例(无 .blend),门禁 5a 用 godot_verify.ps1 验",
    "demo_platformer": "Godot 侧平台跳跃示例,门禁 5b 用 platformer_verify.ps1 验",
    "demo_render": ("渲染出图工程(无 .blend;要真 GPU 出图,无头是 dummy "
                    "渲染器)—— 门禁 5s 用 check_render_demo.py 真开窗口扫光验,"
                    "没 GPU 的环境自动跳过;手动出参考图跑 render_shots.ps1"),
}
# 旧示例(仅存档):有重建脚本 + 自查,但只在 `-Deep` 那一层跑
DEEP_ONLY = {
    "demo_2d_blend": "旧示例(仅存档),verify_anim_system.ps1 的 2.5D 那一层会重建 + 校验",
    "demo_anim_tree": "旧示例(仅存档),verify_anim_system.ps1 第 3 层会重建 + 校验",
}
# 文档 / 代码示例:没有工程可跑
NO_PROJECT = {
    "gdextension_cpp": "C++ GDExtension 文档 + 代码示例(见 docs/Blender与Godot互通.md)",
}


def mentions(text, folder):
    """文本里是否写死了 `examples/<folder>` **这个目录**。

    ⚠ 必须卡边界:`examples/demo_2d` 是 `examples/demo_2d_blend` 的前缀,
    直接 `in` 判断会让 demo_2d 把 demo_2d_blend 的脚本认成自己的(实测)。
    """
    patterns = (f"examples/{folder}", f"examples\\{folder}",
                f'"examples", "{folder}"')
    for pattern in patterns:
        start = 0
        while True:
            index = text.find(pattern, start)
            if index < 0:
                break
            end = index + len(pattern)
            following = text[end:end + 1]
            if pattern.startswith('"examples"') or not (
                    following.isalnum() or following == "_"):
                return True
            start = end
    return False


def scripts_referencing(folder):
    """哪些脚本里写死了 `examples/<folder>`(重建 / 探针就靠这个链)。"""
    found = []
    for path in sorted(glob.glob(os.path.join(SCRIPTS, "*.py")) +
                       glob.glob(os.path.join(SCRIPTS, "*.ps1"))):
        # 跳过自己:白名单表里写着示例目录名,不排除的话会把自己当成
        # 那些示例的"探针"(体检表里多出一列 check_examples.py)
        if os.path.basename(path) == os.path.basename(__file__):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        if mentions(text, folder):
            found.append(os.path.basename(path))
    return found


def main():
    problems = []
    rows = []
    gate_text = ""
    if os.path.isfile(GATE):
        with open(GATE, "r", encoding="utf-8", errors="replace") as handle:
            gate_text = handle.read()
    else:
        problems.append("找不到门禁脚本 scripts\\build_and_test.ps1")

    for folder in sorted(os.listdir(EXAMPLES)):
        full = os.path.join(EXAMPLES, folder)
        if not os.path.isdir(full):
            continue
        readme = os.path.isfile(os.path.join(full, "README.md"))
        blends = [name for name in os.listdir(full) if name.endswith(".blend")]
        refs = scripts_referencing(folder)
        makers = [n for n in refs if n.startswith("make")]
        checkers = [n for n in refs if n.startswith("check")]
        in_gate = any(n in gate_text for n in refs)

        if not readme:
            problems.append(f"[{folder}] 没有 README.md")
        if blends and not makers:
            problems.append(f"[{folder}] 有 .blend 却没有重建脚本(make_*)")
        if blends and not checkers:
            problems.append(f"[{folder}] 有 .blend 却没有探针(check_*)")
        if checkers and not in_gate:
            if folder in DEEP_ONLY:
                pass          # 白名单:只在 -Deep 层跑,理由见上
            else:
                problems.append(
                    f"[{folder}] 探针 {checkers} 没被门禁引用(不在 build_and_test.ps1 里)")

        note = (GODOT_ONLY.get(folder) or DEEP_ONLY.get(folder)
                or NO_PROJECT.get(folder))
        role = f"白名单:{note}" if note else "正式示例"
        rows.append((folder, len(blends), "有" if readme else "缺",
                     ",".join(makers) or "-", ",".join(checkers) or "-",
                     "是" if in_gate else "否", role))

    # ── 反向检查 2:.ps1 带中文就必须有 UTF-8 BOM(第四十五轮真踩到)──────
    # 不带 BOM 时 Windows PowerShell 按 GBK 读中文字节 —— 有些字节对里
    # 会出现 0x22(引号),把字符串"截断",整个脚本直接语法错
    # (实测:verify_anim_system.ps1 被编辑工具重写丢了 BOM,再也跑不起来)。
    for path in sorted(glob.glob(os.path.join(SCRIPTS, "*.ps1"))):
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        if raw[:3] != b"\xef\xbb\xbf" and any(byte > 127 for byte in raw):
            problems.append(
                f"[{os.path.basename(path)}] 含中文却没有 UTF-8 BOM"
                f"(会被 PowerShell 按 GBK 读成语法错)")

    # ── 反向检查:脚本里写死的示例路径必须存在(防"目录改名打断链路")──
    for path in sorted(glob.glob(os.path.join(SCRIPTS, "*.py")) +
                       glob.glob(os.path.join(SCRIPTS, "*.ps1"))):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        for match in re.finditer(r'examples[/\\]([A-Za-z0-9_]+)', text):
            target = match.group(1)
            if not os.path.isdir(os.path.join(EXAMPLES, target)):
                problems.append(
                    f"[{os.path.basename(path)}] 指向不存在的示例目录: examples/{target}")

    print(f"{'示例':<16}{'blend':<6}{'README':<8}{'make':<24}{'check':<28}"
          f"{'门禁':<5}说明")
    for row in rows:
        print(f"{row[0]:<16}{row[1]:<6}{row[2]:<8}{row[3]:<24}{row[4]:<28}"
              f"{row[5]:<5}{row[6]}")

    if problems:
        print("")
        for item in dict.fromkeys(problems):
            print(f"✗ {item}")
        print(f"CHECK_EXAMPLES=FAILED({len(dict.fromkeys(problems))} 处)")
        return 1
    print("CHECK_EXAMPLES=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())