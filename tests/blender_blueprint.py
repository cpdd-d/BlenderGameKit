"""Shared graph instances, editor reroutes and scene-lit export integration."""
import os
import sys
import tempfile
from types import SimpleNamespace
import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import BlenderGameKit as addon
from BlenderGameKit import asset_export, codegen, exporter, godot_look, presets, ui


def main():
    addon.register()
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene.name = '测试大堂'
    role = bpy.data.node_groups.new('共享角色', 'GKGameLogicTree')
    role.gk_scope = 'ROLE'
    ready = role.nodes.new('GKNodeOnReady')
    camera_node = role.nodes.new('GKNodeCameraSetCurrent')
    camera_node.gk_target = '@视角'
    role.links.new(ready.outputs['执行'], camera_node.inputs['执行'])
    player = bpy.data.objects.new('玩家', bpy.data.meshes.new('玩家网格'))
    scene.collection.objects.link(player)
    player.gk_physics.logic_tree = role.name
    player.gk_physics.script_target = True
    camera = bpy.data.objects.new('大堂相机', bpy.data.cameras.new('相机数据'))
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera2 = bpy.data.objects.new('第二相机', bpy.data.cameras.new('相机数据2'))
    scene.collection.objects.link(camera2)
    ui.GK_OT_SyncLogicBindings.execute(SimpleNamespace(), SimpleNamespace(object=player))
    binding = player.gk_physics.logic_bindings[0]
    assert binding.binding_name == '视角'
    binding.target = camera
    options = exporter.build_settings(scene)
    original = codegen.generate(role, options)[0]
    assert '大堂相机' in original and '@视角' not in original
    binding.target = camera2
    second = codegen.generate(role, exporter.build_settings(scene))[0]
    assert '第二相机' in second and '大堂相机' not in second
    assert camera_node.gk_target == '@视角'
    binding.target = None
    try:
        codegen.generate(role, exporter.build_settings(scene))
        raise AssertionError('Missing binding silently accepted')
    except codegen.CodegenAbort:
        pass
    binding.target = camera

    role.links.clear()
    reroute = role.nodes.new('NodeReroute')
    role.links.new(ready.outputs['执行'], reroute.inputs[0])
    role.links.new(reroute.outputs[0], camera_node.inputs['执行'])
    assert codegen.generate(role, options)[0] == original

    data_tree = bpy.data.node_groups.new('数据转接', 'GKGameLogicTree')
    event = data_tree.nodes.new('GKNodeOnReady')
    write = data_tree.nodes.new('GKNodeSetVar')
    write.gk_var_name = '速度'
    value = data_tree.nodes.new('GKNodeConstFloat')
    value.gk_value = 3.5
    data_tree.links.new(event.outputs['执行'], write.inputs['执行'])
    data_tree.links.new(value.outputs[0], write.inputs['值'])
    before = codegen.generate(data_tree, options)[0]
    link = next(l for l in data_tree.links if l.from_node == value)
    data_tree.links.remove(link)
    data_reroute = data_tree.nodes.new('NodeReroute')
    data_tree.links.new(value.outputs[0], data_reroute.inputs[0])
    data_tree.links.new(data_reroute.outputs[0], write.inputs['值'])
    after = codegen.generate(data_tree, options)[0]
    if after != before:
        import difflib
        print(''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True))))
    assert after == before

    lamp = bpy.data.objects.new('聚光灯', bpy.data.lights.new('灯数据', 'SPOT'))
    lamp.data.energy = 3000
    scene.collection.objects.link(lamp)
    text, _files, _name = asset_export.build_environment_nodes(scene)
    assert text.count('type="Camera3D"') == 2, text
    assert text.count('current = true') == 1, text
    text, _files, _name = asset_export.build_environment_nodes(scene, material_only=True)
    assert 'type="Camera3D"' not in text and 'type="SpotLight3D"' not in text

    mat = bpy.data.materials.new('灰色')
    mat.use_nodes = True
    mat.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value = (0.25, 0.25, 0.25, 1)
    player.data.materials.append(mat)
    scene.gk_settings.toon_follow_light = True
    with tempfile.TemporaryDirectory(prefix='gk_blueprint_') as directory:
        paths = asset_export.write_materials(directory, [player])
        with open(paths[0][1], encoding='utf-8') as f:
            material_text = f.read()
        assert 'gk_toon_lit.gdshader' in material_text
        assert 'Color(0.5371, 0.5371, 0.5371' in material_text, material_text
        mat.use_backface_culling = True
        paths = asset_export.write_materials(directory, [player])
        assert os.path.isfile(os.path.join(directory, 'shaders', 'gk_toon_lit_cull.gdshader'))

    scene.gk_settings.env_enabled = True
    scene.gk_settings.env_ambient_energy = 0.2
    godot_look.apply(scene)
    first_world = scene.world
    other = bpy.data.scenes.new('测试走廊')
    other.gk_settings.toon_follow_light = True
    other.gk_settings.env_enabled = True
    other.gk_settings.env_ambient_energy = 0.6
    scene.gk_settings.env_fog = True
    other.gk_settings.env_fog = True
    godot_look.apply(scene)
    godot_look.apply(other)
    assert scene.world != other.world
    assert any(abs(n.inputs['Strength'].default_value - 0.2) < 0.001
               for n in first_world.node_tree.nodes if n.type == 'BACKGROUND')
    godot_look.restore(other)
    assert scene.world == first_world
    fog = bpy.data.objects.get(godot_look.FOG_NAME + ' · ' + scene.name)
    assert fog is not None and asset_export.is_preview_object(fog)
    assert fog not in [obj for obj, _physics, _enabled in asset_export.collect_physics(scene)]
    assert not fog.get(asset_export.PREVIEW_MARK), '雾不应被碰撞预览清理删除'
    print('BLUEPRINT=OK')


main()
