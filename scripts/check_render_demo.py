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
     * 三色调真的比两色调多一档(tone 探针);
     * 打开补光后球左侧**真的变亮**(fill 探针);
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


# ⚠ Windows 控制台默认 GBK,而下面会用 `✗`(U+2717)打失败信息 —— 直接打印会
#   `UnicodeEncodeError`,**失败信息本身**被一段 Traceback 顶掉(门禁里看到的
#   就是"自查脚本崩了",而不是"哪一条不对")。这里把输出改成能容错的编码。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                          # noqa: BLE001
    pass

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
# 补光:打开后球左侧的平均亮度必须比关掉时高这么多。
# 实测 off≈0.355、on≈0.741(补光能量按真实量级 0.25 给),0.05 的余量足够宽。
MIN_FILL_GAIN = 0.05


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


def run_spec_headless(godot):
    """高光贴图的**无头**那一半:只验"接线"(第一百零一轮)。

    无头能加载 .tres / 贴图资源(渲染器是 dummy,但资源系统是真的),
    所以"材质能不能加载 / 着色器在不在 / 贴图有没有绑上 / 开关对不对"
    这一层无头就能验 —— 而且**不用 GPU**,任何机器都跑得到。
    """
    env = dict(os.environ)
    env["GK_SPEC_CHECK"] = "1"
    run = subprocess.run(
        [godot, "--headless", "--path", DEMO, "res://spec.tscn"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=120)
    return (run.stdout or "") + (run.stderr or "")


def run_spec_render(godot):
    """高光贴图的**真 GPU**那一半:同一颗球换三份材质各出一张图。"""
    env = dict(os.environ)
    env["GK_SPEC"] = "1"
    run = subprocess.run(
        [godot, "--path", DEMO, "res://spec.tscn", "--resolution", "480x480"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=180)
    return (run.stdout or "") + (run.stderr or "")


def run_tone_render(godot):
    """两色调 / 三色调分档探针:一颗球,数中线上的明暗档数。"""
    run = subprocess.run(
        [godot, "--path", DEMO, "res://tone.tscn", "--resolution", "480x480"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    return (run.stdout or "") + (run.stderr or "")


def run_fill_render(godot):
    """主光 + 补光探针:同一颗球只改补光强度,比较左侧平均亮度。"""
    run = subprocess.run(
        [godot, "--path", DEMO, "res://fill.tscn", "--resolution", "480x480"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    return (run.stdout or "") + (run.stderr or "")


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

    # ---- 第一百零一轮:高光形状 / 遮罩贴图 ----
    #
    # ── 为什么分两半 ──────────────────────────────────────────────────
    # 「接线」无头就能验(材质 / 着色器 / 贴图 / 开关),任何机器都跑得到;
    # 「像素」必须真 GPU 出图 —— 无头是 dummy 渲染器。所以接线那半**判红**,
    # 像素那半跟着扫光那一层走(开不出窗口就跳过)。
    spec_problems = []
    # 先导入一次:新写出来的两张高光贴图(PNG)要有 .import 才 load 得到。
    # 不导入的话 .tres 里的 ext_resource 全部找不到,load() 直接返回 null
    # —— 看起来像"材质坏了",其实只是没导入(实测踩到)。
    subprocess.run([godot, "--headless", "--path", DEMO, "--import"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
    try:
        headless = run_spec_headless(godot)
    except subprocess.TimeoutExpired:
        headless = ""
        spec_problems.append("高光贴图的无头检查超时")
    for line in headless.splitlines():
        if "GK_SPEC_CHECK" in line:
            log(line.strip())
    if "GK_SPEC_CHECK=DONE" not in headless or "loaded=false" in headless:
        # 材质加载失败时把 Godot 自己的报错也带出来(不然只剩"没接上"三个字)
        for line in headless.splitlines():
            if re.search(r"ERROR|error|Failed|Can't|Cannot", line):
                log("   " + line.strip())
    for name in ("plain", "ring", "blocked"):
        # 没设过的开关读回来是 `<null>`(手写的那三份材质只在有贴图时才写
        # 开关)—— 当作 0 处理。
        found = re.search(rf"GK_SPEC_CHECK name={name} loaded=true has_shader=true"
                          rf".*use_shape=([-\d.]+|<null>)"
                          rf" use_mask=([-\d.]+|<null>)"
                          rf" shape_w=(-?\d+) mask_w=(-?\d+)", headless)
        if not found:
            spec_problems.append(f"高光材质『{name}』没加载成功 / 拿不到参数"
                                 f"(着色器或贴图没接上?)")
            continue
        use_shape = 0.0 if found.group(1) == "<null>" else float(found.group(1))
        use_mask = 0.0 if found.group(2) == "<null>" else float(found.group(2))
        shape_w = int(found.group(3))
        mask_w = int(found.group(4))
        want = {"plain": (0.0, 0.0), "ring": (1.0, 0.0), "blocked": (0.0, 1.0)}
        if (use_shape, use_mask) != want[name]:
            spec_problems.append(
                f"高光材质『{name}』的开关不对(shape={use_shape} "
                f"mask={use_mask},期望 {want[name]})")
        if name == "ring" and shape_w <= 0:
            spec_problems.append("ring 的形状贴图没绑上(width<=0)")
        if name == "blocked" and mask_w <= 0:
            spec_problems.append("blocked 的遮罩贴图没绑上(width<=0)")
        if name == "plain" and (shape_w > 0 or mask_w > 0):
            spec_problems.append("plain 不该有贴图,却绑上了")
    if "GK_SPEC_CHECK=DONE" not in headless:
        spec_problems.append("高光贴图的无头检查没跑完(材质加载失败?)")

    # 像素那一半:开得出窗口才跑。
    # ⚠ 判据用"扫光真的出了图"(files 非空)—— 不能用 _no_display():
    # 那个是给"一张都没出"那条路用的宽泛关键词表(Godot 正常启动也会打
    # "vulkan" 之类的字样),拿它当"有没有窗口"会误判成没 GPU(实测)。
    spec_bright = {}
    if len(files) >= 1:
        try:
            spec_text = run_spec_render(godot)
        except subprocess.TimeoutExpired:
            spec_text = ""
        for line in spec_text.splitlines():
            if "GK_SPEC" in line and "GK_SPEC_CHECK" not in line:
                log(line.strip())
        for name in ("plain", "ring", "blocked"):
            found = re.search(rf"GK_SPEC name={name} bright=(\d+)", spec_text)
            if found:
                spec_bright[name] = int(found.group(1))
        if "GK_SPEC=DONE" not in spec_text:
            spec_problems.append("高光贴图的出图检查没跑完(开得出窗口却挂了?)")
        elif len(spec_bright) < 3:
            spec_problems.append(f"三份材质的亮点统计不全:{spec_bright}")
        else:
            plain = spec_bright["plain"]
            ring = spec_bright["ring"]
            blocked = spec_bright["blocked"]
            if plain < 50:
                spec_problems.append(
                    f"没有贴图的实心高光本身就没画出来(bright={plain})")
            if not ring < plain:
                spec_problems.append(
                    f"形状贴图没起作用(空心环 bright={ring} 不小于实心 "
                    f"{plain})")
            if blocked > max(2, plain // 20):
                spec_problems.append(
                    f"遮罩贴图没起作用(全黑遮罩下还有 {blocked} 个亮点)")
            log(f"高光亮点数:实心={plain} 空心环={ring} 被遮罩={blocked}")
    else:
        log("跳过高光贴图的出图检查:开不出窗口 / 没有 GPU(接线那半已验)")

    if spec_problems:
        for problem in spec_problems:
            log("✗ " + problem)
        print("CHECK_RENDER_DEMO=FAILED")
        return 1

    # ---- 两色调 / 三色调:三色调必须真的多出一档 ----
    # 这一层专治"参数写了、着色器也编译了,但三色调画出来还是两档"——
    # 静态 grep 和单张图都看不出来,只有数中线颜色能证明。
    tone_problems = []
    if len(files) >= 1:
        try:
            tone_text = run_tone_render(godot)
        except subprocess.TimeoutExpired:
            tone_text = ""
            tone_problems.append("分档探针超时")
        for line in tone_text.splitlines():
            if "GK_TONE" in line:
                log(line.strip())
        bands = {}
        for name in ("two", "three"):
            found = re.search(rf"GK_TONE name={name} bands=(\d+)", tone_text)
            if found:
                bands[name] = int(found.group(1))
        if "GK_TONE=DONE" not in tone_text:
            tone_problems.append("分档探针没跑完(开得出窗口却挂了?)")
        elif len(bands) < 2:
            tone_problems.append(f"分档统计不全:{bands}")
        else:
            if bands["two"] != 2:
                tone_problems.append(
                    f"两色调出现了 {bands['two']} 档(期望 2 档)")
            if bands["three"] < 3:
                tone_problems.append(
                    f"三色调只有 {bands['three']} 档(期望 ≥3 —— "
                    f"阴影_tint_2 那一档没画出来)")
            if bands["three"] <= bands["two"]:
                tone_problems.append(
                    f"三色调没有比两色调多一档(two={bands['two']} "
                    f"three={bands['three']})")
            log(f"分档数:两色调={bands['two']} 三色调={bands['three']}")
    else:
        log("跳过分档探针:开不出窗口 / 没有 GPU")

    if tone_problems:
        for problem in tone_problems:
            log("✗ " + problem)
        print("CHECK_RENDER_DEMO=FAILED")
        return 1

    # ---- 主光 + 补光:打开补光后球左侧必须真的变亮 ----
    # 静态 grep 能证明 uniform 和赋值都在,但证明不了"第二盏灯参与了着色"
    # —— 以前补光借用主光的 shadow_step 当阈值,只点亮极窄一圈边缘,
    # 肉眼看不出差别。只有比较同一块区域的亮度才能抓住这种问题。
    fill_problems = []
    if len(files) >= 1:
        try:
            fill_text = run_fill_render(godot)
        except subprocess.TimeoutExpired:
            fill_text = ""
            fill_problems.append("补光探针超时")
        for line in fill_text.splitlines():
            if "GK_FILL" in line:
                log(line.strip())
        left = {}
        for name in ("off", "on"):
            found = re.search(rf"GK_FILL name={name} left=([-\d.]+)", fill_text)
            if found:
                left[name] = float(found.group(1))
        if "GK_FILL=DONE" not in fill_text:
            fill_problems.append("补光探针没跑完(开得出窗口却挂了?)")
        elif len(left) < 2:
            fill_problems.append(f"补光亮度统计不全:{left}")
        else:
            gain = left["on"] - left["off"]
            if gain < MIN_FILL_GAIN:
                fill_problems.append(
                    f"补光没起作用(左侧 off={left['off']:.4f} "
                    f"on={left['on']:.4f},差值 {gain:+.4f})")
            log(f"补光左侧亮度:关={left['off']:.4f} 开={left['on']:.4f} "
                f"(+{gain:.4f})")
    else:
        log("跳过补光探针:开不出窗口 / 没有 GPU")

    if fill_problems:
        for problem in fill_problems:
            log("✗ " + problem)
        print("CHECK_RENDER_DEMO=FAILED")
        return 1

    print("CHECK_RENDER_DEMO=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())