# 2D 寻路示例(俯视 2D:真绕障碍追人)

打开 `2D寻路示例.blend` 就能改这张图;用 Godot 打开 `2D寻路示例_godot` 按 **F5** 就能看。

## 这一关在看什么

俯视 2D 场景(不是横版平台):中间一堵 10 米宽的障碍把『追踪者』和『小玩家』隔开
—— 直线只有 12 米,但**走不通**。追踪者挂着『移动_寻路』:

* 场景设置里勾了『寻路』→ 启动时按**静态碰撞体**烤一块 NavigationPolygon;
* 追踪者每帧查 NavigationServer2D 的绕行路径 → **从障碍两端绕过去**追到玩家
  (直线追只会一头顶在障碍上)。

## ⚠ 2D 寻路的唯一注意事项

**2D 里碰撞体 = 障碍物**。所以:

* 给"墙 / 箱子 / 障碍"加碰撞体 ✓
* 给"地面 / 地板"**不要**加(整块地板会被当成一个巨大的障碍挖掉)——
  俯视 2D 游戏本来也不需要给地板加碰撞。

## 自动检查 / 重建

```powershell
python scripts\check_nav2d_demo.py      # 探针(要 Godot):追踪者真的绕过障碍追到人
blender -b --gpu-backend vulkan --python scripts\make_nav2d_demo.py   # 重建 .blend + 工程
```

> 门禁(`scripts\build_and_test.ps1`)里这一层跑的就是上面两条命令,
> 探针标记 `CHECK_NAV2D_DEMO=OK`。
> 想看全图:Godot 里加一台 Camera2D(2D 默认视图在原点,不加也能玩)。
>
> 3D 寻路的现场在 `examples\demo_physics`(『绕墙追击』『群追避障』『门升起重烤』工位);
> 多层 / 避障 / 区域代价还是待办(见 docs\待办清单.md)。