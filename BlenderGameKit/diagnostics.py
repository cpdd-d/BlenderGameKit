"""一键导出"诊断信息" —— 出问题时点一下,把生成的文件发给我。

用户第 26 轮的原话:
    "我以为我没重载,但我发现一些修改了,就是之前要你删掉,你修改了测试看,
     或者在每次又错误时加入调试信息,这样我就能复制给你看"

所以有了这个。它把"我判断问题需要的全部信息"写成一个纯文本文件:
    * 插件版本 / 装在哪(排查"改了没生效")
    * 场景里每个物体的物理设置(碰撞形状 / 手填尺寸 / 脚本体)
    * 每张节点图、每个节点、每条连线(节点图是不是真存进去了)
    * 主相机解析结果(镜头不对时看这个)
    * 每个物体的**网格尺寸 vs 碰撞体尺寸**(碰撞不对时看这个)
    * 导出结果:脚本路径、行数、告警、动作映射

关键是**全部用纯文本**,用户直接复制粘贴就行,不用截图。
"""

import os

import bpy


def _line(text=""):
    return text


def build_report(context=None):
    """把诊断信息拼成一段纯文本。"""
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    out = []
    add = out.append

    add("=" * 70)
    add("BlenderGameKit 诊断信息")
    add("=" * 70)

    # ---- 1. 插件版本 / 位置(排查"改了没生效")----
    add("")
    add("【1】插件")
    add(f"  Blender: {bpy.app.version_string}")
    # ⚠ 这里以前写死 `bl_ext.user_default.blender_game_kit` —— 只在默认仓库
    # 装的时候对得上;装到别的仓库(门禁用的 gk_test)或作为普通 addon 装时
    # 全部 import 失败,诊断报告里这几段会静默退化成"取不到"。
    # 改用相对导入,跟装在哪无关。
    try:
        add(f"  插件路径: {os.path.dirname(os.path.abspath(__file__))}")
    except Exception as exc:
        add(f"  插件路径: 取不到({exc})")
    try:
        from . import nodes as _nodes
        add(f"  节点数: {len(_nodes.ALL_CLASSES)}")
        # 关键:确认"该删的节点"在这个版本里是不是真的删了
        for name in ("GKNodeSetPosition", "GKNodeDistance", "GKNodeTextJoin",
                     "GKNodeLogicOutput", "GKNodeGetPosition"):
            add(f"    {name}: {'有' if any(c.bl_idname == name for c in _nodes.ALL_CLASSES) else '没有'}")
    except Exception as exc:
        add(f"  节点库: 取不到({exc})")

    # ---- 2. 文件 ----
    add("")
    add("【2】文件")
    add(f"  .blend: {bpy.data.filepath or '(没保存过)'}")
    settings = getattr(scene, "gk_settings", None) if scene else None
    if settings is not None:
        for attr in ("dimension", "base_class", "class_name", "node_name",
                     "main_camera", "toon_materials", "write_scene",
                     "export_tunables"):
            add(f"  {attr} = {getattr(settings, attr, '(没有这个属性)')!r}")

    # ---- 3. 物体 + 物理 + 尺寸 ----
    add("")
    add("【3】物体 / 物理 / 尺寸")
    if scene is not None:
        for obj in scene.objects:
            physics = getattr(obj, "gk_physics", None)
            if physics is None:
                add(f"  {obj.name}: (没有游戏物理属性)")
                continue
            size = tuple(round(float(v), 3) for v in getattr(obj, "dimensions", (0, 0, 0)))
            manual = tuple(round(float(v), 3) for v in getattr(physics, "shape_size", (0, 0, 0)))
            add(f"  {obj.name}:")
            add(f"      类型={getattr(obj, 'type', '?')} 位置={tuple(round(float(v), 3) for v in obj.location)}")
            add(f"      网格尺寸={size}")
            add(f"      物理: 启用={getattr(physics, 'enabled', '?')} "
                f"body={getattr(physics, 'body_type', '?')} "
                f"形状={getattr(physics, 'collision_shape', '?')} "
                f"手填尺寸={manual} 脚本体={getattr(physics, 'script_target', '?')}")
            # 碰撞体实际会生成多大(和网格比一比,一眼看出"陷进去/悬空")
            try:
                from . import asset_export
                data = asset_export.build_collision(obj, is_3d=True, physics=physics)
                if data:
                    sub = str(data.get("sub_resource", "")).replace("\n", " ")
                    node = str(data.get("node", "")).replace("\n", " ")
                    add(f"      碰撞体: {sub[:110]}")
                    if "position" in node:
                        add(f"      碰撞体位置: {node[node.index('position'):][:70]}")
            except Exception as exc:
                add(f"      碰撞体: 算不出来({exc})")

    # ---- 4. 主相机 ----
    add("")
    add("【4】主相机")
    try:
        from . import exporter
        name, why = exporter.resolve_main_camera(scene)
        add(f"  解析结果: {name or '(没有相机)'}  <- {why}")
        view_cam = getattr(scene, "camera", None)
        add(f"  Blender 视口当前相机: {getattr(view_cam, 'name', '(没切到相机视角)')}")
        cameras = [o.name for o in scene.objects if getattr(o, "type", "") == "CAMERA"]
        add(f"  场景里的相机: {cameras}")
    except Exception as exc:
        add(f"  取不到({exc})")

    # ---- 5. 节点图 ----
    add("")
    add("【5】节点图")
    groups = [g for g in bpy.data.node_groups
              if getattr(g, "bl_idname", "") in ("GKGameLogicTree",
                                                 "GKAnimationTree",
                                                 "GKGameAiTree",
                                                 "GKUiDesignTree",
                                                 "GKDisplayTree",
                                                 "GKAssetTree",
                                                 "GKMusicTree")]
    if not groups:
        add("  (一张都没有 —— 节点图可能在存盘时丢了,见 README『use_fake_user』)")
    for group in groups:
        add(f"  [{getattr(group, 'bl_idname', '?')}] {group.name}: "
            f"{len(group.nodes)} 节点 / {len(group.links)} 连线 / "
            f"参与导出={getattr(group, 'gk_enabled', '?')}")
        ports = getattr(group, "gk_ports", None)
        if ports is not None and len(ports):
            add(f"      对外端口: "
                f"{[(p.port_name, p.port_type, p.direction) for p in ports]}")
        for node in group.nodes:
            add(f"      - {getattr(node, 'bl_idname', '?')} 「{node.name}」"
                f" @({int(node.location.x)},{int(node.location.y)})")

    # ---- 6. 导出一遍看结果 ----
    add("")
    add("【6】试导出")
    logic = next((g for g in groups
                  if getattr(g, "bl_idname", "") == "GKGameLogicTree"
                  and getattr(g, "gk_enabled", True)), None)
    if logic is None:
        add("  没有可导出的游戏逻辑图")
    else:
        try:
            from . import codegen, project
            options = {"dimension": str(getattr(settings, "dimension", "3")),
                       "base_class": str(getattr(settings, "base_class", "auto"))}
            # 用返回值拿结果,不再读 codegen 的模块级全局变量(GPT 审查)
            result = codegen.generate_result(logic, options)
            add(f"  生成 {len(result.code.splitlines())} 行")
            add(f"  告警 {len(result.warnings)} 条:")
            for item in result.warnings:
                add(f"      ! {item}")
            for item in result.notes:
                add(f"      · {item}")
            add(f"  输入动作: {result.input_actions}")
            directory, reason = project.resolve(scene)
            add(f"  导出目录: {directory}  ({reason})")
        except Exception as exc:                          # noqa: BLE001
            import traceback
            add(f"  导出失败: {type(exc).__name__}: {exc}")
            add(traceback.format_exc())

    add("")
    add("=" * 70)
    add("(以上全部内容复制给我就行)")
    add("=" * 70)
    return "\n".join(out)


class GK_OT_DumpDiagnostics(bpy.types.Operator):
    """把诊断信息写成一个文本文件,并打印到控制台。"""

    bl_idname = "gk.dump_diagnostics"
    bl_label = "导出诊断信息"
    bl_description = ("把插件版本、物体/碰撞尺寸、节点图、主相机、导出告警"
                      "全部写成一份文本,出问题时发给我")
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            text = build_report(context)
        except Exception as exc:                          # noqa: BLE001
            self.report({"ERROR"}, f"诊断失败: {exc}")
            return {"CANCELLED"}
        # 1) 写文件(放在 .blend 旁边,找不到就放临时目录)
        base = os.path.dirname(bpy.data.filepath) or os.path.expanduser("~")
        path = os.path.join(base, "BlenderGameKit_诊断.txt")
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except Exception as exc:                          # noqa: BLE001
            path = f"(写文件失败: {exc})"
        # 2) 也打印到控制台 —— 用户可以整段复制
        print(text)
        self.report({"INFO"}, f"诊断信息已写到 {path}(也打印在控制台)")
        return {"FINISHED"}
