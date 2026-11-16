# Minecraft demo 代码审查 · 第 1 轮

- 审查对象：`scripts/make_minecraft_demo.py` 及其产物 `examples/demo_minecraft/我的世界_godot`
- 审查范围：基础世界设置（名称 / 种子 / 地形 / 大小 / 植被 / 昼夜 / 渲染距离）接入新建世界与存档、开始菜单入口收敛、渲染视距与地形加载、开始界面可交互化
- 审查方式：**只读**。未修改、未提交、未运行任何会写仓库的命令（没有 git 环境，未做 diff 基线比对）
- 快照（行号以此为准；另一 session 若继续改脚本，行号可能漂移）：

| 文件 | SHA256（前 16 位） | 行数 | 最后修改 |
|---|---|---|---|
| `scripts/make_minecraft_demo.py` | `B162D13184402F26` | 4034 | 2026-11-10 20:31:39 |
| `.../scripts/game_logic_World_Player.gd` | `4B02CDBF2538D8D8` | - | 随导出生成 |

结论概览：**功能错误 6 项 / 回归风险 2 项 / 性能与可维护性 11 项**。其中 F1、F2 建议优先修（直接影响"建世界 / 读档 / 存档"主链路的可玩性）。

---

## 一、会导致功能错误

### F1（高）进世界后鼠标没有切回捕获态，与暂停菜单『继续游戏』行为不一致

**位置**
- `scripts/make_minecraft_demo.py:1510-1524`（`_enter_world`，注释写着"锁鼠标"）
- 产物 `.../scripts/game_logic_World_Player.gd:1639-1652`（`_gk_apply_pause`）、`1606-1619`（`_gk_pause_menu_show` / `_gk_mouse_show` / `_gk_mouse_restore`）、`1629-1636`（`_gk_mouse_sync`）
- 对照：节点图里的两条恢复路径是 `.../game_logic_World_Player.gd:1435-1437`（继续游戏）、`1448-1451`（回到出生点），它们都在 `_gk_apply_pause(false, ...)` **之后**显式补了 `Input.mouse_mode = Input.MOUSE_MODE_CAPTURED`

**现象 / 依据（逐步推演）**
1. 启动时节点图先把鼠标设成 `VISIBLE`，再 `_gk_apply_pause(true, "世界菜单", true)`；`_gk_pause_menu_show(true, ...)` 里 `_gk_pause_menu_open = true` 后调 `_gk_mouse_sync`，于是 `_gk_mouse_show()` 把 `_gk_pause_mouse_was` 记成 `VISIBLE`。
2. 玩家点『开始游戏 / 继续游戏 / 读取存档』或保存：`_enter_world()` → `_gk_apply_pause(false, menu, true)`。
3. 该函数里 `_gk_mouse_sync()` 先执行（此时菜单还开着 → `_gk_mouse_show()`，仍是 `VISIBLE`），随后 `_gk_pause_menu_show(false, ...)` 把 `_gk_pause_menu_open=false` 再 `_gk_mouse_sync()` → `_gk_mouse_restore()` → 把模式**还原成 `VISIBLE`**。
4. `_enter_world` 没有任何后续捕获动作；而 `_gk_mouse_show / restore` 是框架统一入口，不会自己"记住进游戏该捕获"。

⇒ 新建世界、读档、保存并回到游戏这三种路径，**鼠标都停在可见状态**；只有走暂停菜单的『继续游戏』或『回到出生点』才会被捕获。表现为：光标留在画面里、能移出窗口点桌面，跟紧邻的按钮行为不一致。

**建议改法**
- 在 `_enter_world` 走框架分支后补一行 `Input.mouse_mode = Input.MOUSE_MODE_CAPTURED`（与 `gk_on_继续游戏_001` 完全一致）；这是最小改动、不碰框架。
- 若希望根治，把"进游戏态"定义成框架的一等状态（例如 `_gk_apply_pause(false, "", true)` 时恢复"玩家默认鼠标模式"，而不是"暂停前的模式"），但这会牵动框架，不建议本轮做。

---

### F2（高）同类型方块放到第二个之后，存档 / 视距裁剪会漏掉它

