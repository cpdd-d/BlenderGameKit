"""Godot 中文对照表。

── 为什么需要这个东西 ──────────────────────────────────────────
用户反馈:"在 godot 本地化翻译,一堆英文看着迷糊,先汉化(比如模型属性,
添加新的节点等等),我才知道哪个是哪个"。

实测下来的事实(排查过程见文末):

1. **Godot 编辑器本身早就是中文了**。
   编辑器设置里 `interface/editor/localization/editor_language = "auto"`,
   系统区域是 zh_CN,Godot 会自动显示中文(菜单"场景/项目/调试"、
   "文件系统/历史记录/检查器"这些都对)。

2. **剩下的英文改不了,那不是语言设置的问题**:
   * 节点类型名(MeshInstance3D、StaticBody3D…)是**引擎里的类名**,
     任何语言下都不翻译 —— "添加节点"对话框里永远是英文。
   * 检查器里的**分组标题**(Mesh / Transform / Script / Metadata…)
     Godot 的中文语言包没有覆盖。
   这两类东西没有任何设置能让它们变中文。

3. ⚠ **绝对不要给 Godot 加 `-l zh_CN`**。
   实测:加了之后界面**反而全变英文** —— 这个参数要的是两位语言码,
   `zh_CN` 是无效值,Godot 回退到英文。不加才是中文。

既然改不了 Godot,那就给一份**对照表**:用户看到 MeshInstance3D、
Surface Material Override 这些词时,能查到是什么意思。
"""

# 说明文档的默认文件名
FILE_NAME = "Godot中文对照.md"

