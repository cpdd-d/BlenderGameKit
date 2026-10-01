# BlenderGameKit

**在 Blender 里做完游戏,只在测试时打开 Godot。**

**一个 `.blend` 文件对应一个 Godot 项目。** 打开哪个 Blender 工程,就知道该往哪导出,
不用每次填路径。

| 你想做的事 | 在 Blender 里 | 在 Godot 里 |
|---|---|---|
| 摆场景、建模型 | ✅ | |
| 做动作、材质、卡通调参 | ✅(EEVEE 预览) | |
| 写游戏逻辑 | ✅(中文节点图) | |
| **测试与游玩** | | ✅ |

**在 Blender 里摆好的场景会原样出现在 Godot 里** —— 点『导出模型到 Godot』
会把整个场景(含层级、位置、灯光、相机)一起导出。

你喜欢 Blender 的交互，也喜欢 UE5 的蓝图；这个插件就是为这个组合做的：

| 你的痛点 | BlenderGameKit 的做法 |
|---|---|
| UPBGE 逻辑砖太简陋、没汉化 | 79 个中文节点，含分支 / 序列 / 循环 / 等待 / 信号 / 状态机 / 相机 / 存档 / 音效 / 界面 |
| Godot 界面简陋、不熟悉、没有可视化脚本 | 逻辑在**你熟悉的 Blender 节点编辑器**里画，Godot 只负责运行 |
| UE5 太臃肿、配置要求高 | 生成的是纯 GDScript，Godot 本体只有几十 MB |
| Unity 国内版不好用 | 完全不依赖 Unity |
| 导出路径每次都要填 | **跟着 `.blend` 走**,一个文件一个项目,自动建好目录结构 |
| 碰撞体要跑到 Godot 里配 | **在物体属性面板里设**(像 UPBGE),导出时自动生成 |
| 二次元游戏 | **3 个 NPR 卡通着色器**(描边/边缘光/三阶明暗/面部 SDF) + 手绘线条抖动 |

不是运行时解释器，是**代码生成器**：节点图 → 标准 GDScript 4 → 随便改、随便读、Godot 原生调试。
渲染也不是"通用 PBR 降级版"，而是按日式动画赛璐璐的做法写的。

---

## 一分钟上手

1. **保存 `.blend`**(Ctrl+S)—— 项目要建在它旁边,所以必须先有路径
2. 打开节点编辑器 → 新建 **游戏逻辑** 节点树
3. 编辑器顶部会出现 **创建 Godot 项目**,点它 → 建好后问你要不要用 Godot 打开
4. **点『生成示例节点图』** —— 直接给一条能跑的链路,照着改就行
5. 开始画逻辑:按 **Shift+A** 添加节点(和 Blender 其他地方一样)
6. 侧栏 **导出为 Godot 脚本** / **导出模型到 Godot**
7. 想测试时点 **打开 Godot 编辑器**(或 **直接运行游戏**)

> **节点怎么用?** 一句话:**金色线是骨架**。必须先放一个『事件』节点当入口
> (比如「每当_准备」),再用金色『执行』线把后面要做的事串起来。灰色线只是传值。
> 详细教程见 [docs/Blender与Godot互通.md](docs/Blender与Godot互通.md) 第五节。

项目结构自动长这样:

```
我的游戏/
├── 我的游戏.blend          ← 你的 Blender 工程
└── 我的游戏_godot/         ← 配套的 Godot 项目(自动创建)
    ├── project.godot       主场景已自动指好,按 F5 就能跑
    ├── scripts/            逻辑脚本(节点图生成)
    ├── scenes/             场景装配(模型 + 脚本 + 碰撞体)
    ├── art/                模型 .glb 与贴图 .png
    ├── materials/          卡通材质 .tres
    └── shaders/            NPR 着色器
```

**为什么不做成 `.blend` 同目录下?** 那样 `.godot/` 导入缓存和 `.blend1` 备份文件
会混在一起,Godot 还会去扫描 `.blend1` 这类垃圾文件。固定用同名子目录最干净。