**位置**
- 产物放置处：`.../scripts/game_logic_World_Player.gd:943-961`（`_gk_spawn_asset(..., "草方块", ...)` 然后 `get_tree().current_scene.add_child(_asset64)`），名字在 `1327`（`body.name = what`）
- 消费处：`scripts/make_minecraft_demo.py:419-430`（`save_state` 按节点名认放置方块）、`:519-531`（`_cull_placed` 同样按名字认）

**现象 / 依据**
- 放置的方块统一叫 `"草方块" / "石头方块" / …`，全部挂在 **同一个父节点**（`current_scene`）下。
- Godot 的兄弟节点必须唯一，`add_child` 遇到重名会自动改名（本项目导出器自己就为这件事专门做了去重：`BlenderGameKit/asset_export.py:9460-9465` 注释"Godot 的兄弟节点不许重名,用序号"）。也就是说第二个草方块不再叫 `草方块`（会变成 `草方块2` / `@草方块@…`）。
- `PLACE_KINDS.has(String(node.name))` 因此只命中每类**第一块**：`save_state` 不记录、`_cull_placed` 不隐藏、`load_state` 清场时也不删。
- 叠加后果：存档里"少记"的方块本身没人发现；读档后 `_placed` 里本来有、但场上没被清掉的旧实例又留着 → 同一位置可能出现两份。

**复现**：同一局连着放两块草方块 → 保存 → 退出重进 → 预期两块都在、且超距时两块都隐藏。按上述推理只会回来一块。

**建议改法**
- 不要用节点名当身份。spawn 时在实例上打元数据（例如 `body.set_meta("gk_place_kind", "grass")`，或在节点图上给放置实例加 meta），`save_state / _cull_placed / load_state` 一律读 meta。
- 若不想动节点图：`_cull_placed` / `save_state` 改成"名字以某个前缀开头"判断（`label.begins_with("草方块")`），但这是脆弱的兜底，不建议作为最终方案。
- 顺带补一条离线/真机用例：每类放 2 块 → 存 → 读 → 数量一致。

---

### F3（中）创建面板不响应 ESC（读档 / 保存面板响应）

**位置**
- `scripts/make_minecraft_demo.py:1071-1077`（`_unhandled_input` 只处理 `_load_screen` / `_save_screen`）
- `:1378-1393`（`open_create` 只把 `_create_screen.visible = true`）

**现象**：开始菜单（无存档）→『开始游戏』→ 创建面板 → 按 ESC 无任何反应，只能点『取消』。舞台暂停时节点图的 ESC 分支（`game_logic_World_Player.gd` 里的 `ui_cancel` → 暂停）不会执行，所以没有任何兜底。

**建议改法**：`_unhandled_input` 里把 `_create_screen` 一并纳入（优先级：创建 > 保存 > 读取），并复用 `close_panels()`。

---

### F4（中）种子"留空 = 随机"的文案与实际行为不符

**位置**：`scripts/make_minecraft_demo.py:1260`（`placeholder_text = "留空 = 随机"`）对 `:473`（`_hash_string("")` → 0）与 `docs/待办清单.md:328`（"空(= 0)，空种子复现原来的地形"）

**现象**：连续两次"留空"建世界，地形逐格相同（种子恒为 0）。设计文档写的是"复现原来的地形"，UI 却承诺随机 → 玩家会以为周目不同。节点变量的注释（`:3380`"留空 = 默认世界"）也是"默认世界"，只有面板上的 placeholder 写成了"随机"。

**建议改法**：优先改文案（"留空 = 默认世界"）；若确实想要随机，则在 `_on_create` 里生成随机种子并**写回输入框**（否则存档里存的是空串，读档后还能复现，但玩家看不到真实种子）。

---

### F5（中）放下的方块"藏起来"后碰撞仍在 → 隐形墙

**位置**：`scripts/make_minecraft_demo.py:519-531`

**现象**：超距只把 `StaticBody3D.visible = false`，`collision_layer/mask` 不变。玩家走出渲染距离再走回去的路上会撞到看不见的方块；配合"地形列已被卸载"，表现为"空气墙挡住去路"。

**建议改法**：裁剪时同时 `set_deferred("collision_layer", 0)`（恢复时还原），或改用 `process_mode = DISABLED` + 关闭 shape；也可把这些方块换到 `CollisionShape3D.disabled`。

---

### F6（中，已知遗留）有限世界可以走出边界并无限下落

**位置**：`scripts/make_minecraft_demo.py:247-250`（只跳过边界外的列）；`docs/待办清单.md:371` 已登记"走出边界掉下去"的兜底。

