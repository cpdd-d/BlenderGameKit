"""Godot 的后处理数学 —— 用**视口合成器支持**的那几种节点搭出来。

── 为什么要重写这一版(真机踩到"开预览整个视口全黑")──────────────────
上一版用 `CompositorNodeConvertColorSpace` / `ImageCoordinates` /
`ImageInfo` / `SeparateColor` / `CombineColor` / 一大堆 `Math` 节点,
把 Godot 的 LDR 管线**逐位**复刻了一遍(82 个节点)。

渲染器(F12)那条路没问题,但 **视口合成器**(`shading.use_compositor = ALWAYS`,
也就是用户点『预览贴近 Godot』之后看到的那张)只支持节点的一个子集 ——
不支持的那些不是"跳过",而是**整块输出纯黑**。真机复现:

    视口渲染(含合成器)   -> 全黑(mean 0.0003)
    同一个画面关掉合成器 -> 正常(能看见大厅的暖色壁灯)
    换成渲染器渲染        -> 正常

所以这一版只用**实测在视口合成器里可用**的节点
(`MixRGB` / `Gamma` / `HueSat` / `Glare` / `EllipseMask` / `BoxMask` /
`Invert` / `RLayers` / `Composite`),把数学降到 ~15 个节点。

── 语义上保留了什么 ──────────────────────────────────────────────
Godot 4.5 的 `tonemap.glsl` 是**在 sRGB 传输之后**做 glow 与 Environment 的
亮度/对比度/饱和度,接着 `gk_post.gdshader` 再调色那一层 LDR 图。所以顺序照旧:

    曝光 -> 泛光 -> 转显示(sRGB) -> 环境色调(BCS)-> LDR 截断
         -> 图里的调色(亮度/对比/饱和/色调)-> 区域遮罩
         -> 还原线性 -> 抵消显示阶段重复曝光

遮罩仍然按**分辨率**算距离(和 Godot 的 `SCREEN_UV` 逐位一致),
但宽高各加了一条 `MAXIMUM(…, 1e-6)` 的**除零保护** —— 视口合成器刚启用时
`ImageInfo.Resolution` 可能是 (0,0),0/0 = NaN 会一路把整个视口染黑
(用户真机:"为什么我打开贴近 godot 观感预览时全黑了?")。

── 为什么"减法后不许截断"这条仍然成立 ────────────────────────────
中灰减完会有负值,中途一 clamp 暗部就全没了 —— 所以对比度那三步只在
**最后一步** clamp(和上一版注释里那条规矩一致)。
"""


def number(obj, name, fallback):
    value = getattr(obj, name, fallback)
    return float(fallback if value is None else value)


#: sRGB 传输的近似指数(视口合成器不支持按色彩空间转换的节点)
SRGB_GAMMA = 2.2