**想放到别处?** 侧栏『项目』→ **改位置**。这个选择会**写进 `.blend`**,下次打开还记得。

---

## 三条最重要的文档

| 你想知道 | 看这个 |
|---|---|
| **项目怎么组织、模型贴图怎么进 Godot、脚本怎么挂、C++ 怎么写** | [docs/Blender与Godot互通.md](docs/Blender与Godot互通.md) |
| **卡通着色怎么调、参数什么意思** | [docs/渲染-NPR卡通着色.md](docs/渲染-NPR卡通着色.md) |
| 安装、上手、节点清单、踩过的坑 | 就是本文件 |

---

## 目录结构

```
BlenderGameKit/                 插件本体(这个文件夹就是要安装的东西)
├── __init__.py                 注册入口(按依赖顺序注册所有类)
├── blender_manifest.toml       Blender 扩展清单
├── gk_sockets.py               引脚类型:执行流 / 任意值 + 内置类型语义表
├── gk_log.py                   日志(面板里能看最近 200 条)
├── codegen.py                  ★ 节点图 → GDScript 代码生成器
├── exporter.py                 脚本落盘、Godot 工程骨架
├── asset_export.py             ★ 资产导出:glTF + 卡通材质 + 场景装配
├── npr_preview.py              ★ Blender 端 NPR 预览材质(EEVEE 里调卡通参数)
├── project.py                  ★ 一个 .blend 一个 Godot 项目:路径解析 / 建骨架 / 启动 Godot
├── properties.py               场景级导出设置(2D/3D、基类、类名…)
├── operator_io.py              算子:导出脚本 / 导出模型 / NPR 预览 / 素材导出
├── ui.py                       侧栏面板、添加节点菜单、节点引用选择器
├── shaders/                    ★ NPR 着色器(随插件打包)
│   ├── gk_toon.gdshader        3D 卡通:色阶切分 / 三段明暗 / light_map / 面部 SDF
│   ├── gk_outline.gdshader     描边:反面外扩 + 手绘线条抖动(boiling line)
│   └── gk_toon_2d.gdshader     2D 精灵:伪造光照 / 边缘高光 / 受击闪白
└── nodes/
    ├── node_base.py            节点基类、声明式属性、桩环境支持
    └── nodes.py                ★ 79 个节点的定义

docs/
├── Blender与Godot互通.md       资产管线 + 脚本搭载 + C++ GDExtension
└── 渲染-NPR卡通着色.md          着色器用法、参数含义、光照强度甜点区

tests/
├── gk_stub.py                  离线 bpy 打桩(让 codegen 脱离 Blender 可测)
├── test_codegen.py             89 个回归测试,秒级跑完,不需要 Blender
├── blender_smoke.py            35 项冒烟测试,在真实 Blender 里跑
├── blender_asset_export.py     20 项资产导出测试(真的导出 .glb 并检查)
├── blender_npr_preview.py      23 项 NPR 预览测试(含 A/B 渲染对比)
├── blender_face_preview.py     面部 SDF 预览测试(五个角度对比左右脸亮度)
├── blender_project.py          ★ 项目绑定测试(51 项,含导出落点与启动参数自检)
├── blender_scene_export.py     ★ 整场景导出测试(验证层级与 Z-up→Y-up 变换)
├── blender_starter_graph.py    ★ 示例节点图测试(验证生成的 GDScript 可用)
├── blender_persistence.py      ★ 持久化测试(关掉再打开状态还在吗)
├── blender_menu.py             Shift+A / 右键菜单挂载测试
├── check_icons.py              校验节点图标名是否合法
└── bad_shader_fixture.gdshader 故意写错的着色器,用来验证校验台真的能抓错

scripts/
├── install.ps1                 构建 + 安装到 Blender
├── build_and_test.ps1          构建 + 重装 + 冒烟测试
├── godot_verify.ps1            用 Godot 真实编译并运行 2D 示例
├── verify_shaders.py           ★ 在真实 Godot 里编译着色器,把错误带出来
├── render_shots.ps1            扫光出图(验证面部阴影 SDF 必需)
├── verify_jitter.ps1           ★ 手绘描边动画 A/B 验证(抓帧做像素差分)
├── make_face_preview_shots.py  出面部 SDF 预览的对比图(左/中/右三个角度)
├── make_project_demo.py        ★ 演示:一个 .blend 如何产出完整的 Godot 项目
├── analyze_face_sweep.py       扫光出图的量化粗筛(判据不可靠,只做参考)
├── make_face_sdf.py            生成面部阴影贴图模板(含一张示例脸)
├── make_demo.py                生成 2D 游戏示例工程
└── make_render_demo.py         生成 NPR 渲染示例工程(并可出图)

examples/
├── demo_2d/                    可直接用 Godot 打开的 2D 横版示例
├── demo_render/                NPR 渲染示例(含渲染结果 preview.png)
└── gdextension_cpp/            C++ GDExtension 骨架(GKCharacter)
```