**现象**：`世界大小 = 128` 时一直往外走，地形消失、脚下无碰撞，角色持续下坠；掉出后没有重生/回城逻辑，只能退出到桌面。

**建议改法**（本轮可不做，但建议确认排期）：边界外一圈隐形墙；或 `_physics_process` 里检测 `y < -20` 时回到出生点并提示。

---

## 二、会引入回归

### R1（高）植被密度默认值实际是 5%，与"默认 10% / 一棵树都不差"的设计相反

**位置**
- `scripts/make_minecraft_demo.py:3397`（节点变量 `植被密度` 的 `default_value = "2"`，而 `3398` 的注释与 `docs/待办清单.md:331` 都写"默认 10% / 一棵树都不差"）
- `:1606`（旧存档回落 `settings.get("vegetation", 2)`）
- 映射表 `:159`（`TREE_PERCENTS = [0, 5, 10, 25]`）

**影响链**：索引 2 → `TREE_PERCENTS[1]` = **5%**。旧存档（`format` 1，没有 `world_settings`）读回来时植被就从 10% 掉到 5%：
- 树按 `_hash(cell,3) % 100 < 5` 判据重建，大约一半原来有的树**不会刷新**；
- 这些树不在 `_removed_trees` 里，玩家没有砍过它们，但读档后它们消失了 → 看起来像"存档丢东西"。

新建世界不受影响（面板默认 `selected = 2` → 写 `3` → 10%）。

**建议改法**：`3397` 改成 `"3"`，`1606` 的回落值改成 `3`；两处都对齐 `docs/待办清单.md:331`。再补一条离线断言：旧档（无 `world_settings`）读回后 `_tree_percent == 10`。

### R2（中）`set_radius` 注释是"只重铺可见范围"，实际是全量卸载重建

**位置**：`scripts/make_minecraft_demo.py:500-503`（`set_radius` → `_unload_all()`，注释见 `:500`；`docs/待办清单.md:340` 也写"只重铺可见范围"）

**现象**：设置里把 14 调到 18，`_unload_all()` 会把 `_cells` 全部 `queue_free()`、`_queue` 清空、`_center` 重置，再按 1 列/帧重铺。玩家看到的是"整屏地形先消失、再一列列长回来"（14 档约 841 帧 ≈ 14s 才能长满）。14→10 这种"只是缩小"的场景也会整屏重建。

**建议改法**：改成增量——只为 `_wanted \ _cells` 补列、只为 `_cells \ _wanted` 删列，保留 `_center` 与 `_queue` 里仍然有效的项（`_replan` 已经是差集逻辑，直接 `_center = 哨兵` 再 `_replan(当前格)` 即可，不需要先 `_unload_all`）。

---

## 三、性能与可维护性

### P1（中高）渲染距离放大到 22 档后，"1 列/帧"的铺地速度线性放大到 ~34 秒

**位置**：`scripts/make_minecraft_demo.py:148-153`（`BUILD_PER_FRAME := 1` + "一帧 2 列同样会掉帧"的真机结论）、`:155`（档位 22）、`:267-273`

**量化**：半径 22 → 45×45 = 2025 列 → 60fps 下约 **34s** 才能铺满；14 档 841 列 ≈ 14s；原来的 10 档 441 列 ≈ 7.3s。每个非空列还会建 1~3 个 `StaticBody3D` + 树（约 3~10 个节点），22 档满铺约 2~3 千个 body、上万节点。

**建议**：按"列数"或"节点数"给每帧预算，而不是固定 1 列（例如远距离先只建顶面一层的简化列）；或在切到 18/22 档时把初始半径分层铺（先 10 档铺满，再补外圈），避免"进了世界面前是空的"。

### P2（中）`_cull_placed` / `save_state` 每 0.25s / 每次保存全树扫描

**位置**：`:217-223`（0.25s 定时）、`:519-531`、`:419-430`

**成本**：`find_children("*", "StaticBody3D", true, false)` 在 22 档要遍历 ~2~3 千个 body 并逐个 `String(node.name)`；每 0.25s 一次，保存时再来一次。`String()` 转换 + 数组分配在渲染帧里做，正是"移动卡顿"的口感来源。

