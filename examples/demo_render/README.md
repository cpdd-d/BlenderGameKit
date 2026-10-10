# 渲染验证工程(NPR 面部阴影 / 画面对不对)

这是一个**纯 Godot 工程**(没有 .blend):拿导出的模型 + 卡通材质,用脚本出图,
用**像素统计**判断"真的画出了东西",并让主光绕角色转一圈、多个角度各出一张图
—— 这是验证**面部阴影 SDF 是否真的在"滑动"**的唯一可靠方式。

| 文件 | 干什么 |
|---|---|
| `Main.tscn` | 舞台:角色 + 主光 + 相机 |
| `capture.gd` | 出图:存 PNG + 像素统计;`GK_SWEEP=1` 时开扫光模式,输出到 `shots/light_XXX.png` |
| `jitter_probe.gd` | 抖动探针(阴影边缘的抖动 / 闪烁检测) |
| `shots/` | 扫光图(light_090 / 180 / 270 …)|

## 怎么跑

```powershell
powershell -File scripts\render_shots.ps1     # 出图 + 扫光,见脚本里的参数
```

也可以手跑:

```powershell
godot --path examples/demo_render --resolution 640x640
# 环境变量:GK_RENDER_OUT=输出路径  GK_SWEEP=1 开扫光  GK_SWEEP_STEP=角度步长
```

## 和文档的关系

着色器参数含义、光照强度甜点区、面部阴影怎么调 —— 见
`docs\渲染-NPR卡通着色.md`;这个工程是那篇文档里的**验证现场**。

> **守则里的 5s 层**(第九十一轮起):`scripts\check_render_demo.py` 会
> 真开窗口跑 4 个角度的扫光,断言**渲染结果** —— 每张不黑、明暗随光角度
> **滑动**(dark 至少有 3 种取值)、四张两两字节不同(灯真的转了)。
> 它要真 GPU(`--headless` 是 dummy 渲染器,画不出东西),所以**没显卡的
> 机器上自动跳过、不判红**;手动出参考图(写进本目录 `shots/`)还是用
> 上面那条 `render_shots.ps1`。
> 素材(`*.png` / `.godot/`)是 Godot 自己导入的产物。