---

## 安装

### 方式一:一键脚本(推荐)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

如果 Blender 不在默认路径，指定一下：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Blender "C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"
```

### 方式二:在 Blender 界面里装

1. `编辑 > 偏好设置 > 获取扩展 > 右上角下拉 > 从磁盘安装`
2. 选择 `_dist\blender_game_kit-0.1.0.zip`
3. 启用它

> 需要 Blender **4.2 或更高**。开发和验证都在 **Blender 4.5.5 LTS** 上完成。
> 纯 Python 插件，不需要编译，不需要额外依赖。

---

## 五分钟上手

1. 打开 Blender，把某个区域的编辑器切成 **节点编辑器**
2. 编辑器头部把类型切成 **游戏逻辑**(如果没有就点"新建")
3. 按 **N** 打开侧栏 → 看到 **游戏逻辑(BlenderGameKit)** 面板
4. 点 **添加节点** → 从 **事件** 分类里加一个 **每当_物理帧**
5. 再加一个 **移动_按方向**(分类 **移动**)，把前者的 `执行` 连到后者的 `执行`
6. 加一个 **输入_方向向量**(分类 **输入**)，把它输出连到 `移动_按方向` 的 `方向`
7. 侧栏设置里选 **2D**，基类选 **CharacterBody2D**，填好导出目录
8. 点 **导出为 Godot 脚本**

生成的脚本长这样(真实输出，未删改)：

```gdscript
class_name PlayerController
extends CharacterBody2D

# ---- 输入动作名(Godot 输入映射里的动作)----
const ACTION_MOVE_LEFT := "move_left"
const ACTION_MOVE_RIGHT := "move_right"
const ACTION_JUMP := "jump"

# ---- 场景节点引用 ----
@onready var anim_player: AnimationPlayer = $AnimationPlayer

# ---- 可在检查器里调的参数 ----
@export var move_speed: float = 240.0  # 移动_按方向 · 速度
@export var jump_force: float = 520.0  # 移动_跳跃 · 强度

# ---- 成员变量 ----
var health: float

func _ready() -> void:
    health = 3.0

func _physics_process(delta: float) -> void:
    var _dir1 := (Input.get_vector(ACTION_MOVE_LEFT, ACTION_MOVE_RIGHT, ACTION_MOVE_UP, ACTION_MOVE_DOWN, 0.2)).normalized()
    self.velocity = _dir1 * move_speed
    self.move_and_slide()
    if self.velocity.x < 0.0:
        self.scale.x = -abs(self.scale.x)
    else:
        self.scale.x = abs(self.scale.x)
    if (self.is_on_floor() and Input.is_action_just_pressed(ACTION_JUMP)):
        self.velocity.y = -jump_force
