# 环境示例(第四十九轮:阶段②环境与后处理)

这一屏用来把**环境与后处理**这套能力走完:同一个几何搭两遍,只换
`WorldEnvironment`,看**背景 / 环境光 / 雾 / 泛光 / 色调**到底把画面改成什么样 ——
一关是白天的天空,一关是近黑走廊里的冷雾。

| 文件 | 说明 |
|---|---|
| `环境示例.blend` | Blender 工程(**两个场景**:`白天` / `幽暗`)|
| `环境示例_godot/` | 导出的 Godot 工程(用 Godot 打开这个目录,按 F5 跑白天)|

> "关卡"在 Blender 里就是**一个场景**(scene),不是第二棵节点图 ——
> 导出时每个场景各出一张 `.tscn`(`scenes/白天.tscn` / `scenes/幽暗.tscn`)。

## 一、两关差在哪(一眼对照)

| | 白天 | 幽暗 |
|---|---|---|
| 背景 | **程序化天空**(顶色 = 背景色) | 近黑**纯色** `(0.03, 0.04, 0.07)` |
| 环境光 | 亮 **0.8**(暖白) | 暗 **0.15**(冷蓝) |
| 雾 | 关 | **开**,冷蓝,浓度 **0.08** |
| 泛光 | 关 | **开**,强度 **1.2**,溢出 0.2 |
| 色调映射 | Filmic | ACES |
| 灯 | 一盏太阳(3.0) | 弱月光(0.25)+ 暖台灯 + 手电筒(聚光,跟随相机) |

跑法:Godot 打开 `环境示例_godot` 按 **F5** 跑`白天`;想看`幽暗`,在 Godot 里
直接打开 `scenes/幽暗.tscn` 按 **F6**(或把主场景改成它)。

## 二、这些值在哪调:『游戏显示』→『显示_环境氛围』

Blender 里侧栏『游戏逻辑』面板 ->『游戏显示设置』-> 打开/新建显示设置图,
图上有一张 **『显示_环境氛围』**卡(第五张):

```
[显示_环境氛围]
  环境与后处理  [x]                     ← 总开关,关着 = 产物里连节点都不多一个
  ┌ 背景 ────────────────────────┐
  │ [纯色][天空]    背景色 ■       │
  ├ 环境光(全局补光)─────────────┤
  │ 颜色 ■   强度 0.15            │
  ├ MOD_FLUID 雾 [x]  颜色 ■  浓度 0.08
  ├ LIGHT_SUN 泛光 [x] 强度 1.2 溢出 0.2
  └ 色调映射 [ACES] 色调调整 [ ]  │
```

* 卡上的值**写透**当前场景的 `GKSceneSettings`(`scene.gk_settings`)——
  改完**不用点应用**,导出立刻按新值走(和另外四张设置卡一个机制);
* 设置挂在**场景**上:多关卡时每关各有一套,切场景卡上的值跟着切;
* 总开关**默认关**:老工程导出结果**一字不变**(连 `WorldEnvironment`
  节点都不会多出来)。

## 三、导出成了什么

每张 `.tscn` 里多一个根节点 + 一份子资源:

```
[sub_resource type="Environment" id="gk_environment"]
background_mode = 1                     # 1 = 纯色, 2 = 天空
background_color = Color(0.03, 0.04, 0.07, 1)
ambient_light_source = 3                # 3 = 用下面的颜色当环境光
ambient_light_color = Color(0.45, 0.52, 0.72, 1)
ambient_light_energy = 0.150000
fog_enabled = true
fog_light_color = Color(0.24, 0.29, 0.40, 1)
fog_density = 0.080000
glow_enabled = true
glow_intensity = 1.200000
glow_bloom = 0.200000
tonemap_mode = 3                        # 0 线性 / 1 Reinhard / 2 Filmic / 3 ACES
adjustment_enabled = false

[node name="WorldEnvironment" type="WorldEnvironment" parent="."]
environment = SubResource("gk_environment")
```

几条说明(都写在导出器里,别去猜):

* **颜色 1:1 直接写**(不做 sRGB 转换)—— 和灯光颜色同一条约定;
* **天空**背景会多两份子资源(`ProceduralSkyMaterial` + `Sky`),
  天空的顶色 = 你填的**背景色**,地平线往白里提一点(纯色天空不好看);
* **只有主场景挂 `WorldEnvironment`** —— 角色子场景是被实例化的,
  再挂一个会和主场景的打架(同一个视口只有一个环境生效);
* ⚠ Godot 的 `Environment` **没有内置暗角(vignette)** —— 暗角要靠后处理
  着色器做,留到**氛围套件**那一轮。

## 四、自己跑一遍验证

不用打开 Blender / Godot 界面,一条命令(要装 Godot):

```powershell
python scripts\check_env_demo.py --godot "D:\godot\Godot_v4.7.2-stable_win64_console.exe"
```

它会把这个工程拷到 `_dist/env_probe`(**不碰示例本身**),加一个探针场景跑真
Godot,加载两张 `.tscn`,断言:

* **白天**:`background_mode == 2` 且 `sky != null`、`ambient_light_energy ≈ 0.8`、
  `fog_enabled == false`;
* **幽暗**:`background_mode == 1`、`fog_enabled == true` 且浓度 ≈ 0.08、
  `glow_enabled == true`、`tonemap_mode == 3`(ACES)、`ambient_light_energy ≈ 0.15`;
* **两关确实不一样**(对照才说明环境真的在起作用)。

全绿会打印 `CHECK_ENV_DEMO=OK`。

想重建这个示例(需要 Blender):

```powershell
blender -b --python scripts\make_env_demo.py
```

## 五、照这个做一个自己的环境

1. 选中场景(不是物体)->『游戏显示』显示设置图 -> 『显示_环境氛围』卡 ->
   勾**『环境与后处理』**;
2. **恐怖 / 幽暗**:背景选纯色、颜色压到近黑;环境光给一点冷蓝(0.1~0.2);
   雾开(浓度 0.05~0.1);泛光开(点光 / 手电筒会毛起来);色调 ACES
   (暗部更沉);
3. **白天**:背景选天空、背景色给天空蓝;环境光拉亮(0.6~1.0);雾 / 泛光关;
   色调 Filmic;
4. 想再暗一点就开**色调调整**(亮度 / 对比度 / 饱和度)。
