# camclip_accept —— 相机穿模真机验收(第 126 / 128 轮)

改过 `BlenderGameKit/codegen/camera.py`(弹簧臂)之后,用这套脚本在**真 Godot** 上
复验"相机不再穿模、而且正常画面没变"。

```bat
:: 全跑(无头 + 窗口化,约 10 分钟)
python scripts\camclip_accept\accept.py

:: 只无头(快)
python scripts\camclip_accept\accept.py --skip-windowed

:: 只跑一关 / 指定 Godot
python scripts\camclip_accept\accept.py --levels 走廊 --godot "D:\godot\...console.exe"

:: 只挑某个场景(第 128 轮那个"站在方块顶上跳")
python scripts\camclip_accept\setup.py
::   然后在 _dist\camclip_accept\projA\camclip_args.txt 里写:
::   only=box
::   box=结局箱甲
::   ceil=结局天花板
```

## 验收线

1. **进近平面的帧数 = 0** —— 全程"相机到玩家模型表面的最近距离"必须
   `>= Camera3D.near`(示例是 0.1)。比 near 更近的面片会被裁掉,再叠上背面剔除
   就是"看到身体内部"。(第 126 轮:贴墙跳时修复前 0.0539~0.0865 米 / 3~24 帧)
2. **正常画面不变** —— 修复前孪生体 vs 修复后,**相机位置的 XZ 分量逐帧相同**,
   贴墙静止的"离模型距离 / 离墙余量"、走路稳态臂长都对得上。
   这条比"看着对了"强:它证明改动**只碰了该碰的那一维**。
3. **相机不许跑到天花板上面**(第 128 轮补的)——
   `frames_*_box.txt` 里 `camy` 必须始终小于天花板底面。
   ⚠ 只验第 1 条会漏掉这一类:用户"站在第三关正方体上跳"时,相机离玩家模型
   最近 1.43 米(远大于 near,第 1 条全绿),**但相机整个跑到屋子外面去了** ——
   屏幕上只剩天花板顶面,玩家不见了。
4. **相机不许出现在实心体内部**(第 129 轮补的,看 `c1in` / `c3in`)——
   尤其第一人称:它的"眼睛"在 `玩家位置 + 1.55`,而胶囊只到 `+0.9`,
   **竖直方向完全裸奔**。用 `fp=1` 跑,`c1in` 必须全程是 `-`。

## 三个场景怎么跑

| 场景 | 怎么跑 | 看什么 |
|---|---|---|
| 第 126 轮 贴墙跳 | `python scripts\camclip_accept\accept.py --skip-windowed` | 进近平面帧数 = 0;贴墙静止 XZ 逐字节不变 |
| 第 128 轮 方块顶跳 | `python scripts\camclip_accept\accept.py --box 结局箱甲 --ceil 结局天花板` | **相机在天花板上面的帧数 = 0** |
| 第 129 轮 第一人称 | 在 `_dist\camclip_accept\projA\camclip_args.txt` 里写 `only=fp` / `box=结局箱甲` / `ceil=结局天花板` / **`fp=1`**,再跑无头 | **相机在实心体里的帧数 = 0**、比 `near` 更近的帧数 = 0 |
| 手动尺寸轴序 | `python scripts\camclip_accept\check_manual_shape.py` | 手动尺寸原样写进 `BoxShape3D.size` |
| 射线 vs 触发区 | 点开 `probe_ray/` 那个小工程跑一次 | `Area3D` 不参与默认射线 |

### 第一人称那几列怎么看(`fp=1` 才输出)

| 列 | 含义 |
|---|---|
| `c1geo` | 第一人称相机 → 最近几何表面(6 个轴向射线取最小);**在里面时是 -1** |
| `c1in` | 第一人称相机**在哪个实心体里面**(2 厘米小球形状查询);`-` = 不在任何里面 |
| `pgeo` / `pin` | 玩家胶囊中心同样两项 —— 用来证明"**身体在外面、眼睛在里面**" |
| `c1cap` | 相机高出胶囊顶多少。**正数 = 眼睛在碰撞体外面**(第 129 轮实测:恒 +0.65) |
| `c3geo` / `c3in` / `c3cap` | 第三人称相机同样三项(同一帧的对照组) |

⚠ **为什么"在不在里面"必须用小球查询**:`intersect_ray` 看不见"起点所在的那块实心体"
(第 126 轮独立量过:从实心盒子里往外打 → MISS)。相机已经伸进天花板里时,
射线**全都 MISS**,读数看上去"哪儿都不近" —— 最坏的情况被读成最好。
**射线答"在外面时有多近",小球答"在不在里面",两个一起看。**

## 文件