```

几个设计细节：

- **手感参数自动变成 `@export`** —— 速度、跳跃力度这类"没连线的数值"会生成成检查器参数，可以在 Godot 里直接拖着调，不用改代码。连线了的值不会被暴露(图算出来的值不该被外部改)。
- **输入动作抽成常量** —— 所有按键名集中在脚本开头，改键一眼可见，也不会和 Godot 内置的 `ui_*` 动作混淆。
- **`@onready` 自动推断类型** —— `$AnimationPlayer` 会声明成 `AnimationPlayer` 而不是 `Node`，否则调用 `has_animation()` 会被 GDScript 静态类型拒绝。
- **`_unused_delta`** —— 没用到的 `delta` 会自动加一行占位，避免 Godot 警告刷屏。

---

## 节点一览(79 个)

| 分类 | 节点 |
|---|---|
| **事件** | 每当_准备、每当_每帧、每当_物理帧、每当_输入、自定义函数、调用_函数 |
| **流程** | 如果_否则、只执行一次、序列、循环_次数、等待_秒、打印_输出 |
| **移动** | 移动_设置速度、移动_滑动、移动_按方向、移动_跳跃、移动_施加冲量、移动_设置朝向、移动_水平翻转 |
| **动画** | 动画_播放、动画_切换状态 |
| **实例** | 实例_生成、实例_销毁、取值_获取节点、取值_是否在地面 |
| **变量** | 变量_设置、变量_读取 |
| **信号** | 信号_发出、信号_连接 |
| **输入** | 输入_按键按住、输入_按键按下、输入_轴、输入_方向向量 |
| **运算** | 常量_数值、常量_文本、常量_布尔、常量_向量、数学_运算、逻辑_比较、逻辑_布尔运算、数学_随机整数、向量_合并、向量_拆分、向量_长度、向量_归一化、转换_类型 |
| **显示** | 显示_设置可见、显示_暂停节点 |
| **相机** | 相机_切换、相机_缩放、相机_震动 |
| **音效** | 音效_播放、音效_音乐、音效_停止音乐 |
| **存档** | 存档_写入、存档_读取到变量、存档_保存、存档_载入、存档_是否存在、存档_删除 |
| **界面** | 界面_文本、界面_进度条、界面_按钮、界面_隐藏控件 |
| **屏幕效果** | 屏幕_闪白、屏幕_渐变遮罩 |
| **射线** | 射线_检测、射线_有东西吗 |
| **定时** | 定时_每隔几秒、定时_缓动数值 |
| **列表** | 列表_新建、列表_添加、列表_移除、列表_取第几个到变量 |
| **字典** | 字典_写入、字典_读取到变量、字典_有键吗 |
| **时间** | 时间_设置倍速 |
| **实用** | 注释框 |

### 射线 / 定时 / 列表 / 字典(79 节点版本新增)

| 节点 | 做什么 | 设计要点 |
|---|---|---|
| **射线_检测** | 打一条射线看打到什么 | **按项目维度生成两套代码**:2D 用 `get_world_2d()`、3D 用 `get_world_3d()`。写错在**编译期**就被 Godot 拒绝,所以不能用运行时判断。出口是**命中 / 没打中**两个 |
| **射线_有东西吗** | 视线判定 / 撞墙检测 | **纯表达式**,直接接到『如果_否则』的条件上。射线是"就地"射出去的(见下) |
| **定时_每隔几秒** | 刷怪 / 回血 / 自动存档 | `one_shot = false` 才是"每隔 N 秒";触发逻辑放在 `timeout` 回调里 |
| **定时_缓动数值** | 血条缓动 / UI 滑入 | 用 Godot 的 `Tween`,6 种速度曲线 × 3 种缓动方式 |
| **列表_新建/添加/移除/取** | 背包、刷怪表 | 取值**自带越界兜底**:`items[99]` 在 Godot 里会报错并中断当前帧 |
| **字典_写入/读取/有键吗** | 按名字存数据 | 读取时键不存在用『默认值』 |

两个值得记下的实现细节:

- **『射线_有东西吗』是纯表达式,但会"就地"生成好几行代码**。
  GDScript 的表达式在**被使用的作用域**里求值,所以这几行正好落在
  `if <条件>:` 之前,变量一定在使用前就绪。
  (踩过的坑:一开始把缩进写死成 0,代码被写进类体,
   Godot 报 `Unexpected identifier "_query13" in class body`。)
- **射线节点标记了 `gk_no_cache`**。生成器默认会缓存表达式文本;
  射线要是被缓存,第二次求值只会复用第一次的结果 —— 射线就永远停在原地了。


### 界面(68 节点版本新增)

做完这四个节点,**主菜单和暂停菜单就能做了** —— 这是之前最大的一块空白。

| 节点 | 做什么 | 设计要点 |
|---|---|---|
| **界面_文本** | 屏幕上一行字(分数 / 提示 / 对话) | UI 层**自动建**,不需要你在场景里摆控件;重复调用是改文字,不会叠出新的一行 |
| **界面_进度条** | 血条 / 蓝条 / 蓄力条 | 数值自动 `clampf` 钳在 0~最大值 —— 血量变成 -1 时进度条会画出诡异的样子 |
| **界面_按钮** | 点一下才往下走 | 出口叫**『点击』而不是『完成』**:叫『完成』的话 `emit_statement` 会自动续接,按钮刚建出来后面的逻辑就直接跑了(比如游戏立刻开始)。实际的续接逻辑被生成到一个独立回调函数里 |
| **界面_隐藏控件** | 隐藏某个控件 | 按**名字**查找,找不到会告警而不是静默什么都不做 |

三个设计细节:

- **控件变量名按节点名走**,所以中文节点名会生成 `_gk_ui_分数` 这样的变量。
  一开始担心 Godot 不接受,专门写了个探针脚本实测:**Godot 4.7 的标识符允许 Unicode**,中文名反而更好对号。
- **两个同名控件不会互相覆盖**。复制节点后忘了改名很常见,这时生成器会给第二个
  自动加序号(`_gk_ui_分数_2`),否则会出现
  `var _gk_ui_分数: Label` 却 `_gk_ui_分数 = ProgressBar.new()` 这种自相矛盾的脚本。
- **UI 层用 `CanvasLayer(layer=10)`**,盖在游戏画面上、但低于屏幕闪白(layer 100)。


### 音效 / 存档 / 屏幕效果(64 节点版本新增)

| 节点 | 做什么 | 设计要点 |
|---|---|---|
| **音效_播放** | 播一次音效(跳跃 / 受击 / 拾取) | 播放器**运行时自动建、播完自动销毁**。不需要你先在场景里为每个音效摆一个节点；文件缺失时给可读告警而不是静默失败 |
| **音效_音乐** | 播背景音乐 | 重复调用**不会叠着播**；同一首正在播时不打断(主菜单返回游戏很常见)；支持淡入 |
| **音效_停止音乐** | 停 BGM(可淡出) | 音效是一次性的，播完自己就没了，不用手动停 |
| **存档_写入** | 往存档写一个值 | 只改内存，**不落盘** |
| **存档_读取到变量** | 把存档值读进脚本变量 | 键不存在时用「默认值」，不会崩 |
| **存档_保存** | 把内存数据写进磁盘 | 这才是真正写文件的那一次 |
| **存档_载入** | 从磁盘读进内存 | 文件不存在也不会出错，只是读到空存档 |
| **存档_是否存在** | 判断槽位有没有档 | 做「继续游戏」按钮的可用状态。两个出口:**有档 / 没档** |
| **存档_删除** | 删档 | 同时清掉内存里那份，否则「删了档还能读到旧数据」 |
| **屏幕_闪白** | 整屏闪一下 | 用 `ColorRect` + `Tween`，**不占渲染管线、不需要着色器**；只淡 alpha，不改 RGB |
| **屏幕_渐变遮罩** | 黑场转场 / 淡入开场 | 用持久遮罩层，才能做「淡出 → 换场景 → 淡入」；会 `await` 渐变结束再走出口，保证顺序 |

**为什么存档要分成「写入」和「保存」两步**：落盘是磁盘 IO，在 `_process` 里每帧调用会卡帧。
分开之后「存档_写入」是廉价的，「存档_保存」才是真正写文件的那一次。

**存档格式**：`user://gk_<槽位>.json`，纯 JSON，可以直接用文本编辑器打开看。
多个槽位互不影响。JSON 只能存数字/文本/布尔/数组/字典，向量请用「向量_拆分」拆成三个数再存。

