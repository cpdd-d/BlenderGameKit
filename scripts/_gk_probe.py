"""自查脚本共用的一点点小工具(第一百三十四轮)。

── 为什么需要它 ──────────────────────────────────────────────────────
每个示例的"游戏逻辑"可能挂在**关卡图**上,也可能挂在**角色图**上,导出名
跟着变:
  * `game_logic.gd`                      单场景 + 场景图(最老的名字)
  * `game_logic_<场景>.gd`                多场景工程的**关卡图**
  * `game_logic_<场景>_<角色>.gd`          **角色图**(中文角色名会压成 `char`)
  * `game_logic_<场景>_level.gd`          单场景 + 关卡图
以前每个自查都写死其中一个名字,导出侧一改命名就**整片假红** ——
而且红在"没找到生成的脚本"这种看不出所以然的地方(第一百三十四轮一次
就踩了五个自查:curve / game / physics / ai / assets)。这里统一按目录扫。
"""
import glob
import os


def find_logic_script(out_dir, scene=None, prefer=None):
    """在 `<out_dir>/scripts` 里找那份游戏逻辑脚本;找不到返回 ""。

    scene   给了就先只看 `game_logic_<scene>*.gd`(多场景工程必备)
    prefer  给了就优先挑**内容里含这个片段**的那份(最可靠的判据)
    都没有时:多份候选里挑**最大**的 —— 空壳脚本只有几十行,
    真正的逻辑脚本几百到几千行。
    """
    scripts = os.path.join(out_dir, "scripts")
    candidates = sorted(glob.glob(os.path.join(scripts, "game_logic*.gd")))
    if not candidates:
        return ""
    if scene:
        exact = [path for path in candidates
                 if os.path.basename(path).startswith(f"game_logic_{scene}")]
        if exact:
            candidates = exact
    if prefer:
        for path in candidates:
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    if prefer in handle.read():
                        return path
            except OSError:
                continue
    return max(candidates, key=lambda path: os.path.getsize(path))