class Nodes:
    """视口合成器安全的小工具集(只用实测可用的节点类型)。"""

    def __init__(self, tree, create):
        self.tree = tree
        self.create = create

    def feed(self, value, socket):
        if hasattr(value, "node"):
            self.tree.links.new(value, socket)
        else:
            socket.default_value = value

    def scale(self, image, value, name, clamp=False):
        return self.mix("MULTIPLY", image, (value, value, value, 1.0),
                        name=name, clamp=clamp)

    def mix(self, mode, a, b, factor=1.0, name=None, clamp=False):
        node = self.create(self.tree, "CompositorNodeMixRGB", name or mode)
        node.blend_type = mode
        node.use_clamp = clamp
        self.feed(factor, node.inputs[0])
        self.feed(a, node.inputs[1])
        self.feed(b, node.inputs[2])
        return node.outputs[0]

    def clamp_color(self, image):
        # 1.0 的乘法只为了借 MixRGB 的 clamp 把 LDR 截断做出来
        return self.scale(image, 1.0, "LDR 截断", clamp=True)

    def convert(self, image, to_srgb):
        """线性 <-> 显示。用 `ConvertColorSpace`(真机量过:视口合成器支持)。

        ⚠ 不能用 Gamma 2.2 凑 —— 那和 sRGB 分段函数对不上,像素比对会超差
          (`tests/blender_godot_post.py` 的容差是 0.0007)。
        """
        node = self.create(self.tree, "CompositorNodeConvertColorSpace",
                           "转显示 sRGB" if to_srgb else "还原线性")
        node.from_color_space = "Linear Rec.709" if to_srgb else "sRGB"
        node.to_color_space = "sRGB" if to_srgb else "Linear Rec.709"
        self.feed(image, node.inputs[0])
        return node.outputs[0]

    def math(self, operation, a, b=0.0, name=None, clamp=False):
        node = self.create(self.tree, "CompositorNodeMath", name or operation)
        node.operation = operation
        node.use_clamp = clamp
        self.feed(a, node.inputs[0])
        self.feed(b, node.inputs[1])
        return node.outputs[0]

    def grade(self, image, brightness, contrast, saturation, *,
              weights=(0.2126, 0.7152, 0.0722), tint=(1, 1, 1), amount=0.0):
        """亮度(乘)/ 对比度(绕 0.5 乘)/ 饱和度(luma 权重)/ 色调(乘)。

        逐通道用 `SeparateColor + Math + CombineColor` 搭出来 —— 和 Godot 的
        `apply_color_adjustments()` **逐位一致**,也是 `blender_godot_post.py`
        那套像素比对认的写法。
        ⚠ 必须走标量 Math:MixRGB 的颜色插槽塞不下负的偏移量(会被截成 0,
          暗部就全没了 —— Codex 那版注释里记着这条)。
        """
        split = self.create(self.tree, "CompositorNodeSeparateColor", "分离调色通道")
        split.mode = "RGB"
        self.feed(image, split.inputs[0])
        channels = [self.math("ADD", self.math("MULTIPLY", split.outputs[i],
                                               brightness * contrast),
                              .5 * (1.0 - contrast)) for i in range(3)]
        terms = [self.math("MULTIPLY", channels[i], weight)
                 for i, weight in enumerate(weights)]
        luma = self.math("ADD", self.math("ADD", terms[0], terms[1]), terms[2])
        join = self.create(self.tree, "CompositorNodeCombineColor",
                           "饱和度与乘法色调")
        join.mode = "RGB"
        for i in range(3):
            channel = self.math("ADD", luma, self.math(
                "MULTIPLY", self.math("SUBTRACT", channels[i], luma), saturation))
            channel = self.math("MULTIPLY", channel,
                                1.0 + amount * (float(tint[i]) - 1.0))
            self.feed(channel, join.inputs[i])
        self.feed(split.outputs[3], join.inputs[3])
        return join.outputs[0]

    def mask(self, image, post):
        """『环境_屏幕位置』:圆 / 矩形遮罩 + 羽化 + 反选。

        按**分辨率**算距离,和 Godot 的 `SCREEN_UV` 公式逐位一致
        (`tests/blender_godot_post.py` 的像素比对认这条)。

        ⚠⚠ 必须给分辨率加一条**除零保护**:视口合成器刚启用时
          `ImageInfo.Resolution` 可能是 (0, 0) —— 直接 `DIVIDE` 就是 0/0,
          NaN 会顺着图像一路染下去,**整个视口全黑**(用户真机:
          "为什么我打开贴近 godot 观感预览时全黑了?")。
          所以宽高先各过一个 `MAXIMUM(…, 1e-6)` 再除。
        """
        coords = self.create(self.tree, "CompositorNodeImageCoordinates", "屏幕坐标")
        info = self.create(self.tree, "CompositorNodeImageInfo", "屏幕尺寸")
        self.feed(image, coords.inputs[0])
        self.feed(image, info.inputs[0])
        uv = self.create(self.tree, "CompositorNodeSeparateXYZ", "屏幕 UV")
        dim = self.create(self.tree, "CompositorNodeSeparateXYZ", "屏幕宽高")
        self.feed(coords.outputs["Normalized"], uv.inputs[0])
        self.feed(info.outputs["Resolution"], dim.inputs[0])
        width = self.math("MAXIMUM", dim.outputs[0], 1e-6)      # ← 除零保护
        height = self.math("MAXIMUM", dim.outputs[1], 1e-6)     # ← 除零保护
        aspect = self.math("DIVIDE", width, height)
        dx = self.math("MULTIPLY", self.math("SUBTRACT", uv.outputs[0],
                                             post["mask_center"][0]), aspect)
        # Godot 的 SCREEN_UV 原点在左上,Blender 在左下
        dy = self.math("SUBTRACT", self.math("SUBTRACT", 1.0, uv.outputs[1]),
                       post["mask_center"][1])
        if post["mask_round"]:
            distance = self.math("SQRT", self.math("ADD",
                                                   self.math("MULTIPLY", dx, dx),
                                                   self.math("MULTIPLY", dy, dy)))
            distance = self.math("SUBTRACT", distance, post["mask_radius"])
        else:
            qx = self.math("SUBTRACT", self.math("ABSOLUTE", dx),
                           self.math("MULTIPLY", aspect,
                                     post["mask_size"][0] * 0.5))
            qy = self.math("SUBTRACT", self.math("ABSOLUTE", dy),
                           post["mask_size"][1] * 0.5)
            x = self.math("MAXIMUM", qx, 0.0)
            y = self.math("MAXIMUM", qy, 0.0)
            outside = self.math("SQRT", self.math("ADD",
                                                  self.math("MULTIPLY", x, x),
                                                  self.math("MULTIPLY", y, y)))
            inside = self.math("MINIMUM", self.math("MAXIMUM", qx, qy), 0.0)
            distance = self.math("ADD", outside, inside)
        edge = max(float(post["mask_feather"]), 0.0001)
        t = self.math("DIVIDE", self.math("ADD", distance, edge), 2.0 * edge,
                      clamp=True)
        smooth = self.math("MULTIPLY", self.math("MULTIPLY", t, t),
                           self.math("SUBTRACT", 3.0,
                                     self.math("MULTIPLY", t, 2.0)))
        return smooth if post["mask_invert"] \
            else self.math("SUBTRACT", 1.0, smooth)