**添加节点的正确姿势**：先选中一个节点，再点"添加节点"。新节点会**自动连到选中节点的完成出口**，省掉一次拖线。选中两个节点后点菜单里的"自动连线"也能按类型自动接。

---

## 运行示例工程

```powershell
# 2D 横版示例(方向键移动、空格跳跃、J 攻击)
python scripts\make_demo.py
# 用 Godot 打开 examples\demo_2d，按 F5

# NPR 渲染示例(4 个球对比 + 带描边的角色)
python scripts\make_render_demo.py
# 用 Godot 打开 examples\demo_render 按 F5
# 或者直接出图，不需要打开编辑器:
godot --path examples\demo_render --resolution 640x640
```

NPR demo 的渲染结果：

![NPR demo](examples/demo_render/preview.png)

---

## 开发与验证

五层验证，从快到慢。**改任何东西之前先想清楚该跑哪一层**：

```powershell
# 1. 离线回归测试(秒级,不需要 Blender)—— 改 codegen 后必跑
python tests\test_codegen.py

# 2. 着色器编译校验 —— 改 .gdshader 后必跑
python scripts\verify_shaders.py

# 3. 构建 + 装进 Blender + 35 项冒烟测试 —— 改节点/UI 后必跑
powershell -File scripts\build_and_test.ps1

# 4. 资产导出测试(真的导出 .glb 并检查装配结果)
blender -b --python tests\blender_asset_export.py

# 5. NPR 预览测试(含 A/B 渲染对比,证明色阶真的生效)
blender -b --python tests\blender_npr_preview.py

# 5b. 面部 SDF 预览测试(渲染五个角度,看阴影线是否左右滑动)
blender -b --python tests\blender_face_preview.py

# 5c. 项目绑定测试(路径解析 / 建骨架 / 导出落点都在项目内)
blender -b --python tests\blender_project.py

# 5d. Shift+A 菜单挂载测试
blender -b --python tests\blender_menu.py

# 5e. 整场景导出测试(层级 + Z-up→Y-up 变换)
blender -b --python tests\blender_scene_export.py

# 5f. 示例节点图测试(生成的 GDScript 能不能用)
blender -b --python tests\blender_starter_graph.py

# 5g. 持久化测试(导出 -> 保存 -> 重开,状态还在吗)
blender -b --python tests\blender_persistence.py

# 6. 用真实 Godot 编译并运行 —— 改生成代码后必跑
powershell -File scripts\godot_verify.ps1

# 7. 扫光出图 —— 改面部 SDF 后必跑(单张静态图看不出效果)
powershell -File scripts\render_shots.ps1 -Angles "60,75,90,105,120"

# 8. 手绘描边动画 A/B 验证 —— 改描边抖动后必跑
powershell -File scripts\verify_jitter.ps1
```