# 对照表正文。
#
# 内容顺序按"用户实际会遇到的顺序"排:
# 先是他导出的场景里会看到的节点,再是检查器里的分组。
GLOSSARY = """# Godot 中文对照表

> 这份表是 BlenderGameKit 生成的,专门覆盖**你会实际碰到**的那些英文。
> 可以随时删掉,重新点一次按钮会再生成。

---

## 一、先搞清楚:哪些英文是改不了的

| 情况 | 能不能变中文 |
|---|---|
| 菜单栏、按钮、面板标题(场景/项目/检查器…) | **已经是中文** |
| 节点类型名(MeshInstance3D、StaticBody3D…) | ❌ 引擎类名,任何语言都是英文 |
| 检查器里的分组标题(Mesh / Transform / Script…) | ❌ Godot 中文包没覆盖 |

编辑器界面本身已经汉化好了,不用去改设置。

> ⚠️ **不要给 Godot 加 `-l zh_CN` 参数。**
> 实测加了之后界面**反而全变英文**(这个参数要的是两位语言码,
> `zh_CN` 被当成无效值,回退了)。保持编辑器设置里的 `auto` 就是中文。

---

## 二、节点类型(「添加节点」对话框里那些英文)

这是导出场景后你会看到的节点:

| Godot 里显示 | 中文 | 干什么用的 |
|---|---|---|
| `Node3D` | 3D 节点 | 空节点,只用来分组或记位置 |
| `MeshInstance3D` | 网格实例 | **显示模型**(Blender 导出的模型就是这个) |
| `StaticBody3D` | 静态碰撞体 | 不动的墙、地板(游戏物理里选 StaticBody3D) |
| `RigidBody3D` | 刚体 | 会被重力推着到处滚的箱子 |
| `CharacterBody3D` | 角色体 | 玩家/敌人,靠代码控制移动 |
| `Area3D` | 触发区 | 只检测"碰到了",不挡路(对应「作为触发区」) |
| `CollisionShape3D` | 碰撞形状 | 挂在碰撞体下面的盒子/球/胶囊 |
| `Camera3D` | 相机 | 视点 |
| `DirectionalLight3D` | 平行光 | 太阳(不衰减,照亮整个场景) |
| `OmniLight3D` | 点光 | 灯泡(会随距离变暗) |
| `SpotLight3D` | 聚光 | 手电筒 |
| `WorldEnvironment` | 世界环境 | 背景、辉光、色调映射 |
| `Marker3D` | 标记点 | 只记一个坐标位置 |
| `AnimationPlayer` | 动画播放器 | 播动画 |
| `AudioStreamPlayer3D` | 3D 音源 | 发声 |

---

## 三、检查器(右侧)里的分组 —— 以 MeshInstance3D 为例

**这一段就是你截图里那一堆英文。**

| 英文 | 中文 |
|---|---|
| `Mesh` | 网格(用哪个模型) |
| `Skeleton` | 骨骼 |
| `Skin` | 蒙皮 |
| `Surface Material Override` | 表面材质覆盖(给某个面单独换材质) |
| `Geometry` | 几何(实例数量等) |
| `Global Illumination` | 全局光照 |
| `Visibility Range` | 可见距离(远了自动隐藏/LOD) |
| `VisualInstance3D` | 可视实例(下面还有 `Layers` 图层、`Sorting` 排序) |
| `Transform` | **变换**(位置 / 旋转 / 缩放 —— 最常用的一个) |
| `Visibility` | 可见性 |
| `Process` | 处理(是否每帧调用 `_process`) |
| `Physics Interpolation` | 物理插值(减少抖动) |
| `Auto Translate` | 自动翻译 |
| `Editor Description` | 编辑器描述(只是备注) |
| `Script` | **脚本** |
| `Metadata` | 元数据(自己存的自定义数据) |

`Transform` 展开后:

| 英文 | 中文 |
|---|---|
| `Position` | 位置 |
| `Rotation` | 旋转 |
| `Scale` | 缩放 |

---

## 四、碰撞体(StaticBody3D / Area3D)

| 英文 | 中文 |
|---|---|
| `Collision` | 碰撞 |
| `Shape` | 形状 |
| `Disabled` | 禁用 |
| `Collision Layer` | 碰撞层(我在哪一层) |
| `Collision Mask` | 碰撞掩码(我和哪些层碰) |
| `Monitoring` | 监听(Area3D 能不能发现别人) |
| `Monitorable` | 可被监听(别人能不能发现我) |
| `Physics Material Override` | 物理材质覆盖(摩擦力/弹性) |
| `Constant Linear Velocity` | 恒定线速度 |
| `Constant Angular Velocity` | 恒定角速度 |

---

## 五、相机与灯光

**Camera3D**

| 英文 | 中文 |
|---|---|
| `Keep Aspect` | 保持宽高比 |
| `Cull Mask` | 裁剪掩码(哪些层可见) |
| `Environment` | 环境 |
| `H Offset` / `V Offset` | 水平 / 垂直偏移 |
| `Projection` | 投影方式(透视 / 正交) |
| `Current` | 当前相机(勾上才是这个相机在用) |
| `FOV` | 视场角 |
| `Near` / `Far` | 近 / 远裁剪面 |
| `Doppler Tracking` | 多普勒效应 |

**DirectionalLight3D(太阳)**

| 英文 | 中文 |
|---|---|
| `Light` | 灯光 |
| `Light Energy` | **光照强度**(太亮就调小,Godot 默认 1.0) |
| `Light Color` | 灯光颜色 |
| `Shadow` | 阴影 |
| `Enabled` | 启用 |
| `Cull Mask` | 裁剪掩码 |
| `Bake Mode` | 烘焙模式 |

---

## 六、界面上的常用词

| 英文 | 中文 | 英文 | 中文 |
|---|---|---|---|
| `Scene` | 场景 | `Inspector` | 检查器 |
| `Import` | 导入 | `Signals` | 信号 |
| `Project` | 项目 | `Groups` | 分组 |
| `Debug` | 调试 | `Filter Properties` | 筛选属性 |
| `Editor` | 编辑器 | `Filter Nodes` | 筛选节点 |
| `Help` | 帮助 | `Create Root Node` | 创建根节点 |
| `FileSystem` | 文件系统 | `Add Node` | 添加节点 |
| `History` | 历史记录 | `Attach Script` | 挂脚本 |
| `Favorites` | 收藏 | `Load Scene` | 加载场景 |
| `Output` | 输出 | `2D` / `3D` / `Script` / `Game` | 2D / 3D / 脚本 / 游戏 |
| `Debugger` | 调试器 | `Asset Store` | 资源商店 |
| `Audio` | 音频 | `Shader Editor` | 着色器编辑器 |
| `Animation` | 动画 | `Remote` / `Local` | 远程 / 本地 |

---

## 七、调节画面对应到哪

| 想改什么 | 去哪 |
|---|---|
| 整体亮度 | 灯光的 `Light Energy`(1.0 是默认) |
| 背景颜色 / 天空 | `WorldEnvironment` 节点的 `Environment` → `Background` |
| 颜色发灰发淡 | `Environment` → `Tonemap`(默认 `Linear`) |
| 边缘锯齿 | 项目设置 → `Rendering` → `Anti Aliasing` |
| 物体边缘发光 | `Environment` → `Glow` |

改项目设置:菜单 **项目 → 项目设置**,左上角搜索框直接搜 `anti` 之类的关键词。
"""


def write(directory):
    """把对照表写到指定目录。返回 (路径, 是否成功, 说明)。"""
    import os

    if not directory:
        return None, False, "没有可写入的目录"
    path = os.path.join(directory, FILE_NAME)
    try:
        os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(GLOSSARY)
    except OSError as exc:
        return None, False, f"写不了对照表: {exc}"
    return path, True, path


def find(directory):
    """看看对照表是不是已经在那儿了。"""
    import os

    if not directory:
        return None
    path = os.path.join(directory, FILE_NAME)
    return path if os.path.isfile(path) else None


def on_register():
    """没有需要在注册时做的事。"""