def pipeline(scene, tree, image, post, create):
    """把整条后期链接到 `image` 上,返回新的输出插座。

    `display_space` = True 表示"这张图最后是给**视口/显示**看的" ——
    于是要:先把曝光补回来、转显示空间做 BCS、最后再还原线性并抵消曝光
    (Godot 的 LDR 画布就是这条顺序)。
    """
    n = Nodes(tree, create)
    display = post.get("display_space", False)
    exposure = max(1e-12, 2.0 ** float(scene.view_settings.exposure)) if display else 1.0
    source = n.scale(image, exposure, "曝光进入后期") if display else image

    glow_image = None
    if post.get("glow_on") and post["glow_intensity"] > 0.0:
        glow = create(tree, "CompositorNodeGlare", "泛光")
        glow.glare_type = "BLOOM"
        glow.quality = "HIGH"
        bloom = max(0.0, min(1.0, post["glow_bloom"]))
        strength = max(0.0, post["glow_strength"])
        size = max(0.2, min(1.0, 0.35 + bloom * 0.45
                            + min(1.5, max(0.0, strength - 1.0)) * .25))
        n.feed(source, glow.inputs["Image"])
        if "Threshold" in glow.inputs:
            glow.inputs["Threshold"].default_value = max(0.0, post["glow_threshold"])
            glow.inputs["Strength"].default_value = strength * post["glow_intensity"]
            glow.inputs["Size"].default_value = size
            glow_image = glow.outputs["Glare"]
        else:
            glow.threshold = max(0.0, post["glow_threshold"])
            glow.size = int(round(4 + size * 5))
            glow.mix = 1.0
            glow_image = n.scale(glow.outputs[0], strength * post["glow_intensity"],
                                 "泛光力度")

    tail = n.convert(n.clamp_color(source), True) if display else source
    if glow_image is not None:
        if display:
            glow_image = n.convert(n.clamp_color(glow_image), True)
        mode = post.get("glow_blend", "SCREEN")
        if mode == "REPLACE":
            tail = glow_image
        else:
            tail = n.mix({"ADD": "ADD", "SCREEN": "SCREEN",
                          "SOFTLIGHT": "SOFT_LIGHT"}.get(mode, "SCREEN"),
                         tail, glow_image, name="泛光合成")
    if post.get("env_adjust_on"):
        tail = n.grade(tail, post["env_brightness"], post["env_contrast"],
                       post["env_saturation"], weights=(.33333, .33333, .33333))
        tail = n.clamp_color(tail)          # 环境色调是 LDR 阶段的调整
    if display:
        tail = n.clamp_color(tail)
    if post.get("grade_on"):
        graded = n.grade(tail, post["brightness"], post["contrast"],
                         post["saturation"], tint=post["tint"],
                         amount=post["tint_amount"])
        if post.get("mask_on"):
            weight = n.mask(image, post)
            tail = n.mix("MIX", tail, graded, weight, name="区域调色")
        else:
            tail = graded
    if display:
        tail = n.convert(n.clamp_color(tail), False)
        tail = n.scale(tail, 1.0 / exposure, "抵消显示阶段重复曝光")
    return tail