`tests/gk_stub.py` 打了一个假的 `bpy` 模块，所以**节点定义和代码生成器可以完全脱离 Blender 测试**。
`tests/bad_shader_fixture.gdshader` 是故意写错的着色器，用来证明第 2 层真的能抓到错误 ——
**一个抓不到已知错误的校验器比没有校验器更危险**。

同理，第 5 层不看"节点建出来了没"，而是渲染两张图比像素统计：

| | 球心离散颜色 | 主色占比 |
|---|---|---|
| NPR 卡通材质 | **3 种** | **161 / 225 像素** |
| 对照(原始光照) | 30 种 | 23 / 225 像素 |

第 8 层同理 —— 动画不能靠静态图验证，所以抓帧做像素差分：

| 指标 | 开抖动 | 关抖动 |
|---|---|---|
| 最大帧间差 | **0.0044** | **0** |
| 线宽变化量 | **17 像素** | **0** |

注意判据用的是**最大**帧间差而不是平均值：手绘抖动是阶跃式的，平均差只有
0.0005，只看平均值会得出"几乎没动"的错误结论。

---

## 踩过的坑(改代码前请先读)

这些都是实际撞出来的，写在这里省你几个小时：

1. **`--factory-startup` 会让 Blender 完全看不到用户扩展仓库。**
   表现是 `addon_utils.modules()` 里没有 `bl_ext.<repo>.<pkg>`。
   所以 `blender_smoke.py` 不能用工厂启动。