**建议**：维护 `_placed_nodes: Dictionary`（放置时登记 / `tree_exiting` 注销），裁剪与存档只看这一小撮节点；或按 8×8 区域分桶。

### P3（中）`_add_placed` 每建一列都遍历整个 `_placed`

**位置**：`:297-307`

**成本**：读档后 `_add_placed` 在建列时被调用 → `O(列数 × 放置数)`，每列对每个 key 做一次 `str(key).split(",")` 和两次 `int()`。2025 列 × 200 个放置方块 ≈ 40 万次字符串解析，摊在 2025 帧里仍是每帧百次级的抖动。

**建议**：`load_state` 时预处理成 `_placed_by_cell: {Vector2i: {level: kind}}`，建列时 O(1) 取。

### P4（中）渲染距离档位常量在三处各写一遍

**位置**：`:155`（`RADIUS_STEPS = [10,14,18,22]`）、`:1921`（设置脚本里又写 `[10,14,18,22]`）、`:1391` 与 `:1864`（两处 `clampi(..., 1, 4)`）

**风险**：改档位（加 26 / 去掉 10）必须同时改 4 处；漏改时 `_apply_radius` 的 `int(chunk.get("_radius")) != blocks` 永远成立，每次打开设置都会整屏重铺（见 R2），而且不会报错。

**建议**：把档位放在区块脚本里暴露一个只读方法（`RADIUS_STEPS` 常量 + `radius_index_to_blocks()`），设置面板和创建面板都调它；`clampi` 的上界用 `size()` 而不是字面量 4。

### P5（低）`set_radius` 的 clamp 区间与档位不联动

**位置**：`:501-502`（`clampi(value, 6, 24)`）

**风险**：将来档位越过 24（例如 26/30）会被静默夹到 24，且 `_radius` 与玩家变量里的索引不再对应；同时没有对 `RADIUS_STEPS` 做校验。

**建议**：直接接受档位映射后的块数，不做区间 clamp；或在越界时 `push_warning`。

### P6（低）`_time_index` 是死状态

**位置**：`:184`（声明）、`:479`（唯一赋值）

**说明**：昼夜实际由天空脚本 `apply_time()` 直接读 `昼夜时间` 玩家变量（`:536-543`），`_time_index` 从未被使用 → 同一事实存两份，容易在后续改动里读到过期值。

**建议**：删掉 `_time_index`，或把它作为唯一真源并让 `_apply_time` 用 `_time_index` 下发。

### P7（低）每帧 `find_child("世界菜单")` + 重复解析全部存档

**位置**：`:1046-1068`（每帧 `scene.find_child`）、`:1419`（`_refresh_menu` 里 `list_saves()`）、`:1399`（`open_menu_primary` 又 `list_saves()`）

**说明**：单次开销都不大，但 `list_saves()` 会把每个存档 JSON 全读全解析；`_refresh_menu` 与 `open_menu_primary` 在启动时连做两次。`find_child` 每帧一次纯属白烧（菜单引用可以缓存，或用 `visibility_changed` 信号）。

**建议**：`_menu` 缓存 + `visibility_changed` 回调；`list_saves()` 结果缓存到"面板打开/保存后失效"。

### P8（低）靠中文节点名查找框架 UI，与框架自己的约定相冲突

**位置**：`:1416-1428`（`find_child("世界菜单")` / `"继续游戏"` / `"新建世界"` / `"读取存档"` / `"设置"` / `"退出游戏"`）；对照框架注释 `.../game_logic_World_Player.gd:1656-1658`（"按 Godot 组整批收 / 放，不靠控件的名字猜"）

**风险**：改按钮文案或增删节点后，`find_child` 返回 `null`，`_show_menu_button` / `if primary is Button` 都会静默跳过——菜单看起来"正常"，但入口收敛失效，且**不会报错**。

**建议**：给这几个按钮加 group（如 `gk_menu_primary` / `gk_menu_saves_only`），或让生成器把控件引用存到玩家节点上（`_gk_ui_*` 已是这种思路，可暴露成方法）；至少给 `null` 分支加 `push_warning`。

### P9（低）`.gk_export_files` 里有三个指向不存在文件的条目

**位置**：`examples/demo_minecraft/我的世界_godot/.gk_export_files`

**实测**（只读校验）：清单里有 `art/light_cookie_rect.png`、`materials/Ballmat.tres`、`materials/Floor.tres`，磁盘上这三个都不存在（其余 glb / png / tres 都在）。

