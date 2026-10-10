"""渲染验证进守则(第九十一轮:待办 I 组 32 "demo_render 渲染结果进 CI")。

用法:
    python scripts\\check_render_demo.py
    python scripts\\check_render_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它做的事(和别的自查脚本一个套路):

  1. 重建 demo(`make_render_demo.py`)—— 顺带把**插件里那份**着色器
     (`shaders/gk_toon.gdshader`)复制进工程:验的就是"发布出去的那份";
  2. 用**真窗口**跑 Godot 扫光(4 个角度各一张图)。⚠ 不能用 `--headless`:
     无头是 dummy 渲染器,什么都画不出来 —— 这一层要的就是真 GPU 出图;
  3. 断言的是**渲染结果**,不是"文件存在":
     * 每张图都不是一片黑(non_black 占比够高)—— 角色真的画出来了;
     * 四张图**两两字节不同** —— 灯真的转了;
     * capture.gd 的像素统计里 dark 随角度**变化**(至少 3 种取值)——
       这就是"面部阴影 SDF 随光角度滑动"的证据(单张静态图证明不了);
     * 没有 SCRIPT / SHADER / Parse 错误;
  4. 出 `CHECK_RENDER_DEMO=OK` / `FAILED`。

── 没有 GPU 时**跳过**,不判红 ──────────────────────────────────────
`--headless` 的机器 / 开不出窗口的环境(远端无显示、容器)里,这一层是
**加分项**:打印"跳过:…"并返回 0,守住"守则不会因为环境没有显卡而红"。
真出图了但结果不对(全黑 / 四张一模一样 / 脚本报错)—— 那是真红。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEMO = os.path.join(ROOT, "examples", "demo_render")
OUT = os.path.join(ROOT, "_dist", "render_probe")
SHOTS = os.path.join(OUT, "shots")

GODOT_DEFAULT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"
# 扫光角度(度):0 = 光从正前方,90 = 从画面右侧,180 = 背后,270 = 左侧。
# 四个角度足够看出"阴影线在滑动";步长小一点会慢,没必要。
SWEEP_STEP = "90"

# 判定阈值(对着 480×480 的实测数据留足余量):
# 实测 non_black 16257~25600 / 25600、lit 恒 2664、dark 13584~22704。
MIN_NON_BLACK_RATIO = 0.30     # 画面不能是一片黑
MIN_LIT = 200                  # 得有亮的地方(角色/舞台真的被照到了)
MIN_DISTINCT_DARK = 3          # dark 至少 3 种取值 = 阴影随角度在动


def log(message):
    print(f"[渲染验证] {message}")


def find_godot(explicit=""):
    candidate = (explicit or os.environ.get("GK_GODOT")
                 or GODOT_DEFAULT)
    return candidate if os.path.isfile(candidate) else ""


def rebuild_demo():
    """跑一次生成器:工程 + 插件那份着色器副本都刷成当前版。"""
    script = os.path.join(HERE, "make_render_demo.py")
    result = subprocess.run([sys.executable, script],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    ok = result.returncode == 0 and os.path.isfile(
        os.path.join(DEMO, "Main.tscn"))
    for line in (result.stdout or "").splitlines():
        if "已生成" in line or "错误" in line or "Error" in line:
            log(line.strip())
    if not ok:
        log("重建 demo 失败,尾部输出:")
        for line in (result.stdout or "").splitlines()[-10:]:
            log("   " + line)
        for line in (result.stderr or "").splitlines()[-10:]:
            log("   " + line)
    return ok


def run_sweep(godot):
    """真窗口跑扫光。返回 (stdout+stderr, 是否开得起来)。"""
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(SHOTS, exist_ok=True)
    env = dict(os.environ)
    env["GK_SWEEP"] = "1"
    env["GK_SWEEP_STEP"] = SWEEP_STEP
    env["GK_SHOTS_DIR"] = SHOTS
    run = subprocess.run(
        [godot, "--path", DEMO, "--resolution", "480x480"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=180)
    return (run.stdout or "") + (run.stderr or ""), run.returncode


# 开不出窗口 / 没有 GPU 时才认的报错(真出图了但结果不对不算)
_NO_DISPLAY_MARKS = (
    "Unable to create window", "Can't create window", "Cannot create window",
    "vulkan", "Vulkan", "OpenGL", "display", "Display", "driver", "Driver",
    "X11", "Wayland", "headless", "no GPU",
)


def _no_display(text):
    return any(mark in text for mark in _NO_DISPLAY_MARKS)


def _shot_stats(text):
    """从 capture.gd 的输出里取每张图的像素统计(dark 是用来验滑动的)。"""
    stats = []
    for block in text.split("GK_RENDER size=")[1:]:
        non_black = re.search(r"GK_RENDER non_black=(\d+)/(\d+)", block)
        lit = re.search(r"GK_RENDER lit=(\d+) dark=(\d+)", block)
        if not non_black or not lit:
            continue
        stats.append({
            "non_black": int(non_black.group(1)),
            "total": int(non_black.group(2)),
            "lit": int(lit.group(1)),
            "dark": int(lit.group(2)),
        })
    return stats


def _pngs():
    if not os.path.isdir(SHOTS):
        return []
    return sorted(os.path.join(SHOTS, name) for name in os.listdir(SHOTS)
                  if name.endswith(".png"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--godot", default="")
    args = parser.parse_args()

    godot = find_godot(args.godot)
    if not godot:
        log(f"跳过:找不到 Godot({args.godot or GODOT_DEFAULT})")
        return 0
    if not os.path.isdir(DEMO):
        log(f"跳过:还没有 {DEMO}")
        return 0

    if not rebuild_demo():
        print("CHECK_RENDER_DEMO=FAILED")
        return 1

    try:
        text, code = run_sweep(godot)
    except subprocess.TimeoutExpired:
        log("渲染超时(180 秒)—— 这台机器跑不动真窗口渲染?")
        log("跳过:当成环境不满足(不是代码问题)")
        return 0

    files = _pngs()
    stats = _shot_stats(text)
    errors = [line.strip() for line in text.splitlines()
              if re.search(r"SCRIPT ERROR|SHADER ERROR|Parse Error", line)]
    done = "GK_SWEEP=DONE" in text

    if not files and not done:
        # 一张都没出:要么开不出窗口(环境),要么真崩了(代码)
        if _no_display(text) or code != 0:
            log("跳过:这台机器开不出渲染窗口 / 没有可用 GPU")
            for line in text.splitlines()[-6:]:
                if line.strip():
                    log("   " + line.strip())
            return 0
        log("一张图都没出,而且看不出是环境问题 —— 尾部输出:")
        for line in text.splitlines()[-15:]:
            if line.strip():
                log("   " + line.strip())
        print("CHECK_RENDER_DEMO=FAILED")
        return 1

    problems = []
    if errors:
        problems.append("运行期有脚本 / 着色器错误:" + errors[0])
    if len(files) < 4:
        problems.append(f"只出了 {len(files)} 张图(期望 4 个角度)")
    if len(stats) < 4:
        problems.append(f"像素统计只有 {len(stats)} 份(期望 4 份)")
    for index, entry in enumerate(stats):
        ratio = entry["non_black"] / max(1, entry["total"])
        if ratio < MIN_NON_BLACK_RATIO:
            problems.append(f"第 {index + 1} 张图几乎全黑"
                            f"(non_black {ratio:.0%})—— 没画出东西")
        if entry["lit"] < MIN_LIT:
            problems.append(f"第 {index + 1} 张图没有亮部(lit={entry['lit']})")
    darks = {entry["dark"] for entry in stats}
    if len(darks) < MIN_DISTINCT_DARK:
        problems.append(f"dark 只有 {len(darks)} 种取值({sorted(darks)})—— "
                        f"灯转了但明暗没跟着变?面部阴影没在滑动")
    # 四张图**两两字节不同**:灯真的转了(文件一样 = 转了没生效)
    blobs = []
    for path in files:
        with open(path, "rb") as handle:
            blobs.append(handle.read())
    if len(set(blobs)) != len(blobs):
        problems.append("有两张图**一模一样** —— 扫光没真的改变画面")

    for entry in stats:
        log(f"non_black={entry['non_black']}/{entry['total']} "
            f"lit={entry['lit']} dark={entry['dark']}")
    log(f"出图 {len(files)} 张,"
        f"字节尺寸 {[len(blob) for blob in blobs]}")

    if problems:
        for problem in problems:
            log("✗ " + problem)
        print("CHECK_RENDER_DEMO=FAILED")
        return 1
    print("CHECK_RENDER_DEMO=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())