2. **`bpy.types.X` 取不到动态注册的 Python 类。**
   `bpy.types.GKGameLogicTree` 会抛 AttributeError，即使插件已经注册成功。
   判断注册结果要用"能不能真的建出对象"，或 `hasattr(cls, "bl_rna")`。

3. **`bl_icon` 只接受内置白名单，写错会让整类注册静默失败。**
   `ARROW_BRANCH_UP_RIGHT` / `IMAGE_ANIM` / `LOGIC` 都不存在。
   改完图标请跑 `blender -b --python tests/check_icons.py`。

4. **自定义 `NodeSocket` 子类必须显式实现 `draw()` 和 `draw_color()`**，
   否则注册直接抛 `AttributeError`。
   另外继承内置 socket 类型(如 `NodeSocketFloat`)时 `draw` **不会**被继承，也要自己写。
   本项目因此只自定义了"执行流"和"任意值"两种引脚，其余复用内置类型。

5. **`bpy.props` 只认类注解。** 所以节点属性写成声明式元组
   `gk_prop_defs = (("gk_speed", "float", 300.0, "速度", "说明"),)`,
   由 `node_base.finalize()` 在类创建后物化成 `__annotations__`。

6. **`PointerProperty` 引用的 `PropertyGroup` 必须先注册**，
   否则报 `missing bl_rna attribute from '_RNAMetaPropGroup'`。

7. **抽象基类不能交给 `register_class`。**
   `_GKNodeBase` 这类没有 `bl_label` 的基类注册会报
   `expected Node, ... to have an "bl_label" attribute`。

8. **`install-file` 不会覆盖同版本号的扩展。**
   重新构建后要先删掉仓库目录再装，否则你测的一直是旧代码。
   `build_and_test.ps1` 里已经处理了。

9. **Blender 的输入引脚只允许一条连线。**
   第二条 `links.new()` 会被**静默拒绝**。所以"两个事件都连到同一个动作节点"
   实际上做不到，生成器会检测并明确告警(见 `test_shared_node_between_events_warns`)。

10. **GDScript 的 `class_name` 只接受 ASCII。**
    中文类名会被忽略并在告警里说明，而不是生成 `Gknodes` 这种垃圾。

11. **`.res` 是二进制资源格式，文本资源必须用 `.tres`。**
    Godot 会报 `Unrecognized binary resource file`。

12. **Windows PowerShell 5.1 按 ANSI 读无 BOM 的 UTF-8 脚本**，
    含中文的 `.ps1` 会解析失败。本项目的脚本都带 BOM，编辑后请确认 BOM 还在。

13. **Godot 着色器编译器不接受注释里的非 ASCII 字符。**
    写 `// 基础颜色` 直接报 `Tokenizer: Unknown character #22522`。
    所以所有 `.gdshader` 只有英文注释，中文说明在 docs/ 里。

14. **Godot 4.7 的 `light()` 里没有 `LIGHT_ENERGY`,也没有 `SPECULAR_LIGHT`。**
    实测清单见 [渲染文档](docs/渲染-NPR卡通着色.md)。
    卡通高光必须在 `fragment()` 里算再走 `EMISSION`。