**影响**：如果后续有"按 manifest 校验产物完整性 / 按 manifest 清理"的逻辑，会误报或漏清。**建议**：确认这三条是否本轮之前就遗留；让 manifest 写入与实际落盘走同一份列表，并在门禁里加一条"manifest 里每个文件都存在"的断言。

### P10（低）demo README 与当前实现不一致

**位置**：`examples/demo_minecraft/README.md:8-9`（仍写 `开始游戏 / 创建游戏 / 退出游戏` 与"创建新的世界"面板）、`:23`、`:54`（写 `21×21` 块）

**实际**：菜单已收敛成 `继续游戏 / 新建世界 / 读取存档 / 设置 / 退出游戏`；默认渲染距离是 14（29×29）。**建议**：跟本轮一起更新，或由生成器把这段数字改为读常量，避免每轮手工追。

### P11（低）`save_game` 只校验"打开失败"，不校验写入失败

**位置**：`:1527-1535`

**说明**：`FileAccess.open` 成功后 `store_string()` 仍可能失败（磁盘满 / 只读），当前会返回 `true` 并提示"已保存"。`file.close()` 也没有检查 `get_error()`。

**建议**：写完 `file.close()` 后检查 `FileAccess.get_open_error()` / 文件大小 > 0，再决定返回值。

---

## 四、本轮已核对、未发现问题的部分

- **存档格式与兼容**：`format: 2` + `world_settings` 齐全（name/seed/terrain/size/vegetation/time/radius），旧档缺字段时的逐项回落链路（`:1574-1620`）与 `docs/待办清单.md:335` 一致（除 R1 的植被默认值）；`apply_world()` → `load_state()` 的顺序（先清改动、再写回、再重铺）没有发现脏状态。
- **种子确定性**：`_hash_string` / `_hash` 在 64 位 int 上运算，空种子稳定复现原地形；存档存的是原始种子字符串（不是哈希后的数），跨版本可复现。
- **放置方块的坐标反解**：`round(spot.x - 0.5)` / `round(spot.y)` 与生成侧的落点公式（`game_logic_World_Player.gd:943`：顶面 `point.y + n.y*0.5 - 0.5`、底面 `floor(point.y)`，节点原点在方块底面）一致，没有出现"读档后整体抬高/降低一格"。
- **新世界入口链路**：`_on_create` 写入 7 个世界变量 → `apply_world()` 重读 → `_enter_world()` 收起菜单，字段顺序与 `_read_params` 的索引约定一致（除 R1）；面板默认值 = 设计表（无限 / 丘陵 / 默认 10% / 白天 / 14）。
- **开始界面可交互化**：背景 `CanvasLayer(layer=5)` 在 3D 之上、框架 UI（layer=10）之下，`MOUSE_FILTER_IGNORE` 不挡点击；存档层（30）/ 设置层（31）在菜单之上，遮罩 `MOUSE_FILTER_STOP` 能挡住穿透点击。

---

## 五、建议的最小验证清单（只读 / 真机，不改仓库）

1. 无存档启动 →『开始游戏』→ 建世界 → **观察鼠标是否被捕获**（F1）；有存档启动 →『继续游戏』→ 同样检查。
2. 同类型方块放 2 块 → 保存 → 重进 → 数量与位置一致（F2）。
3. 创建面板按 ESC（F3）。
4. 世界大小 128 → 往边界外走 20 秒（F6）。
5. 旧格式存档（无 `world_settings`）读回 → 对比树的数量（R1）。
6. 设置里 14 → 18 → 观察是否整屏重建（R2）。
7. 渲染距离 22 → 计时铺满需要多久、观察掉帧（P1）。

## 六、下一轮计划

- 通读 `我的世界区块` 的挖掘 / 树木 / 树叶逻辑（`_on_tree_exiting`、`_make_leaves`、`_build_leaves`）与昼夜 → 雾 / 天气脚本的相互覆盖（本轮只看到 `_apply_time` 与 `WEATHER_SCRIPT` 都以 20s 周期改 fog，白天/夜晚可能互相打架，尚未定论，留待下一轮核实）。
- 核对 `scripts/` 下的离线单测对上述 F/R 项的覆盖情况（尤其"旧档植被默认值""同名方块存档"这两条目前看没有用例）。