| 文件 | 干什么 |
|---|---|
| `setup.py` | 从**最新示例产物**拷出 `projA/B/C`(胶囊 / 手动盒子 / 盒子+触发区)和"修复前孪生体",建到 `_dist\camclip_accept\` |
| `camclip_probe.gd` | 挂 autoload 的探针:逐帧记相机 / 枢轴 / 模型表面距离 / 射线撞到谁;**发真按键**(`Input.parse_input_event`)跳/切视角 |
| `accept.py` | 跑 + 出验收表 + 起跳最高点"模型开/关"像素差;`--skip-windowed` 只跑无头 |
| `check_manual_shape.py` | 离线问导出器:手动尺寸写进 `BoxShape3D.size` 是**原样**还是换过轴 |
| `probe_ray/` | 独立最小工程:射线 vs `Area3D` 触发区、不排除自己时命中自身的位置、起点在实心体内会怎样 |

## 规矩

- 探针工程只建在 `_dist\camclip_accept\` 下。**不要用 `_dist\story_probe`**
  —— 那是 `scripts\check_story_demo.py` 的共享工作目录,两个进程抢同一个目录
  会让对方报一串**假红**(踩过)。
- 绝不写 `examples/`(脚本只**拷**示例产物)。
- **逐帧表必须自带"跑完了没有"的标记**(见上面第 1 条)。

## 真机量到的结论(改相机之前先看这三条)

- **触发区对相机射线毫无影响**:`PhysicsRayQueryParameters3D.create()` 的
  `collide_with_areas=false`(实测);把 1.2×1.8×1.2 的 `Area3D` 真挂到玩家身上,
  逐帧表与不挂**逐字节相同**。
- **`_gk_exclude` 一旦失效后果很重**:不排除自己时,从枢轴打出去的射线在
  **0.11 米**处命中玩家自己的胶囊 → 被最短臂长夹住 → **每帧都贴脸**。
- **`intersect_ray` 看不见"自己所在的那块实心体"**(起点在盒子里 -> MISS),
  这正是第 128 轮那个 bug 的入口:玩家跳到头顶顶住天花板时,枢轴(玩家原点 +1.4)
  已经穿到天花板**上面**,射线什么都找不到,杆子恢复到全长,相机跟着跑到屋外。

## 踩过的坑(写探针的人都会踩,别重复)

1. **逐帧表只在"跑完"时才写** —— 探针是在 `_run()` 末尾才落盘 `camclip_frames.txt`。
   万一这一跑中途挂了(脚本报错、被 `--quit-after` 掐掉、协程卡住),目录里留着的
   就是**上一跑的真数据**,看起来完全正常 —— 复制回来一分析,会得出
   **"改动没生效"的错误结论**(第 128 轮因此白查了半小时)。
   规矩:**任何逐帧表都必须自带"这一跑跑完了没有"的标记**,复制前先确认
   `CAMCLIP DONE` / `ROWS=n`;建副本时**先删旧表**(`setup.py` 已经这么做了,
   并且会打印 `completed=1 rows=N`)。
2. **`shoot=1` + 无头 = 永久挂死** —— 无头模式没有渲染线程,
   `RenderingServer.frame_post_draw` **永远不会发**,在那里 `await` 会把整个探针
   协程挂住。现象很迷惑:游戏照跑、`HEART` 照打、`phase` 卡死不动、表一个字不写、
   进程要等 `--quit-after` 到点才退。探针里已经加了硬防护
   (`DisplayServer.get_name() == "headless"` 直接跳过拍照),参数文件也别留着
   `shoot=1` 去跑无头。
3. **中文节点名走文件、不走命令行** —— `-- --box=结局箱甲` 在 Windows 上转一手
   名字就变了(节点找不到 → 探针打印 `found=false` 然后 SKIP)。用
   `res://camclip_args.txt`(UTF-8,`key=value` 一行一个)。
4. **GDScript 不是 Python**:相邻字符串字面量**不会**自动拼接(`"a" "b"` 是语法错);
   `+` 和 `%` 的优先级和 Python 一样(`%` 更高),`"a" + "b" % x` 只格式化后半截;
   三元表达式配 `:=` 有时推不出类型(会整份 autoload 加载失败 —— 而探针是唯一
   会 `quit()` 的人,于是 Godot 挂后台不退)。

## 三个场景怎么跑

| 场景 | 命令 | 看什么 |
|---|---|---|
| 第 126 轮 贴墙跳 | `python scripts\camclip_accept\accept.py --skip-windowed` | 进近平面帧数 = 0;贴墙静止 XZ 逐字节不变 |
| 第 128 轮 方块顶跳 | `python scripts\camclip_accept\accept.py --box 结局箱甲 --ceil 结局天花板` | **相机在天花板上面的帧数 = 0** |
| 手动尺寸轴序 | `python scripts\camclip_accept\check_manual_shape.py` | 手动尺寸原样写进 `BoxShape3D.size` |
| 射线 vs 触发区 | `点开 probe_ray/` 那个小工程跑一次 | `Area3D` 不参与默认射线 |