15. **`.tscn` 里的 `sub_resource` 必须先定义后引用**，
    否则报 `_parse_sub_resource ... ERR_INVALID_PARAMETER`，
    而且错误信息看起来完全不像"顺序问题"。

16. **`sub_resource` 引用的 `ext_resource` 必须在同一个文件里声明**，
    只在 `.tres` 里声明是不够的。

17. **`bpy.ops.wm.read_factory_settings()` 会把已启用的插件一起重置**，
    连 `bl_ext` 扩展命名空间都会失效。测试里要清场景就手动删数据。

18. **卡通着色下光照能量要压得很低**(主光 0.4~0.6)。
    超过 0.9 会让 `albedo × LIGHT_COLOR` 突破 1.0 被裁掉，
    结果整个亮部变成一片纯白、明暗分界消失。

19. **校验器必须先用"已知是错误的输入"验证一遍。**
    我写着色器校验台时它一开始报"全部通过"，
    拿一个故意写错的 shader 一试才发现场景文件本身坏了、
    根本没编译到着色器。

---

## 已知限制

- 只支持导出 **GDScript**，不生成 C# 或 GDExtension(C++ 骨架见
  [examples/gdextension_cpp](examples/gdextension_cpp/)，但没有做节点图 → C++ 的生成)。
- 节点图是**单向执行流**，没有做"数据类型推导"的完整类型系统；
  把标量接到向量引脚时会自动包装并告警，而不是报错。
- 一个输入引脚只能接一条线(Blender 限制)，想多处触发请复制节点。
- 卡通着色的**面部阴影还没有做 SDF**，目前靠 `light_map` 手动控制。
- 资产导出只导网格/骨骼/动画 + 主色贴图，不支持 Blender 复杂的程序化材质节点
  (那种材质本来也无法映射到 Godot)。

## 下一步可以做什么

对照 UPBGE 0.50 的 349 个逻辑节点做过缺口盘点。**已补齐**(79 节点版本):

- [x] **存档 / 读档** —— 6 个节点，JSON 落到 `user://`，多槽位。已实测跨进程读写通过
- [x] **音效** —— 播放 / 音乐 / 停止，播放器运行时自动建、播完自动销毁
- [x] **屏幕效果(部分)** —— 闪白、渐变遮罩(黑场转场)
- [x] **界面控件** —— 文本 / 进度条 / 按钮 / 隐藏控件。主菜单与暂停菜单可以做了
- [x] **射线检测** —— 命中判定 / 视线 / 撞墙，2D 与 3D 各一套
- [x] **定时器 / 缓动** —— 每隔 N 秒触发、Tween 缓动数值
- [x] **列表 / 字典** —— 7 个节点，背包与任务列表可以做了

**还没做的**:

- [ ] **屏幕滤镜** —— 泛光 / 像素化 / 屏幕暗角 / 受击变红。
      (闪白和渐变已经做了，但还没接入 `.gdshader` 参数控制)
- [ ] **触屏 / 手柄** —— 移动端要虚拟摇杆，手柄要摇杆轴
- [ ] **网络联机** —— UPBGE 有内置 LAN 节点，我们完全没有
- [ ] **节点群组** —— 节点图一大就没法整理。
      (UPBGE 自己也还没做，见它的 `bge_netlogic/todo.txt`)

渲染与管线方向:

- [ ] **面部阴影 SDF** —— 二次元角色"脸不脏"的核心技术，比 light_map 更进一步
- [ ] Blender 端 NPR 材质预览节点组(不用切引擎就能调参)
- [ ] 节点图 → C++ 生成(把热点逻辑直接生成 GDExtension 类)
- [ ] 从 Blender 场景自动生成完整 Godot 场景树(不只一个角色)
- [ ] 高光形状贴图、卡通描边动画(顶点抖动模拟手绘)
- [ ] 反向能力:解析已有 `.gd` 生成节点图

## 许可

GPL-3.0-or-later(与 Blender 插件生态一致)。
