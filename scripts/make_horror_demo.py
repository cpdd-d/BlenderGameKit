"""恐怖示例(第五十九 / 六十 / 七十 / 一百五十八轮:阶段④⑤)—— 熄灯的教学楼。
用法:
    blender -b --python scripts\\make_horror_demo.py
产出:
    examples/demo_horror/恐怖示例.blend          (一个 .blend,**四个 Blender 场景**)
    examples/demo_horror/恐怖示例_godot/         (可用 Godot 直接打开;每关一张 .tscn)

── 第一百五十八轮:按"系统分工"重排节点图 ─────────────────────────
以前所有东西(HUD / BGM / 存档 / 触发区 / AI / 失败处理)全塞在**角色图**里,
和移动/视角挤成一张 100+ 节点的图;这次拆成三层:

  * **角色图(一张,三关共用)**:输入 / 移动 / 跳跃 / 视角 / 机位。
    三关机位统一叫『侧视机位』『俯瞰机位』,同一个相机物体链接进三关
    (Blender 物体名全局唯一,各建一个会变成 .001/.002,角色图就找不到)。
  * **关卡图(每关一张,挂场景根 Node3D)**:HUD / BGM / 暗角 / 运镜 /
    触发区 / 存档 / 切关 / AI 启动 / 失败处理。玩家没了它也在。
  * **AI 图(一张)**:怪物巡逻 / 追击 / 去查看,并对外输出
    `is_chasing` / `is_investigating` 两个布尔给关卡图读。

图里还用 Blender 原生 NodeFrame 分了①开场②交互③HUD④失败⑤出口等区块
(纯显示,不影响导出;在 Blender 里可以整块折叠)。

── 四关 ─────────────────────────────────────────────────────────
| 幕 | Blender 场景 | 内容 | 用到的 |
|---|---|---|---|
| 0 | 标题 | 标题 + 『开始游戏』(清档) / 『退出游戏』 | 界面_文本 / 界面_按钮 / 存档删除 / 切关卡 |
| 1 | 教室 | **开场俯冲曲线运镜** + 月光/台灯、3 张纸条(拾取即销毁)、**纸条 HUD**、门口门禁 | 相机_切换(沿曲线)/ 灯光 / 触发区 / 存档 |
| 2 | 走廊 | 手电筒(聚光跟相机)、雾、**巡逻怪物(会听动静)**、一次性惊吓区、**被抓失败 + 重试** | 听觉 AI / 心跳紧张度 / 惊吓三连 / 总线音量 / 暂停 |
| 3 | 楼梯间 | 门口 + 结局分支 + 存档,演完**解冻**回标题 | 对话_选择 / 存档 / 比较分支 |

**跨关的数据**(纸条数)走**存档**:教室门口拾满 3 张时 `存档_保存` 落盘,
楼梯间开局 `存档_载入` + `存档_读取到变量` 读回来。
两处『存档_保存』都勾了**『防改档』**(第九十三轮):落盘是带校验和的信封,
玩家手改文件就当没存档;探针里专门有两趟 —— 读回生成代码自己写的信封、
塞一份改过的档验证被拒(`check_horror_demo.py` 的 HORROR_ENVELOPE / HORROR_TAMPER)。

按键:标题页点『开始游戏』/ 游戏里 WASD 移动 / 空格 跳 / 鼠标转视角 /
C·V 切机位 / Tab 上帝机 / **按住 K 弄出动静** / 门口按 **1 或 2** 选结局。

── M0.5(第一百六十轮)────────────────────────────────────────────
1. **笔记日志跨场景**:存档信封里多了三个布尔『已读1 / 已读2 / 已读3』
   (和『纸条数』『结局』同一个字典、同一份校验和)。教室 / 走廊 / 楼梯间
   三关按 **J** 打开的是**同一份**日志;正文来自存档 —— 旧档(没有这三个
   键)一律当"没读过":不崩、面板照开、正文空着。二周目在标题页清档
   之后日志为空。
2. **场景美术**照抄 scripts/make_story_demo.py 里已经调好的那一套
   (make_wall_pieces / make_window / make_pendant / make_sconce):
   走廊加了南北两面带窗洞的墙 + 窗框 + 踢脚线 / 腰线 + 3 盏吊灯(70° 聚光,
   上照 <= 0.12 倍)+ 4 盏壁灯(离墙 1.15 米);教室加了北墙 + 两扇窗 +
   台灯的罩子和发光灯芯。
3. **操作感收口**:开场落点从玩家正面 5.1 米退到 9.6 米(盒子占屏高
   63% -> 33%);走廊手电 4200W -> 480W、射程显式 20 米、角色底色
   0.85 -> 0.45,月光压低压斜 —— 盒子不再是一块白板,顶面和正面有明暗差。

跑法(Godot):打开 `恐怖示例_godot` 直接 F5(主场景是标题页)。
自动检查(不用 Blender,要 Godot):`python scripts\\check_horror_demo.py`
"""

import math
import os
import struct
import sys
import wave

import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "examples", "demo_horror")
BLEND_PATH = os.path.join(OUT_DIR, "恐怖示例.blend")

# ── 四关(Blender 场景名 = 导出的 .tscn 名)──────────────────────────
# 第七十轮加了**标题页**:开局进标题,点『开始游戏』进教室;两个结局演完
# 都回标题(一段可循环的短篇)。
TITLE = "标题"
ROOM = "教室"
CORRIDOR = "走廊"
STAIRS = "楼梯间"

TITLE_RES = "res://scenes/标题.tscn"
ROOM_RES = "res://scenes/教室.tscn"
CORRIDOR_RES = "res://scenes/走廊.tscn"
STAIRS_RES = "res://scenes/楼梯间.tscn"

NOTE_NAMES = ("纸条1", "纸条2", "纸条3")
SCARE_AREA = "惊吓区"
TO_ROOM = "去走廊"
TO_STAIRS = "去楼梯间"
# ⚠ 第 161 轮:原来的『门口』判定区随第 5 章重做**取消**了 —— 结局
#   从"进门厅走 5 米"挪到了**天台**(ROOF_AREA)。老 .blend 里那块
#   判定板重建时会自然消失。
ENEMY = "怪物"
PATROL_A = "巡逻点甲"
PATROL_B = "巡逻点乙"
TORCH = "手电筒"
AI_TREE_NAME = "怪物AI"

# ==========================================================================
# 第 3 章『音乐教室』(M1a 第一刀)—— 标题 -> 教室 -> 走廊 -> 音乐教室 -> 楼梯间
# ==========================================================================
# 玩法闭环(结构别改,见 task-6):
#   ① 从走廊进音乐教室:隔音的音乐教室,钢琴 / 谱架 / 录音机 / 上锁的侧门;
#   ② 侧门锁着,磁带在舞台钢琴上,而**舞台灯亮着**(靠近会被看见);
#   ③ 读 3 张乐谱(靠近按 E)各得一个音位,集齐凑出 4 音顺序(这里 = 3-1-4-2);
#   ④ 录音机(E 打开)按 1/2/3/4 输入四音:
#        错 -> 录音机错次 +1 + 警报(灯闪 / 噪音 / 字幕),错满 2 次怪物进本关;
#        对 -> 播练习曲 + 在『舞台』发噪音(把怪物引过去)+ 舞台灯灭;
#   ⑤ 灯灭了才能去舞台取磁带 -> 侧门(『去楼梯间』)放行 -> 进楼梯间(结局);
#   ⑥ 本关入口有『安全屋区域』,进区存盘;被抓 -> 冻世界 -> 『回到安全屋』
#      -> 传回本关入口,**保留乐谱数与磁带**(不重载整关)。
#
# ⚠ 名字契约(探针按名字找,改名 = 探针失效):
#   场景 / .tscn『音乐教室』、关卡图『音乐教室关卡』、角色『音乐教室玩家』、
#   交互物『乐谱1/2/3』『录音机』『磁带』、区域『舞台』『安全屋区域』
#   (走廊里的出口叫『去音乐教室』,音乐教室里的出口叫『去楼梯间』)。
MUSIC = "音乐教室"
MUSIC_RES = "res://scenes/音乐教室.tscn"
MUSIC_LEVEL_TREE = "音乐教室关卡"
MUSIC_PLAYER = "音乐教室玩家"
MUSIC_ENEMY = "音乐教室怪物"
MUSIC_AI_TREE_NAME = "音乐教室AI"
MUSIC_PATROL_A = "音乐室巡逻点甲"
MUSIC_PATROL_B = "音乐室巡逻点乙"
# 走廊的出口从『去楼梯间』改成『去音乐教室』(中间多插这一章)。
# ⚠ 楼梯间一行代码都不改:它只认自己的载入存档。
TO_MUSIC = "去音乐教室"

# ── 第 2 章保险丝解谜(第一百六十八轮)─────────────────────────────
# 设计文档第 2 章:"找到 3 个保险丝,恢复走廊灯"。走廊开局只有吊灯1 亮
# (出生点照明),吊灯 2/3 是灭的 —— 每捡一个保险丝点亮一盏(渐进反馈,
# 设计文档第四节"每 3–5 分钟要有一次信息或压力的变化"),第三盏亮起 =
# 供电恢复,『去音乐教室』的传送板才放行(以前是路过章,现在有目标了)。
FUSE_COUNT = "保险丝数"
FUSE_GOAL = 3
FUSES = ("保险丝1", "保险丝2", "保险丝3")
FUSE_FLAGS = ("保险丝1拿到", "保险丝2拿到", "保险丝3拿到")
# 位置(Blender x / y):西北柱**旁**(近惊吓区)/ 东南柱**旁**(巡逻线
# 南端)/ 传送板北墙边(顺路拿)。⚠ 别放立柱坐标上(±9,±6.5):判定体
# 埋进 StaticBody 里,玩家一进去就被挤开,body_entered 永远不触发
# (第 168 轮实测,门禁 count 恒 0 就是它)。都避开巡逻线 y=0 与出生点。
FUSE_POS = ((-7.4, 6.2), (7.4, -6.2), (5.0, 8.2))
# 吊灯的 Godot 亮度(make_pendant 默认 4200W,导出 ÷1000):恢复时用。
# ⚠ 『灯光_设置』的『亮度』= Godot light_energy,不是瓦数(第 152 轮)。
FUSE_LIGHT_ENERGY = 4.2

# ── 色彩脚本(第一百七十八轮):每章一个色温 —— 换的是**灯光颜色与雾**,
# 不是模型(设计文档十三节"最小的二次元化动作")。安全屋灯保持暖色不动:
# 它是"家"的信号,和各章冷色形成对比;楼梯间/天台本来就是月蓝,不动。
CORRIDOR_CYAN = (0.72, 0.88, 0.92)    # 走廊:冷青
MUSIC_PURPLE = (0.82, 0.72, 0.95)     # 音乐教室:舞台紫
MED_PALE = (0.86, 0.94, 0.89)         # 医务室:惨白(微绿,医院荧光灯感)
CORRIDOR_GLOW = (0.62, 0.85, 0.90, 1.0)
MUSIC_GLOW = (0.74, 0.60, 0.95, 1.0)
MED_GLOW = (0.86, 0.95, 0.88, 1.0)



def _hidden_note(scene, name, x, y):
    """隐藏笔记:本体(地面上一张淡黄纸片,微光)+ 判定体(hide_mesh)。"""
    decor(no_shadow(make_box(
        f"{name}本体", (0.30, 0.22, 0.03), (x, y, 0.015),
        (0.72, 0.66, 0.40, 1.0), scene=scene,
        glow=((1.0, 0.92, 0.60, 1.0), 0.30))))
    plate = make_box(name, (1.2, 1.2, 1.6), (x, y, 0.8),
                     (0.35, 0.32, 0.20, 1.0), scene=scene)
    physics = getattr(plate, "gk_physics", None)
    if physics is not None:
        physics.hide_mesh = True


def _chase_music_chain(logic, x, y, chase_file, bgm_file,
                      state_var="is_chasing", flag_var="追击中"):
    """追逐切曲(第一百七十九轮):0.25 秒轮询 `is_chasing` —— AI 图**每帧
    按当前状态重算**的 bool(离开追击自动回 false)。进追击且还没切过 ->
    播追逐曲;退出且还挂着 -> 切回环境 BGM。『音效_音乐』重复调用是**切换**
    不叠加(自带淡入淡出),所以来回切是安全的。返回全部节点(进 frame)。
    """
    timer = _add(logic, "GKNodeOnTimer", "追逐轮询", x, y,
                 gk_seconds=0.25, gk_repeat=True)
    get = _add(logic, "GKNodeGetVar", "在读追击吗", x + 200.0, y,
               gk_var_name=state_var)
    # ⚠ `is_chasing` 是 **bool** —— 别过『比较』(bool > 0.5 在 GDScript
    #   里是 Invalid operands),bool 直接接『如果_否则』的『条件』值引脚。
    branch = _add(logic, "GKNodeBranch", "追击分支", x + 380.0, y)
    _link(logic, timer, "执行", branch, "执行")
    _link(logic, get, "值", branch, "条件")
    # 追击 -> 防重标志还是 0 才切
    was = _add(logic, "GKNodeGetVar", "已在放追逐曲吗", x + 200.0, y - 160.0,
               gk_var_name=flag_var)
    was_cmp = _add(logic, "GKNodeCompare", "还没切过?", x + 380.0, y - 160.0,
                   gk_op="LT", gk_b="0.5")
    was_branch = _add(logic, "GKNodeBranch", "切追逐曲分支",
                      x + 560.0, y - 160.0)
    one = _add(logic, "GKNodeConstFloat", "追击中=1", x + 740.0, y - 320.0,
               gk_value=1.0)
    set_one = _add(logic, "GKNodeSetVar", "记追击中", x + 740.0, y - 160.0,
                   gk_var_name=flag_var)
    chase = _add(logic, "GKNodePlayMusic", "切追逐曲", x + 920.0, y - 160.0,
                 gk_path=chase_file, gk_volume=-4.0)
    _link(logic, branch, "是", was_branch, "执行")
    _link(logic, was, "值", was_cmp, "A")
    _link(logic, was_cmp, "结果", was_branch, "条件")
    _link(logic, was_branch, "是", set_one, "执行")
    _link(logic, one, "值", set_one, "值")
    _link(logic, set_one, "完成", chase, "执行")
    # 退出 -> 标志还挂着才切回(只切一次)
    back = _add(logic, "GKNodeGetVar", "退出后还挂着吗", x + 200.0, y + 160.0,
                gk_var_name=flag_var)
    back_cmp = _add(logic, "GKNodeCompare", "挂着追逐曲?", x + 380.0, y + 160.0,
                    gk_op="GE", gk_b="0.5")
    back_branch = _add(logic, "GKNodeBranch", "切回BGM分支",
                       x + 560.0, y + 160.0)
    zero = _add(logic, "GKNodeConstFloat", "追击中=0", x + 740.0, y + 320.0,
                gk_value=0.0)
    set_zero = _add(logic, "GKNodeSetVar", "记退出追击", x + 740.0, y + 160.0,
                    gk_var_name=flag_var)
    bgm_back = _add(logic, "GKNodePlayMusic", "切回环境BGM",
                    x + 920.0, y + 160.0, gk_path=bgm_file)
    _link(logic, branch, "否", back_branch, "执行")
    _link(logic, back, "值", back_cmp, "A")
    _link(logic, back_cmp, "结果", back_branch, "条件")
    _link(logic, back_branch, "是", set_zero, "执行")
    _link(logic, zero, "值", set_zero, "值")
    _link(logic, set_zero, "完成", bgm_back, "执行")
    return [timer, get, branch, was, was_cmp, was_branch, one, set_one,
            chase, back, back_cmp, back_branch, zero, set_zero, bgm_back]


def _torch_chain(logic, x, y, light_name, hud_widget, dead_text,
                 frame_title="手电 / 电量", low_at=20.0):
    """手电链(第一百八十六轮抽成工厂):F 开关 + 每秒 -1.5 耗电 +
    没电自动灭 + 没电字幕 + 电量 HUD + **低电闪烁**。

    走廊 / 医务室此前是两份**逐字内联复制**(设计文档 17.7 的技术债:
    "第 5 章如果再来一份就是第三份洞"),只差灯名和 HUD 名 —— 抽进来。

    两个统一:
      * 没电提示用『字幕』(3 秒自清)。老走廊版写成提示行,会被 0.25 秒
        的目标刷新立刻覆盖(医务室版注释里踩过,走廊那份一直带着这坑)。
      * 低电闪烁:电量 <= low_at 时进『灯光_闪烁』FAULT(接触不良)模式,
        电量回升(拾电池)> low_at 自动停。start 对同一盏灯去重、stop
        没在闪就是空操作,所以"每物理帧二选一"是最省节点的接法 ——
        不用维护"进入/离开低电"的边沿标志。
    """
    phys_torch = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(手电)",
                      x, y)
    f_key = _add(logic, "GKNodeKey", "按F", x, y - 170.0,
                 gk_key=F_KEY, gk_action=F_ACTION, gk_trigger="JUST")
    f_branch = _add(logic, "GKNodeBranch", "按F了", x + 200.0, y)
    read_on = _add(logic, "GKNodeGetVar", "读手电开", x + 200.0, y - 170.0,
                   gk_var_name=TORCH_ON)
    not_on = _add(logic, "GKNodeLogic", "取反", x + 400.0, y - 170.0,
                  gk_op="NOT")
    write_on = _add(logic, "GKNodeSetVar", "切换手电", x + 400.0, y,
                    gk_var_name=TORCH_ON)
    read_on2 = _add(logic, "GKNodeGetVar", "读手电开(灯)", x + 600.0,
                    y - 170.0, gk_var_name=TORCH_ON)
    read_bat = _add(logic, "GKNodeGetVar", "读电量", x + 600.0, y - 310.0,
                    gk_var_name=BATTERY)
    has_power = _add(logic, "GKNodeCompare", "还有电吗", x + 800.0, y - 310.0,
                     gk_op="GT", gk_b="0")
    lit = _add(logic, "GKNodeLogic", "灯亮吗", x + 800.0, y - 170.0,
               gk_op="AND")
    apply_light = _add(logic, "GKNodeSetLight", "应用手电", x + 1000.0, y,
                       gk_light=light_name)
    lit_branch = _add(logic, "GKNodeBranch", "亮着吗", x + 1200.0, y)
    drain = _add(logic, "GKNodeMath", "每秒耗电", x + 1200.0, y - 170.0,
                 gk_op="MUL", gk_a=-BATTERY_DRAIN)
    spend = _add(logic, "GKNodeAddVar", "扣电", x + 1400.0, y,
                 gk_var_name=BATTERY, gk_clamp=True, gk_min=0.0,
                 gk_max=BATTERY_MAX)
    empty = _add(logic, "GKNodeCompare", "没电了吗", x + 1600.0, y - 170.0,
                 gk_op="LE", gk_b="0")
    empty_branch = _add(logic, "GKNodeBranch", "没电分支", x + 1800.0, y)
    off_const = _add(logic, "GKNodeConstBool", "手电关", x + 1800.0, y - 310.0,
                     gk_value=False)
    force_off = _add(logic, "GKNodeSetVar", "强制关", x + 2000.0, y,
                     gk_var_name=TORCH_ON)
    dead_hint = _add(logic, "GKNodeUiSubtitle", "没电提示", x + 2200.0, y,
                     gk_text=dead_text, gk_seconds=3.0, gk_speed=30.0)
    bat_round = _add(logic, "GKNodeMath", "电量取整", x + 1000.0, y - 310.0,
                     gk_op="ROUND")
    bat_join = _add(logic, "GKNodeTextJoin", "电量文字", x + 1200.0, y - 310.0,
                    gk_a="手电电量:")
    bat_ui = _add(logic, "GKNodeUiSet", "刷新电量", x + 1600.0, y + 150.0,
                  gk_widget=hud_widget, gk_set_text=True)
    # ── 低电闪烁(第一百八十六轮)────────────────────────────────────
    low_cmp = _add(logic, "GKNodeCompare", "低电了吗", x + 1800.0, y - 310.0,
                   gk_op="LE", gk_b=str(low_at))
    low_branch = _add(logic, "GKNodeBranch", "低电分支", x + 2200.0, y + 300.0)
    flick_on = _add(logic, "GKNodeLightFlicker", "低电忽闪(开)",
                    x + 2400.0, y + 150.0, gk_light=light_name,
                    gk_mode="FAULT", gk_speed=2.2, gk_depth=0.85)
    flick_off = _add(logic, "GKNodeLightFlicker", "低电忽闪(停)",
                     x + 2400.0, y + 450.0, gk_light=light_name,
                     gk_stop=True)
    _link(logic, f_key, "结果", f_branch, "条件")
    _link(logic, phys_torch, "执行", f_branch, "执行")
    _link(logic, read_on, "值", not_on, "A")
    _link(logic, not_on, "结果", write_on, "值")
    _link(logic, f_branch, "是", write_on, "执行")
    _link(logic, read_on2, "值", lit, "A")
    _link(logic, read_bat, "值", has_power, "A")
    _link(logic, has_power, "结果", lit, "B")
    _link(logic, lit, "结果", apply_light, "开启")
    _link(logic, phys_torch, "执行", apply_light, "执行")
    _link(logic, apply_light, "完成", lit_branch, "执行")
    _link(logic, lit, "结果", lit_branch, "条件")
    _link(logic, phys_torch, "帧间隔", drain, "B")
    _link(logic, drain, "结果", spend, "增减")
    _link(logic, lit_branch, "是", spend, "执行")
    _link(logic, spend, "完成", empty_branch, "执行")
    _link(logic, spend, "新值", empty, "A")
    _link(logic, empty, "结果", empty_branch, "条件")
    _link(logic, empty_branch, "是", force_off, "执行")
    _link(logic, off_const, "值", force_off, "值")
    _link(logic, force_off, "完成", dead_hint, "执行")
    _link(logic, read_bat, "值", bat_round, "A")
    _link(logic, bat_round, "结果", bat_join, "乙")
    _link(logic, bat_join, "结果", bat_ui, "文字")
    # ⚠ 只接『文字』不接『执行』的话这个界面_设定**永远跑不到** ——
    #   电量 HUD 会一直停在初值(实机截图里电量已经 0 了,HUD 还写着 60)。
    _link(logic, phys_torch, "执行", bat_ui, "执行")
    # 低电判断用的是本帧开头读到的电量(扣电前一帧的值),差一帧无妨。
    _link(logic, phys_torch, "执行", low_branch, "执行")
    _link(logic, read_bat, "值", low_cmp, "A")
    _link(logic, low_cmp, "结果", low_branch, "条件")
    _link(logic, low_branch, "是", flick_on, "执行")
    _link(logic, low_branch, "否", flick_off, "执行")
    _frame(logic, frame_title,
           [phys_torch, f_key, f_branch, read_on, not_on, write_on,
            read_on2, read_bat, has_power, lit, apply_light, lit_branch,
            drain, spend, empty, empty_branch, off_const, force_off,
            dead_hint, bat_round, bat_join, bat_ui, low_cmp, low_branch,
            flick_on, flick_off], (0.20, 0.26, 0.30))


def _archive_zero_vars(logic, before_node, x, y):
    """档案状态行要读的三个计数先归零(载入存档随后会覆盖成真值)。

    状态行的『文本_数字』参数是 **typed float** —— 变量没赋过值就是
    null,helper 当场炸(第 171 轮实测)。每个要读的计数都得有确定的
    float 初值;已声明过 float 的(走廊隐藏笔记数等)再归零一次也无害。
    **必须接在『载入存档』之前** —— 放到载入后面就把真值清回 0 了。
    返回链尾(把『载入存档』接在它后面)。
    """
    last = before_node
    for name in ("纸条数", MED_COUNT, HIDDEN_NOTE_COUNT):
        zero = _add(logic, "GKNodeConstFloat", f"档案零({name})", x, y,
                    gk_value=0.0)
        setter = _add(logic, "GKNodeSetVar", f"{name}=0(档案)", x + 180.0, y,
                      gk_var_name=name)
        _link(logic, zero, "值", setter, "值")
        _link(logic, last, "完成", setter, "执行")
        last = setter
        y -= 140.0
    return last


def _hidden_note_chain(logic, name, note_text, player, x, y):
    """隐藏笔记的收集链:走入 → 防重(标志) → 计数+1 → 字幕(读笔记)。"""
    enter = _add(logic, "GKNodeOnAreaEntered", f"捡到{name}", x, y,
                 gk_area=name, gk_filter=player)
    got_get = _add(logic, "GKNodeGetVar", f"{name}拿过吗", x + 200.0, y,
                   gk_var_name=f"{name}拿到")
    got_branch = _add(logic, "GKNodeBranch", f"{name}防重", x + 380.0, y)
    _link(logic, enter, "执行", got_branch, "执行")
    _link(logic, got_get, "值", got_branch, "条件")
    flag_true = _add(logic, "GKNodeConstBool", f"{name}记拿过",
                     x + 200.0, y - 160.0, gk_value=True)
    set_flag = _add(logic, "GKNodeSetVar", f"记下{name}", x + 380.0,
                    y - 160.0, gk_var_name=f"{name}拿到")
    _link(logic, flag_true, "值", set_flag, "值")
    add_count = _add(logic, "GKNodeAddVar", f"隐藏笔记数+1({name})",
                     x + 560.0, y - 160.0, gk_var_name=HIDDEN_NOTE_COUNT,
                     gk_amount=1.0, gk_clamp=True, gk_min=0.0, gk_max=3.0)
    say = _add(logic, "GKNodeUiSubtitle", f"读到{name}", x + 740.0,
               y - 160.0, gk_text=f"「{note_text}」", gk_seconds=5.0,
               gk_speed=30.0)
    paper = _add(logic, "GKNodePlaySound", f"纸张声({name})", x + 940.0,
                 y - 160.0, gk_path=PAPER_FILE)
    _link(logic, got_branch, "否", set_flag, "执行")
    _link(logic, set_flag, "完成", add_count, "执行")
    _link(logic, add_count, "完成", paper, "执行")
    _link(logic, paper, "完成", say, "执行")

SHEET_NAMES = ("乐谱1", "乐谱2", "乐谱3")
SHEET_FLAGS = ("乐谱1读", "乐谱2读", "乐谱3读")
RECORDER = "录音机"
TAPE = "磁带"
# 磁带的本体(看得见的小盒子)和它的判定体(看不见的大盒子)分开:
# 『磁带』要 hide_mesh(判定体不能露在画面里),真磁带就只是一个装饰件。
TAPE_MESH = "磁带本体"
STAGE = "舞台"
STAGE_LIGHT = "舞台灯"
ALARM_LIGHT = "警报灯"
MUSIC_SAFE_ROOM = "音乐教室安全屋"      # 本关的出生标记(和平板区配套)
MUSIC_SAFE_POS = (-9.5, -7.5)          # 本关入口 / 出生点(Blender x / y)
MUSIC_SAFE_SIZE = (5.0, 4.5)
MUSIC_SAFE_SPAWN_Z = 1.0
MUSIC_CHECKPOINT = "音乐教室_安全屋"
MUSIC_HINT = "音乐教室提示"

# ── 谜题:三张乐谱各给一个音位,第四个音刻在钢琴盖上 ───────────────
# 『乐谱数』满了才算"集齐":集齐那一刻用一条字幕把整串 3-1-4-2 念出来。
SHEET_NOTES = ("3", "1", "4")
TAIL_NOTE = "2"
ANSWER = "".join(SHEET_NOTES) + TAIL_NOTE      # "3142"

# ── 宿主变量(普通 var,探针用 get() 读)────────────────────────────
MUSIC_COUNT = "乐谱数"          # float  0..3
REC_INPUT = "录音机输入"         # String 本轮按键进度(判完清空)
REC_LAST = "录音机记录"          # String 最近一次判定的四音(不清,留证据)
REC_MISS = "录音机错次"          # float  0..99
TAPE_GOT = "磁带"               # float  0 / 1
REC_PLAYING = "录音机放音"       # bool
# 下面两个是本关内部用的:已经按了几个键 / 怪物是不是已经放进来过
REC_DIGITS = "录音机位数"        # float  0..4
MONSTER_IN = "怪物入场"          # bool
DIGIT_KEYS = ("1", "2", "3", "4")

# ── 音效:录音机的"练习曲"和警报音(write_audio 现合成,不引外部文件)──
ETUDE_FILE = "res://audio/etude.wav"
ALARM_FILE = "res://audio/alarm.wav"
# 诱饵要盖过整间音乐教室(怪物可能还在门外);警报只是"近处刺耳"。
LURE_LOUD = 34.0
ALARM_LOUD = 16.0
# ⚠ 视野 8 米(走廊是 9):本关的安全屋离巡逻线 9.5 米 —— 怪物走到舞台那
#   一头时看不见安全屋里的人,「回安全屋」才真的安全。
MUSIC_SIGHT = 8.0
MUSIC_LOST = 10.0
MUSIC_LOOK_STOP = 1.2
# 错满两次:在**玩家身上**发一个很响的噪音 -> 已经在本关巡逻的怪物
# 听见"你在这儿",转去查看。旧写法是"这时才把怪物放进本关",但怪物
# 从开局就得在本关(不然『声音诱饵』只是自说自话),所以惩罚改成"它盯上你"。
NOISE_ON_YOU = 40.0
# ── 目标行(引导,随进度切换)────────────────────────────────────
# 集齐乐谱之后还停在"先找乐谱"就等于没引导:下一步是去录音机按顺序播放。
MUSIC_HINT_TEXTS = (
    f"侧门锁着。先找齐 {len(SHEET_NAMES)} 张乐谱 —— 靠近按 E 读",
    f"乐谱齐了。去{RECORDER}按顺序播放那首练习曲",
    f"{STAGE_LIGHT.replace('灯', '的灯')}灭了 —— 上去拿{TAPE}",
    f"拿到{TAPE}了 —— 推开侧门去医务室",
)
SAFE_VISITED = "进过安全屋"      # bool:第一次进安全屋静默存盘(不弹字幕)

# ── 第 4 章『医务室』(M1b)────────────────────────────────────────
# 流程:标题 -> 教室 -> 走廊 -> 音乐教室 -> **医务室** -> 楼梯间(结局)。
#   * 音乐教室的出口从『去楼梯间』改名 **『去医务室』**;
#   * 医务室自己的出口才叫『去楼梯间』(楼梯间一行都不改)。
# 玩法:进关手电只有 60;两节电池各 +40;电量 0 灯灭(只剩窗光);
#       病历1/2/3 集齐才开侧门;躲藏1/2(病床帘)按 E 进出。
MED = "医务室"
MED_RES = "res://scenes/医务室.tscn"
MED_LEVEL_TREE = "医务室逻辑"
MED_PLAYER = "医务室玩家"
MED_ENEMY = "医务室怪物"
MED_AI_TREE_NAME = "医务室AI"
MED_SCARE_AREA = "医务室惊吓区"
MED_PATROL_A = "医务室巡逻点甲"
MED_PATROL_B = "医务室巡逻点乙"
MED_TORCH = "医务室手电筒"
MED_HIDE_CAM = "医务室躲藏机位"
MED_SAFE_AREA = "医务室安全屋区域"
MED_SAFE_ROOM = "医务室安全屋"
MED_SAFE_POS = (-8.0, -6.0)
MED_SAFE_SIZE = (5.0, 4.0)
MED_SAFE_SPAWN_Z = 1.0
MED_CHECKPOINT = "医务室_安全屋"
MED_HINT = "医务室提示"
# 音乐教室里的出口(原『去楼梯间』)。⚠ 名字一改,write_physics 的
# triggers 必须同步 —— 漏了出口板会被判成 StaticBody3D(踩过)。
TO_MED = "去医务室"

MED_SHEETS = ("病历1", "病历2", "病历3")
MED_SHEET_FLAGS = ("病历1读", "病历2读", "病历3读")
MED_BATTERIES = ("电池1", "电池2")
MED_HIDES = ("躲藏1", "躲藏2")

# ── 隐藏笔记(第一百六十九轮:可读文本 6 → 9 条)───────────────────
# 设计文档三节的"可选内容":每章一条藏起来的笔记,不进真结局计数,
# 纯背景故事。**走入即捡**(和保险丝同款,不做 E 交互),字幕读一遍,
# 计数『隐藏笔记数』—— 时长账里"可读文本"从 6 条推到 9 条。
HIDDEN_NOTE_COUNT = "隐藏笔记数"
# (场景名, 判定体名, Blender x / y, 笔记正文)。位置都避开巡逻线、
# 立柱、其他判定区(第 168 轮:判定体和实体重叠 = 永远触发不了)。
HIDDEN_NOTES = (
    (CORRIDOR, "走廊笔记", (-4.5, 9.2),
     "值日生的字迹:「第三周的巡夜。走廊尽头的灯又灭了,换保险丝也没用"
     "—— 不是灯的问题,是**有什么在吸电**。」"),
    (MUSIC, "音乐教室笔记", (8.0, 6.0),
     "谱架夹层里的便条:「她总在午休时弹同一首练习曲。今天琴房没有人,"
     "琴却在响。」"),
    (MED, "医务室笔记", (-8.0, -3.0),
     "值班记录的背面:「9 月 28 日凌晨两点,三号床自己坐了起来,眼睛睁着"
     "—— 监护仪全程没有报警。」"),
)
HIDDEN_NOTE_NAMES = {scene: name for scene, name, _pos, _text in HIDDEN_NOTES}
MED_HIDE_SPOTS = ((-6.5, 3.0), (2.0, 4.0))      # 两张病床(Blender x/y)
MED_HIDE_FRONT = ((-6.5, 0.9, 1.0), (2.0, 1.9, 1.0))   # 从帘子出来的落点
MED_HIDE_ANCHOR = "医务室躲藏锚点"
# 躲进去之后**身体挪到这儿**:视线是纯距离判定(codegen/ai.py 的
# distance <= sight,没有遮挡射线),所以"躲进帘子"要真的看不见,只能让
# 身体离开视野 —— 相机切『医务室躲藏机位』,人看起来还在帘子里。
MED_HIDE_ANCHOR_POS = (0.0, -40.0)
# ⚠ 躲藏机位要站在**帘子里面**(帘子在 y=1.3,相机放 y=1.9)朝房间看。
#   放帘子外面(0.6)时镜头怼在帘子背面 —— 实机截图是一整屏灰。
MED_HIDE_CAM_POS = (-6.5, 1.9, 1.70)
MED_SHEET_SPOTS = ((-8.0, -3.0), (0.0, -6.0), (5.5, 2.0))
MED_BATTERY_SPOTS = ((8.0, 3.0), (-4.0, 6.0))
MED_PATROL_POS = ((-5.0, 3.0), (5.0, -5.0))

MED_COUNT = "病历数"        # float 0..3
MED_BATTERY_COUNT = "电池数"  # float 0..2
HIDING = "躲藏中"            # bool
HIDE_SPOT_VAR = "躲藏点"     # String:记着是从哪个帘子躲进去的
BATTERY_START_MED = 60.0    # 进关电量(第 4 章才这样,前几关是 100)
BATTERY_PICKUP = 40.0       # 一节电池补多少
MED_SIGHT = 6.0             # 视线比走廊(9)/音乐教室(8)都短
MED_LOST = 8.0
MED_LOOK_STOP = 1.0
MED_DOOR_GOAL = 3
MED_HINT_TEXTS = (
    f"摸黑找齐 {MED_DOOR_GOAL} 页病历 —— 靠近按 E 读(电池会补手电)",
    f"病历齐了 —— 侧门在东北角,按 E 推开",
)
MED_RECORDS = (
    "10 月 3 日。3 床夜里一直说听见天花板里有歌声,体温 36.2,瞳孔对光迟钝。",
    "10 月 5 日。熄灯后去二楼取镇痛剂,走廊的灯又灭了。楼上有人走动。",
    "10 月 9 日。侧门锁是从里面插上的。别回答敲门声,也别掀帘子。",
)


# ── 第 5 章『楼梯间』(M2a:断电追逐 + 天台结局)─────────────────────
# 流程:... -> 医务室 -> **楼梯间(断电 + 追上来)** -> 天台(双结局)
#
# 为什么这一章要重做:以前楼梯间只有"一块 5×5 判定板 + 按 1/2 选结局",
# 玩家从医务室推门进来,走 8 米就撞上结局 —— 第 5 章根本不存在。
# 现在它是**一段真的爬楼**:进门厅 → 三折楼梯(每层一个平台)→ 天台门,
# 进门 6 秒后全楼断电,追击者开始往上追,只能一路跑到天台。
#
# ⚠ 名字契约(门禁探针按名字找,改名 = 探针失效):
#   场景 / .tscn『楼梯间』,关卡图『楼梯间关卡』,角色『楼梯间玩家』,
#   追兵『楼梯间追击者』,AI 图『楼梯间AI』,
#   区域『楼梯间一层/二层/三层』『楼梯间天台』『楼梯间安全屋区域』。
STAIRS_ENEMY = "楼梯间追击者"
STAIRS_AI_TREE_NAME = "楼梯间AI"
STAIRS_PATROL_A = "楼梯间巡逻点甲"
STAIRS_PATROL_B = "楼梯间巡逻点乙"
# 三层 + 天台:进一个区域就把目标行改一次("还剩 2 层")。
STAIRS_FLOOR_AREAS = ("楼梯间一层", "楼梯间二层", "楼梯间三层")
ROOF_AREA = "楼梯间天台"
STAIRS_SAFE_AREA = "楼梯间安全屋区域"
STAIRS_SAFE_ROOM = "楼梯间安全屋"
STAIRS_SAFE_POS = (0.0, -9.5)          # 门厅西侧
STAIRS_SAFE_SIZE = (5.0, 4.0)
STAIRS_CHECKPOINT = "楼梯间_安全屋"
# 三盏应急灯:断电时**一起关掉**(『灯光_设置』×3,带 0.35s 渐变)。
STAIRS_LIGHTS = ("楼梯间应急灯1", "楼梯间应急灯2", "楼梯间应急灯3")
BLACKOUT_DELAY = 6.0                   # 进门几秒后断电
# 追兵比前几关快一档:走廊/音乐教室/医务室都是 CHASE_SPEED*0.85(=4.08),
# 这一关写 **6.0**(= 玩家走路速度 BASE_SPEED;玩家跑起来是 9.6)。
# 数值写死而不是 CHASE_SPEED*1.25 —— 常量区在 CHASE_SPEED 之前,引用会
# NameError(踩过)。改 CHASE_SPEED 时记得回来对一眼这个数。
STAIRS_CHASE_SPEED = 6.0
STAIRS_SIGHT = 12.0
STAIRS_LOST = 16.0
# 真结局条件:纸条 3 + 病历 3 都齐(= "看过全部档案的人才知道她是谁")。
# 用**加法**判,省掉一个布尔节点:纸条数 + 病历数 >= 6。
TRUE_END_GOAL = 6
STAIRS_HINT_TEXTS = (
    "往上跑 —— 天台门在三楼尽头",
    "还有 2 层",
    "还有 1 层",
    "天台门就在前面",
)


# ── 系统分工(第一百五十八轮)────────────────────────────────────
#   * 角色图(**一张**,三关共用):只放输入 / 移动 / 跳跃 / 视角 / 机位。
#     三关的机位名必须一致,共用图才能复用。
#   * 关卡图(**每关一张**):HUD / BGM / 暗角 / 运镜 / 触发区 / 存档 / 切关 /
#     失败处理 / AI 启动。挂在场景根(Node3D),角色没了它也在。
#   * AI 图(**一张**):怪物巡逻 / 追击 / 去查看;关卡图用『AI_输出』读状态。
# 这样改一关的剧情不用碰角色图,改手感也不用碰三关的关卡图。
ROLE_TREE_NAME = "玩家逻辑(公共)"
ROOM_LEVEL_TREE = "教室关卡"
CORRIDOR_LEVEL_TREE = "走廊关卡"
STAIRS_LEVEL_TREE = "楼梯间关卡"
TITLE_LEVEL_TREE = "标题关卡"

SIDE_CAM = "侧视机位"
TOP_CAM = "俯瞰机位"
TITLE_CAM = "标题机位"

# 开场运镜:一条 3D 曲线当轨道 + 一台门口特写机位。
# 相机从『侧视机位』起飞,鼓个弯越过课桌,**俯冲**到门口特写;
# 特写停 1.5 秒,再切回第三人称。
ROOM_TRACK = "教室运镜轨道"
ROOM_CLOSE = "门口特写"
# ── 第一百五十九轮:落点从"门口"改成"玩家正面"───────────────────
# 旧落点 (0,-12,3.2) 对着门口,而玩家出生在 (-8,-8):落位那 1.5 秒里
# 画面里根本没有玩家(实机截图 02/15 是空教室)。新落点在玩家正前方
# 6 米、略高,俯角 18° —— 玩家始终在画面中央,门口在右侧边缘。
#
# ── 第一百六十轮(M0.5):落点再**沿同一条视线往后退** 5.1 -> 7.6 米 ──
# 用户看图:"开场推近太近,玩家盒子占屏高约 50%"。实测 02_room_opening
# 里盒子正好 ~370/720 px = 51%。
# 屏幕占比只跟"距离 x 焦距"有关:32mm 在 16:9 下垂直视野 35.2°,
# 1.6 米高的盒子要占 30% 屏高,需要的竖直取景高度 = 1.6/0.30 = 5.33 米
# -> 距离 = 5.33 / (2*tan(17.6°)) = 8.4 …… 这里取 7.6 米(约 33%),
# 因为镜头还是斜的(取景不是正对盒子),留一点余量别又贴回去。
# ⚠ 只沿**原来那条视线**往后退:相机到玩家的方向 (-0.588, 0.706, -0.412)
#   一个字都没变,所以『门口特写』的旋转 (65.8°, 0, 39.8°) 原样有效。
# 实测(02_room_opening,blob 高度占比):5.1 米 = 63% -> 7.6 米 = 42%
#   -> 9.6 米 ≈ 33%。盒子的**剪影**比正面高(俯视时还看得到顶面),
#   所以按剪影量出来的比例会比"正面 1.6 米"的三角估算大一档。
ROOM_CLOSE_POS = (-2.36, -14.78, 4.86)
# ⚠ 轨道**不能抬高**:相机沿曲线飞时朝向是跟着切线走的,升到 11 米
#   那一段等于仰头看天 —— 实机截图就是一片空渐变(旧值 12 米/9 米也会
#   在顶点附近拍空)。所以整条轨道压到 3~5.5 米:贴着课桌上方滑过去,
#   每一帧都朝着教室内部(玩家在画面里)。
# ⚠ 曲线相机的朝向**跟着切线走**(不是看向某个目标,节点也没有"看向"参数),
#   而教室**没有墙**:切线一旦偏向侧面,画面里就是空白背景 —— 上一版
#   升到 11 米拍空、改成横移后又拍空,都是这个原因。
# 所以整条轨道是**朝玩家的一条直线推进**:每一点的切线都指着玩家,
# 相机从侧视机位一路推近到玩家正面,每一帧都看得见人。
# 中间两个点也放在**同一条视线**上(相机 = 玩家 - 距离 x 方向):
#   10.6 米 -> (-1.77,-15.48,5.27);9.0 米 -> (-2.71,-14.35,4.61)。
# 曲线是 AUTO 手柄的贝塞尔,末段切线 = 最后两点的连线 —— 只有这三点共线,
# 落位那一帧才仍然"正对玩家"(否则盒子飞出画面边缘)。
ROOM_TRACK_POINTS = [
    (0.0, -16.0, 5.5),     # 贴着『侧视机位』(起点)
    (-1.41, -15.91, 5.51),  # 朝玩家推进(11.2 米)
    ROOM_CLOSE_POS,        # 落到玩家正面 9.6 米(盒子占屏高约 33%)
]
OPENING_BLEND = 1.2        # 开场运镜时长(秒;长镜头空帧多,且拖开局)
# ⚠ 回第三人称用**硬切**(0 秒):弹簧臂在角色图里、每帧都在摆侧视机位,
#   关卡图这边的过渡会和它抢同一台相机(跨脚本,谁也看不见对方的过渡计时)。
#   运镜本身仍是 2.5 秒缓出,只是收尾那一刀干净利落。
RETURN_BLEND = 0.0
# 『相机_切换』的『完成』是"开始过渡了"就返回(过渡异步跑),所以要等够时长。
OPENING_HOLD = OPENING_BLEND + 1.5

NOTE_GOAL = 3
CATCH_DISTANCE = 1.9       # 怪物离玩家多近算被抓住(米)
SIGHT = 9.0
LOST_RANGE = 11.0
PATROL_SPEED = 3.0
CHASE_SPEED = 4.8
CHASE_STOP = 1.6
LOOK_SPEED = 4.0
LOOK_STOP = 1.0

NOISE_KEY = "K"
NOISE_ACTION = "key_k"
# 响度要盖过出生点(实测玩家出生点离怪 14.4 米;怪巡逻到另一头时 ~20 米),
# 同时仍大于视野 9 —— "看不见但听得见"的窗口还在。
NOISE_LOUD = 22.0
NOISE_INTERVAL = 0.25
EAR_MEMORY = 1.2

TENSION_NEAR = 3.0
TENSION_FAR = 14.0

BGM_FILE = "res://audio/ambient.wav"
HEART_FILE = "res://audio/heartbeat.wav"
STING_FILE = "res://audio/sting.wav"
KNOCK_FILE = "res://audio/knock.wav"
# ── 第一百七十九轮:事件音效 + 追逐曲(设计卡十三节"声音"第二批)──────
PAPER_FILE = "res://audio/paper.wav"      # 纸张:捡笔记/病历/乐谱
SWITCH_FILE = "res://audio/switch.wav"    # 保险丝"咔哒"(插进配电箱)
DOOR_FILE = "res://audio/door.wav"        # 门铰链"吱呀"(放行开门)
WHISPER_FILE = "res://audio/whisper.wav"  # 耳语:受惊后的余韵
CHASE_FILE = "res://audio/chase.wav"      # 追逐曲:被追时切换

# ==========================================================================
# M0 纵切片:蹲走 / 跑动 / E 交互 / 笔记日志 / 安全屋检查点 / 手电电量
# ==========================================================================
# 玩家名字(探针按名找,别改)
ROOM_PLAYER = "教室玩家"
CORRIDOR_PLAYER = "走廊玩家"
STAIRS_PLAYER = "楼梯间玩家"

# ── 安全屋(走廊)─────────────────────────────────────────────────
# 『安全屋』是空物体(Node3D)出生标记,『安全屋区域』是 Area3D 触发体。
# Blender 的 (x, y) -> Godot 的 (x, -y),见 _godot_xy()。
SAFE_ROOM = "安全屋"
# ⚠ M1a:这一关的安全屋改叫『走廊安全屋区域』,把『安全屋区域』这个名字
#   让给音乐教室。Blender 的**物体名全局唯一**(两个场景也不能重名,第二个
#   会被改成 .001,导出的节点名跟着变),而名字契约要求音乐教室那一块
#   Area3D 就叫『安全屋区域』(Godot 侧各场景独立,重名不打架)。
SAFE_ROOM_AREA = "走廊安全屋区域"
MUSIC_SAFE_AREA = "安全屋区域"
SAFE_ROOM_POS = (-12.0, -4.0)          # Blender x / y
SAFE_ROOM_SIZE = (7.0, 6.0)
SAFE_ROOM_SPAWN_Z = 1.0                # 传送回安全屋时的离地高度(米)

# ── 移动:走 / 蹲 / 跑 ───────────────────────────────────────────
BASE_SPEED = 6.0                       # 角色预设『移动_按方向』的默认速度
CROUCH_SCALE = 0.5
RUN_SCALE = 1.8
SPEED_VAR = "移动速度"                    # 角色脚本里的速度变量(接在速度引脚上)
CROUCH_SPEED = BASE_SPEED * CROUCH_SCALE
RUN_SPEED = BASE_SPEED * RUN_SCALE
CROUCH_CAMERA_DROP = 0.62              # 蹲下时相机压低多少(米)
CROUCH_BODY_HEIGHT = 1.05              # 蹲下时碰撞胶囊高度(站立 1.60)
STAND_BODY_HEIGHT = 1.60
RUN_NOISE_LOUD = 6.0                   # 跑动噪音半径(米)
RUN_NOISE_INTERVAL = 0.45
STEP_NOISE_TYPE = "脚步"               # 和 AI 图『听见动静』的听觉匹配

# ── 按键(Godot 动作名 / Blender 键名)──────────────────────────────
E_KEY, E_ACTION = "E", "key_e"
J_KEY, J_ACTION = "J", "key_j"
ESC_KEY, ESC_ACTION = "ESC", "key_esc"
F_KEY, F_ACTION = "F", "key_f"
CTRL_KEY, CTRL_ACTION = "LEFT_CTRL", "key_left_ctrl"
SHIFT_KEY, SHIFT_ACTION = "LEFT_SHIFT", "key_left_shift"

# ── 手电(从 demo_story 那条链移植过来)──────────────────────────
BATTERY = "手电电量"
BATTERY_MAX = 100.0
BATTERY_DRAIN = 1.5                    # 每秒耗电
TORCH_ON = "手电开"

# ── 角色底色(三关一致)───────────────────────────────────────────
# 0.85 的近白色在走廊里会被"贴着眼睛"的手电顶过 1,盒子变成一块没有面与
# 面的白板(实机截图 07_corridor:整块 224/255 + 泛光晕)。
# 实测三轮(同一条射线上的手电,量盒子的灰度跨度):
#   albedo 0.85 + 手电 4200W -> 169..224,顶面和正面几乎一样(白板)
#   albedo 0.70 + 手电  800W -> 143..169(还是偏平:环境光和月光把顶面抬住了)
#   albedo 0.45 + 手电  550W -> 月光照顶面只占一小半,手电照正面占主导,
#                              顶面明显暗一档 = 看得出"这是个立体的盒子"
# 0.45 仍然比地板(0.10)/墙(0.15)亮三倍,人一眼看得见。
# 第 162 轮:从『浅灰板』改成深蓝制服 —— 人形零件的颜色见 PLAYER_PALETTE
PLAYER_COLOR = (0.135, 0.150, 0.225, 1.0)

# ── 探针要读的关卡宿主变量(普通 var)─────────────────────────────
CHECKPOINT = "检查点"
INTERACT_TARGET = "交互目标"
LOG_OPEN = "日志打开"
CHECKPOINT_CORRIDOR = "走廊_安全屋"

# ── 笔记日志:三张纸条的标题 / 正文 ──────────────────────────────
NOTE_TITLES = ("值日日志", "医务室记录", "深夜广播稿")
NOTE_BODIES = (
    "10 月 3 日。走廊的灯又灭了。我听见楼上有脚步声，可上面没有人。",
    "连续三晚有学生说听见天花板里有歌声。体温正常，瞳孔对光迟钝。",
    "熄灯后请留在教室。楼梯间的门不会开。不要回答敲门声。",
)
LOG_PANEL = "日志面板"
INTERACT_TIP = "交互提示"

# 蹲走脚本:Blender 文本块名字(由『脚本_加载』挂进走廊,导出到
# res://scripts/custom/)。改生成脚本就能复现,不用手改 .blend / 产物。
CROUCH_SCRIPT_NAME = "GK蹲走相机"
UI_POLISH_SCRIPT_NAME = "GK界面修补"


def log(message):
    print(f"[恐怖示例] {message}")


# ==========================================================================
# 场景与物体(每个物体链接进指定的 Blender 场景)
# ==========================================================================
def clear_all():
    """清空所有数据块,**不能用 `read_factory_settings`**(会把插件一起卸载)。

    清完把唯一剩下的场景命名为『教室』(另外几关在后面新建)。
    """
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(group)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    for curve in list(bpy.data.curves):
        bpy.data.curves.remove(curve)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for scene in list(bpy.data.scenes)[1:]:
        bpy.data.scenes.remove(scene)
    scene = bpy.context.scene
    scene.name = ROOM
    return scene


def make_box(name, size, location, color=None, scene=None, glow=None):
    """一个长方体(地面 / 墙 / 纸条 / 触发板 / 灯泡都用它)。

    用底层 API 建 + 直接链接进目标场景 —— **不用 `bpy.ops`**(那个只会加进
    当前上下文场景,造不出第二 / 第三关)。

    glow = (颜色, 强度):给材质加自发光(灯泡 / 灯芯"自己在发光")。
    Blender 4.x 的 Principled 输入名是 `Emission Color` / `Emission Strength`。
    """
    sx, sy, sz = [v * 0.5 for v in size]
    verts = [(-sx, -sy, -sz), (sx, -sy, -sz), (sx, sy, -sz), (-sx, sy, -sz),
             (-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz)]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5),
             (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh = bpy.data.meshes.new(f"{name}网格")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    (scene or bpy.context.scene).collection.objects.link(obj)
    obj.location = location
    if color:
        material = bpy.data.materials.new(f"{name}材质")
        material.use_nodes = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is not None:
            principled.inputs["Base Color"].default_value = color
            if glow:
                try:
                    principled.inputs["Emission Color"].default_value = glow[0]
                    principled.inputs["Emission Strength"].default_value = glow[1]
                except Exception:                         # noqa: BLE001
                    pass
        obj.data.materials.append(material)
    return obj


def make_marker(name, location, scene, size=1.0):
    """空物体(Node3D):导出成 Godot 的空节点,当出生点 / 安全屋标记用。

    为什么不用 make_box:那会多出一块看得见的方块,还把碰撞也带进来;
    标记只该是"一个位置"。
    """
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = "ARROWS"
    obj.empty_display_size = size
    obj.location = location
    scene.collection.objects.link(obj)
    return obj


def _godot_xy(blender_xy):
    """Blender 的 (x, y) -> Godot 的 (x, -y)。"""
    return (float(blender_xy[0]), -float(blender_xy[1]))


def make_camera(name, location, rotation, scene, lens=50.0):
    data = bpy.data.cameras.new(f"{name}数据")
    data.lens = lens
    camera = bpy.data.objects.new(name, data)
    scene.collection.objects.link(camera)
    camera.location = location
    camera.rotation_euler = rotation
    return camera


def reuse_camera(scene, name, location, rotation, lens=50.0):
    """机位名在三关里必须**逐字一样**,共用角色图才找得到它。

    ⚠ Blender 的物体名是**全局唯一**的:每关各建一台『侧视机位』会变成
      『侧视机位.001 / .002』,导出的节点名跟着变,角色图里的弹簧臂和
      C/V 切换就全部找不到相机(实测:主相机被写成『侧视机位.002』)。
      所以这里复用**同一个相机物体**,把它链接进每一关 —— 名字不变,
      位置用三关都合适的统一机位。
    """
    camera = bpy.data.objects.get(name)
    if camera is None:
        return make_camera(name, location, rotation, scene, lens)
    if name not in scene.objects:
        scene.collection.objects.link(camera)
    camera.location = location
    camera.rotation_euler = rotation
    # FOV 也要跟着改:环境脚本会把 Blender 相机焦距写进 Godot 的 `fov`。
    # 默认 50mm 是 22.9° 的长焦 —— 第三人称会把角色怼满屏幕(实测)。
    camera.data.lens = lens
    return camera


def make_curve(name, points, scene):
    """一条贝塞尔曲线(自动手柄)—— 当运镜轨道用(『相机_切换』的 CURVE 模式)。

    导出时会被采样成 Godot 的 `Curve3D` 资源(curves/*.tres),相机沿它飞。
    """
    data = bpy.data.curves.new(f"{name}数据", type="CURVE")
    data.dimensions = "3D"
    spline = data.splines.new("BEZIER")
    spline.bezier_points.add(len(points) - 1)
    for handle, co in zip(spline.bezier_points, points):
        handle.co = co
        handle.handle_left_type = "AUTO"
        handle.handle_right_type = "AUTO"
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    return obj


def make_light(name, kind, location, energy, color, scene,
               rotation=(0.0, 0.0, 0.0), cutoff=14.0, spot_size=None,
               shadow=False):
    data = bpy.data.lights.new(f"{name}数据", type=kind)
    data.energy = energy
    data.color = color
    data.use_shadow = shadow
    if kind == "SPOT" and spot_size is not None:
        data.spot_size = spot_size
    if kind in ("POINT", "SPOT"):
        data.shadow_soft_size = cutoff * 0.15
    light = bpy.data.objects.new(name, data)
    scene.collection.objects.link(light)
    light.location = location
    light.rotation_euler = rotation
    return light


# ==========================================================================
# 场景美术:墙 / 窗 / 吊灯 / 壁灯(从 make_story_demo.py 移植)
# ==========================================================================
# 用户原话:"示例里还有一个恐怖闯关示例,可以根据它的游戏设计来进行场景
# 等等的设计"。所以这一整段是**照抄** `scripts/make_story_demo.py` 里已经
# 调好的手法(同一个插件、同一个仓库),不是从零调 —— 连注释里的数值
# 都一起搬过来当"别再试第二次"的清单。
#
# ⚠ 这一段的物体全是**装饰**:一律走 `decor()` 关掉碰撞。原因见 decor 的
#   说明(长条形会被"自动"猜成胶囊,把走廊堵死)。
WALL_THICK = 0.40
WALL_HEIGHT = 4.80
WINDOW_SILL = 1.05          # 窗台高度
WINDOW_HEAD = 3.55          # 窗顶高度

# ── 血换来的数值(照抄 make_story_demo.py 的注释)────────────────────
#   * 吊灯主光用 **70° 聚光**:点光会平方反比把 1.5 米外的天花板照过曝;
#     118° 会在两面墙上切出硬斜线;70° 半径只到 2.4 米,碰不到墙。
#   * 上照能量 **<= 0.12 倍**:灯离天花板只有 1 米,平方反比会放大十倍。
#   * 灯罩 / 灯泡 `render_no_shadow = True`:灯罩投影会被软阴影过滤切成
#     一圈圈同心圆。
#   * 灯罩用**深色金属色**、灯泡挪到罩口下面 **>= 0.3 米**:浅色罩贴点光
#     就是一团白。
#   * 壁灯**离墙 >= 1.1 米**:贴墙 0.4 米时墙面照度按平方反比冲到 18 倍。
#   * 雾浓度要么 >= 0.02 要么关掉(低浓度 froxel + 时域重投影会有
#     同心圆 / 流光伪影)—— 本文件四关的 ENV_* 预设都已 >= 0.02。
PENDANT_SPOT = 70.0         # 吊灯主光锥角(整角,度)
PENDANT_UP_SCALE = 0.10     # 上照能量倍数(<= 0.12)
PENDANT_BULB_DROP = 0.34    # 灯泡在罩口下面多少米(>= 0.3)
SCONCE_LIGHT_OFFSET = 1.15  # 壁灯灯离墙多少米(>= 1.1)


def decor(obj):
    """装饰件:不生成碰撞体。

    `write_physics` 会把场景里每个网格都设成 StaticBody3D,而导出侧对
    "自动形状"的处理是**按包围盒猜**:一根 8 米长的窗框会被判成半径 4 米的
    胶囊把走廊堵死。装饰件压根不该参与碰撞,所以这里直接选『无』。
    """
    physics = getattr(obj, "gk_physics", None)
    if physics is not None:
        try:
            physics.collision_shape = "none"
        except Exception:                                 # noqa: BLE001
            pass
    return obj


def solid_box(obj):
    """结构件:显式用盒子碰撞(别让"自动"把长条猜成胶囊)。"""
    physics = getattr(obj, "gk_physics", None)
    if physics is not None:
        try:
            physics.collision_shape = "box"
        except Exception:                                 # noqa: BLE001
            pass
    return obj


def no_shadow(obj):
    """这个物体**不投阴影**(灯罩 / 灯泡 / 灯芯这种小零件)。

    灯罩是个圆台,灯在它下面 —— 罩子投在天花板上的影子本来只有一个圆环,
    但软阴影过滤会在这么大的投影范围上把它切成一圈圈同心圆(实机截图里
    像水波纹)。不投阴影之后天花板是干净的一片暗,别的物体照样投影。
    """
    physics = getattr(obj, "gk_physics", None)
    if physics is not None:
        try:
            physics.render_no_shadow = True
        except Exception:                                 # noqa: BLE001
            pass
    return obj


def make_cone(name, radius_bottom, radius_top, depth, location, color,
              scene=None, glow=None, segments=24):
    """圆台(灯罩专用):上小下大、**底面开口**。

    方块灯罩在实机里读起来是"天花板下面挂了个白盒子";真实吊灯的罩子是
    上窄下宽的伞形。开口朝下,光才能从罩子里漏出去。
    """
    verts, faces = [], []
    for index in range(segments):
        angle = 2.0 * math.pi * index / segments
        verts.append((math.cos(angle) * radius_bottom,
                      math.sin(angle) * radius_bottom, -depth / 2.0))
    for index in range(segments):
        angle = 2.0 * math.pi * index / segments
        verts.append((math.cos(angle) * radius_top,
                      math.sin(angle) * radius_top, depth / 2.0))
    for index in range(segments):
        nxt = (index + 1) % segments
        faces.append((index, nxt, segments + nxt, segments + index))
    faces.append(tuple(range(segments * 2 - 1, segments - 1, -1)))   # 顶盖
    mesh = bpy.data.meshes.new(f"{name}网格")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    (scene or bpy.context.scene).collection.objects.link(obj)
    obj.location = location
    if color:
        material = bpy.data.materials.new(f"{name}材质")
        material.use_nodes = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is not None:
            principled.inputs["Base Color"].default_value = color
            if glow:
                try:
                    principled.inputs["Emission Color"].default_value = glow[0]
                    principled.inputs["Emission Strength"].default_value = glow[1]
                except Exception:                         # noqa: BLE001
                    pass
        obj.data.materials.append(material)
    return obj


def make_aimed_light(name, location, target, energy, color, scene,
                     spot_size=60.0, cutoff=18.0, shadow=False, volume=1.0,
                     softness=0.07):
    """从 `location` 照向 `target` 的聚光灯(窗光 / 壁灯 / 上照用)。

    自动算朝向 —— 手动填三个欧拉角太难对:一盏"从窗外斜打进来"的灯,
    差一点光斑就跑到墙上了。
    """
    light = make_light(name, "SPOT", location, energy, color, scene,
                       cutoff=cutoff, spot_size=math.radians(spot_size),
                       shadow=shadow)
    direction = Vector(target) - Vector(location)
    if direction.length > 1e-4:
        light.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    light.data.shadow_soft_size = softness
    # ⚠ 射程必须显式写进 Blender 的『自定义距离』:导出侧读的是
    #   `light.cutoff_distance`(默认 40 米),而 Godot 的点 / 聚光按射程做
    #   衰减 —— 40 米的射程等于"10 米内几乎不衰减",一盏灯就把整间屋子
    #   铺平(手电筒 4200W 把玩家打成白板,根因就是这个)。
    light.data.use_custom_distance = True
    light.data.cutoff_distance = cutoff
    if hasattr(light.data, "volume_factor"):
        light.data.volume_factor = volume
    return light


def make_wall_pieces(prefix, axis, wall_pos, span, thickness, height, scene,
                     openings, color):
    """带窗洞的墙:上下两条墙带 + 窗间墙柱(窗洞处留空)。

    `axis="x"` = 墙沿 X 展开(法线朝 ±Y);`axis="y"` = 沿 Y 展开。
    `openings` = [(中心, 宽), ...]。留空的洞真的能看到外面(没有玻璃)。
    """
    start, end = span
    length = end - start
    sill, head = WINDOW_SILL, WINDOW_HEAD
    pieces = []
    for tag, z0, z1 in (("下墙带", 0.0, sill), ("上墙带", head, height)):
        size = (length, thickness, z1 - z0) if axis == "x" \
            else (thickness, length, z1 - z0)
        loc = (((start + end) / 2), wall_pos, (z0 + z1) / 2) if axis == "x" \
            else (wall_pos, (start + end) / 2, (z0 + z1) / 2)
        pieces.append(make_box(f"{prefix}{tag}", size, loc, color, scene=scene))
    edges = [start]
    for center, width in sorted(openings):
        edges += [center - width / 2, center + width / 2]
    edges.append(end)
    index = 0
    for i in range(0, len(edges) - 1, 2):
        left, right = edges[i], edges[i + 1]
        if right - left <= 0.05:
            continue
        index += 1
        size = (right - left, thickness, head - sill) if axis == "x" \
            else (thickness, right - left, head - sill)
        loc = ((left + right) / 2, wall_pos, (sill + head) / 2) if axis == "x" \
            else (wall_pos, (left + right) / 2, (sill + head) / 2)
        pieces.append(make_box(f"{prefix}墙柱{index}", size, loc, color,
                               scene=scene))
    for piece in pieces:
        solid_box(piece)
    return pieces


def make_window(prefix, center, wall_pos, width, scene, axis="x",
                color=(0.20, 0.22, 0.26, 1.0)):
    """窗框:四边框 + 一根竖梃 + 一根横档(纯装饰,不挡人)。"""
    sill, head = WINDOW_SILL, WINDOW_HEAD
    bar, depth = 0.14, 0.30
    height = head - sill
    mid = (sill + head) / 2
    parts = []
    if axis == "x":
        parts.append(make_box(f"{prefix}窗框下", (width, depth, bar),
                              (center, wall_pos, sill), color, scene=scene))
        parts.append(make_box(f"{prefix}窗框上", (width, depth, bar),
                              (center, wall_pos, head), color, scene=scene))
        for side in (-1, 1):
            parts.append(make_box(
                f"{prefix}窗框{'左右'[side > 0]}",
                (bar, depth, height),
                (center + side * (width / 2 - bar / 2), wall_pos, mid),
                color, scene=scene))
        parts.append(make_box(f"{prefix}窗棂竖", (0.10, depth, height),
                              (center, wall_pos, mid), color, scene=scene))
        parts.append(make_box(f"{prefix}窗棂横", (width, depth, 0.10),
                              (center, wall_pos, mid + 0.35), color,
                              scene=scene))
    else:
        parts.append(make_box(f"{prefix}窗框下", (depth, width, bar),
                              (wall_pos, center, sill), color, scene=scene))
        parts.append(make_box(f"{prefix}窗框上", (depth, width, bar),
                              (wall_pos, center, head), color, scene=scene))
        for side in (-1, 1):
            parts.append(make_box(
                f"{prefix}窗框{'左右'[side > 0]}",
                (depth, bar, height),
                (wall_pos, center + side * (width / 2 - bar / 2), mid),
                color, scene=scene))
        parts.append(make_box(f"{prefix}窗棂竖", (depth, 0.10, height),
                              (wall_pos, center, mid), color, scene=scene))
        parts.append(make_box(f"{prefix}窗棂横", (depth, width, 0.10),
                              (wall_pos, center, mid + 0.35), color,
                              scene=scene))
    for part in parts:
        decor(part)
    return parts


def make_trim(prefix, axis, wall_pos, span, facing, scene, color):
    """踢脚线 + 腰线(墙和地面才"接得上")。

    只有墙没有这条线时,墙像是"插"在地板上的;一条 18 厘米高的踢脚线
    加上 1 米高的腰线,墙面立刻有进深。两条都贴在**墙的内侧面上**。
    """
    start, end = span
    length = end - start
    inner = wall_pos + facing * WALL_THICK / 2.0   # 墙面(朝房间那一侧)
    pieces = []
    for tag, z, thick, tall in (("踢脚线", 0.11, 0.10, 0.22),
                                ("腰线", WINDOW_SILL, 0.08, 0.12)):
        offset = facing * (thick / 2.0 + 0.005)
        size = (length, thick, tall) if axis == "x" else (thick, length, tall)
        loc = ((start + end) / 2.0, inner + offset, z) if axis == "x" \
            else (inner + offset, (start + end) / 2.0, z)
        pieces.append(make_box(f"{prefix}{tag}", size, loc, color,
                               scene=scene))
    for piece in pieces:
        decor(piece)
    return pieces


def make_pendant(name, x, ceiling_z, scene, energy=4200.0, cutoff=14.0,
                 color=(1.0, 0.93, 0.85), shade=(0.10, 0.09, 0.08, 1.0),
                 glow=0.12, y=0.0, glow_color=(1.0, 0.68, 0.36, 1.0)):
    """一盏吊灯:吊杆 + **圆台灯罩** + 自发光灯泡 + 向下的聚光 + 一点上照。

    主光为什么是 70° 聚光、上照为什么 <= 0.12 倍、灯罩为什么必须
    `render_no_shadow` —— 见本段开头那份数值清单(全是从 demo_story
    第一百四十八 / 一百五十一轮实机截图里换来的)。
    """
    rod = 0.9
    lamp_z = ceiling_z - rod - 0.18
    # ⚠ 吊杆 / 灯罩**不能有碰撞**:它们挂在天花板下 4 米,玩家永远碰不到,
    #   却会挡住第三人称相机的避让射线(相机被杆子顶住后贴在灯泡上)。
    decor(no_shadow(make_box(f"{name}吊杆", (0.06, 0.06, rod),
                             (x, y, ceiling_z - rod / 2),
                             (0.09, 0.09, 0.10, 1.0), scene=scene)))
    shade_obj = decor(no_shadow(make_cone(
        f"{name}灯罩", 0.42, 0.15, 0.34, (x, y, lamp_z), shade,
        scene=scene, glow=(glow_color, glow))))
    bulb = no_shadow(make_box(
        f"{name}灯泡", (0.17, 0.17, 0.11), (x, y, lamp_z - 0.12),
        (1.0, 0.92, 0.78, 1.0), scene=scene,
        glow=((1.0, 0.88, 0.66, 1.0), 2.2)))
    decor(bulb)
    # 灯点在**罩口下面**(>= 0.3 米):在罩子内部的话,罩子会把它整个挡黑。
    light = make_light(name, "SPOT", (x, y, lamp_z - PENDANT_BULB_DROP),
                       energy, color, scene, cutoff=cutoff,
                       spot_size=math.radians(PENDANT_SPOT))
    light.data.use_custom_distance = True
    light.data.cutoff_distance = cutoff
    if hasattr(light.data, "volume_factor"):
        light.data.volume_factor = 0.9
    light.data.spot_blend = 0.5
    settings = getattr(light, "gk_light", None)
    if settings is not None:
        settings.projector = "SOFT"
    # 上照:只把天花板提亮一点。能量必须**很小**(见清单)。
    up = make_aimed_light(f"{name}上照", (x, y, lamp_z + 0.10),
                          (x, y, ceiling_z + 1.0),
                          max(energy * PENDANT_UP_SCALE, 260.0), color, scene,
                          spot_size=135.0, cutoff=5.5, shadow=False,
                          volume=0.6, softness=0.35)
    up.data.spot_blend = 0.95
    up_settings = getattr(up, "gk_light", None)
    if up_settings is not None:
        up_settings.projector = "SOFT"
    return shade_obj, light


def make_sconce(name, location, scene, axis="y", facing=1, energy=2000.0,
                cutoff=9.0, color=(1.0, 0.91, 0.81), glow=0.55):
    """壁灯:底座 + 深色金属罩 + 发光片 + 朝下洗墙的聚光。

    `axis="y"` = 装在沿 X 展开的墙上(南 / 北墙),法线是 Y;
    `axis="x"` = 装在沿 Y 展开的墙上(东 / 西墙),法线是 X。
    `facing` = 房间在法线的哪一侧(+1 / -1);`location` = 灯罩中心。

    ── 灯罩为什么是深色金属(而不是"一块浅色发光板")───────────────
    以前罩子 albedo 0.92 + 自发光、离灯 0.28 米,灯把它照到全通道过 1,
    再加泛光,墙上就是一团白。现在罩子是深色金属,真正发光的是罩子里
    那片小玻璃 —— "亮"和"发光"分开,形状才读得出来。

    ── 灯离墙**至少 1.1 米**(比 demo_story 的 0.45 更保守)──────────
    贴墙 0.4 米时墙面照度按平方反比冲到 18 倍,实机里就是一团白 + 一大圈
    泛光。这里把灯挪到 1.15 米外、朝下方偏墙一点洗过去,墙面是渐变的一片。
    """
    off = SCONCE_LIGHT_OFFSET
    if axis == "y":
        base_size, shade_size = (0.34, 0.14, 0.62), (0.28, 0.24, 0.36)
        glass_size = (0.20, 0.05, 0.26)
        base_loc = (location[0], location[1] - facing * 0.07, location[2])
        shade_loc = (location[0], location[1] + facing * 0.12, location[2])
        glass_loc = (location[0], location[1] + facing * 0.25, location[2])
        light_loc = (location[0], location[1] + facing * off,
                     location[2] + 0.10)
        aim_loc = (location[0], location[1] + facing * 0.70, location[2] - 2.4)
    else:
        base_size, shade_size = (0.14, 0.34, 0.62), (0.24, 0.28, 0.36)
        glass_size = (0.05, 0.20, 0.26)
        base_loc = (location[0] - facing * 0.07, location[1], location[2])
        shade_loc = (location[0] + facing * 0.12, location[1], location[2])
        glass_loc = (location[0] + facing * 0.25, location[1], location[2])
        light_loc = (location[0] + facing * off, location[1],
                     location[2] + 0.10)
        aim_loc = (location[0] + facing * 0.70, location[1], location[2] - 2.4)
    base = make_box(f"{name}底座", base_size, base_loc,
                    (0.13, 0.13, 0.13, 1.0), scene=scene)
    shade = make_box(f"{name}灯罩", shade_size, shade_loc,
                     (0.22, 0.20, 0.17, 1.0), scene=scene)
    glass = make_box(f"{name}灯芯", glass_size, glass_loc,
                     (1.0, 0.86, 0.64, 1.0), scene=scene,
                     glow=((1.0, 0.78, 0.48, 1.0), glow))
    light = make_aimed_light(name, light_loc, aim_loc, energy * 0.75, color,
                             scene, spot_size=110.0, cutoff=cutoff,
                             volume=1.0, softness=0.35)
    light.data.spot_blend = 0.65
    settings = getattr(light, "gk_light", None)
    if settings is not None:
        # 只挂光斑纹理(打破"正圆");射程 / 衰减走 Blender 自己的
        # 『自定义距离』(make_aimed_light 已经写进去)。
        settings.projector = "LAMP"
    for part in (base, shade, glass):
        decor(part)
        no_shadow(part)
    return shade, light


def _apply_settings(scene, pairs):
    settings = getattr(scene, "gk_settings", None)
    if settings is None:
        return
    for attr, value in pairs:
        if hasattr(settings, attr):
            try:
                setattr(settings, attr, value)
            except Exception:                             # noqa: BLE001
                pass


def _display_color(red, green, blue, alpha=1.0):
    """按"取色器上看到的颜色"写环境色。

    ⚠ 环境颜色在 Blender 里存的是**线性**值,导出时又会 linear→sRGB 写成
      Godot 的 `Color(...)`。如果这里直接写 0.22 想表达"暗灰",最后屏幕上
      会变成 0.51 的中灰 —— 实机游玩时整间教室被雾洗成灰白(第一百五十八
      轮真踩到)。这里先把显示值转回线性,导出再转回去,所见即所得。
    """
    def channel(value):
        value = max(0.0, min(1.0, float(value)))
        return (value / 12.92 if value <= 0.04045
                else ((value + 0.055) / 1.055) ** 2.4)
    return (channel(red), channel(green), channel(blue), float(alpha))


# 幽暗教学楼的四个环境预设(都走『显示_环境氛围』那张卡的字段)
ENV_DARK = (
    ("env_enabled", True),
    ("env_background", "COLOR"),
    ("env_bg_color", _display_color(0.02, 0.02, 0.04)),
    # 走廊:冷青(第 178 轮色彩脚本)—— 原蓝灰往青绿方向压。
    ("env_ambient_color", _display_color(0.36, 0.50, 0.55)),
    # ── 第一百六十轮:0.14 -> 0.18 ─────────────────────────────────
    # 手电从 4200W 收到 550W(它挂在相机上,等于"贴着眼睛的闪光灯",
    # 4200W 把角色照成一整块白板)。走廊不能因此变成纯黑:补一点环境光,
    # 再靠新加的吊灯 / 壁灯撑住"能看清自己在哪"。
    ("env_ambient_energy", 0.20),
    ("env_fog", True),
    ("env_fog_color", _display_color(0.11, 0.18, 0.20)),
    ("env_fog_density", 0.055),
    ("env_glow", True),
    ("env_glow_intensity", 1.0),
    ("env_glow_bloom", 0.2),
    ("env_tonemap", "ACES"),
)
# 教室:暖黄(第 178 轮色彩脚本)—— 原来和音乐教室共用的冷蓝底,
# 暖化:背景/环境光/雾都往琥珀方向压(台灯本来就是暖的,统一色温)。
ENV_ROOM = (
    ("env_enabled", True),
    ("env_background", "COLOR"),
    ("env_bg_color", _display_color(0.07, 0.055, 0.030)),
    ("env_ambient_color", _display_color(0.66, 0.55, 0.38)),
    # 实机取景(第 180 轮):暖黄读得出来但偏暗,提一档
    ("env_ambient_energy", 0.26),
    ("env_fog", True),
    ("env_fog_color", _display_color(0.22, 0.17, 0.11)),
    ("env_fog_density", 0.03),
    ("env_glow", True),
    ("env_glow_intensity", 0.8),
    ("env_glow_bloom", 0.15),
    ("env_tonemap", "ACES"),
)
ENV_STAIRS = (
    ("env_enabled", True),
    # 第 5 章改成**天空**背景:天台是露天的一场戏,纯色背景会像悬在虚空里。
    # 井道里有天花板挡着,看不到天 —— 室内观感和纯色时一致。
    ("env_background", "SKY"),
    ("env_bg_color", _display_color(0.020, 0.026, 0.055)),
    ("env_ambient_color", _display_color(0.35, 0.40, 0.58)),
    ("env_ambient_energy", 0.08),
    ("env_fog", True),
    ("env_fog_color", _display_color(0.12, 0.14, 0.22)),
    ("env_fog_density", 0.045),
    ("env_glow", True),
    ("env_glow_intensity", 1.0),
    ("env_glow_bloom", 0.25),
    ("env_tonemap", "ACES"),
)
# 音乐教室:舞台紫(第 178 轮色彩脚本)—— 紫罗兰的雾和环境光,
# 和走廊的冷青区分开(同一栋楼里两种"冷",情绪完全不同)。
ENV_MUSIC = (
    ("env_enabled", True),
    ("env_background", "COLOR"),
    ("env_bg_color", _display_color(0.05, 0.04, 0.09)),
    ("env_ambient_color", _display_color(0.46, 0.38, 0.62)),
    # 实机取景(第 180 轮):紫读得出来但偏暗,提一档
    ("env_ambient_energy", 0.24),
    ("env_fog", True),
    ("env_fog_color", _display_color(0.14, 0.10, 0.22)),
    ("env_fog_density", 0.035),
    ("env_glow", True),
    ("env_glow_intensity", 0.8),
    ("env_glow_bloom", 0.15),
    ("env_tonemap", "ACES"),
)
# 医务室:惨白(第 178 轮色彩脚本)—— 医院荧光灯的灰白(微绿),
# 比"黑"更瘆人:哪里都看得见,哪里都不对劲。
ENV_MED = (
    ("env_enabled", True),
    ("env_background", "COLOR"),
    ("env_bg_color", _display_color(0.05, 0.06, 0.055)),
    ("env_ambient_color", _display_color(0.54, 0.60, 0.54)),
    # 实机取景(第 180 轮):第一版太暗太绿,"惨白"读不出来 ——
    # 提一档环境光,雾往白里压(医院荧光灯的"哪里都看得见,哪里都不对劲")
    ("env_ambient_energy", 0.26),
    ("env_fog", True),
    ("env_fog_color", _display_color(0.19, 0.21, 0.20)),
    ("env_fog_density", 0.045),
    ("env_glow", True),
    ("env_glow_intensity", 0.8),
    ("env_glow_bloom", 0.15),
    ("env_tonemap", "ACES"),
)
# 标题页:几乎全黑(只有 UI 亮着),一点雾把背景压住
ENV_TITLE = (
    ("env_enabled", True),
    ("env_background", "COLOR"),
    ("env_bg_color", _display_color(0.01, 0.01, 0.02)),
    ("env_ambient_color", _display_color(0.30, 0.34, 0.50)),
    ("env_ambient_energy", 0.10),
    ("env_fog", True),
    ("env_fog_color", _display_color(0.10, 0.11, 0.18)),
    ("env_fog_density", 0.02),
    ("env_glow", True),
    ("env_glow_intensity", 0.7),
    ("env_glow_bloom", 0.2),
    ("env_tonemap", "ACES"),
)


# ==========================================================================
# 三关:物体
# ==========================================================================
# ==========================================================================
# M3 第一次落地:把"占位方块"换成零件拼出来的人形(第 162 轮)
# ==========================================================================
# 用户从第 158 轮起反复提到"角色/怪物还是白盒子,这是现在最不像二次元的地方"。
# 美术资产不能等,先用**零件拼**出一个能读的剪影:
#   * 玩家 = 深色制服 + 白衬领 + 头 + 头发 + 双臂 + 裙子 + 鞋;
#   * 怪物 = 细长黑影 + 白脸 + 垂到膝的长手。
#
# 三条实现约束(都是踩过的):
#   1. 零件一律是**玩家物体的子级**:导出侧 `export_subtree_glb` 把"根 + 全部
#      子级"导成角色自己的 glb,而世界 glb 会把**整棵子树**排除 —— 否则出生点
#      会多一份静止的复制品(和"两份网格重叠一闪一闪"是同一个坑)。
#   2. 零件全部 `decor()`(无碰撞):碰撞体仍然只有玩家/怪物那**一个胶囊**。
#      给零件加碰撞 → 一堆小盒子把路堵死,还会挡第三人称相机。
#   3. 改尺寸改**网格顶点**,不动 `obj.scale`:导出侧按包围盒算碰撞,
#      scale 会跟着进 glb 节点,Godot 里就变成"节点被缩放"(角色压扁/拉伸)。
PLAYER_BOX = (1.0, 1.0, 1.6)          # 各关建玩家用的原始盒子(换算要用)
PLAYER_CORE = (0.52, 0.36, 1.60)      # 腿/躯干核心:自动胶囊半径 0.26(比肩膀窄才有腰)
MONSTER_BOX = (1.4, 1.4, 1.8)         # 走廊 / 音乐教室 / 医务室的怪
MONSTER_BOX_SLIM = (0.9, 0.9, 1.9)    # 楼梯间追兵
MONSTER_CORE = (0.56, 0.46, 1.70)

PLAYER_PALETTE = {
    "uniform": (0.135, 0.150, 0.225, 1.0),   # 深蓝制服(留给躯干盒子)
    "shirt": (0.860, 0.880, 0.920, 1.0),     # 白衬领
    "skirt": (0.105, 0.115, 0.180, 1.0),     # 裙子
    "skin": (0.950, 0.840, 0.760, 1.0),      # 皮肤
    "hair": (0.050, 0.045, 0.060, 1.0),      # 头发(要比制服明显更黑)
    "shoe": (0.090, 0.090, 0.110, 1.0),      # 鞋
}
MONSTER_PALETTE = {
    "coat": (0.150, 0.055, 0.070, 1.0),      # 暗红近黑(还认得出来是"那个红的")
    "face": (0.780, 0.780, 0.820, 1.0),      # 惨白的脸
    "hair": (0.055, 0.050, 0.065, 1.0),      # 更黑的长发
    "hand": (0.700, 0.690, 0.720, 1.0),      # 手
}


def _slim_box(obj, old_size, new_size):
    """把方块网格缩到新尺寸(**改顶点**,不动 scale —— 见上面第 3 条)。"""
    if not obj.data.vertices:
        return
    scale = [new_size[i] / old_size[i] for i in range(3)]
    for vert in obj.data.vertices:
        vert.co.x *= scale[0]
        vert.co.y *= scale[1]
        vert.co.z *= scale[2]
    obj.data.update()


def attach_part(parent, name, size, offset, color, scene, glow=None):
    """给角色加一个零件:挂在角色下面、跟着走、不参与碰撞。

    `offset` 是**相对角色原点**的局部坐标(角色没有旋转/缩放,所以就是位移)。
    """
    part = make_box(name, size, (0.0, 0.0, 0.0), color, scene=scene, glow=glow)
    part.parent = parent
    # 父级只有位移,把 inverse 置成单位阵 → 零件的 location 就是局部偏移
    part.matrix_parent_inverse = Matrix.Identity(4)
    part.location = offset
    decor(part)
    return part


def attach_leg(parent, name, size, top_z, x_off, color, scene, glow=None):
    """加一条**以髋为轴**的腿:网格下移半高,使局部原点落在腿的顶端(髋),
    这样『角色_走路摆动』摆 rotation.x 时是绕髋转,而不是绕腿肚子转。

    `top_z` 是髋(腿顶)相对角色原点的高度;`size[2]` 是腿长,腿从 `top_z`
    往下延伸到 `top_z - size[2]`(脚)。
    """
    part = make_box(name, size, (0.0, 0.0, 0.0), color, scene=scene, glow=glow)
    shift = size[2] / 2.0
    for vert in part.data.vertices:
        vert.co.z -= shift          # 原点抬到腿顶
    part.data.update()
    part.parent = parent
    part.matrix_parent_inverse = Matrix.Identity(4)
    part.location = (x_off, 0.0, top_z)
    decor(part)
    return part


def _hide_core_mesh(host):
    """藏掉角色核心盒子的网格,但**保留碰撞体**(碰撞独立于网格,见第 156 轮)。

    用途:核心盒子只当"碰撞宿主 + 躯干底色",可见的身体由零件拼出来;
    不藏的话下半身会是一整块深色矩形,盖在两条腿外面。
    """
    phys = getattr(host, "gk_physics", None)
    if phys is not None:
        phys.hide_mesh = True


def dress_player(player, scene):
    """把『1×1×1.6 的盒子』换成一个人形(局部坐标以盒子中心为原点)。

    ⚠ 结构是**分段的**,不是"一根柱子 + 一顶帽子"(第一版就是这么写的,
      侧视看像一根灯柱):
        腿/袜(核心盒子,0.10..0.50)-> 裙(0.45..1.00)-> 上衣(1.10..1.66)
        -> 白衬领 -> 头(1.66..2.00)-> 头发
      核心盒子必须保持**满高 1.6**:碰撞胶囊是"按包围盒自动猜"的,
      它矮下去胶囊就跟着矮 —— 角色会陷进地板 / 上不去坡。
      所以可见的"腿"就用核心盒子本身(深色),上面再盖裙子和上衣。
    """
    _slim_box(player, PLAYER_BOX, PLAYER_CORE)
    pal = PLAYER_PALETTE
    prefix = player.name
    # 裙子:盖住核心盒子上半段(0.45..1.00)
    skirt = make_cone(f"{prefix}裙", 0.40, 0.20, 0.55, (0.0, 0.0, 0.0),
                      pal["skirt"], scene=scene)
    skirt.parent = player
    skirt.matrix_parent_inverse = Matrix.Identity(4)
    skirt.location = (0.0, 0.0, 0.10)
    decor(skirt)
    # 上衣(比核心宽一圈,肩膀才出来)+ 白衬领
    # ⚠ 上衣必须比核心盒子(0.62×0.40)**宽**一圈:第一版写成 0.60×0.40,
    #   核心就从衣服里穿出来 —— 远处看是一块"灰板",把怪物/自己糊掉(实测)。
    attach_part(player, f"{prefix}上衣", (0.70, 0.48, 0.56), (0.0, 0.0, 0.48),
                pal["uniform"], scene)
    attach_part(player, f"{prefix}衣领", (0.52, 0.42, 0.14), (0.0, 0.0, 0.78),
                pal["shirt"], scene)
    # 头 / 头发 / 刘海
    attach_part(player, f"{prefix}头", (0.38, 0.34, 0.36), (0.0, 0.0, 0.94),
                pal["skin"], scene)
    attach_part(player, f"{prefix}发", (0.44, 0.40, 0.18), (0.0, 0.02, 1.06),
                pal["hair"], scene)
    attach_part(player, f"{prefix}刘海", (0.40, 0.09, 0.13),
                (0.0, -0.18, 1.00), pal["hair"], scene)
    # 双臂(垂在身体两侧)+ 手
    for side, tag in ((-1.0, "左"), (1.0, "右")):
        attach_part(player, f"{prefix}{tag}臂", (0.12, 0.14, 0.58),
                    (side * 0.33, 0.0, 0.42), pal["uniform"], scene)
        attach_part(player, f"{prefix}{tag}手", (0.13, 0.15, 0.15),
                    (side * 0.33, 0.0, 0.08), pal["skin"], scene)
    # 两只鞋(踩在核心盒子前面一点,能看出"脚")
    for side, tag in ((-1.0, "左"), (1.0, "右")):
        attach_part(player, f"{prefix}{tag}鞋", (0.17, 0.30, 0.12),
                    (side * 0.13, -0.04, -0.74), pal["shoe"], scene)
    # 两条腿(髋在腰下一点、脚接近地面):核心盒子从此**不渲染**(见下面
    #   hide_mesh),下半身由这两条腿拼出来 —— 不再是"一根深色柱子"。
    #   ⚠ 腿名带「腿」:『角色_走路摆动』会自动把它们收进来、和手臂反相摆。
    for side, tag in ((-1.0, "左"), (1.0, "右")):
        attach_leg(player, f"{prefix}{tag}腿", (0.17, 0.17, 0.86),
                   -0.02, side * 0.13, pal["skirt"], scene)
    # 藏掉核心盒子的网格:碰撞胶囊还在(碰撞体独立于网格),只是看不见了。
    #   不藏的话下半身是一整块深色矩形,盖在两条腿外面。
    _hide_core_mesh(player)


def dress_monster(enemy, scene, old_size=MONSTER_BOX):
    """把红色大方块换成『细长黑影 + 白脸 + 垂到膝的长手』。"""
    _slim_box(enemy, old_size, MONSTER_CORE)
    pal = MONSTER_PALETTE
    prefix = enemy.name
    # 长袍(盖住核心上半段)+ 长发(披到肩下)
    attach_part(enemy, f"{prefix}袍", (0.66, 0.54, 0.84), (0.0, 0.0, 0.42),
                pal["coat"], scene)
    attach_part(enemy, f"{prefix}发", (0.40, 0.38, 0.60), (0.0, 0.03, 0.72),
                pal["hair"], scene)
    # 头 + 白脸 + 刘海
    attach_part(enemy, f"{prefix}头", (0.34, 0.32, 0.34), (0.0, 0.0, 0.92),
                pal["face"], scene)
    attach_part(enemy, f"{prefix}脸", (0.24, 0.05, 0.22), (0.0, -0.16, 0.92),
                pal["face"], scene)
    attach_part(enemy, f"{prefix}刘海", (0.36, 0.08, 0.14),
                (0.0, -0.16, 1.04), pal["hair"], scene)
    # 长手:垂到膝盖
    for side, tag in ((-1.0, "左"), (1.0, "右")):
        attach_part(enemy, f"{prefix}{tag}臂", (0.10, 0.12, 1.10),
                    (side * 0.32, 0.0, 0.10), pal["coat"], scene)
        attach_part(enemy, f"{prefix}{tag}手", (0.12, 0.13, 0.18),
                    (side * 0.32, -0.02, -0.52), pal["hand"], scene)
    # 两条腿:长袍盖到腰(约 z=0),腿从袍下露出来一点(暗红,和袍同色)。
    #   核心盒子同样藏掉(只留碰撞),下半身由这两条腿拼出。
    for side, tag in ((-1.0, "左"), (1.0, "右")):
        attach_leg(enemy, f"{prefix}{tag}腿", (0.16, 0.16, 0.78),
                   -0.05, side * 0.14, pal["coat"], scene)
    _hide_core_mesh(enemy)


def build_room(scene):
    """第一关 教室:月光 + 台灯 + 3 张纸条 + 通往走廊的门。

    ⚠ 用**已有的那个场景**(clear_all 已经把它改名成『教室』),不要再
    `scenes.new("教室")` —— 重名会被 Blender 改成『教室.001』,于是导出
    多出一关空场景(踩过)。
    """
    make_box("教室地面", (24.0, 24.0, 0.4), (0.0, 0.0, -0.2),
             (0.12, 0.12, 0.16, 1.0), scene=scene)
    # 课桌(纯粹好看 + 挡一挡视线)
    for index, (x, y) in enumerate(((-4.0, 2.0), (0.0, 2.0), (4.0, 2.0),
                                    (-4.0, -2.0), (0.0, -2.0), (4.0, -2.0))):
        make_box(f"课桌{index + 1}", (2.2, 1.4, 1.4), (x, y, 0.7),
                 (0.22, 0.20, 0.18, 1.0), scene=scene)
    # ⚠ 角色底色别用 0.85:走廊里手电是"贴着眼睛的闪光灯",高反照率一照就
    #   过 1,实机里是一块没有面与面的白板(见 build_corridor 的手电那段)。
    player = make_box("教室玩家", (1.0, 1.0, 1.6), (-8.0, -8.0, 0.9),
                      PLAYER_COLOR, scene=scene)
    dress_player(player, scene)
    note_spots = ((-6.0, -6.0), (6.0, -6.0), (0.0, 7.0))
    for name, (x, y) in zip(NOTE_NAMES, note_spots):
        make_box(name, (1.0, 1.0, 0.6), (x, y, 0.3),
                 (0.92, 0.90, 0.72, 1.0), scene=scene)
    # ⚠ 触发区那一块"薄板"是判定体,**不能露在画面里**:实机截图它是
    #   门口一块比周围亮一档的蓝绿色地板。和『惊吓区』一样 hide_mesh
    #   (glTF 导出会跳过网格、Area3D 照旧)。
    door_plate = make_box(TO_ROOM, (5.0, 5.0, 0.2), (0.0, -10.0, 0.1),
                          (0.22, 0.32, 0.30, 1.0), scene=scene)
    door_plate_physics = getattr(door_plate, "gk_physics", None)
    if door_plate_physics is not None:
        door_plate_physics.hide_mesh = True
    # ── 场景美术(从 demo_story 移植):北墙 + 两扇窗 + 踢脚线 / 腰线 ────
    # 教室以前是"一块地板 + 6 张课桌"悬在空背景里:镜头一偏就是空白。
    # ⚠ 只加**北面**这一道墙:开场运镜的轨道起点在 y=-16(已经在地板外面),
    #   南边 / 东西两侧加满墙之后相机会直接穿墙过去。
    #   窗洞是真的洞(没有玻璃),月光从洞里斜切进来。
    make_wall_pieces("教室北墙", "x", 12.0, (-12.0, 12.0), WALL_THICK,
                     WALL_HEIGHT, scene, [(-5.5, 4.0), (5.5, 4.0)],
                     (0.16, 0.16, 0.21, 1.0))
    for index, center in enumerate((-5.5, 5.5)):
        make_window(f"教室窗{index + 1}", center, 12.0, 4.0, scene,
                    axis="x", color=(0.22, 0.21, 0.20, 1.0))
    make_trim("教室北墙", "x", 12.0, (-12.0, 12.0), -1, scene,
              (0.20, 0.19, 0.18, 1.0))
    # ── 台灯:深色灯罩 + 自发光灯芯(罩子挂在灯上方 0.37 米)──────────
    # 以前只有一盏裸点光:桌上是一团没有形状的白斑。灯本身**不动**
    # (位置 / 能量一个字都没改),只是给它补上"这是个灯"的形状。
    decor(no_shadow(make_box("台灯底座", (0.24, 0.24, 0.08),
                             (2.80, 2.0, 1.44), (0.13, 0.13, 0.12, 1.0),
                             scene=scene)))
    decor(no_shadow(make_box("台灯立柱", (0.05, 0.05, 0.50),
                             (2.80, 2.0, 1.70), (0.13, 0.13, 0.12, 1.0),
                             scene=scene)))
    decor(no_shadow(make_box("台灯横臂", (0.32, 0.05, 0.05),
                             (2.94, 2.0, 1.95), (0.13, 0.13, 0.12, 1.0),
                             scene=scene)))
    decor(no_shadow(make_cone("台灯灯罩", 0.32, 0.13, 0.26,
                              (3.0, 2.0, 2.10), (0.10, 0.09, 0.08, 1.0),
                              scene=scene,
                              glow=((1.0, 0.72, 0.42, 1.0), 0.10))))
    decor(no_shadow(make_box("台灯灯芯", (0.13, 0.13, 0.10),
                             (3.0, 2.0, 1.90), (1.0, 0.90, 0.70, 1.0),
                             scene=scene,
                             glow=((1.0, 0.82, 0.55, 1.0), 2.0))))
    # 统一机位:三关共用**同一个相机物体**,名字才不会被 Blender 加 .001。
    # 第三人称要广角:21mm ≈ 59° 垂直视野(和角色图里『设定初始视野 60』对得上)。
    side = reuse_camera(scene, SIDE_CAM, (0.0, -16.0, 6.0),
                        (math.radians(72.0), 0.0, 0.0), lens=21.0)
    # 俯瞰机位再广一点:18mm ≈ 67°,从 16 米高空能看全走廊/教室。
    reuse_camera(scene, TOP_CAM, (0.0, -6.0, 18.0),
                 (math.radians(22.0), 0.0, 0.0), lens=18.0)
    # 开场运镜的落点(门口特写)+ 轨道曲线(相机沿它从『侧视机位』俯冲过来)。
    # ⚠ 特写要**低头看门口**:74° 在 Blender 里几乎是平视(实机开场落点
    #   拍到的是一片背景);33° 才是从 3.2 米高往下看门口方框。
    # 落点对着玩家:俯角 18°(旧 33° 是"低头看门口"),35mm 把 6 米外的
    # 玩家框在画面中间,门口落在右边缘 —— 每一帧都看得见人。
    # 朝向和轨道切线一致(左前方 42°、俯 17°):飞行途中切线 = 这个方向,
    # 落位那一帧和飞行途中接得上,不会"飞着看这边、停下看那边"。
    make_camera(ROOM_CLOSE, ROOM_CLOSE_POS,
                (math.radians(65.8), 0.0, math.radians(39.8)), scene,
                lens=32.0)
    make_curve(ROOM_TRACK, ROOM_TRACK_POINTS, scene)
    scene.camera = side
    # 月光是"冷色补光",不是白天太阳:1.4 会把整个教室照成灰白(实机截图)。
    make_light("月光", "SUN", (5.0, -5.0, 10.0), 0.55, (0.65, 0.72, 0.95),
               scene, rotation=(math.radians(52.0), 0.0, math.radians(28.0)),
               shadow=True)
    # 暖色台灯:暗环境下要能看出一小片暖光池(160W 在 Godot 里太弱)。
    make_light("台灯", "POINT", (3.0, 2.0, 1.6), 600.0, (1.0, 0.78, 0.45),
               scene, cutoff=9.0)
    log(f"{ROOM}:地面 + 6 张课桌 + 玩家 + 3 张纸条 + {TO_ROOM} + "
        f"月光 / 台灯 + 两台相机")
    return player


def build_corridor():
    """第二关 走廊:手电筒 + 巡逻怪物(会听动静)+ 惊吓区。"""
    scene = bpy.data.scenes.new(CORRIDOR)
    make_box("走廊地面", (30.0, 20.0, 0.4), (0.0, 0.0, -0.2),
             (0.10, 0.11, 0.14, 1.0), scene=scene)
    for index, (x, y) in enumerate(((-9.0, 6.5), (9.0, 6.5),
                                    (-9.0, -6.5), (9.0, -6.5))):
        make_box(f"走廊立柱{index + 1}", (1.2, 1.2, 4.0), (x, y, 2.0),
                 (0.16, 0.15, 0.20, 1.0), scene=scene)
    # ── 场景美术(从 demo_story 移植):墙 / 窗 / 踢脚线 / 腰线 / 吊灯 / 壁灯
    # 这一关以前只有"一块地板 + 4 根柱子",镜头一切就是空背景 —— 用户原话
    # "它现在最空"。搬过来的四样:带窗洞的墙(make_wall_pieces)、带窗框的
    # 窗(make_window)、吊灯(make_pendant)、壁灯(make_sconce)。
    # ⚠ 南墙从 x=-6 才开始:出生点在 (-12,-8),第三人称的弹簧臂在身后 5 米,
    #   那里要是有墙,避让射线会把相机从 5 米拽到 1.6 米(盒子直接怼满屏)。
    make_wall_pieces("走廊北墙", "x", 10.0, (-15.0, 15.0), WALL_THICK,
                     WALL_HEIGHT, scene, [(-9.0, 3.6), (0.0, 3.6),
                                          (9.0, 3.6)], (0.15, 0.15, 0.20, 1.0))
    for index, center in enumerate((-9.0, 0.0, 9.0)):
        make_window(f"走廊窗{index + 1}", center, 10.0, 3.6, scene,
                    axis="x", color=(0.24, 0.23, 0.21, 1.0))
    make_trim("走廊北墙", "x", 10.0, (-15.0, 15.0), -1, scene,
              (0.19, 0.18, 0.17, 1.0))
    make_wall_pieces("走廊南墙", "x", -10.0, (-6.0, 15.0), WALL_THICK,
                     WALL_HEIGHT, scene, [(2.0, 3.6), (11.0, 3.6)],
                     (0.15, 0.15, 0.20, 1.0))
    for index, center in enumerate((2.0, 11.0)):
        make_window(f"走廊南窗{index + 1}", center, -10.0, 3.6, scene,
                    axis="x", color=(0.24, 0.23, 0.21, 1.0))
    make_trim("走廊南墙", "x", -10.0, (-6.0, 15.0), 1, scene,
              (0.19, 0.18, 0.17, 1.0))
    # 横梁:吊灯得有个"挂点"(这一关没有天花板),顺手给空间一点结构。
    for index, x in enumerate((-9.0, 0.0, 9.0)):
        decor(no_shadow(make_box(f"走廊横梁{index + 1}", (0.45, 20.0, 0.32),
                                 (x, 0.0, 4.60), (0.14, 0.14, 0.18, 1.0),
                                 scene=scene)))
        make_pendant(f"走廊吊灯{index + 1}", x, 4.60, scene, y=0.0,
                     color=CORRIDOR_CYAN, glow_color=CORRIDOR_GLOW)
    # 壁灯:南北两面墙各两盏(离墙 1.15 米朝下洗墙,不是贴墙 0.4 米的白团)。
    make_sconce("走廊壁灯1", (-4.5, 10.0, 2.60), scene)
    make_sconce("走廊壁灯2", (4.5, 10.0, 2.60), scene)
    make_sconce("走廊壁灯3", (6.5, -10.0, 2.60), scene, facing=1)
    make_sconce("走廊壁灯4", (-3.0, -10.0, 2.60), scene, facing=1)
    # ⚠ 出生点离怪物(巡逻线 y=0)要**大于视野 9 米** —— 不然一进走廊就被
    #   发现、直接开追,恐怖游戏的"慢慢摸过去"就没有了(实测:放 (0,-8)
    #   只有 8 米,开局就在追击)。
    # ⚠ 出生点(第一百七十六轮,真机反馈):原来在 (-12,-8) 西南角,
    #   离巡逻线 12 米 —— 视野 9 米**够不着**,玩家开局怎么走都遇不到怪,
    #   "牵制"名存实亡。挪到 (-8,5):离怪 9.4 米(视野边缘),走一步就
    #   进视线,开局即紧张。
    player = make_box("走廊玩家", (1.0, 1.0, 1.6), (-8.0, 5.0, 0.9),
                      PLAYER_COLOR, scene=scene)
    dress_player(player, scene)
    enemy = make_box(ENEMY, MONSTER_BOX, (0.0, 0.0, 0.9),
                     (0.55, 0.10, 0.12, 1.0), scene=scene)
    dress_monster(enemy, scene, MONSTER_BOX)
    make_box(PATROL_A, (0.5, 0.5, 2.0), (-7.0, 0.0, 1.0),
             (0.25, 0.30, 0.28, 1.0), scene=scene)
    make_box(PATROL_B, (0.5, 0.5, 2.0), (7.0, 0.0, 1.0),
             (0.25, 0.30, 0.28, 1.0), scene=scene)
    # 惊吓区只是触发体:画面里不该出现一块红色地板(实机截图看到过)。
    # ⚠ 用『游戏物理』的『不渲染网格』(glTF 导出会跳过网格、碰撞照旧);
    #   Blender 的 `hide_render` 对 glTF 导出器**无效**(实测红板还在)。
    scare = make_box(SCARE_AREA, (6.0, 6.0, 0.2), (-11.0, 3.0, 0.1),
                     (0.30, 0.10, 0.12, 1.0), scene=scene)
    scare_physics = getattr(scare, "gk_physics", None)
    if scare_physics is not None:
        scare_physics.hide_mesh = True
    # ── 保险丝(第一百六十八轮):本体看得见 + 判定体看不见 ──────────
    # 走廊开局只有吊灯1 亮;每捡一个保险丝,逻辑图点一盏吊灯(渐进
    # 反馈),集齐 3 个『去音乐教室』的传送板才放行。
    for index, (fx, fy) in enumerate(FUSE_POS):
        # 本体:琥珀色小陶瓷管,黑暗里带一点微光(远处是个小暖点,
        # 给玩家视线引导;0.35 强度不刺眼)
        decor(no_shadow(make_box(
            f"{FUSES[index]}本体", (0.14, 0.10, 0.22), (fx, fy, 0.11),
            (0.70, 0.48, 0.16, 1.0), scene=scene,
            glow=((1.0, 0.72, 0.35, 1.0), 0.35))))
        # 判定体:走进去就算拿到(走廊是移动教学章,不做 E 交互)
        fuse_plate = make_box(FUSES[index], (1.2, 1.2, 1.6),
                              (fx, fy, 0.8), (0.30, 0.26, 0.14, 1.0),
                              scene=scene)
        fuse_physics = getattr(fuse_plate, "gk_physics", None)
        if fuse_physics is not None:
            fuse_physics.hide_mesh = True
    # 配电箱(叙事锚点,北墙内面,纯装饰):保险丝是"它的"——给为什么
    # 捡保险丝一个看得见的理由。
    decor(make_box("走廊配电箱", (0.52, 0.14, 0.72), (0.0, 9.85, 1.40),
                   (0.10, 0.11, 0.13, 1.0), scene=scene))
    # 隐藏笔记(第一百六十九轮):北窗下,巡逻线北,顺路但容易漏看
    _hidden_note(scene, HIDDEN_NOTE_NAMES[CORRIDOR], -4.5, 9.2)
    # ⚠ M1a:这块原来的名字是『去楼梯间』,通向楼梯间那一关。第 3 章插进来
    #   之后它改成『去音乐教室』,通到 M1a 的新关卡;楼梯间改由音乐教室的
    #   侧门进 —— **楼梯间自己一行代码都没动**(它只认自己的载入存档)。
    music_plate = make_box(TO_MUSIC, (5.0, 5.0, 0.2), (0.0, 8.0, 0.1),
                           (0.22, 0.32, 0.30, 1.0), scene=scene)
    music_plate_physics = getattr(music_plate, "gk_physics", None)
    if music_plate_physics is not None:
        music_plate_physics.hide_mesh = True
    # ── 安全屋(M0):标记 + 触发区 + 一盏暖灯 ────────────────────────
    # 出生点在 (-12, -8),安全屋放在它北边一点 —— 被抓之后"回安全屋"
    # 就是回到出生点附近,不会离进度太远,也不会一进门就撞上怪物。
    safe_x, safe_y = SAFE_ROOM_POS
    make_marker(SAFE_ROOM, (safe_x, safe_y, 0.0), scene, size=1.4)
    safe_area = make_box(SAFE_ROOM_AREA, (SAFE_ROOM_SIZE[0], SAFE_ROOM_SIZE[1],
                                         0.2), (safe_x, safe_y, 0.1),
                         (0.30, 0.34, 0.28, 1.0), scene=scene)
    safe_physics = getattr(safe_area, "gk_physics", None)
    if safe_physics is not None:
        safe_physics.hide_mesh = True
    make_light("安全屋灯", "POINT", (safe_x, safe_y, 2.6), 520.0,
               (1.0, 0.88, 0.62), scene, cutoff=8.0)
    side = reuse_camera(scene, SIDE_CAM, (0.0, -16.0, 6.0),
                        (math.radians(72.0), 0.0, 0.0), lens=21.0)
    reuse_camera(scene, TOP_CAM, (0.0, -6.0, 18.0),
                 (math.radians(22.0), 0.0, 0.0), lens=18.0)
    scene.camera = side
    # ── 手电筒(第一百六十轮:M0.5 收口)──────────────────────────────
    # 上一版 4200W(导出 light_energy 4.2)= 实机截图里玩家盒子被自己手电
    # 打成一**整块白板**(07_corridor:只剩轮廓,看不出面与面)。
    # 根因不是"手电照到了自己"这么简单:『跟随相机』把灯整个抄成相机的
    # 位置(相机就在玩家身后 5 米),而 Godot 点/聚光按 `omni_range` 做衰减
    # —— 射程 40 米时 5 米处衰减系数还是 1.0,等于 4.2 的满能量直射脸。
    # 现在:能量降到 800W(0.8),射程显式给 22 米(远处才真的暗下去,
    # 光柱有纵深),锥边继续柔化。走廊的整体亮度靠新增的吊灯 / 壁灯补
    # (见 build_corridor 里的 ② 美术段),不再靠手电一个人硬撑。
    torch = make_light(TORCH, "SPOT", (0.0, -8.0, 1.6), 480.0,
                       (1.0, 0.96, 0.85), scene,
                       rotation=(math.radians(80.0), 0.0, 0.0),
                       spot_size=math.radians(60.0), cutoff=20.0,
                       shadow=True)
    torch.data.spot_blend = 0.55     # 锥边柔一点,别是硬圆圈
    # ⚠ 一定要写 cutoff_distance:导出侧的射程读的是 Blender 的『自定义
    #   距离』(默认 40),不写就是 40 米 —— 5 米处衰减 = 1.0(见上)。
    torch.data.use_custom_distance = True
    torch.data.cutoff_distance = 20.0
    # ── 环境冷月光(补形状)────────────────────────────────────────
    # 光靠手电,没照到的地方是纯黑 —— 玩家看不出自己在走廊哪儿。
    # 一盏很弱的冷太阳光把柱子 / 地面 / 墙的轮廓托出来。
    # ── M0.5:0.54 -> 0.32,并把仰角从 48° 压到 22° ──────────────────
    # 48° 的仰角基本是"从天顶打下来"——它把**角色的顶面**照得和手电照的
    # 正面一样亮,于是盒子又变回一块平板(实测顶面 113 / 正面 122,只差 7%)。
    # 压到 22° + 降能量之后:光主要落在**竖直面**上(墙 / 角色的正面),
    # 和手电同向叠加;顶面只剩一点点 = 一眼能看出这是个立体的盒子。
    # 顺带:22° 的月光也更像从窗洞里斜切进来的夜光。
    make_light("走廊月光", "SUN", (-6.0, 6.0, 9.0), 0.32,
               (0.58, 0.68, 0.95), scene,
               rotation=(math.radians(22.0), 0.0, math.radians(-34.0)))
    log(f"{CORRIDOR}:地面 + 4 根立柱 + 玩家 + {ENEMY} + 两个巡逻点 + "
        f"{SCARE_AREA} + {TO_MUSIC} + {TORCH} + 两台相机")
    return scene, player, enemy, torch


def build_music_room():
    """第 3 章 音乐教室:隔音的教室 + 舞台 + 钢琴 + 谱架 + 录音机 + 上锁侧门。

    布局(Blender x/y;Godot 里 z = -y,见 _godot_xy):
        y=+11 北墙(三扇窗),最里面是**舞台**(钢琴 + 磁带)
        y=-11 南墙中间留一个门洞,门洞外那条"走廊"就是怪物待着的地方
        x=-13 西墙(玩家从走廊进来,出生在西南角的安全屋)
        x=+13 东墙(上锁的侧门 = 『去楼梯间』)
    ⚠ 地板要铺到 y=-17:门洞外面那条"走廊"也得有地,不然怪物站在虚空里
      (它没有 AI 时不掉,一放进来就悬空)。
    """
    scene = bpy.data.scenes.new(MUSIC)
    make_box("音乐教室地面", (26.0, 30.0, 0.4), (0.0, -2.0, -0.2),
             (0.11, 0.10, 0.13, 1.0), scene=scene)
    wall_color = (0.15, 0.15, 0.20, 1.0)
    # ── 四面墙 ─────────────────────────────────────────────────────
    # 北墙三扇窗(月光从洞里斜切进来);南墙**留门洞**(x∈[-2.5,2.5]),
    # 分两段建;东西两道实墙。
    make_wall_pieces("音乐室北墙", "x", 11.0, (-13.0, 13.0), WALL_THICK,
                     WALL_HEIGHT, scene, [(-7.0, 3.6), (0.0, 3.6), (7.0, 3.6)],
                     wall_color)
    for index, center in enumerate((-7.0, 0.0, 7.0)):
        make_window(f"音乐室窗{index + 1}", center, 11.0, 3.6, scene,
                    axis="x", color=(0.23, 0.22, 0.20, 1.0))
    make_trim("音乐室北墙", "x", 11.0, (-13.0, 13.0), -1, scene,
              (0.19, 0.18, 0.17, 1.0))
    for index, (start, end) in enumerate(((-13.0, -2.5), (2.5, 13.0))):
        make_wall_pieces(f"音乐室南墙{index + 1}", "x", -11.0, (start, end),
                         WALL_THICK, WALL_HEIGHT, scene, [], wall_color)
        make_trim(f"音乐室南墙{index + 1}", "x", -11.0, (start, end), 1, scene,
                  (0.19, 0.18, 0.17, 1.0))
    make_wall_pieces("音乐室西墙", "y", -13.0, (-11.0, 11.0), WALL_THICK,
                     WALL_HEIGHT, scene, [(-4.0, 3.6)], wall_color)
    make_window("音乐室西窗1", -4.0, -13.0, 3.6, scene, axis="y",
                color=(0.23, 0.22, 0.20, 1.0))
    make_trim("音乐室西墙", "y", -13.0, (-11.0, 11.0), 1, scene,
              (0.19, 0.18, 0.17, 1.0))
    make_wall_pieces("音乐室东墙", "y", 13.0, (-11.0, 11.0), WALL_THICK,
                     WALL_HEIGHT, scene, [], wall_color)
    make_trim("音乐室东墙", "y", 13.0, (-11.0, 11.0), -1, scene,
              (0.19, 0.18, 0.17, 1.0))
    # ── 舞台:一块(不抬高)的地台 + 判定体 ─────────────────────────
    # ⚠ 舞台**不能有台阶**:CharacterBody3D 不带自动迈步,0.5 米的台沿就是
    #   一堵墙(玩家上不去 = 磁带拿不到)。所以地台只是贴着地面的一块色斑,
    #   判定体(『舞台』)铺在它上面 —— 它同时是诱饵噪音的**发声位置**。
    decor(make_box("音乐教室舞台台面", (11.0, 5.0, 0.06), (0.0, 7.0, 0.03),
                   (0.20, 0.15, 0.13, 1.0), scene=scene))
    stage = make_box(STAGE, (11.0, 5.0, 0.2), (0.0, 7.0, 0.1),
                     (0.30, 0.24, 0.18, 1.0), scene=scene)
    # ── 钢琴(磁带放它顶上)+ 钢琴凳 ────────────────────────────────
    solid_box(make_box("音乐教室钢琴", (3.6, 1.6, 1.1), (0.0, 9.2, 0.55),
                       (0.12, 0.10, 0.10, 1.0), scene=scene))
    decor(no_shadow(make_box("音乐教室钢琴盖", (3.6, 1.5, 0.12),
                             (0.0, 9.2, 1.16), (0.16, 0.13, 0.12, 1.0),
                             scene=scene)))
    decor(make_box("音乐教室琴凳", (1.4, 0.8, 0.5), (0.0, 7.5, 0.25),
                   (0.13, 0.11, 0.10, 1.0), scene=scene))
    # ── 磁带:装饰件(看得见)+ 判定体(看不见)──────────────────────
    decor(no_shadow(make_box(TAPE_MESH, (0.6, 0.42, 0.16),
                             (0.9, 8.8, 1.30),
                             (0.90, 0.86, 0.62, 1.0), scene=scene,
                             glow=((0.9, 0.85, 0.5, 1.0), 0.15))))
    tape = make_box(TAPE, (1.8, 1.8, 1.6), (0.9, 8.0, 1.1),
                    (0.30, 0.28, 0.20, 1.0), scene=scene)
    # 隐藏笔记(第一百六十九轮):东侧课桌区,离乐谱3(9.5,2)有段距离
    _hidden_note(scene, HIDDEN_NOTE_NAMES[MUSIC], 8.0, 6.0)
    # ── 三张乐谱(谱架上)+ 谱架 ────────────────────────────────────
    # ⚠ M1b(lead 硬要求):乐谱2 原来在房间中线 (-2,-2),正落在怪的巡逻线上
    #   —— 视线是**纯距离判定**(codegen/ai.py 里就是 distance <= sight,没有
    #   遮挡射线),所以"躲到东西后面"本身挡不住视线,能救命的只有**距离**。
    #   现在把它挪到西侧课桌后面 (-7,-2):离巡逻线最近点 10.2 米 > 视野 8 米,
    #   课桌(-7,-3)只是让它"看起来"在掩体后面。
    sheet_spots = ((-10.5, 3.0), (-7.0, -2.0), (9.5, 2.0))
    sheets = []
    for index, (x, y) in enumerate(sheet_spots):
        decor(no_shadow(make_box(f"音乐教室谱架{index + 1}底座",
                                 (0.6, 0.6, 0.06), (x, y, 0.03),
                                 (0.12, 0.12, 0.13, 1.0), scene=scene)))
        decor(no_shadow(make_box(f"音乐教室谱架{index + 1}立杆",
                                 (0.08, 0.08, 1.4), (x, y, 0.7),
                                 (0.12, 0.12, 0.13, 1.0), scene=scene)))
        sheets.append(make_box(SHEET_NAMES[index], (1.1, 0.8, 0.6),
                               (x, y, 1.25),
                               (0.92, 0.90, 0.74, 1.0), scene=scene))
    # ── 录音机(小桌上)+ 桌子 ──────────────────────────────────────
    decor(no_shadow(make_box("音乐教室录音桌", (1.8, 1.1, 0.9),
                             (-2.5, -8.5, 0.45),
                             (0.13, 0.11, 0.10, 1.0), scene=scene)))
    recorder = make_box(RECORDER, (1.2, 0.9, 0.7), (-2.5, -8.5, 1.25),
                        (0.16, 0.14, 0.12, 1.0), scene=scene)
    # ── 几套学生课桌(纯好看 + 给视线一点遮挡)──────────────────────
    for index, (x, y) in enumerate(((-7.0, -3.0), (-3.5, -3.0), (3.5, -3.0),
                                    (7.0, -3.0))):
        decor(solid_box(make_box(f"音乐教室课桌{index + 1}", (1.8, 1.0, 0.9),
                                 (x, y, 0.45), (0.20, 0.18, 0.16, 1.0),
                                 scene=scene)))
    # ── 上锁的侧门(东墙)+ 『去楼梯间』判定体 ───────────────────────
    decor(make_box("音乐教室侧门", (0.3, 2.6, 2.6), (12.6, -6.0, 1.3),
                   (0.30, 0.22, 0.16, 1.0), scene=scene))
    decor(make_box("音乐教室侧门楣", (0.36, 3.2, 0.3), (12.6, -6.0, 2.75),
                   (0.24, 0.20, 0.17, 1.0), scene=scene))
    for index, y in enumerate((-7.5, -4.5)):
        decor(make_box(f"音乐教室侧门框柱{index + 1}", (0.36, 0.3, 3.0),
                       (12.6, y, 1.5), (0.24, 0.20, 0.17, 1.0), scene=scene))
    med_plate = make_box(TO_MED, (4.0, 4.0, 0.2), (11.0, -6.0, 0.1),
                            (0.22, 0.32, 0.30, 1.0), scene=scene)
    # ── 安全屋(本关入口)+ 一盏暖灯 ────────────────────────────────
    safe_x, safe_y = MUSIC_SAFE_POS
    make_marker(MUSIC_SAFE_ROOM, (safe_x, safe_y, 0.0), scene, size=1.4)
    safe_area = make_box(MUSIC_SAFE_AREA, (MUSIC_SAFE_SIZE[0],
                                           MUSIC_SAFE_SIZE[1], 0.2),
                         (safe_x, safe_y, 0.1), (0.30, 0.34, 0.28, 1.0),
                         scene=scene)
    make_light("音乐室安全屋灯", "POINT", (safe_x, safe_y, 2.6), 520.0,
               (1.0, 0.88, 0.62), scene, cutoff=8.0)
    # ── 怪物 + 巡逻点(开局就在本关巡逻,见 M1a 的 P0 返工)──────────
    # ⚠ M1b:巡逻近端(甲)从舞台那头**收回门洞内侧**(0,-9.5)。原来那条线
    #   从舞台一直拉到门外,正好把房间中线切成一条"看脸走廊"(乐谱2 就
    #   躺在上面)。现在它只在南侧门洞一带踱步,舞台那半间屋子靠『声音
    #   诱饵』才引得进去 —— 第 3 章的正门玩法还是诱饵。
    enemy = make_box(MUSIC_ENEMY, MONSTER_BOX, (0.0, -13.5, 0.9),
                     (0.55, 0.10, 0.12, 1.0), scene=scene)
    dress_monster(enemy, scene, MONSTER_BOX)
    make_marker(MUSIC_PATROL_A, (0.0, -9.5, 0.0), scene, size=0.8)
    make_marker(MUSIC_PATROL_B, (0.0, -14.5, 0.0), scene, size=0.8)
    # ── 吊灯 / 横梁 / 壁灯(照抄走廊那一套)────────────────────────
    for index, x in enumerate((-7.0, 7.0)):
        decor(no_shadow(make_box(f"音乐室横梁{index + 1}", (0.45, 24.0, 0.32),
                                 (x, 0.0, 4.60), (0.14, 0.14, 0.18, 1.0),
                                 scene=scene)))
        make_pendant(f"音乐室吊灯{index + 1}", x, 4.60, scene, y=0.0,
                     color=MUSIC_PURPLE, glow_color=MUSIC_GLOW)
    make_sconce("音乐室壁灯1", (-13.0, -6.0, 2.60), scene, axis="x", facing=1)
    make_sconce("音乐室壁灯2", (13.0, 2.0, 2.60), scene, axis="x", facing=-1)
    # ── 玩家 + 两台相机(名字三关统一,角色图才找得到)───────────────
    player = make_box(MUSIC_PLAYER, (1.0, 1.0, 1.6), (safe_x, safe_y, 0.9),
                      PLAYER_COLOR, scene=scene)
    dress_player(player, scene)
    side = reuse_camera(scene, SIDE_CAM, (0.0, -16.0, 6.0),
                        (math.radians(72.0), 0.0, 0.0), lens=21.0)
    reuse_camera(scene, TOP_CAM, (0.0, -6.0, 18.0),
                 (math.radians(22.0), 0.0, 0.0), lens=18.0)
    scene.camera = side
    # ── 灯:冷月光补形 / 舞台灯(亮着 = 磁带拿不了)/ 警报灯 ─────────
    make_light("音乐室月光", "SUN", (-6.0, 6.0, 9.0), 0.32,
               (0.58, 0.68, 0.95), scene,
               rotation=(math.radians(22.0), 0.0, math.radians(-34.0)))
    make_aimed_light(STAGE_LIGHT, (0.0, 7.0, 4.5), (0.0, 8.0, 1.0), 2600.0,
                     (1.0, 0.95, 0.82), scene, spot_size=58.0, cutoff=16.0,
                     shadow=True)
    make_light(ALARM_LIGHT, "POINT", (11.5, -6.0, 3.2), 90.0,
               (1.0, 0.26, 0.20), scene, cutoff=9.0)
    # 判定体一律不画(否则地上是一块亮蓝 / 绿色贴片,实机踩过)。
    for plate in (stage, tape, med_plate, safe_area):
        physics = getattr(plate, "gk_physics", None)
        if physics is not None:
            physics.hide_mesh = True
    log(f"{MUSIC}:地面 + 四面墙(南墙留门洞)+ {STAGE} + 钢琴 + 3 张乐谱 + "
        f"{RECORDER} + {TAPE} + 侧门({TO_STAIRS}) + {MUSIC_SAFE_AREA} + "
        f"{MUSIC_ENEMY} + {STAGE_LIGHT} / {ALARM_LIGHT} + 两台相机")
    return scene, player, enemy, sheets, recorder, tape


def build_music_ai_tree():
    """音乐教室的怪物 AI:巡逻 ←→ 追击 + 听见动静去查看。

    和走廊那张 AI 图**同一个结构**,只是巡逻点 / 追击目标换成本关的:
      * 巡逻点在**房间里面** —— 错满两次把它放进来了,它才走得到;
      * 追击目标是『音乐教室玩家』;
      * 对外仍然只有 is_chasing / is_investigating 两个布尔。
    ⚠ 巡逻点**不能**叫『巡逻点甲』:Blender 的物体名全局唯一,走廊那两个
      先占了名字,这里再建就会变成『巡逻点甲.001』,AI 图按名字就找不到。
    """
    tree = bpy.data.node_groups.new(MUSIC_AI_TREE_NAME, "GKGameAiTree")
    tree.use_fake_user = True
    patrol = _add(tree, "GKNodeAiState", "巡逻", -60.0, 0.0,
                  gk_state_name="巡逻", gk_behavior="PATROL",
                  gk_patrol_a=MUSIC_PATROL_A, gk_patrol_b=MUSIC_PATROL_B,
                  gk_speed=PATROL_SPEED, gk_stop_distance=0.7,
                  gk_facing="MOVE", gk_in_count=3)
    chase = _add(tree, "GKNodeAiState", "追击", 700.0, 0.0,
                 gk_state_name="追击", gk_behavior="CHASE",
                 gk_goal=MUSIC_PLAYER, gk_speed=CHASE_SPEED,
                 gk_stop_distance=CHASE_STOP, gk_facing="GOAL")
    look = _add(tree, "GKNodeAiState", "去查看", 700.0, 340.0,
                gk_state_name="去查看", gk_behavior="INVESTIGATE",
                gk_speed=LOOK_SPEED, gk_stop_distance=MUSIC_LOOK_STOP,
                gk_facing="MOVE")
    see = _add(tree, "GKNodeAiTransition", "看见玩家", 320.0, 180.0,
               gk_cond_source="SEE", gk_goal=MUSIC_PLAYER,
               gk_sight=MUSIC_SIGHT)
    back = _add(tree, "GKNodeAiTransition", "丢失玩家", 320.0, -210.0,
                gk_cond_source="LOST", gk_goal=MUSIC_PLAYER,
                gk_sight=MUSIC_LOST)
    heard = _add(tree, "GKNodeAiTransition", "听见动静", 320.0, 340.0,
                 gk_cond_source="HEARD", gk_ear=0.0, gk_memory=EAR_MEMORY)
    calm = _add(tree, "GKNodeAiTransition", "安静下来", 700.0, 480.0,
                gk_cond_source="CALM", gk_ear=0.0, gk_memory=EAR_MEMORY)
    start = _add(tree, "GKNodeAiStart", "AI_开始", -360.0, 0.0)
    _link(tree, start, "开始", patrol, "进入")
    _link(tree, patrol, "转到", see, "上游")
    _link(tree, see, "转到", chase, "进入")
    _link(tree, chase, "转到", back, "上游")
    _link(tree, back, "转到", patrol, "进入 2")
    _link(tree, patrol, "转到", heard, "上游")
    _link(tree, heard, "转到", look, "进入")
    _link(tree, look, "转到", calm, "上游")
    _link(tree, calm, "转到", patrol, "进入 3")
    _add(tree, "GKNodeAiOutput", "追击输出", 1100.0, -80.0,
         gk_state_name="追击", gk_var="is_chasing")
    _add(tree, "GKNodeAiOutput", "查看输出", 1100.0, 120.0,
         gk_state_name="去查看", gk_var="is_investigating")
    log(f"AI 图『{tree.name}』:巡逻({MUSIC_PATROL_A} / {MUSIC_PATROL_B}) "
        f"--看见({MUSIC_SIGHT:g})--> 追击({MUSIC_PLAYER});听见动静 -> 去查看")
    return tree


# ==========================================================================
# 第 4 章 医务室(M1b)—— 潜行 + 资源
# ==========================================================================
# 设计卡:docs/恐怖示例_1-2小时设计.md 第十七节。一句话:
#   「灯只剩一点电,而它就在帘子外面走。」
# 流程:标题 → 教室 → 走廊 → 音乐教室 → **医务室** → 楼梯间(结局)。
#   * 音乐教室的出口从『去楼梯间』改名 **『去医务室』**;
#   * 医务室自己的出口才叫『去楼梯间』(楼梯间一行都不改)。
# 玩法:进关手电只有 60;两节电池各 +40;电量 0 灯灭(只剩窗光);
#       病历1/2/3 集齐才开侧门;躲藏1/2(病床帘)按 E 进出,躲进去怪看不见你。
def _interact_zone(logic, name, tip_text, y, tip_widget, player_name,
                   e_branch):
    """E 交互的公共骨架:靠近报名字 / 走开按需清。

    走开那一段**必须**判"目标还是我才清":从一个触发区直接踏进另一个时,
    exit / enter 落在同一物理帧,谁先谁后不确定(踩过)。
    """
    name_const = _add(logic, "GKNodeConstText", f"名字:{name}", -2100.0,
                      y - 130.0, gk_value=name)
    empty_const = _add(logic, "GKNodeConstText", f"空名字:{name}",
                       -2100.0, y - 250.0, gk_value="")
    entered = _add(logic, "GKNodeOnAreaEntered", f"靠近{name}", -1900.0, y,
                   gk_area=name, gk_filter=player_name)
    target_on = _add(logic, "GKNodeSetVar", f"目标={name}", -1700.0, y,
                     gk_var_name=INTERACT_TARGET)
    tip_on = _add(logic, "GKNodeUiSet", f"提示[{name}]", -1500.0, y,
                  gk_widget=tip_widget, gk_set_text=True, gk_text=tip_text)
    _link(logic, entered, "执行", target_on, "执行")
    _link(logic, name_const, "值", target_on, "值")
    _link(logic, target_on, "完成", tip_on, "执行")
    left = _add(logic, "GKNodeOnAreaExited", f"离开{name}", -2100.0,
                y - 420.0, gk_area=name, gk_filter=player_name)
    off_read = _add(logic, "GKNodeGetVar", f"读目标(离开{name})", -2100.0,
                    y - 540.0, gk_var_name=INTERACT_TARGET)
    off_cmp = _add(logic, "GKNodeCompare", f"离开时目标是{name}吗", -1900.0,
                   y - 540.0, gk_op="EQ")
    off_branch = _add(logic, "GKNodeBranch", f"要清{name}吗", -1700.0,
                      y - 420.0)
    target_off = _add(logic, "GKNodeSetVar", f"清目标({name})", -1500.0,
                      y - 420.0, gk_var_name=INTERACT_TARGET)
    tip_off = _add(logic, "GKNodeUiSet", f"清提示({name})", -1300.0,
                   y - 420.0, gk_widget=tip_widget, gk_set_text=True,
                   gk_text="")
    _link(logic, off_read, "值", off_cmp, "A")
    _link(logic, name_const, "值", off_cmp, "B")
    _link(logic, off_cmp, "结果", off_branch, "条件")
    _link(logic, left, "执行", off_branch, "执行")
    _link(logic, off_branch, "是", target_off, "执行")
    _link(logic, empty_const, "值", target_off, "值")
    _link(logic, target_off, "完成", tip_off, "执行")
    nodes = [name_const, empty_const, entered, target_on, tip_on, left,
             off_read, off_cmp, off_branch, target_off, tip_off]
    return name_const, nodes


def _interact_pick(logic, name, name_const, source, source_pin, y):
    """E 链上的一段:『交互目标』== name 吗(是 -> 调用方接着挂)。

    source/source_pin = 这条支链挂在哪儿(医务室的 E 链挂在『躲藏中吗』的
    否出口上,所以不能写死 e_branch 的『是』)。
    """
    read = _add(logic, "GKNodeGetVar", f"读目标:{name}", -1700.0, y,
                gk_var_name=INTERACT_TARGET)
    cmp = _add(logic, "GKNodeCompare", f"看着{name}吗", -1500.0, y - 60.0,
               gk_op="EQ")
    branch = _add(logic, "GKNodeBranch", f"是{name}吗", -1350.0, y)
    _link(logic, read, "值", cmp, "A")
    _link(logic, name_const, "值", cmp, "B")
    _link(logic, cmp, "结果", branch, "条件")
    _link(logic, source, source_pin, branch, "执行")
    return [read, cmp, branch], branch


def build_med_room():
    """第 4 章 医务室:病床 + 窗帘 + 药柜 + 办公桌 + 上锁侧门。

    布局(Blender x/y;Godot z = -y):
        y=+8 北墙(两扇窗 = 电量见底时唯一的光)/ x=-10 西墙 / x=+10 东墙(侧门)
        病床帘两个(躲藏1/躲藏2)在西侧和北侧,药柜在东侧,病历散在桌上。
    """
    scene = bpy.data.scenes.new(MED)
    make_box("医务室地面", (20.0, 16.0, 0.4), (0.0, 0.0, -0.2),
             (0.09, 0.10, 0.12, 1.0), scene=scene)
    wall_color = (0.14, 0.15, 0.18, 1.0)
    make_wall_pieces("医务室北墙", "x", 8.0, (-10.0, 10.0), WALL_THICK,
                     WALL_HEIGHT, scene, [(-4.0, 3.0), (4.0, 3.0)],
                     wall_color)
    for index, center in enumerate((-4.0, 4.0)):
        make_window(f"医务室窗{index + 1}", center, 8.0, 3.0, scene,
                    axis="x", color=(0.22, 0.21, 0.20, 1.0))
    make_trim("医务室北墙", "x", 8.0, (-10.0, 10.0), -1, scene,
              (0.18, 0.17, 0.16, 1.0))
    make_wall_pieces("医务室南墙", "x", -8.0, (-10.0, 10.0), WALL_THICK,
                     WALL_HEIGHT, scene, [], wall_color)
    make_trim("医务室南墙", "x", -8.0, (-10.0, 10.0), 1, scene,
              (0.18, 0.17, 0.16, 1.0))
    make_wall_pieces("医务室西墙", "y", -10.0, (-8.0, 8.0), WALL_THICK,
                     WALL_HEIGHT, scene, [], wall_color)
    make_trim("医务室西墙", "y", -10.0, (-8.0, 8.0), 1, scene,
              (0.18, 0.17, 0.16, 1.0))
    make_wall_pieces("医务室东墙", "y", 10.0, (-8.0, 8.0), WALL_THICK,
                     WALL_HEIGHT, scene, [], wall_color)
    make_trim("医务室东墙", "y", 10.0, (-8.0, 8.0), -1, scene,
              (0.18, 0.17, 0.16, 1.0))
    # ── 上锁的侧门(东墙)= 『去楼梯间』 ────────────────────────────
    decor(make_box("医务室侧门", (0.3, 2.4, 2.6), (9.6, -5.0, 1.3),
                   (0.28, 0.22, 0.16, 1.0), scene=scene))
    decor(make_box("医务室侧门楣", (0.36, 3.0, 0.3), (9.6, -5.0, 2.75),
                   (0.24, 0.20, 0.17, 1.0), scene=scene))
    for index, y in enumerate((-6.5, -3.5)):
        decor(make_box(f"医务室侧门框柱{index + 1}", (0.36, 0.3, 3.0),
                       (9.6, y, 1.5), (0.24, 0.20, 0.17, 1.0), scene=scene))
    stairs_plate = make_box(TO_STAIRS, (3.5, 3.5, 0.2), (8.0, -5.0, 0.1),
                            (0.22, 0.32, 0.30, 1.0), scene=scene)
    # ── 两张病床 + 病床帘(躲藏1/躲藏2)───────────────────────────
    hide_plates = []
    for index, (x, y) in enumerate(MED_HIDE_SPOTS):
        decor(solid_box(make_box(f"医务室病床{index + 1}", (2.0, 3.0, 0.55),
                                 (x, y, 0.27), (0.20, 0.20, 0.22, 1.0),
                                 scene=scene)))
        decor(no_shadow(make_box(f"医务室帘子{index + 1}",
                                 (2.4, 0.06, 2.2), (x, y - 1.7, 1.7),
                                 (0.46, 0.48, 0.44, 1.0), scene=scene)))
        hide_plates.append(make_box(MED_HIDES[index], (2.4, 3.2, 2.2),
                                    (x, y, 1.1),
                                    (0.30, 0.34, 0.30, 1.0), scene=scene))
    # ── 药柜(电池)+ 办公桌(病历)─────────────────────────────────
    # 隐藏笔记(第一百六十九轮):南墙边,值班桌下 —— 黑暗里一点微光
    _hidden_note(scene, HIDDEN_NOTE_NAMES[MED], -5.0, -6.0)

    # ── 隔断墙(第一百七十二轮):大开间切成"黑暗迷宫"────────────────
    # 设计文档第 4 章的"黑暗迷宫"一直没做 —— 大开间一眼望穿,摸黑探索
    # 名存实亡。三面全高隔断把房间切成四个隔间(西病床 / 西南档案间 /
    # 中南 / 东南),手电光被挡,病历/电池/笔记分散在各隔间里。
    # ⚠ 每面墙都绕开了**巡逻线**(甲 (-5,3)→乙 (5,-5),x=0 处 y=-1)和
    #   全部判定区(判定体嵌墙 = body_entered 永远不触发,第 168 轮):
    #   墙 A x=0 处巡逻线 y=0.6 < 5;墙 B y=-2.0 处巡逻线 x=1.25 > -6.5;
    #   墙 C y=-4.8 端点离巡逻线(x=4, y=-4.2)0.6 米。
    med_part = (0.13, 0.14, 0.17, 1.0)
    # 墙 A(西北隔断,竖):隔出电池 2 的角落,门洞 y 6.2..7.2
    solid_box(make_box("医务室隔断A1", (0.3, 1.2, 3.0), (-2.0, 5.6, 1.5),
                       med_part, scene=scene))
    solid_box(make_box("医务室隔断A2", (0.3, 0.8, 3.0), (-2.0, 7.6, 1.5),
                       med_part, scene=scene))
    # 墙 B(西南档案间,横):病历 1 + 隐藏笔记 + 安全屋在里面,
    # 入口 x -6.5..-5(宽 1.5 米,黑灯瞎火里容易走过头)
    solid_box(make_box("医务室隔断B", (3.5, 0.3, 3.0), (-8.25, -2.0, 1.5),
                       med_part, scene=scene))
    # 墙 C(中南/东南隔断,竖):去侧门必须绕北端,而北端就贴着巡逻线
    solid_box(make_box("医务室隔断C", (0.3, 3.2, 3.0), (4.0, -6.4, 1.5),
                       med_part, scene=scene))
    # ── 惊吓演出(第一百七十五轮):档案间门洞"帘子后有人"────────────
    # 玩家从档案间出来的必经之路,一次性惊吓(复用走廊的『GKNodeScare』
    # 红闪 + sting + 总线压低)。位置盖住墙 B 的门洞(x -6.5..-5),
    # 避开躲藏 1 判定体(y 1.4..4.6)与巡逻线(x=-5.5 处 y=3.4)。
    # ⚠ 高 1.6(不是走廊那种 0.2 薄板):探针/玩家瞬移落点是原点 +1 米,
    #   薄板要等落地才重叠(0.5 秒),加高后**瞬移当帧**就触发。
    scare_zone = make_box(MED_SCARE_AREA, (2.6, 1.6, 1.6), (-5.5, -0.8, 0.8),
                          (0.30, 0.10, 0.12, 1.0), scene=scene)
    scare_physics = getattr(scare_zone, "gk_physics", None)
    if scare_physics is not None:
        scare_physics.hide_mesh = True
    # 俯瞰机位(第一百八十一次):开场运镜的专用特写相机 ——
    # ⚠ 『相机_运镜』不选『特写相机』会和弹簧臂抢同一台当前相机
    #   (codegen 自己都警告;实测玩家被顶着不走、探针全乱)。
    reuse_camera(scene, TOP_CAM, (0.0, 2.0, 16.0),
                 (math.radians(10.0), 0.0, 0.0), lens=20.0)
    decor(solid_box(make_box("医务室药柜", (1.6, 4.0, 2.2), (8.4, 3.0, 1.1),
                             (0.16, 0.17, 0.16, 1.0), scene=scene)))
    decor(solid_box(make_box("医务室办公桌", (2.6, 1.2, 0.9),
                             (0.0, -6.0, 0.45),
                             (0.16, 0.14, 0.12, 1.0), scene=scene)))
    sheets = []
    for index, name in enumerate(MED_SHEETS):
        x, y = MED_SHEET_SPOTS[index]
        decor(no_shadow(make_box(f"医务室文件架{index + 1}", (0.7, 0.5, 1.0),
                                 (x, y, 0.55),
                                 (0.16, 0.15, 0.14, 1.0), scene=scene)))
        sheets.append(make_box(name, (1.0, 0.8, 0.5), (x, y, 1.25),
                               (0.92, 0.90, 0.74, 1.0), scene=scene))
    batteries = []
    for index, name in enumerate(MED_BATTERIES):
        x, y = MED_BATTERY_SPOTS[index]
        decor(no_shadow(make_box(f"医务室药架{index + 1}", (1.2, 0.5, 0.9),
                                 (x, y, 0.5), (0.16, 0.16, 0.15, 1.0),
                                 scene=scene)))
        batteries.append(make_box(name, (0.6, 0.5, 0.4), (x, y, 1.15),
                                  (0.85, 0.72, 0.30, 1.0), scene=scene,
                                  glow=((0.9, 0.75, 0.3, 1.0), 0.4)))
    # ── 安全屋(本关入口)+ 躲藏锚点(躲进去时身体挪到这儿,视距外)────
    safe_x, safe_y = MED_SAFE_POS
    make_marker(MED_SAFE_ROOM, (safe_x, safe_y, 0.0), scene, size=1.4)
    safe_area = make_box(MED_SAFE_AREA, (MED_SAFE_SIZE[0], MED_SAFE_SIZE[1],
                                         0.2), (safe_x, safe_y, 0.1),
                         (0.30, 0.34, 0.28, 1.0), scene=scene)
    make_light("医务室安全屋灯", "POINT", (safe_x, safe_y, 2.6), 420.0,
               (1.0, 0.88, 0.62), scene, cutoff=8.0)
    make_marker(MED_HIDE_ANCHOR, MED_HIDE_ANCHOR_POS + (0.9,), scene,
                size=0.6)
    # ── 怪 + 巡逻点(视线只有 6 米:黑里它比走廊那只吃亏)───────────
    enemy = make_box(MED_ENEMY, MONSTER_BOX, (-5.0, 3.0, 0.9),
                     (0.55, 0.10, 0.12, 1.0), scene=scene)
    dress_monster(enemy, scene, MONSTER_BOX)
    make_marker(MED_PATROL_A, MED_PATROL_POS[0] + (0.0,), scene, size=0.8)
    make_marker(MED_PATROL_B, MED_PATROL_POS[1] + (0.0,), scene, size=0.8)
    # ── 玩家 + 三台相机(两名机位三关共用,躲藏机位是本关独有的)──────
    player = make_box(MED_PLAYER, (1.0, 1.0, 1.6), (safe_x, safe_y, 0.9),
                      PLAYER_COLOR, scene=scene)
    dress_player(player, scene)
    side = reuse_camera(scene, SIDE_CAM, (0.0, -16.0, 6.0),
                        (math.radians(72.0), 0.0, 0.0), lens=21.0)
    reuse_camera(scene, TOP_CAM, (0.0, -6.0, 18.0),
                 (math.radians(22.0), 0.0, 0.0), lens=18.0)
    make_camera(MED_HIDE_CAM, MED_HIDE_CAM_POS,
                (math.radians(86.0), 0.0, 0.0), scene, lens=26.0)
    scene.camera = side
    # ── 灯:冷月光(窗光)/ 一盏很暗的吊灯 / 手电筒 ──────────────────
    make_light("医务室月光", "SUN", (-4.0, 6.0, 9.0), 0.30,
               (0.58, 0.68, 0.95), scene,
               rotation=(math.radians(24.0), 0.0, math.radians(-30.0)))
    make_pendant("医务室吊灯1", 0.0, 4.60, scene, energy=900.0, cutoff=10.0,
                 y=0.0, color=MED_PALE, glow_color=MED_GLOW)
    torch = make_light(MED_TORCH, "SPOT", (safe_x, safe_y, 1.6), 480.0,
                       (1.0, 0.96, 0.85), scene,
                       rotation=(math.radians(80.0), 0.0, 0.0),
                       spot_size=math.radians(60.0), cutoff=20.0,
                       shadow=True)
    torch.data.spot_blend = 0.55
    torch.data.use_custom_distance = True
    torch.data.cutoff_distance = 20.0
    for plate in [stairs_plate, safe_area] + hide_plates:
        physics = getattr(plate, "gk_physics", None)
        if physics is not None:
            physics.hide_mesh = True
    log(f"{MED}:地面 + 四面墙(两扇窗)+ 两张病床帘 + 药柜/电池 + 办公桌/病历 + "
        f"侧门({TO_STAIRS}) + {MED_SAFE_AREA} + {MED_ENEMY} + {MED_TORCH} + "
        f"三台相机")
    return scene, player, enemy, torch


def build_med_ai_tree():
    """医务室的怪 AI:巡逻 ←→ 追击 + 听见动静去查看。

    和走廊 / 音乐教室同一个结构,差别只在**视线 6 米**(走廊 9 / 音乐教室 8):
    这一关黑得只能靠手电,怪在暗处更吃亏,玩家才有"摸黑"的空间。
    """
    tree = bpy.data.node_groups.new(MED_AI_TREE_NAME, "GKGameAiTree")
    tree.use_fake_user = True
    patrol = _add(tree, "GKNodeAiState", "巡逻", -60.0, 0.0,
                  gk_state_name="巡逻", gk_behavior="PATROL",
                  gk_patrol_a=MED_PATROL_A, gk_patrol_b=MED_PATROL_B,
                  gk_speed=PATROL_SPEED * 0.8, gk_stop_distance=0.7,
                  gk_facing="MOVE", gk_in_count=3)
    chase = _add(tree, "GKNodeAiState", "追击", 700.0, 0.0,
                 gk_state_name="追击", gk_behavior="CHASE",
                 gk_goal=MED_PLAYER, gk_speed=CHASE_SPEED * 0.85,
                 gk_stop_distance=CHASE_STOP, gk_facing="GOAL")
    look = _add(tree, "GKNodeAiState", "去查看", 700.0, 340.0,
                gk_state_name="去查看", gk_behavior="INVESTIGATE",
                gk_speed=LOOK_SPEED * 0.85, gk_stop_distance=MED_LOOK_STOP,
                gk_facing="MOVE")
    see = _add(tree, "GKNodeAiTransition", "看见玩家", 320.0, 180.0,
               gk_cond_source="SEE", gk_goal=MED_PLAYER,
               gk_sight=MED_SIGHT)
    back = _add(tree, "GKNodeAiTransition", "丢失玩家", 320.0, -210.0,
                gk_cond_source="LOST", gk_goal=MED_PLAYER,
                gk_sight=MED_LOST)
    heard = _add(tree, "GKNodeAiTransition", "听见动静", 320.0, 340.0,
                 gk_cond_source="HEARD", gk_ear=0.0, gk_memory=EAR_MEMORY)
    calm = _add(tree, "GKNodeAiTransition", "安静下来", 700.0, 480.0,
                gk_cond_source="CALM", gk_ear=0.0, gk_memory=EAR_MEMORY)
    start = _add(tree, "GKNodeAiStart", "AI_开始", -360.0, 0.0)
    _link(tree, start, "开始", patrol, "进入")
    _link(tree, patrol, "转到", see, "上游")
    _link(tree, see, "转到", chase, "进入")
    _link(tree, chase, "转到", back, "上游")
    _link(tree, back, "转到", patrol, "进入 2")
    _link(tree, patrol, "转到", heard, "上游")
    _link(tree, heard, "转到", look, "进入")
    _link(tree, look, "转到", calm, "上游")
    _link(tree, calm, "转到", patrol, "进入 3")
    _add(tree, "GKNodeAiOutput", "追击输出", 1100.0, -80.0,
         gk_state_name="追击", gk_var="is_chasing")
    _add(tree, "GKNodeAiOutput", "查看输出", 1100.0, 120.0,
         gk_state_name="去查看", gk_var="is_investigating")
    log(f"AI 图『{tree.name}』:巡逻({MED_PATROL_A} / {MED_PATROL_B}) "
        f"--看见({MED_SIGHT:g})--> 追击({MED_PLAYER});听见动静 -> 去查看")
    return tree


def build_med_logic(scene, enemy, ai_tree):
    """第 4 章 · 关卡图:手电电量 / 病历 / 电池 / 躲藏 / 被抓回安全屋。

    ── 和走廊那张图的差别 ────────────────────────────────────────────
      * 手电**进关只有 60**(走廊 100),两节电池各 +40;
      * 多了『躲藏』:病床帘按 E 进去(相机切走 + 人隐藏 + 身体挪到视距外),
        再按 E 出来 —— 全用现有卡片拼,没有新节点;
      * 怪开局就在本关巡逻(视线 6 米),被抓回安全屋时病历/电池/电量都不丢。
    """
    logic = _level_tree(MED_LEVEL_TREE)
    _set_scene_logic(scene, logic)

    # ── ① 开局 ────────────────────────────────────────────────────
    ready = _add(logic, "GKNodeOnReady", "每当_准备", -1900.0, 1800.0)
    reset_bus = _add(logic, "GKNodeBusVolume", "总线回到 0dB", -1700.0, 1800.0,
                     gk_bus="Master", gk_db=0.0, gk_fade=0.0)
    hint = _add(logic, "GKNodeUiText", MED_HINT, -1500.0, 1800.0,
                gk_text=MED_HINT_TEXTS[0], gk_x=18.0, gk_y=18.0, gk_size=18)
    bgm = _add(logic, "GKNodePlayMusic", "医务室BGM", -1300.0, 1800.0,
               gk_path=BGM_FILE, gk_volume=-22.0, gk_fade=1.5)
    vignette = _add(logic, "GKNodeScreenVignette", "医务室暗角", -1100.0, 1800.0,
                    gk_amount=0.55, gk_radius=0.55, gk_edge=0.35)
    interact_tip = _add(logic, "GKNodeUiText", INTERACT_TIP, -900.0, 1800.0,
                        gk_text="", gk_align="BOTTOM_CENTER", gk_margin=190.0,
                        gk_size=22, gk_color=(1.0, 0.95, 0.70, 1.0))
    _link(logic, ready, "执行", reset_bus, "执行")
    _link(logic, reset_bus, "完成", hint, "执行")
    _link(logic, hint, "完成", bgm, "执行")
    # 开场运镜(第一百八十一次):推近 医务室怪物 -> 停留 -> 回玩家,
    # 和章节卡叠着演(4.4 秒 ≈ 章节卡 4.5 秒),开场即入戏。
    shot_med = _add(logic, "GKNodeCameraShot", "开场运镜(医务室)",
                 -900.0, 1800.0,
                 gk_camera=TOP_CAM,
                 gk_look_target="医务室怪物", gk_time=2.2,
                 gk_hold=1.2, gk_return_time=1.0, gk_fov_to=38.0)
    _link(logic, bgm, "完成", shot_med, "执行")
    _link(logic, shot_med, "完成", vignette, "执行")
    _link(logic, vignette, "完成", interact_tip, "执行")
    battery_hud = _add(logic, "GKNodeUiText", "电量文本", -700.0, 1800.0,
                       gk_text=f"手电电量:{int(BATTERY_START_MED)}",
                       gk_x=18.0, gk_y=74.0, gk_size=18,
                       gk_color=(0.60, 0.85, 1.0, 1.0))
    _link(logic, interact_tip, "完成", battery_hud, "执行")

    init_specs = (
        (MED_COUNT, "GKNodeConstFloat", 0.0, "病历数=0"),
        (MED_BATTERY_COUNT, "GKNodeConstFloat", 0.0, "电池数=0"),
        (HIDING, "GKNodeConstBool", False, "没躲着"),
        (HIDE_SPOT_VAR, "GKNodeConstText", "", "躲藏点=空"),
        (BATTERY, "GKNodeConstFloat", BATTERY_START_MED, "电量=60"),
        (TORCH_ON, "GKNodeConstBool", True, "手电开着"),
        (INTERACT_TARGET, "GKNodeConstText", "", "交互目标=空"),
        (CHECKPOINT, "GKNodeConstText", MED_CHECKPOINT, "检查点=本关"),
        (SAFE_VISITED, "GKNodeConstBool", False, "没进过安全屋"),
    )
    init_nodes = []
    previous = battery_hud
    for index, (var_name, kind, value, label) in enumerate(init_specs):
        const = _add(logic, kind, f"初值:{label}", -700.0,
                     1560.0 - index * 120.0, gk_value=value)
        node = _add(logic, "GKNodeSetVar", f"开机:{label}", -500.0,
                    1640.0 - index * 120.0, gk_var_name=var_name)
        _link(logic, const, "值", node, "值")
        _link(logic, previous, "完成", node, "执行")
        previous = node
        init_nodes += [const, node]

    load = _add(logic, "GKNodeSaveLoad", "载入存档(医务室)", -300.0, 1700.0,
                gk_slot="save1")
    read_hidden = _add(logic, "GKNodeSaveGet", "读回隐藏笔记数", -100.0, 1700.0,
                       gk_key=HIDDEN_NOTE_COUNT, gk_var=HIDDEN_NOTE_COUNT,
                       gk_default=0.0)
    archive_tail = _archive_zero_vars(logic, previous, -700.0, 1450.0)
    _link(logic, previous, "完成", archive_tail, "执行")
    _link(logic, archive_tail, "完成", load, "执行")
    _link(logic, load, "完成", read_hidden, "执行")
    log_panel, log_lines, journal_ui = _build_journal_ui(logic, -2600.0, 2800.0)
    restore_entry, restore_seq, restore_pin, restore_nodes = _journal_restore(
        logic, -2600.0, 2400.0, log_lines)
    journal_toggle_nodes = _journal_toggle(logic, -2600.0, 1100.0,
                                          log_panel.name, "医务室")
    journal_close_nodes, journal_close_tail = _journal_close(
        logic, -1900.0, 2100.0, "医务室")
    ai_on = _add(logic, "GKNodeAiEnable", "启动怪物AI", -100.0, 1700.0,
                 gk_tree_name=ai_tree.name, gk_target=MED_ENEMY,
                 gk_debug_print=False)
    crouch_script = _add_crouch_script(logic, 100.0, 1700.0, "蹲走脚本(医务)")
    polish = _add_ui_polish(logic, 300.0, 1700.0)
    _link(logic, read_hidden, "完成", journal_close_tail, "执行")
    _link(logic, journal_close_tail, "完成", journal_ui[0], "执行")
    _link(logic, journal_ui[-1], "完成", restore_entry, "执行")
    _link(logic, restore_seq, restore_pin, ai_on, "执行")
    _link(logic, ai_on, "完成", crouch_script, "执行")
    _link(logic, crouch_script, "完成", polish, "执行")
    _frame(logic, "① 开局 / 载入", [ready, reset_bus, hint, bgm, vignette,
                                    interact_tip, battery_hud] + init_nodes +
           [load] + journal_ui + journal_close_nodes + restore_nodes +
           [ai_on, crouch_script, polish], (0.18, 0.22, 0.34))

    # ── ② 病历 HUD(定时读变量,和纸条 HUD 同一个套路)───────────────
    hud_timer = _add(logic, "GKNodeOnTimer", "病历刷新", -2200.0, 1000.0,
                     gk_seconds=0.2, gk_repeat=True)
    hud_read = _add(logic, "GKNodeGetVar", "读病历数", -2200.0, 860.0,
                    gk_var_name=MED_COUNT)
    hud_num = _add(logic, "GKNodeNumberText", "病历数字", -2000.0, 860.0)
    hud_join = _add(logic, "GKNodeTextJoin", "病历N/3", -1800.0, 860.0,
                    gk_middle="/", gk_b=str(MED_DOOR_GOAL))
    hud_label = _add(logic, "GKNodeTextJoin", "病历前缀", -1600.0, 860.0,
                     gk_a="病历 ")
    hud = _add(logic, "GKNodeUiText", "病历HUD", -1400.0, 1000.0,
               gk_text=f"病历 0/{MED_DOOR_GOAL}", gk_x=18.0, gk_y=46.0,
               gk_size=18, gk_color=(0.95, 0.90, 0.70, 1.0))
    _link(logic, hud_read, "值", hud_num, "数值")
    _link(logic, hud_num, "文字", hud_join, "甲")
    _link(logic, hud_join, "结果", hud_label, "乙")
    _link(logic, hud_label, "结果", hud, "文字")
    _link(logic, hud_timer, "执行", hud, "执行")
    _frame(logic, "② 病历 HUD", [hud_timer, hud_read, hud_num, hud_join,
                                 hud_label, hud], (0.20, 0.30, 0.24))

    # ── ③ 目标行(两段:先找病历 / 病历齐了去开侧门)────────────────
    guide_timer = _add(logic, "GKNodeOnTimer", "目标刷新", -900.0, 900.0,
                       gk_seconds=0.25, gk_repeat=True)
    guide_read = _add(logic, "GKNodeGetVar", "读病历数(目标)", -900.0, 760.0,
                      gk_var_name=MED_COUNT)
    guide_cmp = _add(logic, "GKNodeCompare", "病历齐了吗", -700.0, 760.0,
                     gk_op="GE", gk_b=str(MED_DOOR_GOAL))
    guide_branch = _add(logic, "GKNodeBranch", "目标:开侧门", -500.0, 900.0)
    guide_done = _add(logic, "GKNodeUiSet", "目标:侧门", -300.0, 900.0,
                      gk_widget=hint.name, gk_set_text=True,
                      gk_text=MED_HINT_TEXTS[1])
    guide_start = _add(logic, "GKNodeUiSet", "目标:找病历", -100.0, 900.0,
                       gk_widget=hint.name, gk_set_text=True,
                       gk_text=MED_HINT_TEXTS[0])
    _link(logic, guide_timer, "执行", guide_branch, "执行")
    _link(logic, guide_read, "值", guide_cmp, "A")
    _link(logic, guide_cmp, "结果", guide_branch, "条件")
    _link(logic, guide_branch, "是", guide_done, "执行")
    _link(logic, guide_branch, "否", guide_start, "执行")
    _frame(logic, "③ 目标行", [guide_timer, guide_read, guide_cmp,
                               guide_branch, guide_done, guide_start],
           (0.20, 0.26, 0.30))

    # ── ④ 手电:F 开关 + 每秒耗电 + 没电自动灭 + 低电闪烁 ─────────────
    #    原来这段是照抄走廊的内联副本(设计文档 17.7 技术债),第一百八十六
    #    轮抽成 _torch_chain():两关共用一条链,新增的低电闪烁两关同享。
    _torch_chain(logic, -1500.0, -1250.0, MED_TORCH, battery_hud.name,
                 "手电没电了 —— 摸黑走吧", frame_title="④ 手电 / 电量")

    # ── ⑤ E 交互骨架:躲着的时候 E = 出来,否则走常规交互 ────────────
    phys_e = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(E)", -2100.0,
                  -2600.0)
    e_key = _add(logic, "GKNodeKey", "按E", -2100.0, -2740.0, gk_key=E_KEY,
                 gk_action=E_ACTION, gk_trigger="JUST")
    e_branch = _add(logic, "GKNodeBranch", "按E了吗", -1900.0, -2600.0)
    _link(logic, e_key, "结果", e_branch, "条件")
    _link(logic, phys_e, "执行", e_branch, "执行")
    hiding_get = _add(logic, "GKNodeGetVar", "躲着吗", -1900.0, -2880.0,
                      gk_var_name=HIDING)
    hiding_branch = _add(logic, "GKNodeBranch", "在帘子里吗", -1700.0,
                         -2600.0)
    _link(logic, hiding_get, "值", hiding_branch, "条件")
    _link(logic, e_branch, "是", hiding_branch, "执行")

    # ── ⑥ 病历:三页,E 读;集齐才解锁侧门 ──────────────────────────
    sheet_nodes = [phys_e, e_key, e_branch, hiding_get, hiding_branch]
    for index, name in enumerate(MED_SHEETS):
        y = 900.0 - index * 420.0
        name_const, zone = _interact_zone(logic, name, "[E] 读病历", y,
                                         interact_tip.name, MED_PLAYER,
                                         e_branch)
        pick, branch = _interact_pick(logic, name, name_const,
                                     hiding_branch, "否", y - 700.0)
        flag_get = _add(logic, "GKNodeGetVar", f"读过{name}吗", -1150.0,
                        y - 640.0, gk_var_name=MED_SHEET_FLAGS[index])
        flag_branch = _add(logic, "GKNodeBranch", f"{name}读过了吗", -1000.0,
                           y - 700.0)
        _link(logic, flag_get, "值", flag_branch, "条件")
        _link(logic, branch, "是", flag_branch, "执行")
        flag_true = _add(logic, "GKNodeConstBool", f"{name}算读过了", -1150.0,
                         y - 800.0, gk_value=True)
        set_flag = _add(logic, "GKNodeSetVar", f"记下{name}", -800.0,
                        y - 700.0, gk_var_name=MED_SHEET_FLAGS[index])
        add_count = _add(logic, "GKNodeAddVar", f"病历数+1({name})", -600.0,
                         y - 700.0, gk_var_name=MED_COUNT, gk_amount=1.0,
                         gk_clamp=True, gk_min=0.0, gk_max=float(MED_DOOR_GOAL))
        say = _add(logic, "GKNodeUiSubtitle", f"读到{name}", -400.0,
                   y - 700.0, gk_text=f"《{name}》{MED_RECORDS[index]}",
                   gk_seconds=4.0, gk_speed=30.0)
        count_get = _add(logic, "GKNodeGetVar", f"读病历数({name})", -200.0,
                         y - 800.0, gk_var_name=MED_COUNT)
        count_cmp = _add(logic, "GKNodeCompare", "齐了吗", 0.0, y - 800.0,
                         gk_op="GE", gk_b=str(MED_DOOR_GOAL))
        count_branch = _add(logic, "GKNodeBranch", "病历齐了", 200.0, y - 700.0)
        reveal = _add(logic, "GKNodeUiSubtitle", "病历齐了", 400.0, y - 700.0,
                      gk_text="三页病历对上了 —— 侧门在东北角", gk_seconds=4.0,
                      gk_speed=30.0)
        again = _add(logic, "GKNodeUiSubtitle", f"再读{name}", -800.0,
                     y - 840.0, gk_text=f"《{name}》已经看过了", gk_seconds=2.0,
                     gk_speed=30.0)
        _link(logic, flag_true, "值", set_flag, "值")
        _link(logic, flag_branch, "否", set_flag, "执行")
        _link(logic, set_flag, "完成", add_count, "执行")
        _link(logic, add_count, "完成", say, "执行")
        _link(logic, say, "完成", count_branch, "执行")
        _link(logic, count_get, "值", count_cmp, "A")
        _link(logic, count_cmp, "结果", count_branch, "条件")
        _link(logic, count_branch, "是", reveal, "执行")
        _link(logic, flag_branch, "是", again, "执行")
        sheet_nodes += zone + pick + [flag_get, flag_branch, flag_true,
                                      set_flag, add_count, say, count_get,
                                      count_cmp, count_branch, reveal, again]

    # ── ⑦ 电池:E 捡 -> 电池数 +1 / 手电电量 +40 ────────────────────
    battery_nodes = []
    for index, name in enumerate(MED_BATTERIES):
        y = 100.0 - index * 420.0
        name_const, zone = _interact_zone(logic, name, "[E] 换电池", y,
                                         interact_tip.name, MED_PLAYER,
                                         e_branch)
        pick, branch = _interact_pick(logic, name, name_const,
                                     hiding_branch, "否", y - 700.0)
        add_count = _add(logic, "GKNodeAddVar", f"电池数+1({name})", -1150.0,
                         y - 700.0, gk_var_name=MED_BATTERY_COUNT,
                         gk_amount=1.0, gk_clamp=True, gk_min=0.0, gk_max=2.0)
        charge = _add(logic, "GKNodeAddVar", f"电量+40({name})", -950.0,
                      y - 700.0, gk_var_name=BATTERY,
                      gk_amount=BATTERY_PICKUP, gk_clamp=True, gk_min=0.0,
                      gk_max=BATTERY_MAX)
        free = _add(logic, "GKNodeQueueFree", f"拿走{name}", -750.0,
                    y - 700.0, gk_target=name)
        target_off = _add(logic, "GKNodeSetVar", f"清目标({name})", -550.0,
                          y - 700.0, gk_var_name=INTERACT_TARGET)
        tip_off = _add(logic, "GKNodeUiSet", f"清提示({name})", -350.0,
                       y - 700.0, gk_widget=interact_tip.name,
                       gk_set_text=True, gk_text="")
        say = _add(logic, "GKNodeUiSubtitle", f"换上新电池{index + 1}", -150.0,
                   y - 700.0, gk_text="换上新电池 —— 手电又亮了(电量 +40)",
                   gk_seconds=3.0, gk_speed=30.0)
        empty_target = _add(logic, "GKNodeConstText", f"空目标({name})",
                            -550.0, y - 840.0, gk_value="")
        _link(logic, branch, "是", add_count, "执行")
        _link(logic, add_count, "完成", charge, "执行")
        _link(logic, charge, "完成", free, "执行")
        _link(logic, free, "完成", target_off, "执行")
        _link(logic, empty_target, "值", target_off, "值")
        _link(logic, target_off, "完成", tip_off, "执行")
        _link(logic, tip_off, "完成", say, "执行")
        battery_nodes += zone + pick + [add_count, charge, free, target_off,
                                        tip_off, say, empty_target]

    # ── ⑧ 躲藏:进帘子 = 身体挪到视距外 + 相机切走 + 人隐藏 ──────────
    # ⚠ 视线是**纯距离判定**,躲在帘子后面不会自动"看不见" —— 所以进帘子
    #   时把身体传送到『医务室躲藏锚点』(40 米外),相机切『医务室躲藏机位』
    #   并把玩家节点隐藏。出来时按『躲藏点』把人送回对应帘子前面。
    hide_nodes = []
    for index, name in enumerate(MED_HIDES):
        y = -800.0 - index * 460.0
        name_const, zone = _interact_zone(logic, name, "[E] 躲进帘子", y,
                                         interact_tip.name, MED_PLAYER,
                                         e_branch)
        pick, branch = _interact_pick(logic, name, name_const,
                                     hiding_branch, "否", y - 300.0)
        spot_const = _add(logic, "GKNodeConstText", f"躲藏点={name}",
                          -1150.0, y - 420.0, gk_value=name)
        set_spot = _add(logic, "GKNodeSetVar", f"记下躲藏点({name})",
                        -950.0, y - 300.0, gk_var_name=HIDE_SPOT_VAR)
        set_hiding = _add(logic, "GKNodeSetVar", f"躲起来({name})", -750.0,
                          y - 300.0, gk_var_name=HIDING)
        hiding_true = _add(logic, "GKNodeConstBool", f"躲藏中=真({name})",
                           -750.0, y - 420.0, gk_value=True)
        teleport = _add(logic, "GKNodeSetPosition", f"挪进帘子({name})",
                        -550.0, y - 300.0, gk_target=MED_PLAYER,
                        gk_x=MED_HIDE_ANCHOR_POS[0], gk_y=0.9,
                        gk_z=-MED_HIDE_ANCHOR_POS[1])
        hide_cam = _add(logic, "GKNodeCameraSetCurrent", f"躲藏机位({name})",
                        -350.0, y - 300.0, gk_target=MED_HIDE_CAM,
                        gk_blend=0.25, gk_blend_trans="EASE_OUT")
        hide_player = _add(logic, "GKNodeSetVisible", f"隐藏玩家({name})",
                           -150.0, y - 300.0, gk_target=MED_PLAYER,
                           gk_visible=False)
        hide_say = _add(logic, "GKNodeUiSubtitle", f"躲进去({name})", 50.0,
                        y - 300.0, gk_text="你缩到帘子后面 —— 再按 E 出来",
                        gk_seconds=3.0, gk_speed=30.0)
        _link(logic, spot_const, "值", set_spot, "值")
        _link(logic, branch, "是", set_spot, "执行")
        _link(logic, set_spot, "完成", set_hiding, "执行")
        _link(logic, hiding_true, "值", set_hiding, "值")
        _link(logic, set_hiding, "完成", teleport, "执行")
        _link(logic, teleport, "完成", hide_cam, "执行")
        _link(logic, hide_cam, "完成", hide_player, "执行")
        _link(logic, hide_player, "完成", hide_say, "执行")
        hide_nodes += zone + pick + [spot_const, set_spot, set_hiding,
                                     hiding_true, teleport, hide_cam,
                                     hide_player, hide_say]
    # ── 出来:『躲藏点』决定落回哪张床前(SetPosition 只吃固定坐标,所以两条)
    # ⚠ 位置是**显式设的**,不依赖物理落地 —— 躲藏时身体被挪到 40 米外的锚点,
    #   那块地在关卡边界外,身体会一直往下掉(实测 y 会掉到 -4 以下)。
    #   "出来"这里必须 global_position = 帘子前,**别**改成"恢复进去时记下的
    #   坐标":进去记下的位置就是那个正在下坠的锚点,一恢复人就掉进虚空。
    #   也别指望"落地"纠正 —— 锚点下面没有地板。
    out_get = _add(logic, "GKNodeGetVar", "读躲藏点", 700.0, -1400.0,
                   gk_var_name=HIDE_SPOT_VAR)
    out_cmp = _add(logic, "GKNodeCompare", "躲在第一个帘子?", 900.0, -1400.0,
                   gk_op="EQ")
    out_first_const = _add(logic, "GKNodeConstText", "帘子1的名字", 900.0,
                           -1520.0, gk_value=MED_HIDES[0])
    out_branch = _add(logic, "GKNodeBranch", "从哪个帘子出来", 1100.0, -1300.0)
    _link(logic, hiding_branch, "是", out_branch, "执行")
    _link(logic, out_get, "值", out_cmp, "A")
    _link(logic, out_first_const, "值", out_cmp, "B")
    _link(logic, out_cmp, "结果", out_branch, "条件")
    for index, (front, pin) in enumerate(zip(MED_HIDE_FRONT,
                                             ("是", "否"))):
        y = -1500.0 - index * 260.0
        back = _add(logic, "GKNodeSetPosition", f"回到床前{index + 1}",
                    1300.0, y, gk_target=MED_PLAYER, gk_x=front[0],
                    gk_y=front[2], gk_z=-front[1])
        stand_up = _add(logic, "GKNodeSetVar", f"站起来{index + 1}", 1500.0,
                        y, gk_var_name=HIDING)
        hiding_false = _add(logic, "GKNodeConstBool", f"躲藏中=假{index + 1}",
                            1500.0, y - 120.0, gk_value=False)
        front_cam = _add(logic, "GKNodeCameraSetCurrent", f"回第三人称{index + 1}",
                         1700.0, y, gk_target=SIDE_CAM, gk_blend=0.2,
                         gk_blend_trans="EASE_OUT")
        show_player = _add(logic, "GKNodeSetVisible", f"显示玩家{index + 1}",
                           1900.0, y, gk_target=MED_PLAYER, gk_visible=True)
        out_say = _add(logic, "GKNodeUiSubtitle", f"出来了{index + 1}",
                       2100.0, y, gk_text="你从帘子后面出来", gk_seconds=2.5,
                       gk_speed=30.0)
        _link(logic, out_branch, pin, back, "执行")
        _link(logic, back, "完成", stand_up, "执行")
        _link(logic, hiding_false, "值", stand_up, "值")
        _link(logic, stand_up, "完成", front_cam, "执行")
        _link(logic, front_cam, "完成", show_player, "执行")
        _link(logic, show_player, "完成", out_say, "执行")
        hide_nodes += [back, stand_up, hiding_false, front_cam, show_player,
                       out_say]
    hide_nodes += [out_get, out_cmp, out_first_const, out_branch]
    _frame(logic, "⑤ 病历 / 电池 / 躲藏", sheet_nodes + battery_nodes +
           hide_nodes, (0.26, 0.24, 0.22))

    # ── ⑨ 侧门(『去楼梯间』):病历 3 页才放行,先存盘再切关 ──────────
    door_name, door_zone = _interact_zone(logic, TO_STAIRS, "[E] 推开侧门",
                                         900.0, interact_tip.name, MED_PLAYER,
                                         e_branch)
    door_pick, door_branch = _interact_pick(logic, TO_STAIRS, door_name,
                                           hiding_branch, "否", 200.0)
    count_get = _add(logic, "GKNodeGetVar", "病历够了吗", 200.0, 120.0,
                     gk_var_name=MED_COUNT)
    count_cmp = _add(logic, "GKNodeCompare", "病历够吗", 400.0, 120.0,
                     gk_op="GE", gk_b=str(MED_DOOR_GOAL))
    door_branch2 = _add(logic, "GKNodeBranch", "侧门能开吗", 600.0, 200.0)
    save_count = _add(logic, "GKNodeSaveSet", "存病历数", 800.0, 200.0,
                      gk_key=MED_COUNT)
    count_save_get = _add(logic, "GKNodeGetVar", "读病历数(存盘)", 800.0, 80.0,
                          gk_var_name=MED_COUNT)
    save_bat = _add(logic, "GKNodeSaveSet", "存电池数", 1000.0, 200.0,
                    gk_key=MED_BATTERY_COUNT)
    bat_save_get = _add(logic, "GKNodeGetVar", "读电池数(存盘)", 1000.0, 80.0,
                        gk_var_name=MED_BATTERY_COUNT)
    save = _add(logic, "GKNodeSaveWrite", "侧门存盘", 1200.0, 200.0,
                gk_slot="save1", gk_save_verify=True)
    change = _add(logic, "GKNodeChangeScene", "去楼梯间", 1400.0, 200.0,
                  gk_scene_path=STAIRS_RES)
    lock_say = _add(logic, "GKNodeUiSubtitle", "侧门锁着", 800.0, 0.0,
                    gk_text="侧门锁着 —— 病历还没看全", gk_seconds=3.0,
                    gk_speed=30.0)
    _link(logic, door_branch, "是", door_branch2, "执行")
    _link(logic, count_get, "值", count_cmp, "A")
    _link(logic, count_cmp, "结果", door_branch2, "条件")
    _link(logic, door_branch2, "是", save_count, "执行")
    _link(logic, count_save_get, "值", save_count, "值")
    _link(logic, save_count, "完成", save_bat, "执行")
    _link(logic, bat_save_get, "值", save_bat, "值")
    _link(logic, save_bat, "完成", save, "执行")
    door_sfx_e = _add(logic, "GKNodePlaySound", "开门声(去楼梯间)", 1400.0, 280.0,
                   gk_path=DOOR_FILE)
    door_sfx_e_wait = _add(logic, "GKNodeWait", "等门声一拍", 1600.0, 280.0)
    door_sfx_e_wait.inputs["秒"].default_value = 0.55
    _link(logic, save, "完成", door_sfx_e, "执行")
    _link(logic, door_sfx_e, "完成", door_sfx_e_wait, "执行")
    _link(logic, door_sfx_e_wait, "完成", change, "执行")
    _link(logic, door_branch2, "否", lock_say, "执行")
    _frame(logic, "⑥ 侧门(去楼梯间)", door_zone + door_pick +
           [count_get, count_cmp, door_branch2, save_count, count_save_get,
            save_bat, bat_save_get, save, change, lock_say],
           (0.30, 0.20, 0.18))

    # ── ⑩ 安全屋(第一次静默存盘)+ 被抓 -> 回安全屋 ──────────────────
    safe_in = _add(logic, "GKNodeOnAreaEntered", "进安全屋", -1900.0, -3400.0,
                   gk_area=MED_SAFE_AREA, gk_filter=MED_PLAYER)
    cp_text = _add(logic, "GKNodeConstText", "检查点名", -1900.0, -3520.0,
                   gk_value=MED_CHECKPOINT)
    cp_set = _add(logic, "GKNodeSetVar", "设检查点", -1700.0, -3400.0,
                  gk_var_name=CHECKPOINT)
    safe_count = _add(logic, "GKNodeSaveSet", "安全屋存病历数", -1500.0,
                      -3400.0, gk_key=MED_COUNT)
    safe_count_get = _add(logic, "GKNodeGetVar", "读病历数(安全屋)", -1500.0,
                          -3520.0, gk_var_name=MED_COUNT)
    safe_bat = _add(logic, "GKNodeSaveSet", "安全屋存电池数", -1300.0, -3400.0,
                    gk_key=MED_BATTERY_COUNT)
    safe_note = _add(logic, "GKNodeSaveSet", "安全屋存隐藏笔记", -1100.0,
                     -3400.0, gk_key=HIDDEN_NOTE_COUNT)
    safe_bat_get = _add(logic, "GKNodeGetVar", "读电池数(安全屋)", -1300.0,
                        -3520.0, gk_var_name=MED_BATTERY_COUNT)
    safe_save = _add(logic, "GKNodeSaveWrite", "安全屋存盘", -1100.0, -3400.0,
                     gk_slot="save1", gk_save_verify=True)
    visited_get = _add(logic, "GKNodeGetVar", "进过安全屋吗", -1100.0, -3520.0,
                       gk_var_name=SAFE_VISITED)
    visited_branch = _add(logic, "GKNodeBranch", "第二次进?", -900.0, -3400.0)
    visited_true = _add(logic, "GKNodeConstBool", "记下进过", -900.0, -3520.0,
                        gk_value=True)
    visited_set = _add(logic, "GKNodeSetVar", "记住进过安全屋", -700.0,
                       -3400.0, gk_var_name=SAFE_VISITED)
    safe_tip = _add(logic, "GKNodeUiSubtitle", "安全屋提示", -500.0, -3520.0,
                    gk_text="安全屋:进度已保存", gk_seconds=2.5, gk_speed=30.0)
    _link(logic, safe_in, "执行", cp_set, "执行")
    _link(logic, cp_text, "值", cp_set, "值")
    _link(logic, cp_set, "完成", safe_count, "执行")
    _link(logic, safe_count_get, "值", safe_count, "值")
    _link(logic, safe_count, "完成", safe_bat, "执行")
    _link(logic, safe_bat_get, "值", safe_bat, "值")
    _link(logic, safe_bat, "完成", safe_note, "执行")
    _link(logic, safe_note, "完成", safe_save, "执行")
    _link(logic, safe_save, "完成", visited_branch, "执行")
    _link(logic, visited_get, "值", visited_branch, "条件")
    _link(logic, visited_branch, "否", visited_set, "执行")
    _link(logic, visited_true, "值", visited_set, "值")
    _link(logic, visited_branch, "是", safe_tip, "执行")

    phys_fail = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(抓人判定)",
                     -1900.0, -4200.0)
    distance = _add(logic, "GKNodeDistance", "离怪物多远", -1900.0, -4340.0,
                    gk_a=MED_PLAYER, gk_b=MED_ENEMY)
    compare = _add(logic, "GKNodeCompare", "进抓取范围?", -1700.0, -4340.0,
                   gk_op="LT", gk_b=str(CATCH_DISTANCE))
    failed = _add(logic, "GKNodeGetVar", "已经失败了?", -1900.0, -4480.0,
                  gk_var_name="已失败")
    not_failed = _add(logic, "GKNodeLogic", "还没失败", -1700.0, -4480.0,
                      gk_op="NOT")
    can_fail = _add(logic, "GKNodeLogic", "进范围且没失败", -1500.0, -4480.0,
                    gk_op="AND")
    fail_branch = _add(logic, "GKNodeBranch", "被抓住了?", -1300.0, -4200.0)
    set_failed = _add(logic, "GKNodeSetVar", "记下失败", -1100.0, -4200.0,
                      gk_var_name="已失败")
    true_const = _add(logic, "GKNodeConstBool", "真", -1100.0, -4340.0,
                      gk_value=True)
    fall = _add(logic, "GKNodeCameraFall", "镜头倒地", -900.0, -4200.0,
                gk_time=0.9, gk_drop=0.85, gk_roll=72.0, gk_pitch=18.0,
                gk_fade=0.0, gk_restore=True)
    fail_wait = _add(logic, "GKNodeWait", "倒地停一下", -700.0, -4200.0)
    fail_wait.inputs["秒"].default_value = 1.0
    freeze = _add(logic, "GKNodeUiPauseGame", "冻住世界", -500.0, -4200.0,
                  gk_pause=True, gk_show_mouse=True, gk_menu="",
                  gk_hotkey=False)
    fail_text = _add(logic, "GKNodeUiText", "被抓标题", -300.0, -4200.0,
                     gk_text="你被抓住了", gk_align="CENTER", gk_size=44)
    fail_tip = _add(logic, "GKNodeUiText", "被抓提示", -100.0, -4200.0,
                    gk_text="医务室里的东西追上了你", gk_align="BOTTOM_CENTER",
                    gk_margin=260.0, gk_size=20)
    retry_btn = _add(logic, "GKNodeUiButton", "回到安全屋按钮", 100.0, -4280.0,
                     gk_text="回到安全屋", gk_align="BOTTOM_CENTER",
                     gk_margin=180.0, gk_width=200.0, gk_height=52.0)
    title_btn = _add(logic, "GKNodeUiButton", "失败回标题按钮", 100.0, -4420.0,
                     gk_text="回到标题", gk_align="BOTTOM_CENTER",
                     gk_margin=110.0, gk_width=200.0, gk_height=52.0)
    resume = _add(logic, "GKNodeUiPauseGame", "解冻(回安全屋)", 300.0, -4280.0,
                  gk_pause=False, gk_show_mouse=True, gk_menu="",
                  gk_hotkey=False)
    back_home = _add(logic, "GKNodeSetPosition", "传回安全屋", 500.0, -4280.0,
                     gk_target=MED_PLAYER,
                     gk_x=_godot_xy(MED_SAFE_POS)[0], gk_y=MED_SAFE_SPAWN_Z,
                     gk_z=_godot_xy(MED_SAFE_POS)[1])
    cp_again_text = _add(logic, "GKNodeConstText", "检查点名(回程)", 500.0,
                         -4140.0, gk_value=MED_CHECKPOINT)
    cp_again = _add(logic, "GKNodeSetVar", "重记检查点", 700.0, -4280.0,
                    gk_var_name=CHECKPOINT)
    failed_false = _add(logic, "GKNodeConstBool", "还没失败(回程)", 700.0,
                        -4140.0, gk_value=False)
    clear_failed = _add(logic, "GKNodeSetVar", "清失败标记", 900.0, -4280.0,
                        gk_var_name="已失败")
    hide_fail_title = _add(logic, "GKNodeUiHide", "收起被抓标题", 1100.0,
                           -4280.0, gk_widget=fail_text.name,
                           gk_visible=False)
    hide_fail_tip = _add(logic, "GKNodeUiHide", "收起被抓提示", 1300.0,
                         -4280.0, gk_widget=fail_tip.name, gk_visible=False)
    hide_retry = _add(logic, "GKNodeUiHide", "收起回安全屋按钮", 1500.0,
                      -4280.0, gk_widget=retry_btn.name, gk_visible=False)
    hide_title_btn = _add(logic, "GKNodeUiHide", "收起回标题按钮", 1700.0,
                          -4280.0, gk_widget=title_btn.name, gk_visible=False)
    home_tip = _add(logic, "GKNodeUiSubtitle", "回安全屋提示", 1900.0,
                    -4280.0, gk_text="你被拖回了安全屋……", gk_seconds=2.5,
                    gk_speed=30.0)
    resume_title = _add(logic, "GKNodeUiPauseGame", "解冻(回标题)", 300.0,
                        -4420.0, gk_pause=False, gk_show_mouse=True,
                        gk_menu="", gk_hotkey=False)
    to_title = _add(logic, "GKNodeChangeScene", "回标题", 500.0, -4420.0,
                    gk_scene_path=TITLE_RES)
    _link(logic, distance, "距离", compare, "A")
    _link(logic, failed, "值", not_failed, "A")
    _link(logic, not_failed, "结果", can_fail, "A")
    _link(logic, compare, "结果", can_fail, "B")
    _link(logic, can_fail, "结果", fail_branch, "条件")
    _link(logic, phys_fail, "执行", fail_branch, "执行")
    _link(logic, fail_branch, "是", set_failed, "执行")
    _link(logic, true_const, "值", set_failed, "值")
    _link(logic, set_failed, "完成", fall, "执行")
    _link(logic, fall, "完成", fail_wait, "执行")
    _link(logic, fail_wait, "完成", freeze, "执行")
    _link(logic, freeze, "完成", fail_text, "执行")
    _link(logic, fail_text, "完成", fail_tip, "执行")
    _link(logic, fail_tip, "完成", retry_btn, "执行")
    _link(logic, retry_btn, "完成", title_btn, "执行")
    _link(logic, retry_btn, "点击", resume, "执行")
    _link(logic, resume, "完成", back_home, "执行")
    _link(logic, back_home, "完成", cp_again, "执行")
    _link(logic, cp_again_text, "值", cp_again, "值")
    _link(logic, cp_again, "完成", clear_failed, "执行")
    _link(logic, failed_false, "值", clear_failed, "值")
    _link(logic, clear_failed, "完成", hide_fail_title, "执行")
    _link(logic, hide_fail_title, "完成", hide_fail_tip, "执行")
    _link(logic, hide_fail_tip, "完成", hide_retry, "执行")
    _link(logic, hide_retry, "完成", hide_title_btn, "执行")
    _link(logic, hide_title_btn, "完成", home_tip, "执行")
    _link(logic, title_btn, "点击", resume_title, "执行")
    _link(logic, resume_title, "完成", to_title, "执行")
    _frame(logic, "⑦ 安全屋 / 被抓", [safe_in, cp_text, cp_set, safe_count,
                                      safe_count_get, safe_bat, safe_bat_get,
                                      safe_save, visited_get, visited_branch,
                                      visited_true, visited_set, safe_tip,
                                      phys_fail, distance, compare, failed,
                                      not_failed, can_fail, fail_branch,
                                      set_failed, true_const, fall, fail_wait,
                                      freeze, fail_text, fail_tip, retry_btn,
                                      title_btn, resume, back_home,
                                      cp_again_text, cp_again, failed_false,
                                      clear_failed, hide_fail_title,
                                      hide_fail_tip, hide_retry,
                                      hide_title_btn, home_tip, resume_title,
                                      to_title], (0.32, 0.16, 0.14))
    _frame(logic, "⑧ 笔记日志(跨场景)", journal_ui + journal_close_nodes +
           restore_nodes + journal_toggle_nodes, (0.26, 0.20, 0.30))
    # ── 隐藏笔记(第一百六十九轮)──────────────────────────────────
    for scene_name, note_name, _pos, note_text in HIDDEN_NOTES:
        if scene_name == MED:
            _hidden_note_chain(logic, note_name, note_text,
                               MED_PLAYER, -1500.0, -3600.0)
    # ── 惊吓演出(第一百七十五轮):档案间门洞,一次性 ──────────────────
    # 红闪 + sting + 总线压低再恢复(照抄走廊惊吓三连),字幕补一刀叙事。
    med_scared = _add(logic, "GKNodeOnAreaEntered", "穿过档案间门洞",
                      -1500.0, -4400.0, gk_area=MED_SCARE_AREA,
                      gk_filter=MED_PLAYER)
    med_was = _add(logic, "GKNodeGetVar", "医务室受惊过了吗", -1500.0,
                   -4560.0, gk_var_name="医务室已受惊")
    med_scare_branch = _add(logic, "GKNodeBranch", "医务室第一次受惊?",
                            -1300.0, -4400.0)
    med_true = _add(logic, "GKNodeConstBool", "医务室记受惊", -1300.0,
                    -4560.0, gk_value=True)
    med_set = _add(logic, "GKNodeSetVar", "医务室记下受惊", -1100.0, -4400.0,
                   gk_var_name="医务室已受惊")
    med_scare = _add(logic, "GKNodeScare", "帘子后受惊", -900.0, -4400.0,
                     gk_strength=20.0, gk_duration=0.6,
                     gk_color=(0.85, 0.10, 0.10, 1.0), gk_alpha=0.6,
                     gk_flash_time=0.4, gk_path=STING_FILE, gk_volume=-2.0)
    med_duck = _add(logic, "GKNodeBusVolume", "压低总线(惊吓)", -700.0,
                    -4560.0, gk_bus="Master", gk_db=-18.0, gk_fade=0.3)
    med_wait = _add(logic, "GKNodeWait", "等吓劲过去(医务室)", -500.0,
                    -4560.0)
    med_wait.inputs["秒"].default_value = 2.5
    med_restore = _add(logic, "GKNodeBusVolume", "总线恢复(惊吓)", -300.0,
                       -4560.0, gk_bus="Master", gk_db=0.0, gk_fade=1.2)
    _link(logic, med_scared, "执行", med_scare_branch, "执行")
    _link(logic, med_was, "值", med_scare_branch, "条件")
    _link(logic, med_scare_branch, "否", med_set, "执行")
    _link(logic, med_true, "值", med_set, "值")
    _link(logic, med_set, "完成", med_scare, "执行")
    med_whisper = _add(logic, "GKNodePlaySound", "耳语(受惊)", -900.0,
                       -4720.0, gk_path=WHISPER_FILE, gk_volume=-8.0)
    _link(logic, med_scare, "完成", med_whisper, "执行")
    _link(logic, med_whisper, "完成", med_duck, "执行")
    _link(logic, med_duck, "完成", med_wait, "执行")
    _link(logic, med_wait, "完成", med_restore, "执行")
    # ── 追逐切曲(第一百七十九轮)──────────────────────────────────
    _chase_music_chain(logic, -1500.0, -5600.0, CHASE_FILE, BGM_FILE)
    log(f"{MED}关卡图:手电(进关 60 / 电池 +40 / 没电灭)+ 病历 3 页 + "
        f"躲藏 1/2(切机位 + 隐藏玩家 + 身体挪到视距外)+ 侧门({TO_STAIRS})-> "
        f"{STAIRS_RES} / 安全屋 + 被抓回本关")
    return logic


# ── 楼梯间:三折楼梯 + 天台 ────────────────────────────────────────
# 尺寸(Blender x/y/z;Godot z = -y):
#   门厅   y -12..-2   z=0
#   坡道 1 y  -2..4    0   -> 2.8
#   平台 1 y   4..8    z=2.8
#   坡道 2 y   8..14   2.8 -> 5.6
#   平台 2 y  14..18   z=5.6
#   坡道 3 y  18..24   5.6 -> 8.4
#   顶层   y  24..28   z=8.4  + 天台门
#   天台   y  28..38   z=8.4(露天,只有栏杆)
#
# ⚠ 第 167 轮起楼梯是**真台阶**(_stair_steps,一级级实心盒子):预设玩家
#   默认 gk_step_height=0.35、AI 移动接了同一套四关探测 —— 人和怪物都能
#   真爬。旧版是"斜坡 + 装饰踏面"(CharacterBody3D 不会上台阶,只能骗);
#   _stair_ramp/_stair_treads 保留着,别处要用斜坡还在。
STAIR_RISE = 2.8            # 每层抬高
STAIR_RUN = 6.0             # 每段台阶的水平长度(级深 0.6 × 10 级)
STAIR_WIDTH = 5.0
SHAFT_CEIL = 11.2           # 天花板高度(顶层 8.4 + 玩家 1.8 + 余量)
ROOF_END = 38.0             # 天台北边缘
ROOF_START = 28.0


def _stair_ramp(name, y0, y1, z0, z1, width, scene, color):
    """一段坡道:一个绕 X 轴旋转的盒子(玩家走的是它的上表面)。"""
    run = y1 - y0
    rise = z1 - z0
    length = math.hypot(run, rise)
    angle = math.atan2(rise, run)
    ramp = make_box(name, (width, length, 0.4),
                    (0.0, (y0 + y1) * 0.5, (z0 + z1) * 0.5), color,
                    scene=scene)
    ramp.rotation_euler = (angle, 0.0, 0.0)
    return solid_box(ramp)


def _stair_treads(prefix, y0, y1, z0, z1, width, scene, color, count=9):
    """贴在坡面上的踏面(装饰):把斜面切成一级级,纯视觉。"""
    run = (y1 - y0) / count
    rise = (z1 - z0) / count
    for index in range(count):
        y = y0 + run * (index + 0.5)
        z = z0 + rise * (index + 0.5)
        decor(no_shadow(make_box(f"{prefix}踏面{index + 1}",
                                 (width, 0.34, 0.12),
                                 (0.0, y, z + 0.18), color, scene=scene)))


def _stair_steps(prefix, y0, y1, z0, z1, width, scene, riser_color,
                 tread_color, count=10):
    """真台阶(第一百六十七轮):一级级**实心**盒子,有碰撞、能真走上去。

    以前楼梯必须做成斜坡(第 161 轮):CharacterBody3D 不会自动上台阶。
    现在『移动_按方向』预设默认 gk_step_height=0.35、AI 移动也接了同一
    套四关探测 —— 级高 0.28(在 0.35 内)、级深 0.6,角色和怪物都能真爬。
    每级是"从段底到本级顶"的实心柱:侧面看不穿,顶面(tread_color)比
    立面亮一档,楼梯的体积感就出来了。
    """
    run = (y1 - y0) / count
    rise = (z1 - z0) / count
    for index in range(count):
        top = z0 + rise * (index + 1)
        solid_box(make_box(
            f"{prefix}级{index + 1}",
            (width, run, rise * (index + 1)),
            (0.0, y0 + run * (index + 0.5), z0 + top * 0.5),
            tread_color, scene=scene))


def _roof_skyline(scene):
    """天台四周的夜景楼群(第 166 轮):剪影楼体 + 零星亮窗。

    三排深度:近景(紧贴天台北面,压住地平线)/ 中景(错落)/ 远景
    (被雾再推淡一层)。月光从北边照,楼体全背光;窗子是独立的小
    自发光面,只贴朝天台(南)的一侧,网格排布 + 确定性伪随机点亮
    三成 —— 深夜教学楼还亮着几扇窗的感觉。楼体亮度 0.044~0.073(第 166 轮取景后定):
    导出侧把显示色解码成线性,这个区间到手才是"剪影的深蓝"。
    全部 decor + no_shadow:远景不挡人 / 不投影 / 不参与照明。
    """
    towers = (
        #      x      y    宽   深   高   楼体亮度  窗seed
        (-9.5, 43.0, 7.0, 6.0, 16.0, 0.044, 1),
        (10.5, 44.5, 8.0, 7.0, 21.0, 0.046, 2),
        (1.0, 46.0, 6.0, 5.0, 13.0, 0.049, 3),
        (-17.0, 54.0, 6.0, 6.0, 11.0, 0.055, 4),
        (-4.5, 56.0, 7.0, 6.0, 17.0, 0.058, 5),
        (9.5, 57.0, 5.5, 5.5, 14.0, 0.053, 6),
        (20.0, 55.0, 7.5, 6.0, 10.0, 0.059, 7),
        (-27.0, 68.0, 8.0, 6.0, 9.0, 0.068, 8),
        (-13.0, 70.0, 6.0, 5.0, 12.0, 0.071, 9),
        (2.0, 69.0, 7.0, 6.0, 14.0, 0.065, 10),
        (15.5, 71.0, 6.5, 5.5, 10.0, 0.069, 11),
        (28.0, 68.0, 8.0, 6.0, 8.0, 0.073, 12),
        # 东西两侧:从东西栏杆看出去也有一点(不对称,自然些)
        (15.0, 33.0, 6.0, 11.0, 13.0, 0.051, 13),
        (-15.5, 31.0, 6.0, 11.0, 11.0, 0.051, 14),
    )
    for index, (x, y, w, d, h, shade, seed) in enumerate(towers):
        prefix = f"楼梯间远景楼{index + 1:02d}"
        decor(no_shadow(make_box(
            prefix, (w, d, h), (x, y, h * 0.5),
            (shade, shade + 0.004, shade + 0.010, 1.0), scene=scene)))
        # 楼顶小件:水塔 / 天线 + 航空红灯(挑两三栋,别满头天线)
        if index in (1, 5):
            decor(no_shadow(make_box(
                f"{prefix}水塔", (1.6, 1.6, 1.8),
                (x + w * 0.2, y - d * 0.2, h + 0.9),
                (shade + 0.01, shade + 0.01, shade + 0.015, 1.0),
                scene=scene)))
        if index in (4, 9):
            decor(no_shadow(make_box(
                f"{prefix}天线", (0.14, 0.14, 3.2), (x, y, h + 1.6),
                (shade + 0.01, shade + 0.01, shade + 0.014, 1.0),
                scene=scene)))
            decor(no_shadow(make_box(
                f"{prefix}天线灯", (0.22, 0.22, 0.16), (x, y, h + 3.25),
                (0.05, 0.02, 0.02, 1.0), scene=scene,
                glow=((1.0, 0.14, 0.10, 1.0), 0.9))))
        # 亮窗:贴朝南面,网格排布,确定性点亮三成;七成暖黄三成冷白
        face_y = y - d * 0.5 - 0.03
        cols = max(2, int(w / 1.7))
        rows = max(2, int((h - 3.0) / 2.4))
        for ix in range(cols):
            for iz in range(rows):
                if ((ix * 7 + iz * 13 + seed * 5) % 10) >= 3:
                    continue
                wx = x - w * 0.5 + (ix + 0.5) * (w / cols)
                wz = 3.0 + (iz + 0.5) * 2.4
                warm = ((ix + iz + seed) % 3) != 0
                tint = ((1.0, 0.85, 0.58, 1.0) if warm
                        else (0.70, 0.84, 1.0, 1.0))
                strength = 0.55 + 0.15 * ((ix + iz) % 3)
                decor(no_shadow(make_box(
                    f"{prefix}窗{ix}_{iz}", (0.55, 0.06, 0.75),
                    (wx, face_y, wz), (0.02, 0.02, 0.02, 1.0),
                    scene=scene, glow=(tint, strength))))


def build_stairs():
    """第 5 章 楼梯间:门厅 + 三折楼梯 + 天台(断电追逐的场地)。"""
    scene = bpy.data.scenes.new(STAIRS)
    wall_color = (0.13, 0.14, 0.17, 1.0)
    step_color = (0.20, 0.21, 0.26, 1.0)
    tread_color = (0.26, 0.27, 0.31, 1.0)

    # ── 楼板:门厅 + 三个平台 + 天台 ──────────────────────────────
    solid_box(make_box("楼梯间门厅地面", (12.0, 10.0, 0.4),
                       (0.0, -7.0, -0.2), (0.10, 0.10, 0.13, 1.0),
                       scene=scene))
    for index, y0 in enumerate((-1.0, 9.0, 19.0)):
        solid_box(make_box(f"楼梯间平台{index + 1}", (12.0, 5.0, 0.4),
                           (0.0, y0, STAIR_RISE * (index + 1) - 0.7),
                           (0.12, 0.13, 0.16, 1.0), scene=scene))
    solid_box(make_box("楼梯间顶层地面", (12.0, 4.0, 0.4),
                       (0.0, 26.0, STAIR_RISE * 3 - 0.7),
                       (0.12, 0.13, 0.16, 1.0), scene=scene))
    solid_box(make_box("楼梯间天台地面", (12.0, 10.0, 0.4),
                       (0.0, 33.0, STAIR_RISE * 3 - 0.7),
                       (0.15, 0.16, 0.19, 1.0), scene=scene))

    # ── 三段真台阶(第一百六十七轮:插件会爬台阶了)────────────────
    # 平台1 从 y=4 起(段 1 走 -2..4);平台2 从 y=14 起;顶层从 y=24 起。
    # 以前是"斜坡 + 装饰踏面" —— CharacterBody3D 不会自动上台阶,只能骗
    # (第 161 轮)。现在『移动_按方向』预设默认 gk_step_height=0.35、
    # AI 移动也接了四关探测,楼梯就换成**真台阶**:每段 10 级实心盒子,
    # 级高 0.28、级深 0.6(在 0.35 的迈步高度内)。
    flights = ((-2.0, 4.0, 0.0), (8.0, 14.0, STAIR_RISE),
               (18.0, 24.0, STAIR_RISE * 2))
    for index, (y0, y1, z0) in enumerate(flights):
        _stair_steps(f"楼梯间{index + 1}", y0, y1, z0, z0 + STAIR_RISE,
                     STAIR_WIDTH, scene, step_color, tread_color)

    # ── 墙 / 天花:一个 11 米高的井道 ────────────────────────────
    for name, pos in (("西墙", -6.0), ("东墙", 6.0)):
        make_wall_pieces(f"楼梯间{name}", "y", pos, (-12.0, ROOF_START),
                         WALL_THICK, SHAFT_CEIL, scene, [], wall_color)
        make_trim(f"楼梯间{name}", "y", pos, (-12.0, ROOF_START),
                  -1 if pos < 0 else 1, scene, (0.18, 0.17, 0.16, 1.0))
    make_wall_pieces("楼梯间南墙", "x", -12.0, (-6.0, 6.0), WALL_THICK,
                     SHAFT_CEIL, scene, [], wall_color)
    solid_box(make_box("楼梯间天花", (12.4, ROOF_START + 12.4, 0.4),
                       (0.0, (ROOF_START - 12.4) * 0.5, SHAFT_CEIL - 0.2),
                       (0.10, 0.11, 0.14, 1.0), scene=scene))

    # ── 天台:栏杆 + 天台门(门框是真的,门板开着)────────────────
    for name, size, loc in (
            ("天台栏杆北", (12.0, 0.18, 1.10), (0.0, ROOF_END, 9.5)),
            ("天台栏杆东", (0.18, 10.0, 1.10), (6.0, 33.0, 9.5)),
            ("天台栏杆西", (0.18, 10.0, 1.10), (-6.0, 33.0, 9.5))):
        # 第 166 轮压暗(0.30 -> 0.19 -> 0.075):第一版月光把它照成
        # 全画面最亮的一条;0.19 的 albedo 解码后还有 0.47,乘环境光
        # 依旧是一条亮梁 —— 压到和楼群剪影同级才退到背景层。
        decor(no_shadow(make_box(name, size, loc, (0.075, 0.080, 0.090, 1.0),
                                 scene=scene)))
    door_z = STAIR_RISE * 3
    decor(make_box("楼梯间天台门框柱1", (0.24, 0.30, 2.50),
                   (-1.6, ROOF_START, door_z + 1.25),
                   (0.09, 0.08, 0.07, 1.0), scene=scene))
    decor(make_box("楼梯间天台门框柱2", (0.24, 0.30, 2.50),
                   (1.6, ROOF_START, door_z + 1.25),
                   (0.09, 0.08, 0.07, 1.0), scene=scene))
    decor(make_box("楼梯间天台门楣", (3.6, 0.30, 0.30),
                   (0.0, ROOF_START, door_z + 2.55),
                   (0.09, 0.08, 0.07, 1.0), scene=scene))
    # 出口指示灯:一小条**自发光**,只占门楣中间 0.5 米。
    # ⚠ 别让整根门楣发光:第三人称相机被门口挡住时会贴在门楣背后,
    #   一整块 3.6×0.3 的发光面 = 整屏洋红(实机截图 22_choice 反复踩到)。
    # ⚠ 面积再小一点:第三人称相机被门口挡住时会贴到 0.3~0.5 米,
    #   0.5 米宽的自发光条在那么近的距离上 + bloom = 一条 3 米宽的亮带
    #   (截图里像一扇发光的门)。0.32 × 0.06、亮度 0.5,近看也只是一个红点。
    decor(no_shadow(make_box("楼梯间天台门灯", (0.32, 0.10, 0.06),
                             (0.0, ROOF_START - 0.16, door_z + 2.34),
                             (0.30, 0.06, 0.05, 1.0), scene=scene,
                             glow=((1.0, 0.16, 0.12, 1.0), 0.5))))
    decor(make_box("楼梯间天台门板", (0.16, 2.40, 2.40),
                   (2.4, ROOF_START + 0.4, door_z + 1.2),
                   (0.28, 0.23, 0.18, 1.0), scene=scene))

    # ── 天台北望:校园/城市剪影(第 166 轮)──────────────────────
    # 天台原本看出去只有夜空(第 159 轮留的坑)。现在放一片**低多边形
    # 夜景楼群**:月光从北边照过来(楼梯间月光在 y=-33 打向 +y),楼群
    # 正好是**背光的剪影**;楼的"亮"不靠灯,靠零星的自发光窗 —— 深夜
    # 教学楼还亮着几扇窗,就是那种感觉。
    # ⚠ 全部 decor + no_shadow:远景不挡人/怪物/相机,不投影、不参与照明。
    # ⚠ 楼体显示色压在 0.03~0.06:导出侧解码成线性(0.16→0.44),这个
    #   区间到手才是"剪影的深蓝",再亮就成了灰板(第 162 轮的教训)。
    _roof_skyline(scene)

    # ── 角色 / 追兵 ─────────────────────────────────────────────
    # 玩家出生在**门厅最里面**(安全屋那块),追兵从他们刚进来的门上来 ——
    # 断电时它离你 3 米,这一章从第一秒就没有"回头看看"的余地。
    player = make_box("楼梯间玩家", (1.0, 1.0, 1.6), (0.0, -8.4, 0.9),
                      PLAYER_COLOR, scene=scene)
    dress_player(player, scene)
    # ⚠ 颜色压到 0.09:导出侧把『显示色』解码成线性(0.16 -> 0.44),
    #   0.16 在灯下会变成一块灰板(实机截图里像"人形前面挡了块板")。
    make_box(STAIRS_ENEMY, MONSTER_BOX_SLIM, (0.0, -11.4, 0.95),
             (0.040, 0.036, 0.050, 1.0), scene=scene)
    chaser = scene.objects[STAIRS_ENEMY]
    dress_monster(chaser, scene, MONSTER_BOX_SLIM)
    make_marker(STAIRS_PATROL_A, (0.0, -11.0, 0.0), scene, size=0.8)
    make_marker(STAIRS_PATROL_B, (0.0, -3.2, 0.0), scene, size=0.8)

    # ── 区域:三层 + 天台 + 安全屋(判定体一律不画)────────────────
    # 高度都给 2.0:玩家从坡上过来时脚可能刚好在区域底板上下,给厚一点
    # 才不会漏判(Area3D 重叠判的是包围盒相交)。
    zones = (
        (STAIRS_FLOOR_AREAS[0], (0.0, -7.0, 1.0), (11.0, 9.0, 2.0)),
        (STAIRS_FLOOR_AREAS[1], (0.0, 5.5, STAIR_RISE + 1.0),
         (11.0, 4.0, 2.0)),
        (STAIRS_FLOOR_AREAS[2], (0.0, 15.5, STAIR_RISE * 2 + 1.0),
         (11.0, 4.0, 2.0)),
        (ROOF_AREA, (0.0, 31.0, STAIR_RISE * 3 + 1.0), (11.0, 14.0, 2.0)),
        (STAIRS_SAFE_AREA, (STAIRS_SAFE_POS[0], STAIRS_SAFE_POS[1], 1.0),
         (STAIRS_SAFE_SIZE[0], STAIRS_SAFE_SIZE[1], 2.0)),
    )
    for name, loc, size in zones:
        plate = make_box(name, size, loc, (0.20, 0.35, 0.30, 1.0),
                         scene=scene)
        plate_physics = getattr(plate, "gk_physics", None)
        if plate_physics is not None:
            plate_physics.hide_mesh = True
    make_marker(STAIRS_SAFE_ROOM, STAIRS_SAFE_POS + (0.0,), scene, size=1.2)

    # ── 灯 ──────────────────────────────────────────────────────
    # 三盏应急灯:断电时一起关(逻辑图里三张『灯光_设置』)。
    # 亮度按"能看出台阶、看不见脸"标定(2000W 在 25° 坡上会把墙面打出白斑)。
    # ⚠ 第一盏别压在出生点正上方:1600W 在 3 米内会把角色盒子打成纯白
    #   (实机截图 21_stairs 就是一坨白),往后挪到门口、降到 900W。
    for index, (y, z, energy) in enumerate(((-10.8, 3.2, 900.0),
                                            (6.0, STAIR_RISE + 3.0, 1400.0),
                                            (16.0, STAIR_RISE * 2 + 3.0,
                                             1400.0))):
        make_light(STAIRS_LIGHTS[index], "POINT", (0.0, y, z), energy,
                   (0.80, 0.86, 1.0), scene, cutoff=13.0)
    # 天台门口的红色指示灯:断电后**唯一还亮的东西**,给玩家方向。
    # ⚠ 红灯的"亮"不靠照明强度,靠**门楣自发光 + 一盏很小的灯**:
    #   520W / 射程 9 米时,站在门口那几米内它把整片天台地面(和雾)照成粉色
    #   (实机截图 22_choice 整屏洋红);170W 也只是好转一点。
    #   现在:门楣自己发光(glow)+ 60W / 射程 4 米的小灯点一下门缝 ——
    #   远处看到的是一个红点,不是一整片红雾。
    # 灯离**任何表面**都要 ≥2 米:贴着门楣放(0.3 米)时,60W 在平方反比下
    # 把门楣照成一块发光板,相机一靠近就是满屏粉。摆到天台中间偏门口,
    # 40W / 射程 5 米 —— 只在地上留一圈暗红。
    # ⚠ 灯离**任何表面**都要 ≥2 米:贴着门楣放(0.3 米)时 60W 在平方反比下
    #   把整根门楣照成一块发光板,第三人称相机被门口挡住时就是满屏粉
    #   (实机截图 22_choice 连踩三次)。现在摆在天台中间偏门口,
    #   40W / 射程 5 米 —— 只在地上留一圈暗红,门框另有 0.5 米的小指示灯。
    make_light("楼梯间天台红灯", "POINT", (0.0, ROOF_START - 2.4,
                                           door_z + 1.1), 25.0,
               (1.0, 0.20, 0.16), scene, cutoff=5.0)
    # 天台月光:露天那一块才照得到(井道有天花板挡着)。
    make_light("楼梯间月光", "SUN", (0.0, 33.0, 16.0), 0.55,
               (0.62, 0.70, 0.95), scene,
               rotation=(math.radians(58.0), 0.0, math.radians(30.0)))

    side = reuse_camera(scene, SIDE_CAM, (0.0, -22.0, 6.0),
                        (math.radians(76.0), 0.0, 0.0), lens=24.0)
    reuse_camera(scene, TOP_CAM, (0.0, 10.0, 40.0),
                 (math.radians(0.0), 0.0, 0.0), lens=20.0)
    scene.camera = side
    log(f"{STAIRS}:门厅 + 3 段坡道(每段 {STAIR_RUN:g}m/升 {STAIR_RISE:g}m)"
        f" + 3 个平台 + 天台(y={ROOF_START:g}..{ROOF_END:g})"
        f" + 天台门 + {STAIRS_ENEMY}"
        f" + 三层区域 + 天台区域 + 安全屋 + 3 盏应急灯 + 天台红灯/月光")
    return scene, player, chaser


def build_stairs_ai_tree():
    """第 5 章的追兵:**只有追和丢**。

    它没有巡逻的闲情 —— 断电那一刻『AI_启用』才把它启动(见关卡图的
    断电链),起始状态就是**追击**。玩家跑到天台后关门,它丢失目标就
    退回巡逻(在门厅里来回走)。
    """
    tree = bpy.data.node_groups.new(STAIRS_AI_TREE_NAME, "GKGameAiTree")
    tree.use_fake_user = True
    chase = _add(tree, "GKNodeAiState", "追击", 700.0, 0.0,
                 gk_state_name="追击", gk_behavior="CHASE",
                 gk_goal="楼梯间玩家", gk_speed=STAIRS_CHASE_SPEED,
                 gk_stop_distance=CHASE_STOP, gk_facing="GOAL")
    patrol = _add(tree, "GKNodeAiState", "巡逻", 700.0, 320.0,
                  gk_state_name="巡逻", gk_behavior="PATROL",
                  gk_patrol_a=STAIRS_PATROL_A, gk_patrol_b=STAIRS_PATROL_B,
                  gk_speed=PATROL_SPEED, gk_stop_distance=0.7,
                  gk_facing="MOVE", gk_in_count=2)
    see = _add(tree, "GKNodeAiTransition", "看见玩家", 340.0, 160.0,
               gk_cond_source="SEE", gk_goal="楼梯间玩家",
               gk_sight=STAIRS_SIGHT)
    back = _add(tree, "GKNodeAiTransition", "丢失玩家", 340.0, -180.0,
                gk_cond_source="LOST", gk_goal="楼梯间玩家",
                gk_sight=STAIRS_LOST)
    start = _add(tree, "GKNodeAiStart", "AI_开始", -360.0, 0.0)
    _link(tree, start, "开始", chase, "进入")
    _link(tree, chase, "转到", back, "上游")
    _link(tree, back, "转到", patrol, "进入 2")
    _link(tree, patrol, "转到", see, "上游")
    _link(tree, see, "转到", chase, "进入 2")
    _add(tree, "GKNodeAiOutput", "追击输出", 1100.0, -80.0,
         gk_state_name="追击", gk_var="is_chasing")
    log(f"AI 图『{tree.name}』:追击({STAIRS_CHASE_SPEED:.1f}m/s,视线 "
        f"{STAIRS_SIGHT:g})--丢失({STAIRS_LOST:g})--> 巡逻,再看见->追击")
    return tree


def build_title():
    """第零幕 标题页:地面 + 相机 + 灯。

    标题逻辑挂**场景根**(关卡图),不再需要那块"看不见的管理器" ——
    以前是为"物体才能挂脚本"准备的假宿主,现在关卡图直接 extends Node3D。
    """
    scene = bpy.data.scenes.new(TITLE)
    make_box("标题地面", (20.0, 20.0, 0.4), (0.0, 0.0, -0.2),
             (0.06, 0.06, 0.09, 1.0), scene=scene)
    camera = make_camera(TITLE_CAM, (0.0, -10.0, 2.4),
                         (math.radians(76.0), 0.0, 0.0), scene)
    scene.camera = camera
    make_light("标题灯", "SUN", (3.0, -4.0, 6.0), 1.0, (0.70, 0.75, 0.92),
               scene, rotation=(math.radians(50.0), 0.0, math.radians(25.0)))
    log(f"{TITLE}:标题地面 + {TITLE_CAM} + 标题灯")
    return scene


def write_physics(scene, player, enemy=None, torch=None, triggers=()):
    """角色体 / 静态体 / 触发区 / 手电筒,按物体归位。"""
    for obj in scene.objects:
        physics = getattr(obj, "gk_physics", None)
        if physics is None:
            continue
        try:
            physics.shape_size = (0.0, 0.0, 0.0)
        except Exception:                                 # noqa: BLE001
            pass
        if obj is player:
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
            physics.script_target = True
        elif enemy is not None and obj is enemy:
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
        elif obj.name in set(triggers):
            physics.body_type = "Area3D"
            physics.dimension = "3"
            physics.is_trigger = True
        elif getattr(obj, "type", "") == "MESH":
            physics.body_type = "StaticBody3D"
            physics.dimension = "3"
    if torch is not None:
        torch_physics = getattr(torch, "gk_physics", None)
        if torch_physics is not None:
            torch_physics.light_follow_camera = True
            log(f"{TORCH}:已勾『跟随相机(手电筒)』")


# ==========================================================================
# 节点图
# ==========================================================================
def _add(tree, kind, name, x, y, **props):
    node = tree.nodes.new(kind)
    node.name = name
    node.location = (x, y)
    for key, value in props.items():
        setattr(node, key, value)
    return node


def _link(tree, source, source_pin, target, target_pin):
    tree.links.new(source.outputs[source_pin], target.inputs[target_pin])


def build_ai_tree():
    """走廊的怪物 AI:巡逻 ←→ 追击 + 听见动静去查看。

    只放"怪物怎么动";HUD / 失败判定 / 音频全在关卡图里。
    对外给两个布尔(『AI_输出』),关卡图用『变量_读取』读:
      is_chasing     = 正在追击(紧张度"被看见"、失败判定都靠它)
      is_investigating = 正在去查看(想换氛围音时用)
    """
    tree = bpy.data.node_groups.new(AI_TREE_NAME, "GKGameAiTree")
    tree.use_fake_user = True

    patrol = _add(tree, "GKNodeAiState", "巡逻", -60.0, 0.0,
                  gk_state_name="巡逻", gk_behavior="PATROL",
                  gk_patrol_a=PATROL_A, gk_patrol_b=PATROL_B,
                  gk_speed=PATROL_SPEED, gk_stop_distance=0.7,
                  gk_facing="MOVE", gk_in_count=3)
    chase = _add(tree, "GKNodeAiState", "追击", 700.0, 0.0,
                 gk_state_name="追击", gk_behavior="CHASE",
                 gk_goal="走廊玩家", gk_speed=CHASE_SPEED,
                 gk_stop_distance=CHASE_STOP, gk_facing="GOAL")
    look = _add(tree, "GKNodeAiState", "去查看", 700.0, 340.0,
                gk_state_name="去查看", gk_behavior="INVESTIGATE",
                gk_speed=LOOK_SPEED, gk_stop_distance=LOOK_STOP,
                gk_facing="MOVE")
    see = _add(tree, "GKNodeAiTransition", "看见玩家", 320.0, 180.0,
               gk_cond_source="SEE", gk_goal="走廊玩家", gk_sight=SIGHT)
    back = _add(tree, "GKNodeAiTransition", "丢失玩家", 320.0, -210.0,
                gk_cond_source="LOST", gk_goal="走廊玩家", gk_sight=LOST_RANGE)
    heard = _add(tree, "GKNodeAiTransition", "听见动静", 320.0, 340.0,
                 gk_cond_source="HEARD", gk_ear=0.0, gk_memory=EAR_MEMORY)
    calm = _add(tree, "GKNodeAiTransition", "安静下来", 700.0, 480.0,
                gk_cond_source="CALM", gk_ear=0.0, gk_memory=EAR_MEMORY)

    # 显式起点:巡逻有三条入边,循环图里"没人指向的状态"选不出入口,
    # 不接『AI_开始』的话起始状态会随节点顺序漂(实测漂到了『去查看』)。
    start = _add(tree, "GKNodeAiStart", "AI_开始", -360.0, 0.0)
    _link(tree, start, "开始", patrol, "进入")
    _link(tree, patrol, "转到", see, "上游")
    _link(tree, see, "转到", chase, "进入")
    _link(tree, chase, "转到", back, "上游")
    _link(tree, back, "转到", patrol, "进入 2")
    _link(tree, patrol, "转到", heard, "上游")
    _link(tree, heard, "转到", look, "进入")
    _link(tree, look, "转到", calm, "上游")
    # 巡逻现在有三条入边(起点 / 丢失 / 安静):第三条用『进入 3』
    _link(tree, calm, "转到", patrol, "进入 3")

    # 对外布尔:不用连线,关卡图按名字读。
    _add(tree, "GKNodeAiOutput", "追击输出", 1100.0, -80.0,
         gk_state_name="追击", gk_var="is_chasing")
    _add(tree, "GKNodeAiOutput", "查看输出", 1100.0, 120.0,
         gk_state_name="去查看", gk_var="is_investigating")
    _add(tree, "GKNodeComment", "说明", -500.0, 700.0,
         gk_text=("AI 图只负责怪物的行为。\n"
                  "关卡图里『AI_启用』启动它,并用『变量_读取』读 "
                  "is_chasing / is_investigating 做失败判定和氛围切换。"))
    log(f"AI 图『{tree.name}』:巡逻 --看见({SIGHT:g})--> 追击 "
        f"--丢失({LOST_RANGE:g})--> 巡逻;巡逻 --听见--> 去查看 --安静--> 巡逻;"
        f"输出 is_chasing / is_investigating")
    return tree


def _frame(tree, label, nodes, color=(0.16, 0.30, 0.45)):
    """把一组节点框进 Blender 原生的 NodeFrame(纯显示,不影响导出)。

    坐标换算:NodeFrame 的子节点 `location` 是**相对框原点**的,所以先记下
    绝对值,再把 parent 指过去、减去框原点 —— 否则整组会"跳"到别处。
    """
    nodes = [node for node in nodes if node is not None]
    if not nodes:
        return None
    xs = [node.location.x for node in nodes]
    ys = [node.location.y for node in nodes]
    x0 = min(xs) - 120.0
    y0 = min(ys) - 240.0
    width = max(420.0, max(xs) - min(xs) + 620.0)
    height = max(180.0, max(ys) - min(ys) + 380.0)
    frame = tree.nodes.new("NodeFrame")
    frame.name = label
    frame.label = label
    frame.location = (x0, y0)
    frame.width = width
    frame.height = height
    frame.use_custom_color = True
    frame.color = color
    if hasattr(frame, "label_size"):
        frame.label_size = 18
    for node in nodes:
        absolute = (node.location.x, node.location.y)
        node.parent = frame
        node.location = (absolute[0] - x0, absolute[1] - y0)
    return frame


def _level_tree(name):
    """新建一张关卡逻辑图(挂场景根)。"""
    tree = bpy.data.node_groups.new(name, "GKGameLogicTree")
    tree.gk_scope = "LEVEL"
    tree.use_fake_user = True
    return tree


def _set_scene_logic(scene, tree):
    settings = getattr(scene, "gk_settings", None)
    if settings is not None and hasattr(settings, "logic_tree"):
        settings.logic_tree = tree.name


def _ready_of(tree):
    for node in tree.nodes:
        if node.bl_idname == "GKNodeOnReady":
            return node
    raise RuntimeError(f"{tree.name} 里没有『准备』节点")


def build_player_role(scene, player):
    """建**一张**三角色关共用的角色图:输入 / 移动 / 跳跃 / 视角 / 机位。

    三关的相机统一叫『侧视机位』『俯瞰机位』,所以同一张图走到哪一关都能用。
    玩法(HUD / 存档 / 触发区 / AI 启动)一律放关卡图 —— 角色脚本只负责"人"。
    """
    from BlenderGameKit import presets
    logic, _anim, notes = presets.create_character_preset(
        scene, character=player,
        logic_name=ROLE_TREE_NAME,
        class_name="HorrorPlayer",
        with_animation=False,
        with_ports=False,
        with_god_camera=True,
        camera_side=SIDE_CAM,
        camera_top=TOP_CAM,
        footstep="res://audio/footstep.wav",
    )
    for note in notes:
        log(f"预设({ROLE_TREE_NAME}): {note}")
    # M0:走 / 蹲 / 跑三档速度(三关共用这一张图,所以哪儿都能蹲能跑)
    _wire_crouch_run(logic)
    # class_name 全工程唯一;三关各挂同一张图,不要每关都注册一个同名类。
    settings = getattr(scene, "gk_settings", None)
    if settings is not None and hasattr(settings, "class_name"):
        settings.class_name = ""
    return logic


def _wire_crouch_run(logic):
    """蹲走 / 跑动:每物理帧按住的键决定 move_speed。

    ── 为什么改 move_speed 就够 ──────────────────────────────────
    角色预设里的『移动_按方向』把速度存成**成员变量** move_speed
    (生成的 _physics_process 里是 velocity.x = dir.x * move_speed),
    所以『变量_设置』直接写它 = 改走路速度,不用另造一套移动逻辑。

    ── 为什么用嵌套『如果_否则』而不是三个并列判断 ───────────────
    蹲 > 跑 > 走 有优先级:按着 Ctrl 还按着 Shift 时**听蹲的**
    (蹲是潜行意图)。并列三个判断会互相覆盖,顺序取决于节点位置,
    读图的人也看不出谁赢。
    """
    # ⚠ 不能写成 `move_speed`:预设把『移动_按方向』的速度做成了
    #   **检查器参数** @export var move_speed,再声明一个同名脚本变量会被
    #   导出侧改名(实测:参数变 move_speed_param,而移动代码读的是改名的那个
    #   —— 于是 SetVar 写了个没人读的变量,蹲跑完全没效果)。
    #   正确做法:把速度**引脚**接上自己的变量,让移动代码读它。
    mover = next((n for n in logic.nodes
                  if n.bl_idname == "GKNodeMoveDirection"), None)
    read_speed = _add(logic, "GKNodeGetVar", "读移动速度", -1700.0, -1500.0,
                      gk_var_name=SPEED_VAR)
    if mover is not None:
        _link(logic, read_speed, "值", mover, "速度")
    phys = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(蹲跑)",
                -1500.0, -1500.0)
    crouch_key = _add(logic, "GKNodeKey", "按住LeftCtrl", -1500.0, -1700.0,
                      gk_key=CTRL_KEY, gk_action=CTRL_ACTION,
                      gk_trigger="PRESSED")
    crouch_branch = _add(logic, "GKNodeBranch", "在蹲吗", -1300.0, -1500.0)
    set_crouch = _add(logic, "GKNodeSetVar", "蹲走速度", -1100.0, -1400.0,
                      gk_var_name=SPEED_VAR, gk_value=CROUCH_SPEED)
    run_key = _add(logic, "GKNodeKey", "按住LeftShift", -1300.0, -1720.0,
                   gk_key=SHIFT_KEY, gk_action=SHIFT_ACTION,
                   gk_trigger="PRESSED")
    run_branch = _add(logic, "GKNodeBranch", "在跑吗", -900.0, -1600.0)
    set_run = _add(logic, "GKNodeSetVar", "跑动速度", -700.0, -1500.0,
                   gk_var_name=SPEED_VAR, gk_value=RUN_SPEED)
    set_walk = _add(logic, "GKNodeSetVar", "走路速度", -700.0, -1720.0,
                    gk_var_name=SPEED_VAR, gk_value=BASE_SPEED)
    _link(logic, phys, "执行", crouch_branch, "执行")
    _link(logic, crouch_key, "结果", crouch_branch, "条件")
    _link(logic, crouch_branch, "是", set_crouch, "执行")
    _link(logic, crouch_branch, "否", run_branch, "执行")
    _link(logic, run_key, "结果", run_branch, "条件")
    _link(logic, run_branch, "是", set_run, "执行")
    _link(logic, run_branch, "否", set_walk, "执行")
    log(f"角色图:蹲走({CTRL_KEY} {CROUCH_SPEED:g}) / 跑动({SHIFT_KEY} "
        f"{RUN_SPEED:g}) / 走路 {BASE_SPEED:g}")
    return [phys, crouch_key, crouch_branch, set_crouch, run_key, run_branch,
            set_run, set_walk]


def adopt_player(scene, player):
    """让另外两关的角色复用同一张角色图(不再各建一张)。"""
    physics = getattr(player, "gk_physics", None)
    if physics is None:
        raise RuntimeError(f"『{player.name}』没有『游戏物理』属性")
    physics.enabled = True
    physics.dimension = "3"
    physics.body_type = "CharacterBody3D"
    physics.script_target = True
    physics.logic_tree = ROLE_TREE_NAME
    settings = getattr(scene, "gk_settings", None)
    if settings is not None:
        for attr, value in (("dimension", "3"), ("class_name", ""),
                            ("god_camera", True)):
            if hasattr(settings, attr):
                try:
                    setattr(settings, attr, value)
                except Exception:                         # noqa: BLE001
                    pass


def _add_crouch_script(logic, x, y, name="蹲走相机脚本"):
    """挂『脚本_加载』:蹲走时压低相机 + 碰撞胶囊(三关都挂,脚本自己认人)。

    ⚠ 必须**三关都挂**:验收探针在**教室**量 crouch(角色图只管速度,
    相机/胶囊这一半在脚本里);只在走廊挂的话教室量到的是 0
    (第一轮实机 PLAY_MISSING 就是这么来的)。
    """
    return _add(logic, "GKNodeScriptLoad", name, x, y,
                gk_script_text=bpy.data.texts[CROUCH_SCRIPT_NAME])


def _add_ui_polish(logic, x, y, name="界面修补脚本"):
    """挂『脚本_加载』:运行时把字幕板/选择框的底色改结实(见 UI_POLISH_SCRIPT)。"""
    return _add(logic, "GKNodeScriptLoad", name, x, y,
                gk_script_text=bpy.data.texts[UI_POLISH_SCRIPT_NAME])


def _hud_count(logic, prefix, x, y):
    """纸条计数 HUD:定时读变量 -> 『纸条 N/3』(一处更新,三关复用思路)。

    不逐次拾取各连一遍:『定时触发』每 0.2 秒读一次变量,
    拾到纸条 / 读档回来 / 改档被拒 都会自动反映到同一行字上。
    """
    timer = _add(logic, "GKNodeOnTimer", f"{prefix}刷新", x, y,
                 gk_seconds=0.2, gk_repeat=True)
    read = _add(logic, "GKNodeGetVar", f"{prefix}读纸条数", x, y - 180.0,
                gk_var_name="纸条数")
    number = _add(logic, "GKNodeNumberText", f"{prefix}数字", x + 220.0,
                  y - 180.0)
    joined = _add(logic, "GKNodeTextJoin", f"{prefix}拼成N/3", x + 440.0,
                  y - 180.0, gk_middle="/", gk_b=str(NOTE_GOAL))
    label = _add(logic, "GKNodeTextJoin", f"{prefix}加标签", x + 660.0,
                 y - 180.0, gk_a="纸条 ")
    hud = _add(logic, "GKNodeUiText", f"{prefix}纸条HUD", x + 880.0, y,
               gk_text=f"纸条 0/{NOTE_GOAL}", gk_x=18.0, gk_y=46.0, gk_size=18)
    _link(logic, read, "值", number, "数值")
    _link(logic, number, "文字", joined, "甲")
    _link(logic, joined, "结果", label, "乙")
    _link(logic, label, "结果", hud, "文字")
    _link(logic, timer, "执行", hud, "执行")
    return [timer, read, number, joined, label, hud]


def _build_journal_ui(logic, x, y):
    """笔记日志面板:底板 + 标题 + 三行正文(**三关共用同一套名字**)。

    名字(日志面板 / 日志标题 / 日志行N)三关逐字一样 —— 每关有自己的一份
    脚本,重名不会打架;而探针(play_horror_full.py)是靠"按 J 之后可见
    控件变多"来量日志的,名字和层级一致才能三关都量到。

    返回 (面板节点, 三行正文节点, 已串好链的节点列表:入口在前、末尾在后)。
    """
    panel = _add(logic, "GKNodeUiPanel", LOG_PANEL, x, y,
                 gk_x=0.0, gk_y=0.0, gk_width=780.0, gk_height=440.0,
                 gk_radius=14, gk_align="CENTER", gk_margin=0.0,
                 gk_color=(0.05, 0.06, 0.09, 0.92),
                 gk_start_hidden=True)
    title = _add(logic, "GKNodeUiText", "日志标题", x + 200.0, y,
                 gk_text="笔记日志      J / Esc 关闭",
                 gk_align="TOP_LEFT", gk_size=20,
                 gk_color=(0.95, 0.90, 0.70, 1.0),
                 gk_parent_widget=panel.name, gk_x=30.0, gk_y=22.0)
    lines = []
    chain = [panel, title]
    for index in range(NOTE_GOAL):
        line = _add(logic, "GKNodeUiText", f"日志行{index + 1}",
                    x + 400.0 + index * 240.0, y, gk_text="",
                    gk_align="TOP_LEFT", gk_size=17,
                    gk_parent_widget=panel.name,
                    gk_x=30.0, gk_y=76.0 + index * 118.0,
                    gk_color=(0.86, 0.88, 0.94, 1.0))
        lines.append(line)
        chain.append(line)
    for earlier, later in zip(chain, chain[1:]):
        _link(logic, earlier, "完成", later, "执行")

    # ── 收集状态行(第一百七十轮):档案系统的可见层 ──────────────────
    # 面板底部一行动态计数:档案 · 纸条 X/3 · 病历 Y/3 · 隐藏笔记 Z/3。
    # 0.25 秒一刷(照抄病历 HUD 的 timer 模式);三关共用本函数,
    # 三个计数变量由各关的"载入存档 + 归零链"保证有 float 值
    # (『文本_数字』的参数是 typed float,null 会当场炸)。
    status_timer = _add(logic, "GKNodeOnTimer", "档案刷新", x - 200.0, y,
                        gk_seconds=0.25, gk_repeat=True)
    counts = (("纸条数", "纸条", "读纸条数(档案)"), (MED_COUNT, "病历",
              "读病历数(档案)"), (HIDDEN_NOTE_COUNT, "隐藏笔记",
              "读隐藏笔记数(档案)"))
    join_prev = None
    for index, (var_name, label, read_name) in enumerate(counts):
        read = _add(logic, "GKNodeGetVar", read_name,
                    x + index * 300.0, y - 160.0, gk_var_name=var_name)
        num = _add(logic, "GKNodeNumberText", f"{label}数字",
                   x + 150.0 + index * 300.0, y - 320.0)
        seg = _add(logic, "GKNodeTextJoin", f"{label}N/3",
                   x + 300.0 + index * 300.0, y - 320.0,
                   gk_middle="/", gk_b="3")
        _link(logic, read, "值", num, "数值")
        _link(logic, num, "文字", seg, "甲")
        head = _add(logic, "GKNodeTextJoin", f"{label}前缀",
                    x + 450.0 + index * 300.0, y - 320.0,
                    gk_a=f"档案 · {label} " if index == 0
                    else f" · {label} ")
        if join_prev is not None:
            _link(logic, join_prev, "结果", head, "甲")
        _link(logic, seg, "结果", head, "乙")
        join_prev = head
    status = _add(logic, "GKNodeUiText", "日志档案", x + 400.0, y + 240.0,
                  gk_text="档案 · 纸条 0/3 · 病历 0/3 · 隐藏笔记 0/3",
                  gk_align="TOP_LEFT", gk_size=16,
                  gk_parent_widget=panel.name, gk_x=30.0, gk_y=398.0,
                  gk_color=(0.62, 0.78, 0.72, 1.0))
    _link(logic, join_prev, "结果", status, "文字")
    _link(logic, status_timer, "执行", status, "执行")

    return panel, lines, chain


def _journal_restore(logic, x, y, lines):
    """开机:按存档里的『已读N』把正文填回面板。

    ── 存档信封里放了什么(M0.5 第一百六十轮)────────────────────────
    三个布尔:『已读1 / 已读2 / 已读3』,和『纸条数』『结局』放在同一个
    字典里,跟着『存档_保存』的信封一起落盘(教室门口 / 安全屋 / 楼梯间
    三处都勾了『防改档』,所以它们一起被 sha256 封住)。

    ── 旧档怎么办(向前兼容的关键)──────────────────────────────────
    读侧是 `data.get("已读1", 0.0)`:`存档_读取到变量` 的默认值给 0
    (False 的同义值),所以
      * 老存档(信封里没有这三个键)-> 读到 0 -> 当"没读过",日志空白;
      * 手改过 / 校验对不上的档 -> 上层已经当空档处理 -> 同样读到 0;
      * 新档 -> 读到 true -> 正文照常显示。
    三种情况都不抛错,面板也照常能开(J 永远可用,只是没内容)。

    为什么不是"if 已读 then 设文字 else 设空":每行一条独立小链
    (变量_读取 -> 条件分支 -> 界面_设定),三条都挂在同一个执行输出上
    —— 这是本仓库图里"一个输出接多条链"的既有用法(教室的 E 交互就是
    这么分三张纸条的),不需要额外的汇合节点。
    """
    gets = []
    for index in range(NOTE_GOAL):
        gets.append(_add(logic, "GKNodeSaveGet", f"读回已读{index + 1}",
                         x, y - index * 200.0,
                         gk_key=f"已读{index + 1}",
                         gk_var=f"已读{index + 1}", gk_default=0.0))
    for earlier, later in zip(gets, gets[1:]):
        _link(logic, earlier, "完成", later, "执行")
    # ── 为什么这里要一个『序列』节点 ────────────────────────────────
    # 三条"填正文"小链和主流程都要从同一个输出岔出去。直接一个输出接四条
    # 链也不是不行(教室的 E 交互就这么写),但**主流程下游有『等待』**
    # (开场的 settle / 走廊的惊吓),生成器会告警"执行顺序依赖节点位置"。
    # 用『序列』的编号出口把"三条填字"和"继续走"分开,顺序就是确定的:
    # 1/2/3 填字,4 才继续开 AI / 开场运镜。
    sequence = _add(logic, "GKNodeSequence", "填完日志再继续",
                    x + 200.0, y + 220.0, gk_step_count=NOTE_GOAL + 1)
    _link(logic, gets[-1], "完成", sequence, "执行")
    extra = [sequence]
    for index in range(NOTE_GOAL):
        read = _add(logic, "GKNodeGetVar", f"看已读{index + 1}",
                    x + 220.0, y - 140.0 - index * 200.0,
                    gk_var_name=f"已读{index + 1}")
        branch = _add(logic, "GKNodeBranch", f"读过{index + 1}吗",
                      x + 420.0, y - 40.0 - index * 200.0)
        text = _add(logic, "GKNodeConstText", f"读档文本{index + 1}",
                    x + 420.0, y - 160.0 - index * 200.0,
                    gk_value=f"{NOTE_TITLES[index]}\n{NOTE_BODIES[index]}")
        fill = _add(logic, "GKNodeUiSet", f"填日志行{index + 1}",
                    x + 620.0, y - 40.0 - index * 200.0,
                    gk_widget=lines[index].name, gk_set_text=True)
        _link(logic, read, "值", branch, "条件")
        _link(logic, branch, "是", fill, "执行")
        _link(logic, text, "值", fill, "文字")
        # 『变量_读取』是纯取值节点(没有执行引脚):流程直接进分支,
        # 值从旁边接进『条件』—— 和教室里"读目标 / 看看的是我吗"同一套写法。
        _link(logic, sequence, str(index + 1), branch, "执行")
        extra += [read, branch, text, fill]
    return gets[0], sequence, str(NOTE_GOAL + 1), gets + extra


def _journal_toggle(logic, x, y, panel_name, prefix=""):
    """J 开 / J 再按关 / Esc 关(节点名带前缀,免得和本关其它节点撞)。

    按键一律走**输入动作**(`gk_action`),不轮询 keycode —— 合成事件
    只有动作才量得到(实机踩过)。
    """
    phys = _add(logic, "GKNodeOnPhysicsProcess", f"{prefix}物理帧(日志)", x, y)
    read_open = _add(logic, "GKNodeGetVar", f"{prefix}读日志打开(J)",
                     x, y - 160.0, gk_var_name=LOG_OPEN)
    read_open_esc = _add(logic, "GKNodeGetVar", f"{prefix}读日志打开(Esc)",
                         x, y - 280.0, gk_var_name=LOG_OPEN)
    j_key = _add(logic, "GKNodeKey", f"{prefix}按J", x + 200.0, y - 160.0,
                 gk_key=J_KEY, gk_action=J_ACTION, gk_trigger="JUST")
    esc_key = _add(logic, "GKNodeKey", f"{prefix}按Esc", x + 200.0, y - 280.0,
                   gk_key=ESC_KEY, gk_action=ESC_ACTION, gk_trigger="JUST")
    j_branch = _add(logic, "GKNodeBranch", f"{prefix}按J了吗", x + 400.0, y)
    esc_branch = _add(logic, "GKNodeBranch", f"{prefix}按Esc了吗",
                      x + 400.0, y - 280.0)
    open_branch = _add(logic, "GKNodeBranch", f"{prefix}日志开着吗(J)",
                       x + 600.0, y)
    esc_open_branch = _add(logic, "GKNodeBranch", f"{prefix}日志开着吗(Esc)",
                           x + 600.0, y - 280.0)
    open_true = _add(logic, "GKNodeConstBool", f"{prefix}日志开",
                     x + 400.0, y + 140.0, gk_value=True)
    open_false = _add(logic, "GKNodeConstBool", f"{prefix}日志关(常)",
                      x + 400.0, y - 420.0, gk_value=False)
    show_panel = _add(logic, "GKNodeUiHide", f"{prefix}显示日志面板",
                      x + 800.0, y + 140.0, gk_widget=panel_name,
                      gk_visible=True)
    hide_panel = _add(logic, "GKNodeUiHide", f"{prefix}隐藏日志面板(J)",
                      x + 800.0, y - 40.0, gk_widget=panel_name,
                      gk_visible=False)
    hide_panel_esc = _add(logic, "GKNodeUiHide", f"{prefix}隐藏日志面板(Esc)",
                          x + 800.0, y - 280.0, gk_widget=panel_name,
                          gk_visible=False)
    set_open = _add(logic, "GKNodeSetVar", f"{prefix}记日志开", x + 1000.0,
                    y + 140.0, gk_var_name=LOG_OPEN)
    set_closed = _add(logic, "GKNodeSetVar", f"{prefix}记日志关(J)",
                      x + 1000.0, y - 40.0, gk_var_name=LOG_OPEN)
    set_closed_esc = _add(logic, "GKNodeSetVar", f"{prefix}记日志关(Esc)",
                          x + 1000.0, y - 280.0, gk_var_name=LOG_OPEN)
    _link(logic, phys, "执行", j_branch, "执行")
    _link(logic, j_key, "结果", j_branch, "条件")
    _link(logic, j_branch, "是", open_branch, "执行")
    _link(logic, read_open, "值", open_branch, "条件")
    _link(logic, open_branch, "否", show_panel, "执行")
    _link(logic, show_panel, "完成", set_open, "执行")
    _link(logic, open_true, "值", set_open, "值")
    _link(logic, open_branch, "是", hide_panel, "执行")
    _link(logic, hide_panel, "完成", set_closed, "执行")
    _link(logic, open_false, "值", set_closed, "值")
    _link(logic, phys, "执行", esc_branch, "执行")
    _link(logic, esc_key, "结果", esc_branch, "条件")
    _link(logic, esc_branch, "是", esc_open_branch, "执行")
    _link(logic, read_open_esc, "值", esc_open_branch, "条件")
    _link(logic, esc_open_branch, "是", hide_panel_esc, "执行")
    _link(logic, hide_panel_esc, "完成", set_closed_esc, "执行")
    _link(logic, open_false, "值", set_closed_esc, "值")
    return [phys, read_open, read_open_esc, j_key, esc_key, j_branch,
            esc_branch, open_branch, esc_open_branch, open_true, open_false,
            show_panel, hide_panel, hide_panel_esc, set_open, set_closed,
            set_closed_esc]


def _journal_close(logic, x, y, prefix=""):
    """开机把『日志打开』显式置 false:探针开局读到的是确定的类型和值。"""
    const = _add(logic, "GKNodeConstBool", f"{prefix}开机:日志关", x, y,
                 gk_value=False)
    node = _add(logic, "GKNodeSetVar", f"{prefix}开机:日志关闭", x + 200.0,
                y, gk_var_name=LOG_OPEN)
    _link(logic, const, "值", node, "值")
    return [const, node], node


def build_room_logic(scene):
    """第一关 · 关卡图:开场运镜 + 氛围 + 3 张纸条 + 门口门禁。

    ⚠ 门禁放在**教室门口**而不是楼梯间:纸条没齐就不让走,避免
      "进了楼梯间才发现门打不开、又回不去"的死局(旧版真踩到)。
    """
    logic = _level_tree(ROOM_LEVEL_TREE)
    _set_scene_logic(scene, logic)
    ready = _add(logic, "GKNodeOnReady", "每当_准备", -1500.0, 1500.0)
    bgm = _add(logic, "GKNodePlayMusic", "教室BGM", -1300.0, 1500.0,
               gk_path=BGM_FILE, gk_volume=-18.0, gk_fade=2.0)
    vignette = _add(logic, "GKNodeScreenVignette", "教室暗角", -1100.0, 1500.0,
                    gk_amount=0.35, gk_radius=0.65, gk_edge=0.40)
    goal = _add(logic, "GKNodeUiText", "教室目标", -900.0, 1500.0,
                gk_text=f"晚自习后教学楼熄了灯……先找齐 {NOTE_GOAL} 张纸条",
                gk_x=18.0, gk_y=18.0, gk_size=18)
    # ⚠ 运镜开始前先把侧视机位设为**当前**:『相机_切换』是从"视口当前相机"
    #   起飞的;刚切场景时那台可能是标题机位(朝天空),于是开场飞到一半
    #   全是空背景(实机游玩截图抓到)。
    # ⚠ 先等一两帧:侧视机位的**游戏朝向**由角色图的弹簧臂每物理帧摆;
    #   刚 _ready 时它还停在 Blender 的原始朝向(朝上),直接从这里起飞
    #   会拍到整片天空(实机开场截图就是纯背景)。
    settle = _add(logic, "GKNodeWait", "等弹簧臂就位", -700.0, 1280.0)
    settle.inputs["秒"].default_value = 0.12
    reset_side = _add(logic, "GKNodeCameraSetCurrent", "先回侧视",
                      -500.0, 1280.0, gk_target=SIDE_CAM, gk_blend=0.0)
    opening = _add(logic, "GKNodeCameraSetCurrent", "开场运镜", -300.0, 1280.0,
                   gk_target=ROOM_CLOSE, gk_blend=OPENING_BLEND,
                   gk_blend_trans="EASE_OUT",
                   gk_path_mode="CURVE", gk_path_curve=ROOM_TRACK)
    hold = _add(logic, "GKNodeWait", "特写停留", -100.0, 1280.0)
    hold.inputs["秒"].default_value = OPENING_HOLD
    back = _add(logic, "GKNodeCameraSetCurrent", "回第三人称", 100.0, 1280.0,
                gk_target=SIDE_CAM, gk_blend=RETURN_BLEND)
    _link(logic, ready, "执行", bgm, "执行")
    _link(logic, bgm, "完成", vignette, "执行")
    _link(logic, vignette, "完成", goal, "执行")
    # ── M0:E 交互提示 + 笔记日志面板(界面_面板 + 界面_文本)──────────
    # 这两个控件必须在**章节卡 / 开场运镜的 await 之前**建出来 ——
    # 排在 settle(等一下)后面的话,开局那几秒按 E/J 都没反应。
    interact_tip = _add(logic, "GKNodeUiText", INTERACT_TIP, -900.0, 1120.0,
                        gk_text="", gk_align="BOTTOM_CENTER",
                        gk_margin=190.0, gk_size=22,
                        gk_color=(1.0, 0.95, 0.70, 1.0))
    # 日志面板本体由 _build_journal_ui 建(三关共用):名字一致,探针才量得到
    log_panel, log_lines, journal_ui = _build_journal_ui(logic, -700.0, 1120.0)
    # 面板 -> 标题 -> 三行正文,按这个顺序建;最后一个接回开场运镜。
    _link(logic, goal, "完成", interact_tip, "执行")
    _link(logic, interact_tip, "完成", journal_ui[0], "执行")
    tail = journal_ui[-1]
    # ── 读回存档里的『已读N』(M0.5:日志跨场景)──────────────────────
    # 教室是"第一关",看上去不用读档 —— 但它得能在**中途重进**的时候看到
    # 之前读过的东西(玩家在走廊退出、从标题再进教室),而且它自己写下去的
    # 三个布尔必须和走廊 / 楼梯间读到的是同一套键。
    save_load = _add(logic, "GKNodeSaveLoad", "载入存档(教室)", -700.0,
                     760.0, gk_slot="save1")
    restore_entry, restore_seq, restore_pin, restore_nodes = _journal_restore(
        logic, -700.0, 560.0, log_lines)
    archive_tail = _archive_zero_vars(logic, tail, -900.0, 900.0)
    _link(logic, archive_tail, "完成", save_load, "执行")
    _link(logic, save_load, "完成", restore_entry, "执行")
    # 开局把两个新变量显式摆到"空 / 关":探针开局就能读到确定的类型和值。
    target_empty = _add(logic, "GKNodeConstText", "开机:空目标", -100.0,
                        1000.0, gk_value="")
    target_init = _add(logic, "GKNodeSetVar", "开机:清空交互目标", 100.0,
                       1120.0, gk_var_name=INTERACT_TARGET)
    journal_close_nodes, log_init = _journal_close(logic, -100.0, 880.0)
    _link(logic, restore_seq, restore_pin, target_init, "执行")
    _link(logic, target_empty, "值", target_init, "值")
    _link(logic, target_init, "完成", log_init, "执行")
    crouch_script = _add_crouch_script(logic, 300.0, 1000.0, "蹲走脚本(教室)")
    polish = _add_ui_polish(logic, 500.0, 1120.0)
    _link(logic, log_init, "完成", crouch_script, "执行")
    _link(logic, crouch_script, "完成", polish, "执行")
    _link(logic, polish, "完成", settle, "执行")
    _link(logic, settle, "完成", reset_side, "执行")
    _link(logic, reset_side, "完成", opening, "执行")
    _link(logic, opening, "完成", hold, "执行")
    _link(logic, hold, "完成", back, "执行")
    _frame(logic, "① 开场 / 氛围", [ready, bgm, vignette, goal,
                                    interact_tip] + journal_ui +
           [save_load] + restore_nodes + journal_close_nodes +
           [target_empty, target_init, crouch_script, polish, settle,
            reset_side, opening, hold, back],
           (0.18, 0.22, 0.34))

    # ── 纸条:靠近出 [E] 提示,按 E 才真的拿走 ───────────────────────
    # 旧版是 Area3D 一进就自动拿(玩家没得选,也没有"交互"这件事);
    # 现在触发区只负责**报名字**给『交互目标』,真拿由 E 键那条链做。
    phys_e = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(E)",
                  -1500.0, -1300.0)
    e_key = _add(logic, "GKNodeKey", "按E", -1500.0, -1140.0,
                 gk_key=E_KEY, gk_action=E_ACTION, gk_trigger="JUST")
    e_branch = _add(logic, "GKNodeBranch", "按E了吗", -1300.0, -1300.0)
    _link(logic, e_key, "结果", e_branch, "条件")
    _link(logic, phys_e, "执行", e_branch, "执行")
    note_nodes = [phys_e, e_key, e_branch]
    for index, name in enumerate(NOTE_NAMES):
        y = 760.0 - index * 340.0
        name_const = _add(logic, "GKNodeConstText", f"名字{index + 1}",
                          -1900.0, y - 120.0, gk_value=name)
        empty_const = _add(logic, "GKNodeConstText", f"空名字{index + 1}",
                           -1900.0, y - 240.0, gk_value="")
        # ① 靠近:把名字写进『交互目标』,屏幕上出 [E]
        entered = _add(logic, "GKNodeOnAreaEntered", f"靠近{name}",
                       -1500.0, y, gk_area=name, gk_filter=ROOM_PLAYER)
        target_on = _add(logic, "GKNodeSetVar", f"目标={name}",
                         -1300.0, y, gk_var_name=INTERACT_TARGET)
        tip_on = _add(logic, "GKNodeUiSet", f"提示[E]{index + 1}",
                      -1100.0, y, gk_widget=interact_tip.name,
                      gk_set_text=True, gk_text="[E] 拾取纸条")
        _link(logic, entered, "执行", target_on, "执行")
        _link(logic, name_const, "值", target_on, "值")
        _link(logic, target_on, "完成", tip_on, "执行")
        # ② 走开:只在"目标还是我"的时候才清 —— 无条件清会踩到下面这个坑:
        #    从一个触发区直接踏进另一个时,body_exited 和 body_entered 落在
        #    同一物理帧,谁先谁后不确定;exit 后到就把刚设好的新目标抹掉了
        #    (实机探针里"传送到纸条3 -> 按 E 没反应"就是这个)。
        left = _add(logic, "GKNodeOnAreaExited", f"离开{name}",
                    -1900.0, y - 400.0, gk_area=name,
                    gk_filter=ROOM_PLAYER)
        off_read = _add(logic, "GKNodeGetVar", f"读目标(离开{index + 1})",
                        -1900.0, y - 520.0, gk_var_name=INTERACT_TARGET)
        off_cmp = _add(logic, "GKNodeCompare", f"离开时目标还是{name}吗",
                       -1700.0, y - 520.0, gk_op="EQ")
        off_branch = _add(logic, "GKNodeBranch", f"要清{name}吗",
                          -1500.0, y - 400.0)
        target_off = _add(logic, "GKNodeSetVar", f"清目标{index + 1}",
                          -1300.0, y - 400.0,
                          gk_var_name=INTERACT_TARGET)
        tip_off = _add(logic, "GKNodeUiSet", f"清提示{index + 1}",
                       -1100.0, y - 400.0,
                       gk_widget=interact_tip.name,
                       gk_set_text=True, gk_text="")
        _link(logic, off_read, "值", off_cmp, "A")
        _link(logic, name_const, "值", off_cmp, "B")
        _link(logic, off_cmp, "结果", off_branch, "条件")
        _link(logic, left, "执行", off_branch, "执行")
        _link(logic, off_branch, "是", target_off, "执行")
        _link(logic, empty_const, "值", target_off, "值")
        _link(logic, target_off, "完成", tip_off, "执行")
        # ③ 按 E:『交互目标』等于我 -> 拿走 + 计数 + 写进笔记日志
        read_t = _add(logic, "GKNodeGetVar", f"读目标{index + 1}",
                      -1300.0, -1500.0, gk_var_name=INTERACT_TARGET)
        cmp_n = _add(logic, "GKNodeCompare", f"看着{name}吗",
                     -1100.0, -1450.0, gk_op="EQ")
        br = _add(logic, "GKNodeBranch", f"是{name}吗", -950.0,
                  -1300.0)
        _link(logic, read_t, "值", cmp_n, "A")
        _link(logic, name_const, "值", cmp_n, "B")
        _link(logic, cmp_n, "结果", br, "条件")
        _link(logic, e_branch, "是", br, "执行")
        add_one = _add(logic, "GKNodeAddVar", f"纸条+1_{index + 1}",
                       -750.0, -1200.0, gk_var_name="纸条数",
                       gk_amount=1.0, gk_clamp=True, gk_min=0.0,
                       gk_max=float(NOTE_GOAL))
        say = _add(logic, "GKNodeUiSubtitle", f"拾到纸条{index + 1}",
                   -550.0, -1200.0,
                   gk_text=f"拾到《{NOTE_TITLES[index]}》" +
                   " —— 按 J 看笔记", gk_seconds=2.5, gk_speed=30.0)
        note_text = _add(logic, "GKNodeConstText", f"日志文本{index + 1}",
                          -750.0, -1350.0,
                          gk_value=f"{NOTE_TITLES[index]}" +
                          f"\n{NOTE_BODIES[index]}")
        write_line = _add(logic, "GKNodeUiSet", f"写日志行{index + 1}",
                         -350.0, -1200.0,
                         gk_widget=log_lines[index].name,
                         gk_set_text=True)
        # ④ 把"这张读过了"写进存档字典(键 = 已读1/2/3,值 = 布尔)
        #    —— 走廊 / 楼梯间开局读的就是这三个键。只改内存,真正落盘是
        #    门口那次『存档_保存』(信封 + 校验和,和纸条数一起封住)。
        flag_on = _add(logic, "GKNodeConstBool", f"读过{index + 1}",
                       -350.0, -1380.0, gk_value=True)
        flag_save = _add(logic, "GKNodeSaveSet", f"存已读{index + 1}",
                         -150.0, -1200.0, gk_key=f"已读{index + 1}")
        remove = _add(logic, "GKNodeQueueFree", f"拿走{name}", 50.0,
                      -1200.0, gk_target=name)
        clear_target = _add(logic, "GKNodeSetVar",
                            f"清目标(拾取){index + 1}", 50.0,
                            -1200.0, gk_var_name=INTERACT_TARGET)
        clear_tip = _add(logic, "GKNodeUiSet",
                         f"清提示(拾取){index + 1}", 250.0, -1200.0,
                         gk_widget=interact_tip.name,
                         gk_set_text=True, gk_text="")
        _link(logic, br, "是", add_one, "执行")
        _link(logic, add_one, "完成", say, "执行")
        _link(logic, say, "完成", write_line, "执行")
        _link(logic, note_text, "值", write_line, "文字")
        _link(logic, write_line, "完成", flag_save, "执行")
        _link(logic, flag_on, "值", flag_save, "值")
        _link(logic, flag_save, "完成", remove, "执行")
        _link(logic, remove, "完成", clear_target, "执行")
        _link(logic, empty_const, "值", clear_target, "值")
        _link(logic, clear_target, "完成", clear_tip, "执行")
        note_nodes += [name_const, empty_const, entered, target_on,
                       tip_on, left, off_read, off_cmp, off_branch, target_off,
                       tip_off, read_t, cmp_n,
                       br, add_one, say, note_text, write_line, flag_on,
                       flag_save, remove, clear_target, clear_tip]
    hud_nodes = _hud_count(logic, "教室", -1900.0, 400.0)
    _frame(logic, "② 纸条 + E 交互 + HUD", note_nodes + hud_nodes,
           (0.20, 0.30, 0.24))

    # ── 笔记日志:J 开关,Esc 关(层级:面板比 HUD 后建 = 压在 HUD 上,
    #    暂停菜单是运行时才 new 的控件,永远后建 = 压在日志上)─────────
    # 开关这一段三关共用 _journal_toggle —— 教室 / 走廊 / 楼梯间按 J 都是
    # 同一份日志(正文来自存档里的『已读N』,见 _journal_restore)。
    journal_toggle_nodes = _journal_toggle(logic, -2200.0, -1700.0,
                                          log_panel.name)
    _frame(logic, "④ 笔记日志(J / Esc)", journal_toggle_nodes,
           (0.26, 0.20, 0.30))

    # ── 门口:也走 E 交互链(靠近出提示,按 E 才判定能不能开)────────
    # 旧版是"走进门口区域就判定" —— 玩家没有"我要开门"这个动作,
    # 够 3 张就直接被切场景,不够就被字幕拦一下。现在两种交互
    # (纸条 / 门)共用『交互目标』+ E 键,提示也统一在底部那行。
    door_in = _add(logic, "GKNodeOnAreaEntered", "靠近教室门口", -2200.0,
                   -2400.0, gk_area=TO_ROOM, gk_filter=ROOM_PLAYER)
    door_name = _add(logic, "GKNodeConstText", "门名字", -2200.0,
                     -2520.0, gk_value=TO_ROOM)
    door_target = _add(logic, "GKNodeSetVar", "目标=去走廊", -2000.0,
                       -2400.0, gk_var_name=INTERACT_TARGET)
    door_tip = _add(logic, "GKNodeUiSet", "提示[E]开门", -1800.0,
                    -2400.0, gk_widget=interact_tip.name,
                    gk_set_text=True, gk_text="[E] 推开门")
    _link(logic, door_in, "执行", door_target, "执行")
    _link(logic, door_name, "值", door_target, "值")
    _link(logic, door_target, "完成", door_tip, "执行")
    door_out = _add(logic, "GKNodeOnAreaExited", "离开教室门口", -2200.0,
                    -2650.0, gk_area=TO_ROOM, gk_filter=ROOM_PLAYER)
    door_empty = _add(logic, "GKNodeConstText", "门空名字", -2200.0,
                      -2770.0, gk_value="")
    door_off_read = _add(logic, "GKNodeGetVar", "读目标(离开门)", -2200.0,
                         -2890.0, gk_var_name=INTERACT_TARGET)
    door_off_cmp = _add(logic, "GKNodeCompare", "离开时目标还是门吗",
                        -2000.0, -2890.0, gk_op="EQ")
    door_off_branch = _add(logic, "GKNodeBranch", "要清门吗", -2000.0,
                           -2650.0)
    door_clear = _add(logic, "GKNodeSetVar", "清目标(门)", -1800.0,
                      -2650.0, gk_var_name=INTERACT_TARGET)
    door_tip_off = _add(logic, "GKNodeUiSet", "清提示(门)", -1600.0,
                        -2650.0, gk_widget=interact_tip.name,
                        gk_set_text=True, gk_text="")
    _link(logic, door_off_read, "值", door_off_cmp, "A")
    _link(logic, door_name, "值", door_off_cmp, "B")
    _link(logic, door_off_cmp, "结果", door_off_branch, "条件")
    _link(logic, door_out, "执行", door_off_branch, "执行")
    _link(logic, door_off_branch, "是", door_clear, "执行")
    _link(logic, door_empty, "值", door_clear, "值")
    _link(logic, door_clear, "完成", door_tip_off, "执行")
    door_read_t = _add(logic, "GKNodeGetVar", "读目标(门)", -2200.0,
                       -2900.0, gk_var_name=INTERACT_TARGET)
    door_cmp = _add(logic, "GKNodeCompare", "看着门吗", -2000.0,
                    -2900.0, gk_op="EQ")
    door_branch = _add(logic, "GKNodeBranch", "按E开门?", -1800.0,
                       -2900.0)
    _link(logic, door_read_t, "值", door_cmp, "A")
    _link(logic, door_name, "值", door_cmp, "B")
    _link(logic, door_cmp, "结果", door_branch, "条件")
    _link(logic, e_branch, "是", door_branch, "执行")

    # 门禁:够 3 张才存盘离开;不够只提示,不切场景
    read = _add(logic, "GKNodeGetVar", "读纸条数", -2200.0, -3100.0,
                gk_var_name="纸条数")
    compare = _add(logic, "GKNodeCompare", "够3张吗?", -2000.0, -3100.0,
                   gk_op="GE", gk_b=str(NOTE_GOAL))
    branch = _add(logic, "GKNodeBranch", "纸条够吗?", -1800.0, -3100.0)
    _link(logic, read, "值", compare, "A")
    _link(logic, compare, "结果", branch, "条件")
    _link(logic, door_branch, "是", branch, "执行")
    read_short = _add(logic, "GKNodeGetVar", "读纸条数(缺几张)",
                      -2200.0, -3280.0, gk_var_name="纸条数")
    missing = _add(logic, "GKNodeMath", "还差几张", -2000.0, -3280.0,
                   gk_op="SUB", gk_a=float(NOTE_GOAL))
    missing_num = _add(logic, "GKNodeNumberText", "还差数字", -1800.0,
                      -3280.0)
    missing_text = _add(logic, "GKNodeTextJoin", "还差前缀", -1600.0,
                        -3280.0, gk_a="还差 ")
    missing_full = _add(logic, "GKNodeTextJoin", "还差后缀", -1400.0,
                        -3280.0, gk_b=" 张")
    short = _add(logic, "GKNodeUiSubtitle", "门打不开", -1200.0, -3100.0,
                 gk_seconds=3.0, gk_speed=0.0)
    _link(logic, read_short, "值", missing, "B")
    _link(logic, missing, "结果", missing_num, "数值")
    _link(logic, missing_num, "文字", missing_text, "乙")
    _link(logic, missing_text, "结果", missing_full, "甲")
    _link(logic, missing_full, "结果", short, "文字")
    _link(logic, branch, "否", short, "执行")
    write = _add(logic, "GKNodeSaveSet", "存纸条数", -1600.0, -3100.0,
                 gk_key="纸条数")
    save = _add(logic, "GKNodeSaveWrite", "保存纸条数", -1400.0, -3100.0,
                gk_slot="save1", gk_save_verify=True)
    change = _add(logic, "GKNodeChangeScene", "去走廊", -1200.0, -3100.0,
                  gk_scene_path=CORRIDOR_RES)
    _link(logic, read, "值", write, "值")
    _link(logic, branch, "是", write, "执行")
    _link(logic, write, "完成", save, "执行")
    door_sfx_c = _add(logic, "GKNodePlaySound", "开门声(去走廊)", 1400.0, -2900.0,
                   gk_path=DOOR_FILE)
    door_sfx_c_wait = _add(logic, "GKNodeWait", "等门声一拍", 1600.0, -2900.0)
    door_sfx_c_wait.inputs["秒"].default_value = 0.55
    _link(logic, save, "完成", door_sfx_c, "执行")
    _link(logic, door_sfx_c, "完成", door_sfx_c_wait, "执行")
    _link(logic, door_sfx_c_wait, "完成", change, "执行")
    _frame(logic, "③ 门口(E 开门)", [door_in, door_name, door_target,
                                     door_tip, door_out, door_empty,
                                     door_off_read, door_off_cmp,
                                     door_off_branch, door_clear,
                                     door_tip_off, door_read_t,
                                     door_cmp, door_branch, read, compare,
                                     branch, read_short, missing,
                                     missing_num, missing_text,
                                     missing_full, short, write, save,
                                     change], (0.30, 0.20, 0.18))
    log(f"{ROOM}关卡图:开场运镜 / BGM / 暗角 / HUD + 3 张纸条(E 拾取 + "
        f"J 日志) + 门口(E 开门,判 >= {NOTE_GOAL})")
    return logic


def build_title_logic(scene):
    """标题页 · 关卡图:标题 + 开始(清档) / 退出。"""
    logic = _level_tree(TITLE_LEVEL_TREE)
    _set_scene_logic(scene, logic)
    ready = _add(logic, "GKNodeOnReady", "每当_准备", -1500.0, 300.0)
    # ⚠ 场景切换回来时,上一关的上帝机/相机可能还占着视口;环境脚本
    #   "有相机在用就不抢",于是标题页会停在上一帧。这里显式把标题机位
    #   设为当前(硬切),保证每次进标题都是标题自己的画面。
    show_cam = _add(logic, "GKNodeCameraSetCurrent", "回标题机位",
                    -1300.0, 300.0, gk_target=TITLE_CAM, gk_blend=0.0)
    title = _add(logic, "GKNodeUiText", "标题文字", -1100.0, 300.0,
                 gk_text="恐怖示例 · 熄灯的教学楼", gk_align="TOP_CENTER",
                 gk_margin=120.0, gk_size=42)
    tip = _add(logic, "GKNodeUiText", "提示文字", -900.0, 300.0,
               gk_text="WASD 移动 / 空格 跳 / 鼠标转视角 / 按住 K 弄出动静",
               gk_align="TOP_CENTER", gk_margin=196.0, gk_size=20)
    start = _add(logic, "GKNodeUiButton", "开始按钮", -700.0, 300.0,
                 gk_text="开始游戏", gk_align="CENTER",
                 gk_width=220.0, gk_height=56.0)
    exit_btn = _add(logic, "GKNodeUiButton", "退出按钮", -500.0, 300.0,
                    gk_text="退出游戏", gk_align="BOTTOM_CENTER",
                    gk_margin=120.0, gk_width=200.0, gk_height=52.0)
    delete = _add(logic, "GKNodeSaveDelete", "新开一局先清档", -700.0, 60.0,
                  gk_slot="save1")
    change = _add(logic, "GKNodeChangeScene", "去教室", -500.0, 60.0,
                  gk_scene_path=ROOM_RES)
    quit_game = _add(logic, "GKNodeQuitGame", "退出游戏", -300.0, 300.0)
    _link(logic, ready, "执行", show_cam, "执行")
    _link(logic, show_cam, "完成", title, "执行")
    _link(logic, title, "完成", tip, "执行")
    _link(logic, tip, "完成", start, "执行")
    _link(logic, start, "完成", exit_btn, "执行")
    # ⚠ 按钮用『点击』出口:『完成』= 按钮建好了(建完就往下跑会把游戏立刻切走)
    _link(logic, start, "点击", delete, "执行")
    _link(logic, delete, "完成", change, "执行")
    _link(logic, exit_btn, "点击", quit_game, "执行")
    _frame(logic, "① 标题页", [ready, show_cam, title, tip, start, exit_btn,
                              delete, change, quit_game], (0.18, 0.22, 0.34))
    _apply_settings(scene, (("dimension", "3"),))
    log(f"{TITLE}关卡图:标题 + 开始(清档 -> {ROOM_RES}) + 退出")
    return logic


def build_corridor_logic(scene, enemy, ai_tree):
    """第二关 · 关卡图:AI / 心跳 / 动静 / 惊吓 / 被抓失败 / 去楼梯间。

    旧版所有东西都塞在玩家角色图里:AI 状态变量在玩家身上、UI 在玩家身上、
    玩家被销毁就全没了。现在关卡图挂场景根,角色图只管"人怎么走"。
    """
    logic = _level_tree(CORRIDOR_LEVEL_TREE)
    _set_scene_logic(scene, logic)
    ready = _add(logic, "GKNodeOnReady", "每当_准备", -1500.0, 1600.0)
    reset_bus = _add(logic, "GKNodeBusVolume", "总线回到 0dB", -1300.0, 1600.0,
                     gk_bus="Master", gk_db=0.0, gk_fade=0.0)
    hint = _add(logic, "GKNodeUiText", "走廊提示", -1100.0, 1600.0,
                gk_text="走廊里有东西……按住 K 弄出动静能把它引开",
                gk_x=18.0, gk_y=18.0, gk_size=18)
    bgm = _add(logic, "GKNodePlayMusic", "走廊BGM", -900.0, 1600.0,
               gk_path=BGM_FILE, gk_volume=-14.0, gk_fade=1.5)
    vignette = _add(logic, "GKNodeScreenVignette", "走廊暗角", -700.0, 1600.0,
                    gk_amount=0.45, gk_radius=0.60, gk_edge=0.40)
    enable = _add(logic, "GKNodeAiEnable", "启动怪物AI", -500.0, 1600.0,
                  gk_tree_name=ai_tree.name, gk_target=ENEMY,
                  gk_debug_print=False)
    # ── 手电电量 HUD + 开局满电(做法照抄 demo_story 那条链)────────
    battery_hud = _add(logic, "GKNodeUiText", "电量文本", -300.0, 1600.0,
                       gk_text=f"手电电量:{int(BATTERY_MAX)}", gk_x=18.0,
                       gk_y=74.0, gk_size=18,
                       gk_color=(0.60, 0.85, 1.0, 1.0))
    bat_full = _add(logic, "GKNodeSetVar", "电量开局满", -100.0, 1600.0,
                    gk_var_name=BATTERY, gk_value=BATTERY_MAX)
    torch_on_const = _add(logic, "GKNodeConstBool", "手电默认开", -300.0,
                          1480.0, gk_value=True)
    torch_on_set = _add(logic, "GKNodeSetVar", "手电开局开", 100.0, 1600.0,
                        gk_var_name=TORCH_ON)
    # ⚠ 上面这行只把『值』连上还不够 —— 忘了这条线的话,『变量_设置』的
    #   gk_value 回退默认是 **100.0**,生成 `手电开 = 100.0`(float):
    #   F 开关第一次按下之前 `not 100.0` 这种鬼东西就进脚本了(第一百八十
    #   六轮修:常量必须连『值』引脚,和医务室 init_specs 同一套连法)。
    # ── 载入存档:把第一关存下的『纸条数』带回走廊 ─────────────────
    # 关卡变量默认是 0;不读回来的话,
    # 『被抓回安全屋还保留已拿纸条数』就无从谈起(变量本来就空)。
    load = _add(logic, "GKNodeSaveLoad", "载入存档", 300.0, 1600.0,
                gk_slot="save1")
    read_notes = _add(logic, "GKNodeSaveGet", "读回纸条数", 500.0, 1600.0,
                      gk_key="纸条数", gk_var="纸条数", gk_default=0.0)
    # ── 档案系统(第一百七十轮):隐藏笔记数**跨关累计** —— 走廊捡到 1,
    #    音乐教室读回 1 再捡变 2,医务室读回 2 变 3(存档信封接力)。
    read_hidden = _add(logic, "GKNodeSaveGet", "读回隐藏笔记数", 700.0, 1600.0,
                       gk_key=HIDDEN_NOTE_COUNT, gk_var=HIDDEN_NOTE_COUNT,
                       gk_default=0.0)
    # ── 笔记日志(跨场景,M0.5):面板 + J/Esc 开关 + 按存档填正文 ──────
    # 上一版日志只存在教室:一进走廊按 J 什么都不会发生(用户:"J 只在教室
    # 能开")。现在三关共用同一套面板,正文来自存档信封里的『已读1..3』。
    journal_ui_entry, log_lines, journal_ui = _build_journal_ui(
        logic, -2600.0, 2600.0)
    log_panel = journal_ui_entry
    restore_entry, restore_seq, restore_pin, restore_nodes = _journal_restore(
        logic, -2600.0, 2200.0, log_lines)
    journal_toggle_nodes = _journal_toggle(logic, -2600.0, 900.0,
                                          log_panel.name, "走廊")
    journal_close_nodes, journal_close_tail = _journal_close(
        logic, -1900.0, 1900.0, "走廊")
    # ── 蹲走:相机 / 碰撞压低(速度在角色图里,这里只管"人矮下去")──
    # 弹簧臂每物理帧都会把相机拽回"枢轴+1.4 米",所以节点里改相机没用;
    # 用一个『脚本_加载』挂的小脚本排到玩家之后补一刀(见 CROUCH_SCRIPT)。
    crouch_script = _add_crouch_script(logic, 700.0, 1600.0)
    _link(logic, ready, "执行", reset_bus, "执行")
    _link(logic, reset_bus, "完成", hint, "执行")
    _link(logic, hint, "完成", battery_hud, "执行")
    _link(logic, battery_hud, "完成", bgm, "执行")
    # 开场运镜(第一百八十一次):推近 怪物 -> 停留 -> 回玩家,
    # 和章节卡叠着演(4.4 秒 ≈ 章节卡 4.5 秒),开场即入戏。
    shot_corridor = _add(logic, "GKNodeCameraShot", "开场运镜(走廊)",
                 -500.0, 1600.0,
                 gk_camera=TOP_CAM,
                 gk_look_target="怪物", gk_time=2.2,
                 gk_hold=1.2, gk_return_time=1.0, gk_fov_to=34.0)
    _link(logic, bgm, "完成", shot_corridor, "执行")
    _link(logic, shot_corridor, "完成", vignette, "执行")
    _link(logic, vignette, "完成", bat_full, "执行")
    _link(logic, bat_full, "完成", torch_on_set, "执行")
    _link(logic, torch_on_const, "值", torch_on_set, "值")
    archive_tail = _archive_zero_vars(logic, torch_on_set, 900.0, 1450.0)
    _link(logic, archive_tail, "完成", load, "执行")
    _link(logic, load, "完成", read_notes, "执行")
    # 日志面板 / 开关 / 填正文排在启动 AI 之前:面板得先建出来,J 才有东西可开
    _link(logic, read_notes, "完成", read_hidden, "执行")
    _link(logic, read_hidden, "完成", journal_close_tail, "执行")
    _link(logic, journal_close_tail, "完成", journal_ui[0], "执行")
    _link(logic, journal_ui[-1], "完成", restore_entry, "执行")
    _link(logic, restore_seq, restore_pin, enable, "执行")
    polish = _add_ui_polish(logic, 900.0, 1600.0)
    _link(logic, enable, "完成", crouch_script, "执行")
    _link(logic, crouch_script, "完成", polish, "执行")
    # ── 保险丝解谜(第一百六十八轮)────────────────────────────────
    # 开局先把吊灯 2/3 灭掉:走廊只剩吊灯1 + 壁灯,后半段是黑的 ——
    # 手电的戏份和"渐进点亮"的反馈都从这里来(设计文档第 2 章)。
    fuse_dark1 = _add(logic, "GKNodeSetLight", "开局灭吊灯2", -1100.0, 1300.0,
                      gk_light="走廊吊灯2", gk_on=False, gk_energy=0.0)
    fuse_dark2 = _add(logic, "GKNodeSetLight", "开局灭吊灯3", -900.0, 1300.0,
                      gk_light="走廊吊灯3", gk_on=False, gk_energy=0.0)
    _link(logic, polish, "完成", fuse_dark1, "执行")
    _link(logic, fuse_dark1, "完成", fuse_dark2, "执行")
    # 收集链 ×3:走入判定区自动捡 → 防重(标志) → 计数+1 → 点亮一盏
    # 吊灯(第三个不放灯,放行的事交给出口那条链)→ 字幕。
    fuse_nodes = [fuse_dark1, fuse_dark2]
    for index, name in enumerate(FUSES):
        fx = -1500.0
        fy = -1600.0 - index * 460.0
        enter = _add(logic, "GKNodeOnAreaEntered", f"捡到{name}", fx, fy,
                     gk_area=name, gk_filter=CORRIDOR_PLAYER)
        got_get = _add(logic, "GKNodeGetVar", f"{name}拿过吗", fx + 200.0, fy,
                       gk_var_name=FUSE_FLAGS[index])
        got_branch = _add(logic, "GKNodeBranch", f"{name}防重", fx + 380.0, fy)
        _link(logic, enter, "执行", got_branch, "执行")
        _link(logic, got_get, "值", got_branch, "条件")
        flag_true = _add(logic, "GKNodeConstBool", f"{name}记拿过",
                         fx + 200.0, fy - 160.0, gk_value=True)
        set_flag = _add(logic, "GKNodeSetVar", f"记下{name}", fx + 380.0,
                        fy - 160.0, gk_var_name=FUSE_FLAGS[index])
        _link(logic, flag_true, "值", set_flag, "值")
        add_count = _add(logic, "GKNodeAddVar", f"保险丝数+1({name})",
                         fx + 560.0, fy - 160.0, gk_var_name=FUSE_COUNT,
                         gk_amount=1.0, gk_clamp=True, gk_min=0.0,
                         gk_max=float(FUSE_GOAL))
        tail = add_count
        if index < 2:
            relight = _add(logic, "GKNodeSetLight", f"点亮吊灯{index + 2}",
                           fx + 740.0, fy - 160.0,
                           gk_light=f"走廊吊灯{index + 2}", gk_on=True,
                           gk_energy=FUSE_LIGHT_ENERGY, gk_fade=0.5)
            _link(logic, add_count, "完成", relight, "执行")
            tail = relight
        say_text = ("捡到一段保险丝。配电箱还缺着 —— 走廊后半截还黑着"
                    if index < 2 else
                    "保险丝齐了 —— 走廊的供电回来了,音乐教室的门开了")
        say = _add(logic, "GKNodeUiSubtitle", f"拿到{name}", fx + 940.0,
                   fy - 160.0, gk_text=say_text, gk_seconds=3.0, gk_speed=30.0)
        switch_sfx = _add(logic, "GKNodePlaySound", f"插保险丝声({name})",
                          fx + 940.0, fy - 160.0, gk_path=SWITCH_FILE)
        _link(logic, got_branch, "否", set_flag, "执行")
        _link(logic, set_flag, "完成", add_count, "执行")
        _link(logic, tail, "完成", switch_sfx, "执行")
        _link(logic, switch_sfx, "完成", say, "执行")
        fuse_nodes += [enter, got_get, got_branch, flag_true, set_flag,
                       add_count, tail, switch_sfx, say]
    _frame(logic, "① 开局 / 氛围", [ready, reset_bus, hint, battery_hud, bgm,
                                    vignette, bat_full, torch_on_const,
                                    torch_on_set, load, read_notes, enable,
                                    crouch_script, polish] + fuse_nodes,
           (0.18, 0.22, 0.34))
    _frame(logic, "⑥ 笔记日志(跨场景)", journal_ui + journal_close_nodes +
           restore_nodes + journal_toggle_nodes, (0.26, 0.20, 0.30))

    # ── 每物理帧:紧张度 -> 心跳 -> 按 K 发噪音(一条链)─────────────
    phys = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(氛围)", -1500.0,
                1000.0)
    # ⚠ 关卡图挂场景根:必须显式填『测量谁 = 走廊玩家』,
    #   否则量的是世界原点到怪物的距离(玩家贴脸紧张度也不动)。
    tension = _add(logic, "GKNodeTension", "紧张度", -1500.0, 800.0,
                   gk_threat=ENEMY, gk_source="走廊玩家",
                   gk_near=TENSION_NEAR, gk_far=TENSION_FAR)
    chasing = _add(logic, "GKNodeGetVar", "读追击中", -1300.0, 800.0,
                   gk_var_name="is_chasing")
    heartbeat = _add(logic, "GKNodeHeartbeat", "心跳", -1100.0, 1000.0,
                     gk_path=HEART_FILE, gk_slow_interval=1.1,
                     gk_fast_interval=0.32, gk_quiet_db=-19.0,
                     gk_loud_db=-5.0, gk_quiet=0.15, gk_smooth=1.5)
    noise_key = _add(logic, "GKNodeKey", "按住K", -1500.0, 520.0,
                     gk_key=NOISE_KEY, gk_action=NOISE_ACTION,
                     gk_trigger="PRESSED")
    noise_branch = _add(logic, "GKNodeBranch", "按住K?", -1300.0, 520.0)
    # 先给玩家一个"我确实敲了一下"的听觉反馈,再让 AI 听见。
    knock = _add(logic, "GKNodePlaySound", "敲出声", -1100.0, 520.0,
                 gk_path=KNOCK_FILE, gk_volume=-8.0, gk_2d=False)
    noise = _add(logic, "GKNodeNoise", "弄出动静", -900.0, 520.0,
                 gk_loudness=NOISE_LOUD, gk_interval=NOISE_INTERVAL,
                 gk_target="走廊玩家", gk_debug_print=False)
    # ── 第一百五十九轮:引怪的**屏幕反馈**────────────────────────
    # 旧版按 K 只有"咚"一声,画面一个字都不变(实机 07 与 08a 两张截图
    # sha256 完全相同),玩家根本不知道引没引到。
    # 这里改两处:① 顶部提示行立刻改字(一定在画面里,保证帧不同);
    # ② 再飘一条字幕(叙事层)。
    heard_hint = _add(logic, "GKNodeUiSet", "它听见了(提示行)", -700.0,
                      520.0, gk_widget="走廊提示", gk_set_text=True,
                      gk_text="它听见了 —— 快躲开")
    heard_sub = _add(logic, "GKNodeUiSubtitle", "它听见了(字幕)", -500.0,
                     520.0, gk_text="它听见了……", gk_seconds=1.8,
                     gk_speed=0.0)
    _link(logic, noise, "完成", heard_hint, "执行")
    _link(logic, heard_hint, "完成", heard_sub, "执行")
    _link(logic, chasing, "值", tension, "被看见")
    _link(logic, tension, "紧张度", heartbeat, "紧张度")
    _link(logic, phys, "执行", heartbeat, "执行")
    _link(logic, heartbeat, "完成", noise_branch, "执行")
    _link(logic, noise_key, "结果", noise_branch, "条件")
    _link(logic, noise_branch, "是", knock, "执行")
    _link(logic, knock, "完成", noise, "执行")
    _frame(logic, "② 心跳 + 动静", [phys, tension, chasing, heartbeat,
                                    noise_key, noise_branch, knock, noise,
                                    heard_hint, heard_sub],
           (0.22, 0.24, 0.34))

    # ── 走廊的纸条 HUD(载入存档之后就显示真实张数)──────────────────
    corridor_hud = _hud_count(logic, "走廊", -1900.0, 1000.0)
    _frame(logic, "③ 纸条 HUD", corridor_hud, (0.20, 0.30, 0.24))

    # ── 跑动噪音:Shift 跑 = 半径 6 米的脚步,怪物会过来看 ────────────
    # 走的是**现有听觉通道**:『声音_发出噪音』广播 -> AI 图『听见动静』
    # -> 『去查看』。蹲着按 Shift 不算跑(蹲优先),所以蹲走真的无声。
    run_key = _add(logic, "GKNodeKey", "按住LeftShift(跑)", -1500.0, 360.0,
                   gk_key=SHIFT_KEY, gk_action=SHIFT_ACTION,
                   gk_trigger="PRESSED")
    crouch_key = _add(logic, "GKNodeKey", "按住LeftCtrl(蹲)", -1500.0,
                      240.0, gk_key=CTRL_KEY, gk_action=CTRL_ACTION,
                      gk_trigger="PRESSED")
    crouch_not = _add(logic, "GKNodeLogic", "没在蹲", -1300.0, 240.0,
                      gk_op="NOT")
    run_and = _add(logic, "GKNodeLogic", "跑且没蹲", -1100.0, 360.0,
                   gk_op="AND")
    run_branch = _add(logic, "GKNodeBranch", "在跑吗", -900.0, 360.0)
    run_noise = _add(logic, "GKNodeNoise", "跑步噪音", -700.0, 360.0,
                     gk_loudness=RUN_NOISE_LOUD,
                     gk_interval=RUN_NOISE_INTERVAL,
                     gk_target=CORRIDOR_PLAYER,
                     gk_noise_type=STEP_NOISE_TYPE)
    _link(logic, crouch_key, "结果", crouch_not, "A")
    _link(logic, run_key, "结果", run_and, "A")
    _link(logic, crouch_not, "结果", run_and, "B")
    _link(logic, run_and, "结果", run_branch, "条件")
    _link(logic, phys, "执行", run_branch, "执行")
    _link(logic, run_branch, "是", run_noise, "执行")
    _frame(logic, "④ 跑动噪音", [run_key, crouch_key, crouch_not, run_and,
                                  run_branch, run_noise], (0.26, 0.22, 0.30))

    # ── 手电:F 开关 + 每秒耗电 + 没电自动关 + 低电闪烁(第 186 轮进工厂)
    #    原来这段和医务室逐字重复;抽成 _torch_chain() 后顺带修掉一个老坑:
    #    没电提示写提示行会被 0.25 秒的目标刷新覆盖,工厂里统一用『字幕』。
    _torch_chain(logic, -1500.0, -1250.0, TORCH, battery_hud.name,
                 "手电没电了 —— 摸黑走吧", frame_title="⑤ 手电 / 电量")

    # ── 安全屋:进区域就存盘 + 记检查点(被抓之后回这里)──────────────
    safe_in = _add(logic, "GKNodeOnAreaEntered", "进安全屋", -1900.0,
                   -1500.0, gk_area=SAFE_ROOM_AREA,
                   gk_filter=CORRIDOR_PLAYER)
    cp_text = _add(logic, "GKNodeConstText", "检查点名", -1900.0,
                   -1650.0, gk_value=CHECKPOINT_CORRIDOR)
    cp_set = _add(logic, "GKNodeSetVar", "设检查点", -1700.0, -1500.0,
                  gk_var_name=CHECKPOINT)
    safe_read = _add(logic, "GKNodeGetVar", "读纸条数(存盘)", -1700.0,
                     -1650.0, gk_var_name="纸条数")
    safe_save_set = _add(logic, "GKNodeSaveSet", "存纸条数", -1500.0,
                         -1500.0, gk_key="纸条数")
    safe_note_set = _add(logic, "GKNodeSaveSet", "存隐藏笔记数", -1400.0,
                         -1500.0, gk_key=HIDDEN_NOTE_COUNT)
    safe_save = _add(logic, "GKNodeSaveWrite", "安全屋存盘", -1300.0,
                     -1500.0, gk_slot="save1", gk_save_verify=True)
    safe_tip = _add(logic, "GKNodeUiSubtitle", "安全屋提示", -1100.0,
                    -1500.0, gk_text="安全屋:进度已保存", gk_seconds=2.5,
                    gk_speed=30.0)
    _link(logic, safe_in, "执行", cp_set, "执行")
    _link(logic, cp_text, "值", cp_set, "值")
    _link(logic, cp_set, "完成", safe_save_set, "执行")
    _link(logic, safe_read, "值", safe_save_set, "值")
    _link(logic, safe_save_set, "完成", safe_note_set, "执行")
    _link(logic, safe_note_set, "完成", safe_save, "执行")
    _link(logic, safe_save, "完成", safe_tip, "执行")
    _frame(logic, "⑥ 安全屋 / 检查点", [safe_in, cp_text, cp_set, safe_read,
                                         safe_save_set, safe_save, safe_tip],
           (0.24, 0.30, 0.26))

    # ── 惊吓:只触发一次,压低总线后**会恢复**(旧版压低就不还了)────
    true_const = _add(logic, "GKNodeConstBool", "真", -700.0, -900.0,
                      gk_value=True)
    scared = _add(logic, "GKNodeOnAreaEntered", "走过惊吓区", -1500.0, 120.0,
                  gk_area=SCARE_AREA, gk_filter="走廊玩家")
    was_scared = _add(logic, "GKNodeGetVar", "受惊过了吗", -1500.0, -80.0,
                      gk_var_name="已受惊")
    scare_branch = _add(logic, "GKNodeBranch", "第一次受惊?", -1300.0, 120.0)
    set_scared = _add(logic, "GKNodeSetVar", "记下受惊", -1100.0, 120.0,
                      gk_var_name="已受惊")
    scare = _add(logic, "GKNodeScare", "受惊一下", -900.0, 120.0,
                 gk_strength=18.0, gk_duration=0.5,
                 gk_color=(0.85, 0.10, 0.10, 1.0), gk_alpha=0.55,
                 gk_flash_time=0.35, gk_path=STING_FILE, gk_volume=-3.0)
    duck = _add(logic, "GKNodeBusVolume", "压低总线", -700.0, 120.0,
                gk_bus="Master", gk_db=-18.0, gk_fade=0.3)
    restore_wait = _add(logic, "GKNodeWait", "等吓劲过去", -500.0, 120.0)
    restore_wait.inputs["秒"].default_value = 2.5
    restore = _add(logic, "GKNodeBusVolume", "总线恢复", -300.0, 120.0,
                   gk_bus="Master", gk_db=0.0, gk_fade=1.2)
    _link(logic, scared, "执行", scare_branch, "执行")
    _link(logic, was_scared, "值", scare_branch, "条件")
    _link(logic, scare_branch, "否", set_scared, "执行")
    _link(logic, true_const, "值", set_scared, "值")
    _link(logic, set_scared, "完成", scare, "执行")
    whisper = _add(logic, "GKNodePlaySound", "耳语(受惊)", -1100.0, 380.0,
                   gk_path=WHISPER_FILE, gk_volume=-8.0)
    _link(logic, scare, "完成", whisper, "执行")
    _link(logic, whisper, "完成", duck, "执行")
    _link(logic, duck, "完成", restore_wait, "执行")
    _link(logic, restore_wait, "完成", restore, "执行")
    _frame(logic, "③ 惊吓(一次性)", [scared, was_scared, scare_branch,
                                     set_scared, scare, duck, restore_wait,
                                     restore, true_const],
           (0.28, 0.18, 0.16))

    # ── 被抓:距离够近 + 还没失败 -> 倒地 -> 冻住 -> 重试 / 回标题────
    phys_fail = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(抓人判定)",
                     -1500.0, -400.0)
    distance = _add(logic, "GKNodeDistance", "离怪物多远", -1500.0, -620.0,
                    gk_a="走廊玩家", gk_b=ENEMY)
    compare = _add(logic, "GKNodeCompare", "进抓取范围?", -1300.0, -620.0,
                   gk_op="LT", gk_b=str(CATCH_DISTANCE))
    failed = _add(logic, "GKNodeGetVar", "已经失败了?", -1500.0, -820.0,
                  gk_var_name="已失败")
    not_failed = _add(logic, "GKNodeLogic", "还没失败", -1300.0, -820.0,
                      gk_op="NOT")
    can_fail = _add(logic, "GKNodeLogic", "进范围且没失败", -1100.0, -820.0,
                    gk_op="AND")
    fail_branch = _add(logic, "GKNodeBranch", "被抓住了?", -900.0, -400.0)
    set_failed = _add(logic, "GKNodeSetVar", "记下失败", -700.0, -400.0,
                      gk_var_name="已失败")
    # ⚠ 『恢复』必须勾(M0.5 第一百六十轮收尾):不勾的话倒地会把相机从弹簧臂
    #   手里**永久**接管(两处锁:关卡图的 _gk_cam_locked + 相机节点上的
    #   gk_cam_locked meta),回到安全屋 / 重开都还在倒地机位 —— 被抓一次之后
    #   整局都用歪 72° 的镜头看走廊,"惩罚"被永久化了。插件清锁的路径只有这一条
    #   (常驻玩家脚本只在**换关**时统一摘锁,见 persistent_player.py:490)。
    #   时长 0.9 秒 = 倒地演出照旧看得见;1.9 秒后才冻世界,所以恢复到第三人称
    #   发生在暂停之前(tween 暂停后不跑,不能在暂停里等它)。
    fall = _add(logic, "GKNodeCameraFall", "镜头倒地", -500.0, -400.0,
                gk_time=0.9, gk_drop=0.85, gk_roll=72.0, gk_pitch=18.0,
                gk_fade=0.0, gk_restore=True)
    fail_wait = _add(logic, "GKNodeWait", "倒地停一下", -300.0, -400.0)
    fail_wait.inputs["秒"].default_value = 1.0
    freeze = _add(logic, "GKNodeUiPauseGame", "冻住世界", -100.0, -400.0,
                  gk_pause=True, gk_show_mouse=True, gk_menu="",
                  gk_hotkey=False)
    fail_text = _add(logic, "GKNodeUiText", "被抓标题", 100.0, -400.0,
                     gk_text="你被抓住了", gk_align="CENTER", gk_size=44)
    fail_tip = _add(logic, "GKNodeUiText", "被抓提示", 300.0, -400.0,
                    gk_text="走廊里的东西追上了你", gk_align="BOTTOM_CENTER",
                    gk_margin=260.0, gk_size=20)
    # ── M0:被抓不再重载整关,改成回安全屋(保留纸条数)───────────────
    # 以前点这个按钮 = 重载场景:AI 复位、纸条数清零、已拿的道具全没了。
    # 现在 = 解冻 -> 传回安全屋 -> 重记检查点 -> 清失败标记 -> 继续。
    retry_btn = _add(logic, "GKNodeUiButton", "回到安全屋按钮", 500.0,
                     -320.0, gk_text="回到安全屋", gk_align="BOTTOM_CENTER",
                     gk_margin=180.0, gk_width=200.0, gk_height=52.0)
    title_btn = _add(logic, "GKNodeUiButton", "失败回标题按钮", 500.0, -520.0,
                     gk_text="回到标题", gk_align="BOTTOM_CENTER",
                     gk_margin=110.0, gk_width=200.0, gk_height=52.0)
    resume = _add(logic, "GKNodeUiPauseGame", "解冻(回安全屋)", 700.0,
                  -320.0, gk_pause=False, gk_show_mouse=True, gk_menu="",
                  gk_hotkey=False)
    back_home = _add(logic, "GKNodeSetPosition", "传回安全屋", 900.0,
                     -320.0, gk_target=CORRIDOR_PLAYER,
                     gk_x=_godot_xy(SAFE_ROOM_POS)[0],
                     gk_y=SAFE_ROOM_SPAWN_Z,
                     gk_z=_godot_xy(SAFE_ROOM_POS)[1])
    cp_again_text = _add(logic, "GKNodeConstText", "检查点名(回程)",
                         700.0, -180.0, gk_value=CHECKPOINT_CORRIDOR)
    cp_again = _add(logic, "GKNodeSetVar", "重记检查点", 1100.0, -320.0,
                    gk_var_name=CHECKPOINT)
    failed_false = _add(logic, "GKNodeConstBool", "还没失败", 900.0,
                        -180.0, gk_value=False)
    clear_failed = _add(logic, "GKNodeSetVar", "清失败标记", 1300.0,
                        -320.0, gk_var_name="已失败")
    # ⚠ 失败界面的四个控件是**运行时建出来的**,回安全屋的时候必须自己收掉:
    #   不收的话"你被抓住了 + 两个按钮"会一直挂在安全屋画面上(实机截图抓到)。
    hide_fail_title = _add(logic, "GKNodeUiHide", "收起被抓标题", 1100.0,
                           -480.0, gk_widget=fail_text.name, gk_visible=False)
    hide_fail_tip = _add(logic, "GKNodeUiHide", "收起被抓提示", 1300.0,
                         -480.0, gk_widget=fail_tip.name, gk_visible=False)
    hide_retry = _add(logic, "GKNodeUiHide", "收起回安全屋按钮", 1500.0,
                      -480.0, gk_widget=retry_btn.name, gk_visible=False)
    hide_title_btn = _add(logic, "GKNodeUiHide", "收起回标题按钮", 1700.0,
                          -480.0, gk_widget=title_btn.name, gk_visible=False)
    home_tip = _add(logic, "GKNodeUiSubtitle", "回安全屋提示", 1900.0,
                    -320.0, gk_text="你被拖回了安全屋……",
                    gk_seconds=2.5, gk_speed=30.0)
    resume_title = _add(logic, "GKNodeUiPauseGame", "解冻(回标题)", 700.0,
                        -520.0, gk_pause=False, gk_show_mouse=True,
                        gk_menu="", gk_hotkey=False)
    to_title = _add(logic, "GKNodeChangeScene", "回标题", 900.0, -520.0,
                    gk_scene_path=TITLE_RES)
    _link(logic, distance, "距离", compare, "A")
    _link(logic, failed, "值", not_failed, "A")
    _link(logic, not_failed, "结果", can_fail, "A")
    _link(logic, compare, "结果", can_fail, "B")
    _link(logic, can_fail, "结果", fail_branch, "条件")
    _link(logic, phys_fail, "执行", fail_branch, "执行")
    _link(logic, fail_branch, "是", set_failed, "执行")
    _link(logic, true_const, "值", set_failed, "值")
    _link(logic, set_failed, "完成", fall, "执行")
    _link(logic, fall, "完成", fail_wait, "执行")
    _link(logic, fail_wait, "完成", freeze, "执行")
    _link(logic, freeze, "完成", fail_text, "执行")
    _link(logic, fail_text, "完成", fail_tip, "执行")
    _link(logic, fail_tip, "完成", retry_btn, "执行")
    _link(logic, retry_btn, "完成", title_btn, "执行")
    _link(logic, retry_btn, "点击", resume, "执行")
    _link(logic, resume, "完成", back_home, "执行")
    _link(logic, back_home, "完成", cp_again, "执行")
    _link(logic, cp_again_text, "值", cp_again, "值")
    _link(logic, cp_again, "完成", clear_failed, "执行")
    _link(logic, failed_false, "值", clear_failed, "值")
    _link(logic, clear_failed, "完成", hide_fail_title, "执行")
    _link(logic, hide_fail_title, "完成", hide_fail_tip, "执行")
    _link(logic, hide_fail_tip, "完成", hide_retry, "执行")
    _link(logic, hide_retry, "完成", hide_title_btn, "执行")
    _link(logic, hide_title_btn, "完成", home_tip, "执行")
    _link(logic, title_btn, "点击", resume_title, "执行")
    _link(logic, resume_title, "完成", to_title, "执行")
    _frame(logic, "④ 被抓 / 失败", [phys_fail, distance, compare, failed,
                                    not_failed, can_fail, fail_branch,
                                    set_failed, fall, fail_wait, freeze,
                                    fail_text, fail_tip, retry_btn, title_btn,
                                    resume, back_home, cp_again_text, cp_again,
                                    failed_false, clear_failed,
                                    hide_fail_title, hide_fail_tip, hide_retry,
                                    hide_title_btn, home_tip, resume_title,
                                    to_title],
           (0.32, 0.16, 0.14))

    # 走廊出口 → **有 3 个保险丝才放行**(第一百六十八轮:以前是路过章,
    # 现在传送板先"咬"一口 —— 没集齐就提示,集齐了才切音乐教室)。
    door = _add(logic, "GKNodeOnAreaEntered", "走到音乐教室", -1500.0,
                -1100.0, gk_area=TO_MUSIC, gk_filter=CORRIDOR_PLAYER)
    change = _add(logic, "GKNodeChangeScene", "去音乐教室", -1300.0, -1100.0,
                  gk_scene_path=MUSIC_RES)
    fuse_get = _add(logic, "GKNodeGetVar", "保险丝齐了吗", -1300.0, -940.0,
                    gk_var_name=FUSE_COUNT)
    fuse_cmp = _add(logic, "GKNodeCompare", "三个都有?", -1120.0, -940.0,
                    gk_op="GE", gk_b=str(FUSE_GOAL))
    fuse_branch = _add(logic, "GKNodeBranch", "供电恢复了吗", -940.0, -1100.0)
    block = _add(logic, "GKNodeUiSubtitle", "出口还锁着", -740.0, -940.0,
                 gk_text="配电箱还缺保险丝 —— 走廊的灯没修好,音乐教室的门打不开",
                 gk_seconds=3.0, gk_speed=30.0)
    _link(logic, door, "执行", fuse_branch, "执行")
    _link(logic, fuse_get, "值", fuse_cmp, "A")
    _link(logic, fuse_cmp, "结果", fuse_branch, "条件")
    door_sfx = _add(logic, "GKNodePlaySound", "开门声(去音乐教室)",
                    -1300.0, -780.0, gk_path=DOOR_FILE)
    door_wait = _add(logic, "GKNodeWait", "等门声一拍", -1100.0, -780.0)
    door_wait.inputs["秒"].default_value = 0.55
    _link(logic, fuse_branch, "是", door_sfx, "执行")
    _link(logic, door_sfx, "完成", door_wait, "执行")
    _link(logic, door_wait, "完成", change, "执行")
    _link(logic, fuse_branch, "否", block, "执行")
    _frame(logic, "⑤ 去下一关", [door, fuse_get, fuse_cmp, fuse_branch,
                                 change, block], (0.24, 0.26, 0.34))
    # ── 追逐切曲(第一百七十九轮):被追 -> chase;甩掉 -> 切回 BGM ────
    _chase_music_chain(logic, -1500.0, -5400.0, CHASE_FILE, BGM_FILE)
    # ── 隐藏笔记(第一百六十九轮):走入即捡,纯背景故事 ─────────────
    for scene_name, note_name, _pos, note_text in HIDDEN_NOTES:
        if scene_name == CORRIDOR:
            _hidden_note_chain(logic, note_name, note_text,
                               CORRIDOR_PLAYER, -1500.0, -3150.0)
    log(f"{CORRIDOR}关卡图:AI / 心跳(被看见更紧张) / 动静 / 惊吓(一次性"
        f" + 总线恢复) / 被抓失败 + 重试 / 保险丝×3 渐进点亮 + {TO_MUSIC}"
        f" 放行 -> {MUSIC_RES}")
    return logic


def build_music_logic(scene, enemy, ai_tree):
    """第 3 章 · 关卡图:乐谱 -> 录音机四音 -> 诱饵 / 警报 -> 磁带 -> 侧门。

    ── 这一关有五条**互相独立**的链(各有自己的事件入口)─────────────
    ① 开局(每当_准备):提示 / BGM / 暗角 / 交互提示 / 变量归零 / 载入存档 /
       日志面板 / 蹲走脚本 / 界面修补。
    ② E 交互:一个『每当_物理帧』->『按E了吗』-> 六条"是 XXX 吗"平行支链
       (3 张乐谱 / 录音机 / 磁带 / 侧门)。这是教室 E 交互的同一套写法。
    ③ 数字键:一个『每当_物理帧』-> 四条『按 1/2/3/4 了吗』-> 各自"追加一位"。
       ⚠ 四条支链**各自**写 变量_设置:一个执行输入引脚只能接**一条**线,
         所以它们汇不回同一个节点(生成器会"先到先得"并把另一条整段丢掉)。
    ④ 判定:『定时_每隔』每 0.2 秒看一次『录音机位数』,满 4 位才判对错。
       用定时器而不是"第四下按键"驱动 —— 按键是四条独立链,汇不到一起;
       定时器让"判对错"只有一份代码(错 / 对两条出口)。
    ⑤ 被抓 / 安全屋 / 侧门:和走廊那几条一一对应。
    """
    logic = _level_tree(MUSIC_LEVEL_TREE)
    _set_scene_logic(scene, logic)

    # ── ① 开局 ────────────────────────────────────────────────────
    ready = _add(logic, "GKNodeOnReady", "每当_准备", -1900.0, 1800.0)
    reset_bus = _add(logic, "GKNodeBusVolume", "总线回到 0dB", -1700.0, 1800.0,
                     gk_bus="Master", gk_db=0.0, gk_fade=0.0)
    hint = _add(logic, "GKNodeUiText", MUSIC_HINT, -1500.0, 1800.0,
                gk_text=f"侧门锁着。先找齐 {len(SHEET_NAMES)} 张乐谱 —— "
                        f"靠近按 E 读", gk_x=18.0, gk_y=18.0, gk_size=18)
    bgm = _add(logic, "GKNodePlayMusic", "音乐教室BGM", -1300.0, 1800.0,
               gk_path=BGM_FILE, gk_volume=-20.0, gk_fade=1.5)
    vignette = _add(logic, "GKNodeScreenVignette", "音乐教室暗角", -1100.0,
                    1800.0, gk_amount=0.45, gk_radius=0.60, gk_edge=0.40)
    interact_tip = _add(logic, "GKNodeUiText", INTERACT_TIP, -900.0, 1800.0,
                        gk_text="", gk_align="BOTTOM_CENTER",
                        gk_margin=190.0, gk_size=22,
                        gk_color=(1.0, 0.95, 0.70, 1.0))
    _link(logic, ready, "执行", reset_bus, "执行")
    _link(logic, reset_bus, "完成", hint, "执行")
    _link(logic, hint, "完成", bgm, "执行")
    _link(logic, bgm, "完成", vignette, "执行")
    _link(logic, vignette, "完成", interact_tip, "执行")

    # ── 变量归零:探针开局就能读到确定的**类型和值** ────────────────
    # (『录音机输入』是 String,必须显式给 "" —— 不给的话它是 null,探针
    #  在玩家按第一下之前读到的是 <null>,拼字符串时还会炸。)
    init_specs = (
        (MUSIC_COUNT, "GKNodeConstFloat", 0.0, "乐谱数=0"),
        (REC_MISS, "GKNodeConstFloat", 0.0, "错次=0"),
        (TAPE_GOT, "GKNodeConstFloat", 0.0, "磁带=0"),
        (REC_DIGITS, "GKNodeConstFloat", 0.0, "位数=0"),
        (REC_INPUT, "GKNodeConstText", "", "输入=空"),
        (REC_LAST, "GKNodeConstText", "", "记录=空"),
        (REC_PLAYING, "GKNodeConstBool", False, "放音=关"),
        (MONSTER_IN, "GKNodeConstBool", False, "怪物没进来"),
        (INTERACT_TARGET, "GKNodeConstText", "", "交互目标=空"),
        (CHECKPOINT, "GKNodeConstText", MUSIC_CHECKPOINT, "检查点=本关"),
        (SAFE_VISITED, "GKNodeConstBool", False, "没进过安全屋"),
    )
    init_nodes = []
    previous = interact_tip
    for index, (var_name, kind, value, label) in enumerate(init_specs):
        const = _add(logic, kind, f"初值:{label}", -700.0,
                     1620.0 - index * 120.0, gk_value=value)
        node = _add(logic, "GKNodeSetVar", f"开机:{label}", -500.0,
                    1700.0 - index * 120.0, gk_var_name=var_name)
        _link(logic, const, "值", node, "值")
        _link(logic, previous, "完成", node, "执行")
        previous = node
        init_nodes += [const, node]

    # ── 载入存档 + 日志面板(三关同一套名字 / 同一份正文)─────────────
    load = _add(logic, "GKNodeSaveLoad", "载入存档(音乐教室)", -300.0, 1700.0,
                gk_slot="save1")
    read_hidden = _add(logic, "GKNodeSaveGet", "读回隐藏笔记数", -100.0, 1700.0,
                       gk_key=HIDDEN_NOTE_COUNT, gk_var=HIDDEN_NOTE_COUNT,
                       gk_default=0.0)
    archive_tail = _archive_zero_vars(logic, previous, -700.0, 1450.0)
    _link(logic, previous, "完成", archive_tail, "执行")
    _link(logic, archive_tail, "完成", load, "执行")
    _link(logic, load, "完成", read_hidden, "执行")
    log_panel, log_lines, journal_ui = _build_journal_ui(logic, -2600.0, 2800.0)
    restore_entry, restore_seq, restore_pin, restore_nodes = _journal_restore(
        logic, -2600.0, 2400.0, log_lines)
    journal_toggle_nodes = _journal_toggle(logic, -2600.0, 1100.0,
                                          log_panel.name, "音乐教室")
    journal_close_nodes, journal_close_tail = _journal_close(
        logic, -1900.0, 2100.0, "音乐教室")
    # ── M1a 返工(lead P0):怪物**开局就启用** ────────────────────
    # 『AI_启用』放在开局链里(而不是"错满两次才放进来"):诱饵要有对象可引,
    # 这一章才有压力。AI 图用的是本关自己那张(巡逻点 / 追击目标都在本关)。
    ai_on = _add(logic, "GKNodeAiEnable", "启动怪物AI", -100.0, 1700.0,
                 gk_tree_name=ai_tree.name, gk_target=MUSIC_ENEMY,
                 gk_debug_print=False)
    crouch_script = _add_crouch_script(logic, 100.0, 1700.0, "蹲走脚本(音乐)")
    polish = _add_ui_polish(logic, 300.0, 1700.0)
    _link(logic, read_hidden, "完成", journal_close_tail, "执行")
    _link(logic, journal_close_tail, "完成", journal_ui[0], "执行")
    _link(logic, journal_ui[-1], "完成", restore_entry, "执行")
    _link(logic, restore_seq, restore_pin, ai_on, "执行")
    _link(logic, ai_on, "完成", crouch_script, "执行")
    _link(logic, crouch_script, "完成", polish, "执行")
    _frame(logic, "① 开局 / 载入", [ready, reset_bus, hint, bgm, vignette,
                                    interact_tip] + init_nodes + [load] +
           journal_ui + journal_close_nodes + restore_nodes +
           [ai_on, crouch_script, polish], (0.18, 0.22, 0.34))

    # ── 目标行随进度切换(lead P1:集齐乐谱之后要告诉玩家下一步)──────
    # 一条定时器 + 三段比较 + 四个『界面_设定』—— 和纸条 HUD 同一个套路。
    guide_timer = _add(logic, "GKNodeOnTimer", "目标刷新", -900.0, 900.0,
                       gk_seconds=0.25, gk_repeat=True)
    guide_tape = _add(logic, "GKNodeGetVar", "读磁带(目标)", -900.0, 760.0,
                      gk_var_name=TAPE_GOT)
    guide_tape_cmp = _add(logic, "GKNodeCompare", "拿到磁带了吗", -700.0,
                          760.0, gk_op="GE", gk_b="1")
    guide_tape_br = _add(logic, "GKNodeBranch", "目标:拿磁带", -500.0, 900.0)
    guide_play = _add(logic, "GKNodeGetVar", "读放音(目标)", -700.0, 620.0,
                      gk_var_name=REC_PLAYING)
    guide_play_br = _add(logic, "GKNodeBranch", "目标:去录音机", -500.0, 620.0)
    guide_count = _add(logic, "GKNodeGetVar", "读乐谱数(目标)", -700.0, 480.0,
                       gk_var_name=MUSIC_COUNT)
    guide_count_cmp = _add(logic, "GKNodeCompare", "乐谱齐了吗", -500.0,
                           480.0, gk_op="GE", gk_b=str(len(SHEET_NAMES)))
    guide_count_br = _add(logic, "GKNodeBranch", "目标:乐谱齐了", -300.0,
                          620.0)
    guide_tape_set = _add(logic, "GKNodeUiSet", "目标:拿磁带", -100.0, 1000.0,
                          gk_widget=hint.name, gk_set_text=True,
                          gk_text=MUSIC_HINT_TEXTS[3])
    guide_play_set = _add(logic, "GKNodeUiSet", "目标:舞台灯灭了", -100.0,
                          900.0, gk_widget=hint.name, gk_set_text=True,
                          gk_text=MUSIC_HINT_TEXTS[2])
    guide_count_set = _add(logic, "GKNodeUiSet", "目标:去录音机", 100.0,
                           900.0, gk_widget=hint.name, gk_set_text=True,
                           gk_text=MUSIC_HINT_TEXTS[1])
    guide_start_set = _add(logic, "GKNodeUiSet", "目标:先找乐谱", 300.0,
                           900.0, gk_widget=hint.name, gk_set_text=True,
                           gk_text=MUSIC_HINT_TEXTS[0])
    _link(logic, guide_timer, "执行", guide_tape_br, "执行")
    _link(logic, guide_tape, "值", guide_tape_cmp, "A")
    _link(logic, guide_tape_cmp, "结果", guide_tape_br, "条件")
    _link(logic, guide_tape_br, "是", guide_tape_set, "执行")
    _link(logic, guide_tape_br, "否", guide_play_br, "执行")
    _link(logic, guide_play, "值", guide_play_br, "条件")
    _link(logic, guide_play_br, "是", guide_play_set, "执行")
    _link(logic, guide_play_br, "否", guide_count_br, "执行")
    _link(logic, guide_count, "值", guide_count_cmp, "A")
    _link(logic, guide_count_cmp, "结果", guide_count_br, "条件")
    _link(logic, guide_count_br, "是", guide_count_set, "执行")
    _link(logic, guide_count_br, "否", guide_start_set, "执行")
    _frame(logic, "⑨ 目标行(随进度)", [guide_timer, guide_tape,
                                       guide_tape_cmp, guide_tape_br,
                                       guide_play, guide_play_br,
                                       guide_count, guide_count_cmp,
                                       guide_count_br, guide_tape_set,
                                       guide_play_set, guide_count_set,
                                       guide_start_set], (0.20, 0.26, 0.30))

    # ── 乐谱 HUD:定时读『乐谱数』-> 『乐谱 N/3』────────────────────
    hud_timer = _add(logic, "GKNodeOnTimer", "乐谱刷新", -2200.0, 1000.0,
                     gk_seconds=0.2, gk_repeat=True)
    hud_read = _add(logic, "GKNodeGetVar", "读乐谱数", -2200.0, 860.0,
                    gk_var_name=MUSIC_COUNT)
    hud_num = _add(logic, "GKNodeNumberText", "乐谱数字", -2000.0, 860.0)
    hud_join = _add(logic, "GKNodeTextJoin", "乐谱N/3", -1800.0, 860.0,
                    gk_middle="/", gk_b=str(len(SHEET_NAMES)))
    hud_label = _add(logic, "GKNodeTextJoin", "乐谱前缀", -1600.0, 860.0,
                     gk_a="乐谱 ")
    hud = _add(logic, "GKNodeUiText", "乐谱HUD", -1400.0, 1000.0,
               gk_text=f"乐谱 0/{len(SHEET_NAMES)}", gk_x=18.0, gk_y=46.0,
               gk_size=18, gk_color=(0.95, 0.90, 0.70, 1.0))
    _link(logic, hud_read, "值", hud_num, "数值")
    _link(logic, hud_num, "文字", hud_join, "甲")
    _link(logic, hud_join, "结果", hud_label, "乙")
    _link(logic, hud_label, "结果", hud, "文字")
    _link(logic, hud_timer, "执行", hud, "执行")
    _frame(logic, "② 乐谱 HUD", [hud_timer, hud_read, hud_num, hud_join,
                                 hud_label, hud], (0.20, 0.30, 0.24))

    # ── E 交互的公共骨架(靠近 -> 报名字 / 走开 -> 清名字)──────────────
    def _proximity(name, tip_text, y):
        """靠近 name 出 [E] 提示并把名字写进『交互目标』;走开时按需清掉。

        走开那一段**必须**判"目标还是我才清":从一个触发区直接踏进另一个
        时,exit / enter 落在同一物理帧,谁先谁后不确定(实机踩过)。
        """
        name_const = _add(logic, "GKNodeConstText", f"名字:{name}", -2100.0,
                          y - 130.0, gk_value=name)
        empty_const = _add(logic, "GKNodeConstText", f"空名字:{name}",
                           -2100.0, y - 250.0, gk_value="")
        entered = _add(logic, "GKNodeOnAreaEntered", f"靠近{name}", -1900.0,
                       y, gk_area=name, gk_filter=MUSIC_PLAYER)
        target_on = _add(logic, "GKNodeSetVar", f"目标={name}", -1700.0, y,
                         gk_var_name=INTERACT_TARGET)
        tip_on = _add(logic, "GKNodeUiSet", f"提示[{name}]", -1500.0, y,
                      gk_widget=interact_tip.name, gk_set_text=True,
                      gk_text=tip_text)
        _link(logic, entered, "执行", target_on, "执行")
        _link(logic, name_const, "值", target_on, "值")
        _link(logic, target_on, "完成", tip_on, "执行")
        left = _add(logic, "GKNodeOnAreaExited", f"离开{name}", -2100.0,
                    y - 420.0, gk_area=name, gk_filter=MUSIC_PLAYER)
        off_read = _add(logic, "GKNodeGetVar", f"读目标(离开{name})",
                        -2100.0, y - 540.0, gk_var_name=INTERACT_TARGET)
        off_cmp = _add(logic, "GKNodeCompare", f"离开时目标是{name}吗",
                       -1900.0, y - 540.0, gk_op="EQ")
        off_branch = _add(logic, "GKNodeBranch", f"要清{name}吗", -1700.0,
                          y - 420.0)
        target_off = _add(logic, "GKNodeSetVar", f"清目标({name})", -1500.0,
                          y - 420.0, gk_var_name=INTERACT_TARGET)
        tip_off = _add(logic, "GKNodeUiSet", f"清提示({name})", -1300.0,
                       y - 420.0, gk_widget=interact_tip.name,
                       gk_set_text=True, gk_text="")
        _link(logic, off_read, "值", off_cmp, "A")
        _link(logic, name_const, "值", off_cmp, "B")
        _link(logic, off_cmp, "结果", off_branch, "条件")
        _link(logic, left, "执行", off_branch, "执行")
        _link(logic, off_branch, "是", target_off, "执行")
        _link(logic, empty_const, "值", target_off, "值")
        _link(logic, target_off, "完成", tip_off, "执行")
        return [name_const, empty_const, entered, target_on, tip_on, left,
                off_read, off_cmp, off_branch, target_off, tip_off]

    phys_e = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(E)", -2100.0,
                  -2600.0)
    e_key = _add(logic, "GKNodeKey", "按E", -2100.0, -2740.0,
                 gk_key=E_KEY, gk_action=E_ACTION, gk_trigger="JUST")
    e_branch = _add(logic, "GKNodeBranch", "按E了吗", -1900.0, -2600.0)
    _link(logic, e_key, "结果", e_branch, "条件")
    _link(logic, phys_e, "执行", e_branch, "执行")

    def _is_target(name, name_const, y):
        """E 链上的一段:『交互目标』== name 吗(是 -> 调用方接着挂)。"""
        read = _add(logic, "GKNodeGetVar", f"读目标:{name}", -1700.0, y,
                    gk_var_name=INTERACT_TARGET)
        cmp = _add(logic, "GKNodeCompare", f"看着{name}吗", -1500.0,
                   y - 60.0, gk_op="EQ")
        branch = _add(logic, "GKNodeBranch", f"是{name}吗", -1350.0, y)
        _link(logic, read, "值", cmp, "A")
        _link(logic, name_const, "值", cmp, "B")
        _link(logic, cmp, "结果", branch, "条件")
        _link(logic, e_branch, "是", branch, "执行")
        return [read, cmp, branch]

    # ── 三张乐谱:读一张记一个音位,集齐念出整串 ────────────────────
    sheet_nodes = []
    for index, name in enumerate(SHEET_NAMES):
        y = 900.0 - index * 420.0
        zone = _proximity(name, "[E] 读乐谱", y)
        target_nodes = _is_target(name, zone[0], y - 700.0)
        flag_get = _add(logic, "GKNodeGetVar", f"读过{name}吗", -1150.0,
                        y - 640.0, gk_var_name=SHEET_FLAGS[index])
        flag_branch = _add(logic, "GKNodeBranch", f"{name}读过了吗", -1000.0,
                           y - 700.0)
        _link(logic, flag_get, "值", flag_branch, "条件")
        _link(logic, target_nodes[2], "是", flag_branch, "执行")
        flag_true = _add(logic, "GKNodeConstBool", f"{name}算读过了",
                         -1150.0, y - 800.0, gk_value=True)
        set_flag = _add(logic, "GKNodeSetVar", f"记下{name}", -800.0,
                        y - 700.0, gk_var_name=SHEET_FLAGS[index])
        add_count = _add(logic, "GKNodeAddVar", f"乐谱数+1({name})", -600.0,
                         y - 700.0, gk_var_name=MUSIC_COUNT, gk_amount=1.0,
                         gk_clamp=True, gk_min=0.0,
                         gk_max=float(len(SHEET_NAMES)))
        say = _add(logic, "GKNodeUiSubtitle", f"读到{name}", -400.0,
                   y - 700.0,
                   gk_text=f"《{name}》第 {index + 1} 个音是 "
                           f"{SHEET_NOTES[index]}", gk_seconds=3.0,
                   gk_speed=30.0)
        count_get = _add(logic, "GKNodeGetVar", f"读乐谱数({name})", -200.0,
                         y - 800.0, gk_var_name=MUSIC_COUNT)
        count_cmp = _add(logic, "GKNodeCompare", "集齐了吗", 0.0, y - 800.0,
                         gk_op="GE", gk_b=str(len(SHEET_NAMES)))
        count_branch = _add(logic, "GKNodeBranch", "集齐了", 200.0, y - 700.0)
        reveal = _add(logic, "GKNodeUiSubtitle", "四音凑齐", 400.0,
                      y - 700.0,
                      gk_text=f"四个音凑齐了:{ANSWER} —— 去录音机按 1/2/3/4",
                      gk_seconds=4.5, gk_speed=30.0)
        again = _add(logic, "GKNodeUiSubtitle", f"再读{name}", -800.0,
                     y - 840.0,
                     gk_text=f"《{name}》:第 {index + 1} 个音是 "
                             f"{SHEET_NOTES[index]}", gk_seconds=2.5,
                     gk_speed=30.0)
        _link(logic, flag_true, "值", set_flag, "值")
        _link(logic, flag_branch, "否", set_flag, "执行")
        _link(logic, set_flag, "完成", add_count, "执行")
        _link(logic, add_count, "完成", say, "执行")
        _link(logic, say, "完成", count_branch, "执行")
        _link(logic, count_get, "值", count_cmp, "A")
        _link(logic, count_cmp, "结果", count_branch, "条件")
        # 集齐之后**立刻收掉**「[E] 读乐谱」:乐谱已经没用了,提示还挂在
        # 屏幕中央 = 玩家以为还得继续读(lead 截图就是这一条)。
        reveal_target = _add(logic, "GKNodeSetVar", "集齐:清目标", 600.0,
                             y - 700.0, gk_var_name=INTERACT_TARGET)
        reveal_tip = _add(logic, "GKNodeUiSet", "集齐:清提示", 800.0,
                          y - 700.0, gk_widget=interact_tip.name,
                          gk_set_text=True, gk_text="")
        reveal_empty = _add(logic, "GKNodeConstText", "集齐:空目标", 600.0,
                            y - 840.0, gk_value="")
        _link(logic, count_branch, "是", reveal, "执行")
        _link(logic, reveal, "完成", reveal_target, "执行")
        _link(logic, reveal_empty, "值", reveal_target, "值")
        _link(logic, reveal_target, "完成", reveal_tip, "执行")
        _link(logic, flag_branch, "是", again, "执行")
        sheet_nodes += zone + target_nodes + [flag_get, flag_branch, flag_true,
                                              set_flag, add_count, say,
                                              count_get, count_cmp,
                                              count_branch, reveal,
                                              reveal_target, reveal_tip,
                                              reveal_empty, again]

    # ── 录音机:E 打开(清进度)+ 1/2/3/4 输入 ───────────────────────
    rec_zone = _proximity(RECORDER, "[E] 打开录音机", -300.0)
    rec_target = _is_target(RECORDER, rec_zone[0], -1000.0)
    clear_in = _add(logic, "GKNodeSetVar", "打开:清输入", -1150.0, -1000.0,
                    gk_var_name=REC_INPUT)
    clear_digits = _add(logic, "GKNodeSetVar", "打开:清位数", -950.0,
                        -1000.0, gk_var_name=REC_DIGITS)
    open_say = _add(logic, "GKNodeUiSubtitle", "录音机开着", -750.0, -1000.0,
                    gk_text="录音机咔哒一声 —— 按 1/2/3/4 输入你听到的四个音",
                    gk_seconds=3.5, gk_speed=30.0)
    empty_in = _add(logic, "GKNodeConstText", "空输入", -1150.0, -1100.0,
                    gk_value="")
    zero_digits = _add(logic, "GKNodeConstFloat", "零位数", -950.0, -1100.0,
                       gk_value=0.0)
    _link(logic, rec_target[2], "是", clear_in, "执行")
    _link(logic, empty_in, "值", clear_in, "值")
    _link(logic, clear_in, "完成", clear_digits, "执行")
    _link(logic, zero_digits, "值", clear_digits, "值")
    _link(logic, clear_digits, "完成", open_say, "执行")

    # ⚠ 一个『每当_物理帧』分给四条数字键支链:执行输出**分岔**是允许的
    #   (每条支链的下游都是独立节点);反过来的"汇合"不允许。
    phys_dig = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(录音机按键)",
                    -2100.0, -1600.0)
    digit_nodes = [phys_dig]
    for index, digit in enumerate(DIGIT_KEYS):
        y = -1500.0 - index * 260.0
        key = _add(logic, "GKNodeKey", f"按{digit}", -1900.0, y - 120.0,
                   gk_key=digit, gk_action=f"key_{digit}",
                   gk_trigger="JUST")
        branch = _add(logic, "GKNodeBranch", f"按下{digit}了吗", -1750.0, y)
        _link(logic, key, "结果", branch, "条件")
        _link(logic, phys_dig, "执行", branch, "执行")
        # ⚠ 满四位之后就不再收键:判定是**定时器**驱动的(最多晚 0.2 秒),
        #   没有这道闸的话手快的人能在判之前按出第五位 —— "31425 != 3142",
        #   一次明明按对了的输入会被判成错。
        digits_get = _add(logic, "GKNodeGetVar", f"读位数({digit})",
                          -1750.0, y - 180.0, gk_var_name=REC_DIGITS)
        digits_cmp = _add(logic, "GKNodeCompare", f"{digit}:还能按吗",
                          -1550.0, y - 180.0, gk_op="LT", gk_b="4")
        room_branch = _add(logic, "GKNodeBranch", f"{digit}:还有位置吗",
                           -1550.0, y)
        _link(logic, digits_get, "值", digits_cmp, "A")
        _link(logic, digits_cmp, "结果", room_branch, "条件")
        _link(logic, branch, "是", room_branch, "执行")
        read_in = _add(logic, "GKNodeGetVar", f"读输入({digit})", -1350.0,
                       y - 120.0, gk_var_name=REC_INPUT)
        digit_const = _add(logic, "GKNodeConstText", f"数字{digit}", -1350.0,
                           y - 240.0, gk_value=digit)
        join = _add(logic, "GKNodeTextJoin", f"输入+{digit}", -1150.0, y)
        set_in = _add(logic, "GKNodeSetVar", f"追加{digit}", -950.0, y,
                      gk_var_name=REC_INPUT)
        add_digit = _add(logic, "GKNodeAddVar", f"位数+1({digit})", -750.0,
                         y, gk_var_name=REC_DIGITS, gk_amount=1.0,
                         gk_clamp=True, gk_min=0.0, gk_max=4.0)
        _link(logic, read_in, "值", join, "甲")
        _link(logic, digit_const, "值", join, "乙")
        _link(logic, join, "结果", set_in, "值")
        _link(logic, room_branch, "是", set_in, "执行")
        _link(logic, set_in, "完成", add_digit, "执行")
        digit_nodes += [key, branch, digits_get, digits_cmp, room_branch,
                        read_in, digit_const, join, set_in, add_digit]

    # ── 判定:位数满 4 -> 对 / 错两条出口 ───────────────────────────
    # 对:录音机放音 + 练习曲 + 在『舞台』发噪音(把怪物引过去)+ 舞台灯灭。
    # 错:错次 +1 + 警报(灯闪 / 噪音 / 字幕);错满 2 次把怪物放进本关。
    judge_timer = _add(logic, "GKNodeOnTimer", "判定(每 0.2 秒)", 700.0,
                       900.0, gk_seconds=0.2, gk_repeat=True)
    digits_get = _add(logic, "GKNodeGetVar", "读位数", 700.0, 760.0,
                      gk_var_name=REC_DIGITS)
    digits_cmp = _add(logic, "GKNodeCompare", "四位了吗", 900.0, 760.0,
                      gk_op="GE", gk_b="4")
    digits_branch = _add(logic, "GKNodeBranch", "够四位了", 1100.0, 900.0)
    _link(logic, judge_timer, "执行", digits_branch, "执行")
    _link(logic, digits_get, "值", digits_cmp, "A")
    _link(logic, digits_cmp, "结果", digits_branch, "条件")
    # 把这次按的四音抄一份留证据(判完『录音机输入』会清空,这里不清)。
    last_get = _add(logic, "GKNodeGetVar", "读输入(留档)", 1300.0, 820.0,
                    gk_var_name=REC_INPUT)
    keep_last = _add(logic, "GKNodeSetVar", "记下本次四音", 1500.0, 900.0,
                     gk_var_name=REC_LAST)
    _link(logic, digits_branch, "是", keep_last, "执行")
    _link(logic, last_get, "值", keep_last, "值")
    answer_get = _add(logic, "GKNodeGetVar", "读输入(判对错)", 1500.0, 760.0,
                      gk_var_name=REC_INPUT)
    # ⚠ 『逻辑_比较』的 A / B 是**文本框**,纯数字会被当成数字字面量:
    #   gk_b="3142" 生成的是 `录音机输入 == 3142`(String 比 int,永远不等)。
    #   字符串必须自己带引号 —— 加一对引号才会生成 `== "3142"`。
    answer_cmp = _add(logic, "GKNodeCompare", f"是 {ANSWER} 吗", 1700.0,
                      760.0, gk_op="EQ", gk_b=f'"{ANSWER}"')
    answer_branch = _add(logic, "GKNodeBranch", "四个音对了吗", 1900.0, 900.0)
    _link(logic, keep_last, "完成", answer_branch, "执行")
    _link(logic, answer_get, "值", answer_cmp, "A")
    _link(logic, answer_cmp, "结果", answer_branch, "条件")
    # ⚠ 一条链里有两处『等待』是不行的 —— 这里全是"立刻返回"的语句
    #   (放音 / 发噪音 / 开灯关灯 / 字幕),所以两条出口各自一条直线。
    playing_true = _add(logic, "GKNodeConstBool", "放音=开", 2200.0, 840.0,
                        gk_value=True)
    set_playing = _add(logic, "GKNodeSetVar", "录音机放音", 2100.0, 900.0,
                       gk_var_name=REC_PLAYING)
    etude = _add(logic, "GKNodePlaySound", "放练习曲", 2300.0, 900.0,
                 gk_path=ETUDE_FILE, gk_volume=-4.0, gk_2d=False)
    lure = _add(logic, "GKNodeNoise", "诱饵(舞台)", 2500.0, 900.0,
                gk_loudness=LURE_LOUD, gk_interval=0.4,
                gk_target=STAGE, gk_debug_print=False)
    stage_off = _add(logic, "GKNodeSetLight", "舞台灯灭", 2700.0, 900.0,
                     gk_light=STAGE_LIGHT, gk_on=False, gk_fade=0.4)
    lure_say = _add(logic, "GKNodeUiSubtitle", "诱饵生效", 2900.0, 900.0,
                    gk_text="录音机开始放那首练习曲 —— 舞台的灯灭了",
                    gk_seconds=3.5, gk_speed=30.0)
    clear_in_ok = _add(logic, "GKNodeSetVar", "判完:清输入", 3100.0, 900.0,
                       gk_var_name=REC_INPUT)
    clear_dig_ok = _add(logic, "GKNodeSetVar", "判完:清位数", 3300.0, 900.0,
                        gk_var_name=REC_DIGITS)
    empty_in_ok = _add(logic, "GKNodeConstText", "空输入(判完)", 3100.0,
                      800.0, gk_value="")
    zero_dig_ok = _add(logic, "GKNodeConstFloat", "零位数(判完)", 3300.0,
                       800.0, gk_value=0.0)
    _link(logic, answer_branch, "是", set_playing, "执行")
    _link(logic, playing_true, "值", set_playing, "值")
    _link(logic, set_playing, "完成", etude, "执行")
    _link(logic, etude, "完成", lure, "执行")
    _link(logic, lure, "完成", stage_off, "执行")
    _link(logic, stage_off, "完成", lure_say, "执行")
    _link(logic, lure_say, "完成", clear_in_ok, "执行")
    _link(logic, empty_in_ok, "值", clear_in_ok, "值")
    _link(logic, clear_in_ok, "完成", clear_dig_ok, "执行")
    _link(logic, zero_dig_ok, "值", clear_dig_ok, "值")
    # ── 错:先清进度再判"错满两次"(清进度两条出口都要,所以放在岔口前面)
    miss_add = _add(logic, "GKNodeAddVar", "错次+1", 2100.0, 520.0,
                    gk_var_name=REC_MISS, gk_amount=1.0, gk_clamp=True,
                    gk_min=0.0, gk_max=99.0)
    alarm_flicker = _add(logic, "GKNodeLightFlicker", "警报灯闪", 2300.0,
                         520.0, gk_light=ALARM_LIGHT, gk_mode="ALARM",
                         gk_speed=1.6, gk_depth=0.85)
    alarm_sound = _add(logic, "GKNodePlaySound", "警报声", 2500.0, 520.0,
                       gk_path=ALARM_FILE, gk_volume=-3.0, gk_2d=False)
    alarm_noise = _add(logic, "GKNodeNoise", "警报噪音(录音机)", 2700.0,
                       520.0, gk_loudness=ALARM_LOUD, gk_interval=0.5,
                       gk_target=RECORDER, gk_debug_print=False)
    alarm_say = _add(logic, "GKNodeUiSubtitle", "警报", 2900.0, 520.0,
                     gk_text="刺耳的警报响了起来 —— 录音机不认这四个音",
                     gk_seconds=3.0, gk_speed=30.0)
    clear_in_bad = _add(logic, "GKNodeSetVar", "错:清输入", 3100.0, 520.0,
                        gk_var_name=REC_INPUT)
    clear_dig_bad = _add(logic, "GKNodeSetVar", "错:清位数", 3300.0, 520.0,
                         gk_var_name=REC_DIGITS)
    empty_in_bad = _add(logic, "GKNodeConstText", "空输入(错)", 3100.0,
                        420.0, gk_value="")
    zero_dig_bad = _add(logic, "GKNodeConstFloat", "零位数(错)", 3300.0,
                        420.0, gk_value=0.0)
    miss_get = _add(logic, "GKNodeGetVar", "读错次", 3500.0, 420.0,
                    gk_var_name=REC_MISS)
    miss_cmp = _add(logic, "GKNodeCompare", "错满两次了吗", 3700.0, 420.0,
                    gk_op="GE", gk_b="2")
    miss_branch = _add(logic, "GKNodeBranch", "错满两次", 3900.0, 520.0)
    _link(logic, answer_branch, "否", miss_add, "执行")
    _link(logic, miss_add, "完成", alarm_flicker, "执行")
    _link(logic, alarm_flicker, "完成", alarm_sound, "执行")
    _link(logic, alarm_sound, "完成", alarm_noise, "执行")
    _link(logic, alarm_noise, "完成", alarm_say, "执行")
    _link(logic, alarm_say, "完成", clear_in_bad, "执行")
    _link(logic, empty_in_bad, "值", clear_in_bad, "值")
    _link(logic, clear_in_bad, "完成", clear_dig_bad, "执行")
    _link(logic, zero_dig_bad, "值", clear_dig_bad, "值")
    _link(logic, clear_dig_bad, "完成", miss_branch, "执行")
    _link(logic, miss_get, "值", miss_cmp, "A")
    _link(logic, miss_cmp, "结果", miss_branch, "条件")
    # ── 错满两次:它听见你在哪(只来一次 —— 『怪物入场』当护栏)────────
    # 怪物**已经**在本关巡逻了(开局就启用),所以这里的惩罚不是"出现",
    # 而是"盯上你":在玩家身上发一个很响的噪音,AI 会转去查看那个位置。
    in_get = _add(logic, "GKNodeGetVar", "已经被惊动了吗", 4100.0, 420.0,
                  gk_var_name=MONSTER_IN)
    in_branch = _add(logic, "GKNodeBranch", "还没被惊动", 4300.0, 520.0)
    alert_noise = _add(logic, "GKNodeNoise", "它听见你在哪", 4500.0, 520.0,
                       gk_loudness=NOISE_ON_YOU, gk_interval=0.5,
                       gk_target=MUSIC_PLAYER, gk_debug_print=False)
    in_true = _add(logic, "GKNodeConstBool", "怪物入场=真", 4500.0, 400.0,
                   gk_value=True)
    set_in = _add(logic, "GKNodeSetVar", "记下怪物入场", 4700.0, 520.0,
                  gk_var_name=MONSTER_IN)
    in_say = _add(logic, "GKNodeUiSubtitle", "它盯上你了", 4900.0, 520.0,
                  gk_text="警报把它引过来了 —— 它知道你在哪", gk_seconds=3.0,
                  gk_speed=30.0)
    _link(logic, miss_branch, "是", in_branch, "执行")
    _link(logic, in_get, "值", in_branch, "条件")
    _link(logic, in_branch, "否", alert_noise, "执行")
    _link(logic, alert_noise, "完成", set_in, "执行")
    _link(logic, in_true, "值", set_in, "值")
    _link(logic, set_in, "完成", in_say, "执行")
    _frame(logic, "③ 录音机 / 四音谜题", rec_zone + rec_target +
           [clear_in, clear_digits, open_say, empty_in, zero_digits] +
           digit_nodes + [judge_timer, digits_get, digits_cmp, digits_branch,
                          last_get, keep_last, answer_get, answer_cmp,
                          answer_branch, playing_true, set_playing, etude,
                          lure, stage_off, lure_say, clear_in_ok,
                          clear_dig_ok, empty_in_ok, zero_dig_ok, miss_add,
                          alarm_flicker, alarm_sound, alarm_noise, alarm_say,
                          clear_in_bad, clear_dig_bad, empty_in_bad,
                          zero_dig_bad, miss_get, miss_cmp, miss_branch,
                          in_get, in_branch, alert_noise, in_true, set_in,
                          in_say], (0.30, 0.24, 0.16))

    # ── 磁带:只有『录音机放音』(诱饵生效、舞台灯灭)之后才拿得走 ────
    tape_zone = _proximity(TAPE, "[E] 拿磁带", 900.0)
    tape_target = _is_target(TAPE, tape_zone[0], 300.0)
    play_get = _add(logic, "GKNodeGetVar", "诱饵还在吗", 200.0, 220.0,
                    gk_var_name=REC_PLAYING)
    play_branch = _add(logic, "GKNodeBranch", "灯灭了吗", 400.0, 300.0)
    tape_one = _add(logic, "GKNodeConstFloat", "磁带=1", 400.0, 180.0,
                    gk_value=1.0)
    set_tape = _add(logic, "GKNodeSetVar", "拿到磁带", 600.0, 300.0,
                    gk_var_name=TAPE_GOT)
    free_tape = _add(logic, "GKNodeQueueFree", "拿走磁带(判定体)", 800.0,
                     300.0, gk_target=TAPE)
    free_mesh = _add(logic, "GKNodeQueueFree", "拿走磁带(本体)", 1000.0,
                     300.0, gk_target=TAPE_MESH)
    tape_target_off = _add(logic, "GKNodeSetVar", "清目标(磁带)", 1200.0,
                           300.0, gk_var_name=INTERACT_TARGET)
    tape_tip_off = _add(logic, "GKNodeUiSet", "清提示(磁带)", 1400.0, 300.0,
                        gk_widget=interact_tip.name, gk_set_text=True,
                        gk_text="")
    tape_say = _add(logic, "GKNodeUiSubtitle", "拿到磁带", 1600.0, 300.0,
                    gk_text="拿到磁带了 —— 侧门的锁松开了", gk_seconds=3.0,
                    gk_speed=30.0)
    tape_empty = _add(logic, "GKNodeConstText", "空目标(磁带)", 1200.0,
                      180.0, gk_value="")
    too_bright = _add(logic, "GKNodeUiSubtitle", "灯还亮着", 600.0, 100.0,
                      gk_text="磁带就在舞台钢琴上,可那盏灯还亮着 —— "
                              "现在过去会被看见", gk_seconds=3.5,
                      gk_speed=30.0)
    _link(logic, tape_target[2], "是", play_branch, "执行")
    _link(logic, play_get, "值", play_branch, "条件")
    _link(logic, play_branch, "是", set_tape, "执行")
    _link(logic, tape_one, "值", set_tape, "值")
    _link(logic, set_tape, "完成", free_tape, "执行")
    _link(logic, free_tape, "完成", free_mesh, "执行")
    _link(logic, free_mesh, "完成", tape_target_off, "执行")
    _link(logic, tape_empty, "值", tape_target_off, "值")
    _link(logic, tape_target_off, "完成", tape_tip_off, "执行")
    _link(logic, tape_tip_off, "完成", tape_say, "执行")
    _link(logic, play_branch, "否", too_bright, "执行")
    _frame(logic, "④ 磁带", tape_zone + tape_target +
           [play_get, play_branch, tape_one, set_tape, free_tape, free_mesh,
            tape_target_off, tape_tip_off, tape_say, tape_empty, too_bright],
           (0.26, 0.30, 0.20))

    # ── 侧门(『去楼梯间』):磁带 1 才放行,先存盘再切关 ─────────────
    # M1b:出口改名『去医务室』(医务室自己的出口才叫『去楼梯间』)
    door_zone = _proximity(TO_MED, "[E] 推开侧门", 900.0)
    door_target = _is_target(TO_MED, door_zone[0], 300.0)
    tape_get = _add(logic, "GKNodeGetVar", "磁带有吗", 200.0, 220.0,
                    gk_var_name=TAPE_GOT)
    tape_cmp = _add(logic, "GKNodeCompare", "磁带够吗", 400.0, 220.0,
                    gk_op="GE", gk_b="1")
    tape_branch = _add(logic, "GKNodeBranch", "侧门能开吗", 600.0, 300.0)
    save_count = _add(logic, "GKNodeSaveSet", "存乐谱数", 800.0, 300.0,
                      gk_key=MUSIC_COUNT)
    count_get = _add(logic, "GKNodeGetVar", "读乐谱数(存盘)", 800.0, 180.0,
                     gk_var_name=MUSIC_COUNT)
    save_tape = _add(logic, "GKNodeSaveSet", "存磁带", 1000.0, 300.0,
                     gk_key=TAPE_GOT)
    tape_get2 = _add(logic, "GKNodeGetVar", "读磁带(存盘)", 1000.0, 180.0,
                     gk_var_name=TAPE_GOT)
    save = _add(logic, "GKNodeSaveWrite", "侧门存盘", 1200.0, 300.0,
                gk_slot="save1", gk_save_verify=True)
    change = _add(logic, "GKNodeChangeScene", "去医务室", 1400.0, 300.0,
                  gk_scene_path=MED_RES)
    lock_say = _add(logic, "GKNodeUiSubtitle", "侧门锁着", 800.0, 100.0,
                    gk_text="侧门锁着 —— 得先拿到钢琴上的磁带",
                    gk_seconds=3.0, gk_speed=30.0)
    _link(logic, door_target[2], "是", tape_branch, "执行")
    _link(logic, tape_get, "值", tape_cmp, "A")
    _link(logic, tape_cmp, "结果", tape_branch, "条件")
    _link(logic, tape_branch, "是", save_count, "执行")
    _link(logic, count_get, "值", save_count, "值")
    _link(logic, save_count, "完成", save_tape, "执行")
    _link(logic, tape_get2, "值", save_tape, "值")
    _link(logic, save_tape, "完成", save, "执行")
    door_sfx_m = _add(logic, "GKNodePlaySound", "开门声(去医务室)", 1400.0, 380.0,
                   gk_path=DOOR_FILE)
    door_sfx_m_wait = _add(logic, "GKNodeWait", "等门声一拍", 1600.0, 380.0)
    door_sfx_m_wait.inputs["秒"].default_value = 0.55
    _link(logic, save, "完成", door_sfx_m, "执行")
    _link(logic, door_sfx_m, "完成", door_sfx_m_wait, "执行")
    _link(logic, door_sfx_m_wait, "完成", change, "执行")
    _link(logic, tape_branch, "否", lock_say, "执行")
    _frame(logic, "⑤ 侧门(去医务室)", door_zone + door_target +
           [tape_get, tape_cmp, tape_branch, save_count, count_get, save_tape,
            tape_get2, save, change, lock_say], (0.30, 0.20, 0.18))

    # ── 安全屋:进区存盘 + 记检查点(被抓之后回这里)─────────────────
    safe_in = _add(logic, "GKNodeOnAreaEntered", "进安全屋", -1900.0, -3400.0,
                   gk_area=MUSIC_SAFE_AREA, gk_filter=MUSIC_PLAYER)
    cp_text = _add(logic, "GKNodeConstText", "检查点名", -1900.0, -3520.0,
                   gk_value=MUSIC_CHECKPOINT)
    cp_set = _add(logic, "GKNodeSetVar", "设检查点", -1700.0, -3400.0,
                  gk_var_name=CHECKPOINT)
    safe_count = _add(logic, "GKNodeSaveSet", "安全屋存乐谱数", -1500.0,
                      -3400.0, gk_key=MUSIC_COUNT)
    safe_count_get = _add(logic, "GKNodeGetVar", "读乐谱数(安全屋)", -1500.0,
                          -3520.0, gk_var_name=MUSIC_COUNT)
    safe_tape = _add(logic, "GKNodeSaveSet", "安全屋存磁带", -1300.0, -3400.0,
                     gk_key=TAPE_GOT)
    safe_note = _add(logic, "GKNodeSaveSet", "安全屋存隐藏笔记", -1100.0,
                     -3400.0, gk_key=HIDDEN_NOTE_COUNT)
    safe_tape_get = _add(logic, "GKNodeGetVar", "读磁带(安全屋)", -1300.0,
                         -3520.0, gk_var_name=TAPE_GOT)
    safe_save = _add(logic, "GKNodeSaveWrite", "安全屋存盘", -1100.0, -3400.0,
                     gk_slot="save1", gk_save_verify=True)
    # ⚠ 第一次进安全屋**不出字幕**:出生点就在这块区域里,开机第一帧弹一条
    #   「安全屋:进度已保存」会和"上一关的字幕还挂在屏幕上"长得一模一样
    #   (lead 的截图就误读成串场了)。存盘照做,提示留给"被抓回来 / 再进一次"。
    visited_get = _add(logic, "GKNodeGetVar", "进过安全屋吗", -1100.0,
                       -3520.0, gk_var_name=SAFE_VISITED)
    visited_branch = _add(logic, "GKNodeBranch", "第二次进?", -900.0, -3400.0)
    visited_true = _add(logic, "GKNodeConstBool", "记下进过", -900.0, -3520.0,
                        gk_value=True)
    visited_set = _add(logic, "GKNodeSetVar", "记住进过安全屋", -700.0,
                       -3400.0, gk_var_name=SAFE_VISITED)
    safe_tip = _add(logic, "GKNodeUiSubtitle", "安全屋提示", -500.0, -3520.0,
                    gk_text="安全屋:进度已保存", gk_seconds=2.5,
                    gk_speed=30.0)
    _link(logic, safe_in, "执行", cp_set, "执行")
    _link(logic, cp_text, "值", cp_set, "值")
    _link(logic, cp_set, "完成", safe_count, "执行")
    _link(logic, safe_count_get, "值", safe_count, "值")
    _link(logic, safe_count, "完成", safe_tape, "执行")
    _link(logic, safe_tape_get, "值", safe_tape, "值")
    _link(logic, safe_tape, "完成", safe_note, "执行")
    _link(logic, safe_note, "完成", safe_save, "执行")
    _link(logic, safe_save, "完成", visited_branch, "执行")
    _link(logic, visited_get, "值", visited_branch, "条件")
    _link(logic, visited_branch, "否", visited_set, "执行")
    _link(logic, visited_true, "值", visited_set, "值")
    _link(logic, visited_branch, "是", safe_tip, "执行")
    _frame(logic, "⑥ 安全屋 / 检查点", [safe_in, cp_text, cp_set, safe_count,
                                        safe_count_get, safe_tape,
                                        safe_tape_get, safe_save,
                                        visited_get, visited_branch,
                                        visited_true, visited_set, safe_tip],
           (0.24, 0.30, 0.26))

    # ── 被抓:离怪物够近 + 还没失败 -> 倒地 -> 冻住 -> 回安全屋 ──────
    # 和走廊那一条**一模一样**,只换了两个人名 / 一个安全屋坐标。
    # ⚠ 倒地必须勾『恢复』:不勾就把相机从弹簧臂手里永久接管(踩过)。
    phys_fail = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(抓人判定)",
                     -1900.0, -4200.0)
    distance = _add(logic, "GKNodeDistance", "离怪物多远", -1900.0, -4340.0,
                    gk_a=MUSIC_PLAYER, gk_b=MUSIC_ENEMY)
    compare = _add(logic, "GKNodeCompare", "进抓取范围?", -1700.0, -4340.0,
                   gk_op="LT", gk_b=str(CATCH_DISTANCE))
    failed = _add(logic, "GKNodeGetVar", "已经失败了?", -1900.0, -4480.0,
                  gk_var_name="已失败")
    not_failed = _add(logic, "GKNodeLogic", "还没失败", -1700.0, -4480.0,
                      gk_op="NOT")
    can_fail = _add(logic, "GKNodeLogic", "进范围且没失败", -1500.0, -4480.0,
                    gk_op="AND")
    fail_branch = _add(logic, "GKNodeBranch", "被抓住了?", -1300.0, -4200.0)
    set_failed = _add(logic, "GKNodeSetVar", "记下失败", -1100.0, -4200.0,
                      gk_var_name="已失败")
    true_const = _add(logic, "GKNodeConstBool", "真", -1100.0, -4340.0,
                      gk_value=True)
    fall = _add(logic, "GKNodeCameraFall", "镜头倒地", -900.0, -4200.0,
                gk_time=0.9, gk_drop=0.85, gk_roll=72.0, gk_pitch=18.0,
                gk_fade=0.0, gk_restore=True)
    fail_wait = _add(logic, "GKNodeWait", "倒地停一下", -700.0, -4200.0)
    fail_wait.inputs["秒"].default_value = 1.0
    freeze = _add(logic, "GKNodeUiPauseGame", "冻住世界", -500.0, -4200.0,
                  gk_pause=True, gk_show_mouse=True, gk_menu="",
                  gk_hotkey=False)
    fail_text = _add(logic, "GKNodeUiText", "被抓标题", -300.0, -4200.0,
                     gk_text="你被抓住了", gk_align="CENTER", gk_size=44)
    fail_tip = _add(logic, "GKNodeUiText", "被抓提示", -100.0, -4200.0,
                    gk_text="音乐教室里的东西追上了你",
                    gk_align="BOTTOM_CENTER", gk_margin=260.0, gk_size=20)
    retry_btn = _add(logic, "GKNodeUiButton", "回到安全屋按钮", 100.0, -4280.0,
                     gk_text="回到安全屋", gk_align="BOTTOM_CENTER",
                     gk_margin=180.0, gk_width=200.0, gk_height=52.0)
    title_btn = _add(logic, "GKNodeUiButton", "失败回标题按钮", 100.0, -4420.0,
                     gk_text="回到标题", gk_align="BOTTOM_CENTER",
                     gk_margin=110.0, gk_width=200.0, gk_height=52.0)
    resume = _add(logic, "GKNodeUiPauseGame", "解冻(回安全屋)", 300.0, -4280.0,
                  gk_pause=False, gk_show_mouse=True, gk_menu="",
                  gk_hotkey=False)
    back_home = _add(logic, "GKNodeSetPosition", "传回安全屋", 500.0, -4280.0,
                     gk_target=MUSIC_PLAYER,
                     gk_x=_godot_xy(MUSIC_SAFE_POS)[0],
                     gk_y=MUSIC_SAFE_SPAWN_Z,
                     gk_z=_godot_xy(MUSIC_SAFE_POS)[1])
    cp_again_text = _add(logic, "GKNodeConstText", "检查点名(回程)", 500.0,
                         -4140.0, gk_value=MUSIC_CHECKPOINT)
    cp_again = _add(logic, "GKNodeSetVar", "重记检查点", 700.0, -4280.0,
                    gk_var_name=CHECKPOINT)
    failed_false = _add(logic, "GKNodeConstBool", "还没失败(回程)", 700.0,
                        -4140.0, gk_value=False)
    clear_failed = _add(logic, "GKNodeSetVar", "清失败标记", 900.0, -4280.0,
                        gk_var_name="已失败")
    hide_fail_title = _add(logic, "GKNodeUiHide", "收起被抓标题", 1100.0,
                           -4280.0, gk_widget=fail_text.name,
                           gk_visible=False)
    hide_fail_tip = _add(logic, "GKNodeUiHide", "收起被抓提示", 1300.0,
                         -4280.0, gk_widget=fail_tip.name, gk_visible=False)
    hide_retry = _add(logic, "GKNodeUiHide", "收起回安全屋按钮", 1500.0,
                      -4280.0, gk_widget=retry_btn.name, gk_visible=False)
    hide_title_btn = _add(logic, "GKNodeUiHide", "收起回标题按钮", 1700.0,
                          -4280.0, gk_widget=title_btn.name, gk_visible=False)
    home_tip = _add(logic, "GKNodeUiSubtitle", "回安全屋提示", 1900.0,
                    -4280.0, gk_text="你被拖回了安全屋……", gk_seconds=2.5,
                    gk_speed=30.0)
    resume_title = _add(logic, "GKNodeUiPauseGame", "解冻(回标题)", 300.0,
                        -4420.0, gk_pause=False, gk_show_mouse=True,
                        gk_menu="", gk_hotkey=False)
    to_title = _add(logic, "GKNodeChangeScene", "回标题", 500.0, -4420.0,
                    gk_scene_path=TITLE_RES)
    _link(logic, distance, "距离", compare, "A")
    _link(logic, failed, "值", not_failed, "A")
    _link(logic, not_failed, "结果", can_fail, "A")
    _link(logic, compare, "结果", can_fail, "B")
    _link(logic, can_fail, "结果", fail_branch, "条件")
    _link(logic, phys_fail, "执行", fail_branch, "执行")
    _link(logic, fail_branch, "是", set_failed, "执行")
    _link(logic, true_const, "值", set_failed, "值")
    _link(logic, set_failed, "完成", fall, "执行")
    _link(logic, fall, "完成", fail_wait, "执行")
    _link(logic, fail_wait, "完成", freeze, "执行")
    _link(logic, freeze, "完成", fail_text, "执行")
    _link(logic, fail_text, "完成", fail_tip, "执行")
    _link(logic, fail_tip, "完成", retry_btn, "执行")
    _link(logic, retry_btn, "完成", title_btn, "执行")
    _link(logic, retry_btn, "点击", resume, "执行")
    _link(logic, resume, "完成", back_home, "执行")
    _link(logic, back_home, "完成", cp_again, "执行")
    _link(logic, cp_again_text, "值", cp_again, "值")
    _link(logic, cp_again, "完成", clear_failed, "执行")
    _link(logic, failed_false, "值", clear_failed, "值")
    _link(logic, clear_failed, "完成", hide_fail_title, "执行")
    _link(logic, hide_fail_title, "完成", hide_fail_tip, "执行")
    _link(logic, hide_fail_tip, "完成", hide_retry, "执行")
    _link(logic, hide_retry, "完成", hide_title_btn, "执行")
    _link(logic, hide_title_btn, "完成", home_tip, "执行")
    _link(logic, title_btn, "点击", resume_title, "执行")
    _link(logic, resume_title, "完成", to_title, "执行")
    _frame(logic, "⑦ 被抓 / 回安全屋", [phys_fail, distance, compare, failed,
                                        not_failed, can_fail, fail_branch,
                                        set_failed, true_const, fall,
                                        fail_wait, freeze, fail_text, fail_tip,
                                        retry_btn, title_btn, resume,
                                        back_home, cp_again_text, cp_again,
                                        failed_false, clear_failed,
                                        hide_fail_title, hide_fail_tip,
                                        hide_retry, hide_title_btn, home_tip,
                                        resume_title, to_title],
           (0.32, 0.16, 0.14))
    _frame(logic, "⑧ 笔记日志(跨场景)", journal_ui + journal_close_nodes +
           restore_nodes + journal_toggle_nodes, (0.26, 0.20, 0.30))
    # ── 隐藏笔记(第一百六十九轮)──────────────────────────────────
    for scene_name, note_name, _pos, note_text in HIDDEN_NOTES:
        if scene_name == MUSIC:
            _hidden_note_chain(logic, note_name, note_text,
                               MUSIC_PLAYER, -1500.0, -3400.0)
    # ── 追逐切曲(第一百七十九轮)──────────────────────────────────
    _chase_music_chain(logic, -1500.0, -4600.0, CHASE_FILE, BGM_FILE)
    log(f"{MUSIC}关卡图:3 张乐谱(E)+ 录音机四音(1/2/3/4,错两次放怪物)+ "
        f"诱饵(舞台噪音 + 舞台灯灭)+ 磁带 + 侧门({TO_MED})-> "
        f"{MED_RES} / 安全屋 + 被抓回本关")
    return logic


def build_stairs_logic(scene, enemy, ai_tree):
    """第 5 章 · 关卡图:断电 -> 追兵 -> 三层进度 -> 天台(三结局)。

    三条链:
      ① **开局**:载入存档 / 日志面板 / 目标行 / BGM / 暗角 / 蹲走脚本;
      ② **断电**(进门 6 秒):三盏应急灯灭 -> 警报(降调)-> 字幕
         -> 『AI_启用』放出追兵 -> 目标改成"往上跑";
      ③ **楼层**:进二层 / 三层各改一次目标;进天台 -> 冻世界 -> 三选项结局
         (选项 3 = 真结局,要求 `纸条数 + 病历数 >= 6`)。

    ⚠ 追兵**不是**在开局启动的:AI 图起始状态就是追击,开局就启动等于
      一进门就被追。改成断电那一刻才『AI_启用』—— 这也是"灯灭 = 它来了"
      这条叙事的实际落点。
    """
    logic = _level_tree(STAIRS_LEVEL_TREE)
    _set_scene_logic(scene, logic)
    ready = _add(logic, "GKNodeOnReady", "每当_准备", -1500.0, 1400.0)
    reset_bus = _add(logic, "GKNodeBusVolume", "总线回到 0dB", -1300.0, 1400.0,
                     gk_bus="Master", gk_db=0.0, gk_fade=0.0)
    load = _add(logic, "GKNodeSaveLoad", "载入存档", -1100.0, 1400.0,
                gk_slot="save1")
    archive_tail = _archive_zero_vars(logic, reset_bus, -1300.0, 1250.0)
    _link(logic, reset_bus, "完成", archive_tail, "执行")
    _link(logic, archive_tail, "完成", load, "执行")
    read_notes = _add(logic, "GKNodeSaveGet", "读回纸条数", -900.0, 1400.0,
                      gk_key="纸条数", gk_var="纸条数", gk_default=0.0)
    # 真结局要两边的档案:纸条(教室)+ 病历(医务室)。
    read_records = _add(logic, "GKNodeSaveGet", "读回病历数", -700.0, 1400.0,
                        gk_key=MED_COUNT, gk_var=MED_COUNT, gk_default=0.0)
    hint = _add(logic, "GKNodeUiText", "楼梯间目标", -500.0, 1400.0,
                gk_text=STAIRS_HINT_TEXTS[0], gk_x=18.0, gk_y=18.0,
                gk_size=18)
    bgm = _add(logic, "GKNodePlayMusic", "楼梯间BGM", -300.0, 1400.0,
               gk_path=BGM_FILE, gk_volume=-20.0, gk_fade=1.5)
    vignette = _add(logic, "GKNodeScreenVignette", "楼梯间暗角", -100.0,
                    1400.0, gk_amount=0.55, gk_radius=0.55, gk_edge=0.35)
    # ── 笔记日志(跨场景,M0.5):和教室 / 走廊 / 医务室同一份 ────────
    log_panel, log_lines, journal_ui = _build_journal_ui(logic, -2600.0, 2600.0)
    restore_entry, restore_seq, restore_pin, restore_nodes = _journal_restore(
        logic, -2600.0, 2200.0, log_lines)
    journal_toggle_nodes = _journal_toggle(logic, -2600.0, 900.0,
                                          log_panel.name, "楼梯间")
    journal_close_nodes, journal_close_tail = _journal_close(
        logic, -1900.0, 1900.0, "楼梯间")
    crouch_script = _add_crouch_script(logic, 100.0, 1400.0, "蹲走脚本(楼梯)")
    polish = _add_ui_polish(logic, 300.0, 1400.0)
    _link(logic, ready, "执行", reset_bus, "执行")
    _link(logic, reset_bus, "完成", load, "执行")
    _link(logic, load, "完成", read_notes, "执行")
    _link(logic, read_notes, "完成", read_records, "执行")
    _link(logic, read_records, "完成", journal_close_tail, "执行")
    _link(logic, journal_close_tail, "完成", journal_ui[0], "执行")
    _link(logic, journal_ui[-1], "完成", restore_entry, "执行")
    _link(logic, restore_seq, restore_pin, hint, "执行")
    _link(logic, hint, "完成", bgm, "执行")
    _link(logic, bgm, "完成", vignette, "执行")
    _link(logic, vignette, "完成", crouch_script, "执行")
    _link(logic, crouch_script, "完成", polish, "执行")
    _frame(logic, "① 开局 / 载入", [ready, reset_bus, load, read_notes,
                                    read_records, hint, bgm, vignette,
                                    crouch_script, polish] + journal_ui +
           journal_close_nodes + restore_nodes, (0.18, 0.22, 0.34))
    _frame(logic, "⑤ 笔记日志(跨场景)", journal_ui + journal_close_nodes +
           restore_nodes + journal_toggle_nodes, (0.26, 0.20, 0.30))

    hud_nodes = _hud_count(logic, "楼梯间", -1500.0, 1000.0)
    _frame(logic, "② 纸条 HUD", hud_nodes, (0.20, 0.30, 0.24))

    # ── ③ 断电(进门 6 秒):灯灭 + 警报 + 追兵启动 ──────────────────
    blackout = _add(logic, "GKNodeOnTimer", "断电倒计时", -1500.0, 600.0,
                    gk_seconds=BLACKOUT_DELAY, gk_repeat=False)
    blackout_nodes = [blackout]
    previous = blackout
    previous_pin = "执行"
    for index, light_name in enumerate(STAIRS_LIGHTS):
        off = _add(logic, "GKNodeSetLight", f"关灯{index + 1}",
                   -1300.0 + index * 200.0, 600.0, gk_light=light_name,
                   gk_on=False, gk_energy=0.0, gk_fade=0.35)
        _link(logic, previous, previous_pin, off, "执行")
        previous, previous_pin = off, "完成"
        blackout_nodes.append(off)
    alarm = _add(logic, "GKNodePlaySound", "停电警报", -700.0, 600.0,
                 gk_path=ALARM_FILE, gk_volume=-9.0, gk_pitch=0.62,
                 gk_2d=False)
    blackout_say = _add(logic, "GKNodeUiSubtitle", "断电字幕", -500.0, 600.0,
                        gk_text="灯全灭了。有什么东西开始上楼。",
                        gk_seconds=4.0, gk_speed=0.0)
    wake = _add(logic, "GKNodeAiEnable", "放出追兵", -300.0, 600.0,
                gk_tree_name=ai_tree.name, gk_target=STAIRS_ENEMY,
                gk_debug_print=False)
    blackout_goal = _add(logic, "GKNodeUiSet", "目标:往上跑", -100.0, 600.0,
                         gk_widget=hint.name, gk_set_text=True,
                         gk_text=STAIRS_HINT_TEXTS[0])
    _link(logic, previous, previous_pin, alarm, "执行")
    _link(logic, alarm, "完成", blackout_say, "执行")
    _link(logic, blackout_say, "完成", wake, "执行")
    _link(logic, wake, "完成", blackout_goal, "执行")
    blackout_nodes += [alarm, blackout_say, wake, blackout_goal]
    _frame(logic, "③ 断电 / 追兵", blackout_nodes, (0.32, 0.16, 0.16))

    # ── ④ 楼层进度:进二层 / 三层各改一次目标(不用定时轮询)────────
    floor_nodes = []
    for index, area in enumerate(STAIRS_FLOOR_AREAS[1:], start=1):
        enter = _add(logic, "GKNodeOnAreaEntered", f"进入{area}",
                     -1500.0 + index * 500.0, 200.0, gk_area=area,
                     gk_filter="楼梯间玩家")
        say = _add(logic, "GKNodeUiSubtitle", f"{area}字幕",
                   -1300.0 + index * 500.0, 200.0,
                   gk_text=STAIRS_HINT_TEXTS[index], gk_seconds=2.6,
                   gk_speed=0.0)
        goal = _add(logic, "GKNodeUiSet", f"{area}目标",
                    -1100.0 + index * 500.0, 200.0, gk_widget=hint.name,
                    gk_set_text=True, gk_text=STAIRS_HINT_TEXTS[index])
        _link(logic, enter, "执行", say, "执行")
        _link(logic, say, "完成", goal, "执行")
        floor_nodes += [enter, say, goal]
    _frame(logic, "④ 楼层进度", floor_nodes, (0.22, 0.26, 0.18))

    # ── ⑤ 天台:冻世界 -> 三选项(第 3 个 = 真结局)────────────────
    roof = _add(logic, "GKNodeOnAreaEntered", "走到天台", -1500.0, -300.0,
                gk_area=ROOF_AREA, gk_filter="楼梯间玩家")
    freeze = _add(logic, "GKNodeUiPauseGame", "结局冻住世界", -1300.0,
                  -300.0, gk_pause=True, gk_show_mouse=True, gk_menu="",
                  gk_hotkey=False)
    choice = _add(logic, "GKNodeUiChoice", "天台选择", -1100.0, -300.0,
                  gk_question="天台的风很大。你要怎么做?",
                  gk_option_count=3,
                  gk_options="回头救她|独自逃出|回头看清那张脸",
                  gk_speed=0.0)
    _link(logic, roof, "执行", freeze, "执行")
    _link(logic, freeze, "完成", choice, "执行")

    # 档案齐不齐:纸条数 + 病历数 >= TRUE_END_GOAL
    # ⚠ 『存档_读取到变量』**没有值输出**(它只往脚本变量里写),要再挂两个
    #   『变量_读取』才拿得到值 —— 直接连它的"值"会 KeyError(踩过)。
    notes_var = _add(logic, "GKNodeGetVar", "读纸条数(值)", -1500.0, -700.0,
                     gk_var_name="纸条数")
    records_var = _add(logic, "GKNodeGetVar", "读病历数(值)", -1500.0, -840.0,
                       gk_var_name=MED_COUNT)
    total = _add(logic, "GKNodeMath", "档案总数", -1300.0, -700.0,
                 gk_op="ADD")
    enough = _add(logic, "GKNodeCompare", "档案齐了吗", -1100.0, -700.0,
                  gk_op="GE", gk_b=str(TRUE_END_GOAL))
    archive_branch = _add(logic, "GKNodeBranch", "够不够看清", -900.0, -700.0)
    _link(logic, notes_var, "值", total, "A")
    _link(logic, records_var, "值", total, "B")
    _link(logic, total, "结果", enough, "A")
    _link(logic, enough, "结果", archive_branch, "条件")
    # ⚠ 这条**不能漏**:选项 3 -> 档案判断。漏了的话选择框上有第三个选项,
    #   但按 3 之后什么都不发生(生成物里连 match 的第 3 个 case 都没有)。
    _link(logic, choice, "选项3", archive_branch, "执行")

    def _ending(trigger, pin, value, label, tip_text, y, display=None):
        """一条结局链:记变量 -> 存档 -> 文案 -> 两个按钮 -> 解冻回标题。

        ⚠ 回标题前**必须先解冻**:`SceneTree.paused` 跨场景不自动清零。
        ⚠ `label` 会进**节点名**(`结局文字2_<label>`),所以只能放
          标识符安全的字;要带括号 / 标点就另给 `display`
          (节点名里有 `(` 会让 GDScript 直接编不过)。
        """
        display = display or f"结局:{label}"
        const = _add(logic, "GKNodeConstFloat", f"结局值{value}_{label}",
                     -700.0, y, gk_value=float(value))
        set_end = _add(logic, "GKNodeSetVar", f"记结局{value}_{label}",
                       -500.0, y, gk_var_name="结局")
        write = _add(logic, "GKNodeSaveSet", f"写结局{value}_{label}",
                     -300.0, y, gk_key="结局")
        save = _add(logic, "GKNodeSaveWrite", f"保存{value}_{label}",
                    -100.0, y, gk_slot="save1", gk_save_verify=True)
        text = _add(logic, "GKNodeUiText", f"结局文字{value}_{label}",
                    100.0, y, gk_text=f"结局:{label}", gk_align="CENTER",
                    gk_size=44)
        tip = _add(logic, "GKNodeUiText", f"结局提示{value}_{label}",
                   300.0, y, gk_text=tip_text, gk_align="BOTTOM_CENTER",
                   gk_margin=260.0, gk_size=20)
        back_btn = _add(logic, "GKNodeUiButton", f"回到标题按钮{value}_{label}",
                        500.0, y - 80.0, gk_text="回到标题",
                        gk_align="BOTTOM_CENTER", gk_margin=180.0,
                        gk_width=200.0, gk_height=52.0)
        quit_btn = _add(logic, "GKNodeUiButton", f"退出游戏按钮{value}_{label}",
                        500.0, y + 80.0, gk_text="退出游戏",
                        gk_align="BOTTOM_CENTER", gk_margin=110.0,
                        gk_width=200.0, gk_height=52.0)
        resume = _add(logic, "GKNodeUiPauseGame", f"解冻{value}_{label}",
                      700.0, y - 80.0, gk_pause=False, gk_show_mouse=True,
                      gk_menu="", gk_hotkey=False)
        back = _add(logic, "GKNodeChangeScene", f"回标题{value}_{label}",
                    900.0, y - 80.0, gk_scene_path=TITLE_RES)
        quit_game = _add(logic, "GKNodeQuitGame", f"退出{value}_{label}",
                         900.0, y + 80.0)
        _link(logic, const, "值", set_end, "值")
        _link(logic, const, "值", write, "值")
        _link(logic, trigger, pin, set_end, "执行")
        _link(logic, set_end, "完成", write, "执行")
        _link(logic, write, "完成", save, "执行")
        _link(logic, save, "完成", text, "执行")
        _link(logic, text, "完成", tip, "执行")
        _link(logic, tip, "完成", back_btn, "执行")
        _link(logic, back_btn, "完成", quit_btn, "执行")
        _link(logic, back_btn, "点击", resume, "执行")
        _link(logic, resume, "完成", back, "执行")
        _link(logic, quit_btn, "点击", quit_game, "执行")
        return [const, set_end, write, save, text, tip, back_btn, quit_btn,
                resume, back, quit_game]

    ending_nodes = [roof, freeze, choice, total, enough, archive_branch]
    ending_tip = "点『回到标题』再玩一遍,或点『退出游戏』"
    ending_nodes += _ending(choice, "选项1", 1, "回头救她", ending_tip, -1100.0)
    ending_nodes += _ending(choice, "选项2", 2, "独自逃出", ending_tip, -1700.0)
    # 选项 3:**先判档案**。齐了才是真结局,不齐就落到"记忆缺页"的普通结局。
    ending_nodes += _ending(archive_branch, "是", 3, "回头看清那张脸",
                            "真结局 —— 你把所有档案都看过了", -2300.0)
    remember = _add(logic, "GKNodeUiSubtitle", "想不起来", -700.0, -2900.0,
                    gk_text="你想不起来她是谁。风把哭声吹散了。",
                    gk_seconds=4.0, gk_speed=0.0)
    _link(logic, archive_branch, "否", remember, "执行")
    ending_nodes += _ending(remember, "完成", 2, "独自逃出_记忆缺页",
                            ending_tip, -2900.0,
                            display="结局:独自逃出(记忆缺页)")
    _frame(logic, "⑥ 天台结局", ending_nodes, (0.30, 0.18, 0.30))
    log(f"{STAIRS}关卡图:载入 纸条数/病历数 + HUD + 断电({BLACKOUT_DELAY:g}s"
        f" 后关 3 盏灯 + 放出追兵)+ 二层/三层目标 + 天台三结局"
        f"(真结局要 纸条+病历 >= {TRUE_END_GOAL})")
    return logic

# ==========================================================================
# 音频(运行时生成小 wav,放进 Godot 工程的 audio/)
# ==========================================================================
def _write_wav(path, samples, rate=22050):
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = b"".join(struct.pack("<h", max(-32767, min(32767, int(s * 32767))))
                          for s in samples)
        handle.writeframes(frames)


def write_audio(directory):
    """生成三个小 wav(示例自包含):心跳 / 惊吓 / 环境低鸣。"""
    audio_dir = os.path.join(directory, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    rate = 22050

    def thump(duration, freq, decay):
        total = int(rate * duration)
        out = []
        for i in range(total):
            t = i / rate
            env = math.exp(-decay * t)
            out.append(0.55 * env * math.sin(2.0 * math.pi * freq * t))
        return out

    _write_wav(os.path.join(audio_dir, "heartbeat.wav"),
               thump(0.16, 55.0, 26.0), rate)
    _write_wav(os.path.join(audio_dir, "sting.wav"),
               thump(0.40, 300.0, 9.0), rate)
    # 按 K 的反馈:短促的"咚"一声(旧版只有 AI 听得见,玩家毫无反馈)
    _write_wav(os.path.join(audio_dir, "knock.wav"),
               thump(0.10, 95.0, 34.0), rate)
    total = int(rate * 2.0)
    ambient = []
    for i in range(total):
        t = i / rate
        fade = min(1.0, t / 0.3, (2.0 - t) / 0.3)
        tone = (math.sin(2.0 * math.pi * 60.0 * t)
                + 0.6 * math.sin(2.0 * math.pi * 90.0 * t))
        ambient.append(0.12 * max(0.0, fade) * tone / 1.6)
    _write_wav(os.path.join(audio_dir, "ambient.wav"), ambient, rate)
    # ── 第 3 章(音乐教室)────────────────────────────────────────
    # 『练习曲』= 录音机谜题解开之后放的那一段:四个音**就是答案 3-1-4-2**
    # (300/100/400/200 Hz 的方波,带一点衰减),玩家听得出来自己按对了。
    # 现合成,不引外部文件 —— 示例要自包含(和 heartbeat / sting 一样)。
    note_freqs = (300.0, 100.0, 400.0, 200.0)
    etude = []
    step = 0.34
    for freq in note_freqs:
        total = int(rate * step)
        for i in range(total):
            t = i / rate
            env = min(1.0, t / 0.02) * math.exp(-3.6 * t)
            square = 1.0 if math.sin(2.0 * math.pi * freq * t) >= 0.0 \
                else -1.0
            etude.append(0.22 * env * square)
        etude.extend([0.0] * int(rate * 0.06))
    # 尾巴留一点余响,循环听也不突兀
    tail = int(rate * 0.6)
    for i in range(tail):
        t = i / rate
        etude.append(0.10 * math.exp(-5.0 * t)
                     * math.sin(2.0 * math.pi * note_freqs[-1] * t))
    _write_wav(os.path.join(audio_dir, "etude.wav"), etude, rate)
    # 『警报』= 两声刺耳的方波(错音的时候响):低音压着高音一起扫
    alarm = []
    total = int(rate * 0.9)
    for i in range(total):
        t = i / rate
        gate = 1.0 if (t % 0.30) < 0.18 else 0.0
        env = gate * math.exp(-1.4 * (t % 0.30))
        tone = (math.sin(2.0 * math.pi * 880.0 * t)
                + 0.7 * math.sin(2.0 * math.pi * 440.0 * t))
        alarm.append(0.30 * env * tone / 1.7)
    _write_wav(os.path.join(audio_dir, "alarm.wav"), alarm, rate)

    # ── 第一百七十九轮:事件音效 + 追逐曲 ────────────────────────────
    # 全部运行时合成(示例自包含);音量都压在 0.3 左右,别刺耳。
    import random
    rng = random.Random(179)

    # 纸张:三段短噪声("沙、沙沙")—— 捡笔记/病历/乐谱共用
    def noise_bursts(bursts, tail):
        total = int(rate * tail)
        out = [0.0] * total
        prev = 0.0
        for i in range(total):
            t = i / rate
            white = rng.uniform(-1.0, 1.0)
            prev = 0.6 * prev + 0.4 * white      # 一阶低通 -> 纸的"沙"
            amp = 0.0
            for start, dur, g in bursts:
                if start <= t < start + dur:
                    amp = g * math.exp(-18.0 * (t - start))
            out[i] = amp * prev
        return out

    _write_wav(os.path.join(audio_dir, "paper.wav"),
               noise_bursts(((0.00, 0.07, 0.9), (0.10, 0.08, 1.0),
                             (0.21, 0.05, 0.7)), 0.32), rate)
    # 保险丝"咔哒":短噪声瞬态 + 1.9kHz 阻尼振铃
    switch = []
    for i in range(int(rate * 0.14)):
        t = i / rate
        click = 0.4 * math.exp(-60.0 * t) * rng.uniform(-1.0, 1.0)
        ping = 0.5 * math.exp(-30.0 * t) * math.sin(2.0 * math.pi * 1900.0 * t)
        switch.append(click + ping)
    _write_wav(os.path.join(audio_dir, "switch.wav"), switch, rate)
    # 门铰链"吱呀":主频缓慢上滑 + 颤音,叠一条高频"吱"
    door = []
    for i in range(int(rate * 0.55)):
        t = i / rate
        env = min(1.0, t / 0.04) * math.exp(-2.2 * t)
        f = 110.0 + 70.0 * (t / 0.55)
        vib = 1.0 + 0.06 * math.sin(2.0 * math.pi * 9.0 * t)
        creak = math.sin(2.0 * math.pi * f * vib * t)
        squeak = 0.25 * math.sin(2.0 * math.pi * (950.0 + 260.0 * t) * t
                                 + 3.0 * math.sin(2.0 * math.pi * 5.0 * t))
        door.append(0.30 * env * (creak + squeak) / 1.4)
    _write_wav(os.path.join(audio_dir, "door.wav"), door, rate)
    # 耳语:低通白噪 + 音节包络(像凑在耳边说话),受惊后的余韵
    whisper = []
    prev = 0.0
    for i in range(int(rate * 2.2)):
        t = i / rate
        syll = 0.5 + 0.5 * math.sin(2.0 * math.pi * 2.1 * t)
        gate = syll ** 3.0
        fade = min(1.0, t / 0.25, (2.2 - t) / 0.4)
        white = rng.uniform(-1.0, 1.0)
        prev = 0.75 * prev + 0.25 * white
        whisper.append(0.16 * gate * max(0.0, fade) * prev)
    _write_wav(os.path.join(audio_dir, "whisper.wav"), whisper, rate)
    # 追逐曲:150 BPM 低音脉冲 + 反拍沙锤 + 小二度不祥音型(A/Bb 交替)
    chase = []
    step = 0.20                      # 八分音(150 BPM)
    steps = 32                       # 6.4 秒,循环衔接在拍点上
    total = int(rate * step * steps)
    rng2 = random.Random(1790)
    for i in range(total):
        t = i / rate
        pos = t / step
        idx = int(pos) % steps
        frac = pos - int(pos)
        out = 0.0
        bass_env = math.exp(-9.0 * frac * step) * min(1.0, frac * step / 0.008)
        ph = (55.0 * t) % 1.0
        out += 0.30 * bass_env * (2.0 * ph - 1.0)
        if idx % 2 == 1:
            out += 0.10 * math.exp(-40.0 * frac * step) * rng2.uniform(-1.0, 1.0)
        note = 220.0 if (idx // 4) % 2 == 0 else 233.0
        tone_env = math.exp(-2.5 * frac * step) * min(1.0, frac * step / 0.01)
        det = math.sin(2.0 * math.pi * note * t) \
            + 0.5 * math.sin(2.0 * math.pi * note * 1.005 * t)
        out += 0.16 * tone_env * det / 1.5
        if idx % 8 == 7:
            sweep = frac
            out += 0.10 * sweep * math.sin(
                2.0 * math.pi * (300.0 + 900.0 * sweep * sweep) * frac * step)
        chase.append(out)
    _write_wav(os.path.join(audio_dir, "chase.wav"), chase, rate)
    # 脚步声(第一百八十二轮):落地闷响(60Hz 短音)+ 擦地噪声,
    # 0.09 秒 —— 播放侧再做 ±10% 音调随机,连走不像机关枪。
    footstep = []
    prev = 0.0
    for i in range(int(rate * 0.09)):
        t = i / rate
        thud = 0.5 * math.exp(-45.0 * t) * math.sin(2.0 * math.pi * 60.0 * t)
        white = rng.uniform(-1.0, 1.0)
        prev = 0.55 * prev + 0.45 * white
        scuff = 0.35 * math.exp(-30.0 * t) * prev
        fade = min(1.0, t / 0.004)
        footstep.append((thud + scuff) * fade)
    _write_wav(os.path.join(audio_dir, "footstep.wav"), footstep, rate)
    log(f"音频:heartbeat / sting / knock / ambient / etude / alarm / "
        f"paper / switch / door / whisper / chase / footstep -> {audio_dir}")


UI_POLISH_SCRIPT = """extends Node
# M0 界面修补:把插件**硬编码**的两个面板底色改结实(只动运行时样式,
# 不碰插件源码)。为什么需要它:
#   ① 『对话框』(界面_字幕用的那块)底色是 Color(0.05,0.06,0.09,0.85),
#      亮色角色站在字幕后面会透出来,把字幕中段糊成一块暗影
#      (实机截图 05/06);节点上没有"字幕底不透明度"这个参数。
#   ② 『选择框』(对话_选择)的选项行是深底 + 暗字,实机里几乎看不清
#      (实机截图 12)。
# 两个面板都是**懒创建**(第一次弹才 new),所以这里每 0.25 秒补一次;
# 改过的面板打 meta 标记,不会反复重建样式。

# 字幕板直接**不透明**:0.96 也还是能看见白色角色透出来(实测:
# 白盒子在 4% 线性混合下换算到 sRGB 仍有约 20% 的亮度,一条明显的亮柱)。
const SUBTITLE_ALPHA := 1.0
const CHOICE_ALPHA := 0.96
const ROW_ALPHA := 0.92
const POLL := 0.25


func _ready() -> void:
    # 选择框是**暂停状态**下弹出来的,进程模式必须是 ALWAYS 才修得到。
    process_mode = Node.PROCESS_MODE_ALWAYS
    _polish()
    var timer := Timer.new()
    timer.wait_time = POLL
    timer.one_shot = false
    timer.process_mode = Node.PROCESS_MODE_ALWAYS
    timer.timeout.connect(_polish)
    add_child(timer)
    timer.start()


func _polish() -> void:
    var root := get_parent()
    if root == null:
        return
    for node in root.find_children("*", "Panel", true, false):
        var panel := node as Panel
        if panel == null or panel.has_meta("gk_m0_polished"):
            continue
        var panel_name := String(panel.name)
        if panel_name == "对话框":
            _set_panel_alpha(panel, SUBTITLE_ALPHA)
            panel.set_meta("gk_m0_polished", true)
        elif panel_name == "选择框":
            _set_panel_alpha(panel, CHOICE_ALPHA)
            _polish_choice_rows(panel)
            panel.set_meta("gk_m0_polished", true)


func _set_panel_alpha(panel: Panel, alpha: float) -> void:
    # ── 换皮(第一百八十五回):圆角 + 苍白描边 + 柔和投影 ──────────────
    # 原来只是把底色改结实;现在给对话框/选择框一个"电影对白卡"的观感:
    # 12px 圆角、1px 冷白描边(35%)、向下 10px 的黑色柔影。
    var box := panel.get_theme_stylebox("panel")
    if box is StyleBoxFlat:
        var flat := (box as StyleBoxFlat).duplicate() as StyleBoxFlat
        flat.bg_color.a = alpha
        flat.set_corner_radius_all(12)
        flat.set_border_width_all(1)
        flat.border_color = Color(0.72, 0.78, 0.85, 0.35)
        flat.shadow_color = Color(0.0, 0.0, 0.0, 0.45)
        flat.shadow_size = 10
        flat.shadow_offset = Vector2(0.0, 4.0)
        panel.add_theme_stylebox_override("panel", flat)


func _polish_choice_rows(panel: Node) -> void:
    for node in panel.find_children("*", "Button", true, false):
        var button := node as Button
        if button == null:
            continue
        var box := StyleBoxFlat.new()
        box.bg_color = Color(0.10, 0.12, 0.18, ROW_ALPHA)
        box.set_corner_radius_all(8)
        button.add_theme_stylebox_override("normal", box)
        var hot := box.duplicate() as StyleBoxFlat
        hot.bg_color = Color(0.18, 0.22, 0.32, ROW_ALPHA)
        button.add_theme_stylebox_override("hover", hot)
        button.add_theme_stylebox_override("pressed", hot)
        button.add_theme_color_override("font_color", Color(0.97, 0.96, 0.90))
        button.add_theme_color_override("font_hover_color", Color(1, 1, 1))
        button.add_theme_color_override("font_pressed_color", Color(1, 1, 1))
        button.add_theme_font_size_override("font_size", 22)
"""

CROUCH_SCRIPT = """extends Node
# M0 蹲走:相机压低 + 碰撞胶囊压低。
# 速度在角色图(玩家逻辑(公共))里改 move_speed,这里只管"人矮下去"。
#
# ── 为什么这件事必须用脚本,节点做不到 ────────────────────────────
# 弹簧臂在**角色脚本的 _physics_process 里**每物理帧把相机拽回
# "玩家位置 + 1.4 米";节点里 tween / 设置相机位置,下一帧就被覆盖。
# 这个脚本把 process_physics_priority 调大 = 排在角色脚本**之后**跑,
# 等它把相机摆好,再往下压一截。

# 玩家名兜底名册(五幕各一个)。**正常路径不靠它** —— 见 _find_player():
# 白名单漏一个,蹲走的相机 / 胶囊那一半就静默失效,而速度那一半还是好的
# (第 3/4 章加进来时就这么"假绿"过一轮,门禁只验到速度)。
const PLAYER_NAMES := ["走廊玩家", "教室玩家", "楼梯间玩家", "音乐教室玩家",
    "医务室玩家"]
const CROUCH_ACTION := "key_left_ctrl"
const CAMERA_DROP := 0.62
const CROUCH_HEIGHT := 1.05
const CROUCH_IN_SPEED := 8.0
const CROUCH_OUT_SPEED := 2.2

var player: Node3D = null
var capsule: CapsuleShape3D = null
var shape_node: CollisionShape3D = null
var stand_centre := 0.0
var stand_height := 1.6
var dip := 0.0


# ── 怎么认人(别再靠名册)─────────────────────────────────────────
# ① 先扫本关所有 CharacterBody3D,挑**身上带『移动速度』那个变量**的那台:
#    角色图(玩家逻辑(公共))挂在玩家身上,只有它带这个变量;怪是人手摆的
#    空壳,没有。以后加关卡不用回来改名册。
# ② 扫不到才退回 PLAYER_NAMES(万一哪天变量被改名)。
func _find_player(host: Node) -> Node3D:
    for node in host.find_children("*", "CharacterBody3D", true, false):
        var body := node as Node3D
        if body != null and body.get("移动速度") != null:
            return body
    for candidate in PLAYER_NAMES:
        var found := host.find_child(candidate, true, false) as Node3D
        if found != null:
            return found
    return null


func _ready() -> void:
    process_physics_priority = 100
    var host := get_parent()
    if host == null:
        return
    player = _find_player(host)
    if player == null:
        push_warning("[GK 蹲走] 找不到玩家(试过 " + str(PLAYER_NAMES) + ")")
        return
    for node in player.find_children("*", "CollisionShape3D", true, false):
        var cs := node as CollisionShape3D
        if cs == null or not (cs.shape is CapsuleShape3D):
            continue
        shape_node = cs
        capsule = (cs.shape as CapsuleShape3D).duplicate()
        cs.shape = capsule
        stand_centre = cs.position.y
        stand_height = capsule.height
        break


func _physics_process(delta: float) -> void:
    if player == null:
        return
    # ⚠ 必须走**输入动作**,不能 Input.is_key_pressed(KEY_CTRL):
    #   动作是按 physical_keycode 匹配的(project.godot 里导出的就是它),
    #   is_key_pressed 比的是 keycode。合成事件(探针 / 手柄映射)里
    #   keycode 可能是 0,于是"速度那一半动了、相机/胶囊这一半没动"
    #   —— 上一轮就是这么漏的。动作写法三条输入路径一致。
    var crouching := false
    if InputMap.has_action(CROUCH_ACTION):
        crouching = Input.is_action_pressed(CROUCH_ACTION)
    else:
        crouching = Input.is_key_pressed(KEY_CTRL)
    # 下蹲快、起身慢(起身 2.2 米/秒):既有"站起来"的过渡感,
    # 也让"松开 Ctrl 之后 0.15 秒采样"的探针量得到压低量。
    var rate: float = CROUCH_IN_SPEED if crouching else CROUCH_OUT_SPEED
    dip = move_toward(dip, CAMERA_DROP if crouching else 0.0, delta * rate)
    # ⚠ 只在玩家**看得见**的时候压相机:
    #   躲进病床帘时当前相机是场景里那台『医务室躲藏机位』,压它等于把
    #   "从帘子里往外看"的镜头整体往下沉;而且出来时会带着残留的 dip 跳一下。
    #   所以隐藏期间把 dip 归零,出来第一帧就是干净的。
    if not player.visible:
        dip = 0.0
    var cam := get_viewport().get_camera_3d()
    if cam != null and player.visible:
        cam.global_position.y -= dip
    if capsule != null:
        # 胶囊跟着"蹲姿"走(含起身过渡),不是直接跟按键 —— 否则一松手
        # 碰撞体瞬间弹回 1.60,和画面里的高度对不上。
        var low := crouching or dip > 0.06
        var want: float = CROUCH_HEIGHT if low else stand_height
        capsule.height = want
        if shape_node != null:
            shape_node.position.y = stand_centre - (stand_height - want) * 0.5
"""

def save_blend(path):
    if not bpy.app.background:
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return
    blocks = set(bpy.data.objects) | set(bpy.data.meshes) | \
        set(bpy.data.materials) | set(bpy.data.node_groups) | \
        set(bpy.data.cameras) | set(bpy.data.lights) | set(bpy.data.curves) | \
        set(bpy.data.texts)
    # 文本块显式收进工程，包括尚未被节点引用的脚本。
    # 节点通过 gk_script_text 保存 Text 引用，保存重载后仍指向同一脚本。
    scenes = list(bpy.data.scenes)
    bpy.data.libraries.write(path, blocks | set(scenes), fake_user=True)


def main():
    from BlenderGameKit import asset_export, project, register

    # ⚠ 直接 `blender -b --factory-startup --python scripts\make_horror_demo.py`
    #    跑时插件**没有被启用**,节点类一个都不存在 ——
    #    第一步 `bpy.data.node_groups.new(..., "GKGameLogicTree")` 就炸。
    #    门禁是"先装扩展再跑",所以这个洞一直没暴露(用 `--factory-startup`
    #    复现过:脚本必失败)。这里补上和 `make_story_demo.py` 一样的自注册:
    #    已经注册过就什么都不做(`register()` 幂等),装好扩展再跑也一样。
    if bpy.types.NodeTree.bl_rna_get_subclass_py("GKGameLogicTree", None) is None:
        register()

    os.makedirs(OUT_DIR, exist_ok=True)
    # ── M0:蹲走脚本写进 Blender 文本块(『脚本_加载』挑它)──────────
    # 导出时 script_assets 会把**被节点引用到的**文本块写成
    # res://scripts/custom/<安全名>.gd;改这里再重建就能复现。
    crouch_text = bpy.data.texts.get(CROUCH_SCRIPT_NAME)
    if crouch_text is None:
        crouch_text = bpy.data.texts.new(CROUCH_SCRIPT_NAME)
    crouch_text.use_fake_user = True
    crouch_text.clear()
    crouch_text.write(CROUCH_SCRIPT)
    polish_text = bpy.data.texts.get(UI_POLISH_SCRIPT_NAME)
    if polish_text is None:
        polish_text = bpy.data.texts.new(UI_POLISH_SCRIPT_NAME)
    polish_text.use_fake_user = True
    polish_text.clear()
    polish_text.write(UI_POLISH_SCRIPT)

    room = clear_all()

    # ---- 第零幕 标题页(开局进这里)----
    # 标题逻辑挂**场景根**(关卡图),没有角色图。
    title = build_title()
    write_physics(title, None)
    build_title_logic(title)
    _apply_settings(title, (("dimension", "3"),) + ENV_TITLE)

    # ---- 第一关 教室(用已有的那个场景)----
    room_player = build_room(room)
    write_physics(room, room_player,
                  triggers=set(NOTE_NAMES) | {TO_ROOM})
    # 角色图只建**一次**,三关共用(机位名统一)。
    build_player_role(room, room_player)
    build_room_logic(room)
    _apply_settings(room, (("dimension", "3"),
                           ("toon_follow_light", True)) + ENV_ROOM)

    # ---- 第二关 走廊 ----
    corridor, corridor_player, enemy, torch = build_corridor()
    # ⚠ 出口板的名字跟着关卡图一起改:漏改这里的话『去音乐教室』会被当成
    #   普通网格判成 StaticBody3D —— 一块挡路的实体,Area3D 事件永远不触发。
    # 第 168 轮:三个保险丝判定体也要进 triggers —— 不然是 StaticBody3D,
    # 『区域_进入』生成的 `var x: Area3D` 拿到 StaticBody3D,整张关卡图
    # 直接 Parse Error(实测)。
    write_physics(corridor, corridor_player, enemy=enemy, torch=torch,
                  triggers={SCARE_AREA, TO_MUSIC, SAFE_ROOM_AREA}
                  | set(FUSES) | {HIDDEN_NOTE_NAMES[CORRIDOR]})
    adopt_player(corridor, corridor_player)
    ai_tree = build_ai_tree()
    build_corridor_logic(corridor, enemy, ai_tree)
    _apply_settings(corridor, (("dimension", "3"),
                               ("toon_follow_light", True)) + ENV_DARK)

    # ---- 第三关 音乐教室(M1a:插在走廊和楼梯间之间)----
    # 流程:标题 -> 教室 -> 走廊 -> **音乐教室** -> 楼梯间(结局)
    music, music_player, music_enemy, _sheets, _recorder, _tape = \
        build_music_room()
    # ⚠ M1b:出口改名『去医务室』—— triggers 必须同步,漏了这块判定板
    #   会被判成 StaticBody3D(一块挡路实体 + Area3D 事件永不触发)。
    write_physics(music, music_player, enemy=music_enemy,
                  triggers=set(SHEET_NAMES) |
                  {RECORDER, TAPE, STAGE, MUSIC_SAFE_AREA, TO_MED,
                   HIDDEN_NOTE_NAMES[MUSIC]})
    adopt_player(music, music_player)
    music_ai = build_music_ai_tree()
    build_music_logic(music, music_enemy, music_ai)
    _apply_settings(music, (("dimension", "3"),
                            ("toon_follow_light", True)) + ENV_MUSIC)

    # ---- 第四关 医务室(M1b:插在音乐教室和楼梯间之间)----
    # 流程:标题 -> 教室 -> 走廊 -> 音乐教室 -> **医务室** -> 楼梯间(结局)
    med, med_player, med_enemy, med_torch = build_med_room()
    write_physics(med, med_player, enemy=med_enemy, torch=med_torch,
                  triggers=set(MED_SHEETS) | set(MED_BATTERIES) |
                  set(MED_HIDES) | {MED_SAFE_AREA, TO_STAIRS,
                  HIDDEN_NOTE_NAMES[MED], MED_SCARE_AREA})
    adopt_player(med, med_player)
    med_ai = build_med_ai_tree()
    build_med_logic(med, med_enemy, med_ai)
    _apply_settings(med, (("dimension", "3"),
                          ("toon_follow_light", True)) + ENV_MED)

    # ---- 第五关 楼梯间(M2a:断电追逐 + 天台结局)----
    # 流程:标题 -> 教室 -> 走廊 -> 音乐教室 -> 医务室 -> **楼梯间** -> 天台
    stairs, stairs_player, stairs_enemy = build_stairs()
    write_physics(stairs, stairs_player, enemy=stairs_enemy,
                  triggers=set(STAIRS_FLOOR_AREAS) |
                  {ROOF_AREA, STAIRS_SAFE_AREA})
    adopt_player(stairs, stairs_player)
    stairs_ai = build_stairs_ai_tree()
    build_stairs_logic(stairs, stairs_enemy, stairs_ai)
    _apply_settings(stairs, (("dimension", "3"),
                             ("toon_follow_light", True)) + ENV_STAIRS)

    directory = os.path.join(OUT_DIR, "恐怖示例_godot")
    project.create_project(directory, title="恐怖示例",
                           blend_file_name=os.path.basename(BLEND_PATH))
    result = asset_export.export_all_scenes(directory, dimension="3")
    if result.get("error"):
        log(f"导出失败:{result['error']}")
        return 1
    for entry in result.get("scenes", []):
        log(f"场景 {entry['scene']} -> {entry['scene_name']}.tscn"
            f"(脚本 {entry['script_name']})")
    for warning in result.get("warnings", []):
        log(f"告警: {warning}")
    if result.get("deleted"):
        log(f"清理旧文件: {result['deleted']}")
    # 主场景显式指到**标题页**:开局先给标题,点『开始游戏』才进教室
    exported = {f"res://scenes/{e['scene_name']}.tscn"
                for e in result.get("scenes", []) if e.get("ok")}
    if TITLE_RES in exported:
        project.set_main_scene(directory, TITLE_RES)
    log(f"主场景: {TITLE_RES}")

    write_audio(directory)

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    log(f"工程目录: {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
