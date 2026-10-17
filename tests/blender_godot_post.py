"""Real compositor pixel regressions; run with Blender 4.5 --background.

The oracle evaluates the exported shader equations, not the compositor nodes.
Images are synthetic so lighting cannot hide an error in grading or masking.
"""
import math
import os
from pathlib import Path
import sys
import tempfile

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from BlenderGameKit import godot_look as look


def clamp(x):
    return max(0.0, min(1.0, x))


def grade(c, b, k, s, weights, tint=(1, 1, 1), amount=0):
    g = [(v * b - .5) * k + .5 for v in c]
    luma = sum(v * w for v, w in zip(g, weights))
    return [(luma + (v - luma) * s) * (1 + amount * (t - 1))
            for v, t in zip(g, tint)]


def oracle(rgb, x, y, w, h, p, exposure):
    c = [look.linear_to_srgb(clamp(v * exposure)) for v in rgb]
    if p['env_adjust_on']:
        c = grade(c, p['env_brightness'], p['env_contrast'], p['env_saturation'], (.33333,) * 3)
    c = [clamp(v) for v in c]
    if p['grade_on']:
        g = grade(c, p['brightness'], p['contrast'], p['saturation'],
                  (.2126, .7152, .0722), p['tint'], p['tint_amount'])
        weight = 1.0
        if p['mask_on']:
            dx = ((x + .5) / w - p['mask_center'][0]) * w / h
            dy = 1 - (y + .5) / h - p['mask_center'][1]
            if p['mask_round']:
                d = math.hypot(dx, dy) - p['mask_radius']
            else:
                qx = abs(dx) - p['mask_size'][0] * .5 * w / h
                qy = abs(dy) - p['mask_size'][1] * .5
                d = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0)
            edge = max(p['mask_feather'], .0001)
            t = clamp((d + edge) / (2 * edge))
            weight = 1 - t * t * (3 - 2 * t)
            if p['mask_invert']:
                weight = 1 - weight
        c = [v * (1 - weight) + q * weight for v, q in zip(c, g)]
    return [look.srgb_to_linear(clamp(v)) / exposure for v in c]


def main():
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE_NEXT'
    scene.eevee.taa_render_samples = 1
    scene.render.resolution_x, scene.render.resolution_y = 64, 40
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'OPEN_EXR'
    scene.render.image_settings.color_depth = '32'
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    scene.render.film_transparent = False
    # Exercise Blender's real default tree (4.5 includes Reroute and Viewer).
    look._remember(scene)
    native_post = look.post_values(scene)
    native_post['grade_on'] = True
    assert look.build_post(scene, native_post) == ''
    assert scene.use_nodes and look._post_nodes(scene.node_tree)
    look.clear_post(scene)
    assert not scene.use_nodes
    scene[look._BACKUP['active']] = False
    scene.use_nodes = True
    tree = scene.node_tree
    tree.nodes.clear()
    render = tree.nodes.new('CompositorNodeRLayers')
    out = tree.nodes.new('CompositorNodeComposite')
    tree.links.new(render.outputs['Image'], out.inputs['Image'])
    scene[look._BACKUP['use_nodes']] = True
    defaults = dict(grade_on=True, brightness=1., contrast=1., saturation=1.,
                    tint=(1., 1., 1.), tint_amount=0.,
                    env_adjust_on=False, env_brightness=1., env_contrast=1., env_saturation=1.,
                    glow_on=False, glow_intensity=0., glow_bloom=0., glow_strength=1.,
                    glow_threshold=1., glow_blend='SCREEN', display_space=True,
                    mask_on=False, mask_round=True, mask_center=(.5, .5), mask_radius=.25,
                    mask_size=(.45, .35), mask_feather=.08, mask_invert=False)
    cases = [
        ('neutral', {}),
        ('contrast-preserves-shadows', dict(contrast=1.65)),
        ('zero-brightness', dict(brightness=0.)),
        ('zero-contrast', dict(contrast=0.)),
        ('rec709-desaturation', dict(saturation=0.)),
        ('oversaturation-and-tint', dict(saturation=1.75, tint=(.6, .2, .95), tint_amount=.7)),
        ('both-grades', dict(env_adjust_on=True, env_brightness=.85, env_contrast=1.3,
                            env_saturation=.25, brightness=1.2, contrast=.8, saturation=1.6)),
        ('round-offcenter-feather', dict(mask_on=True, mask_center=(.3, .65), brightness=0.)),
        ('rectangle-offcenter-feather', dict(mask_on=True, mask_round=False,
                                           mask_center=(.7, .3), brightness=0.)),
        ('rectangle-invert', dict(mask_on=True, mask_round=False, mask_invert=True, brightness=0.)),
        ('exposure-before-grade', dict(brightness=.7, contrast=1.2, exposure=1.)),
        ('zero-glow-strength', dict(glow_on=True, glow_intensity=1., glow_strength=0., glow_threshold=0.)),
        ('zero-glow-intensity', dict(glow_on=True, glow_intensity=0., glow_strength=2., glow_threshold=0.)),
        ('grade-runs-after-glow', dict(glow_on=True, glow_intensity=1., glow_strength=2.,
                                     glow_threshold=0., brightness=0.)),
    ]
    with tempfile.TemporaryDirectory(prefix='gk_post_pixels_') as folder:
        for name, changes in cases:
            p = dict(defaults, **changes)
            scene.view_settings.exposure = p.get('exposure', 0.)
            width, height = scene.render.resolution_x, scene.render.resolution_y
            pixels = []
            for y in range(height):
                for x in range(width):
                    pixels.extend(((x / (width - 1)) ** 2, (y / (height - 1)) ** 2,
                                   ((x + y) % 13) / 20, 1.))
            source = bpy.data.images.new('Post source', width=width, height=height, float_buffer=True)
            source.colorspace_settings.name = 'Linear Rec.709'
            source.pixels[:] = pixels
            source.update()
            assert look.build_post(scene, p) == ''
            node = tree.nodes.new('CompositorNodeImage')
            node.image = source
            for link in list(render.outputs['Image'].links):
                tree.links.new(node.outputs['Image'], link.to_socket)
            scene.render.filepath = os.path.join(folder, name + '.exr')
            bpy.ops.render.render(write_still=True)
            result = bpy.data.images.load(scene.render.filepath, check_existing=False)
            actual = list(result.pixels)
            errors = []
            exposure = 2 ** scene.view_settings.exposure
            for y in range(height):
                for x in range(width):
                    i = (y * width + x) * 4
                    expected = oracle(pixels[i:i+3], x, y, width, height, p, exposure)
                    errors.extend(abs(a - b) for a, b in zip(actual[i:i+3], expected))
            error = max(errors)
            print(f'POST PIXELS {name}: max error={error:.7f}', flush=True)
            if error >= .0007:
                worst = errors.index(error) // 3
                x, y = worst % width, worst // width
                i = worst * 4
                print('WORST', x, y, pixels[i:i+3], actual[i:i+3],
                      oracle(pixels[i:i+3], x, y, width, height, p, exposure), flush=True)
            assert error < .0007, (name, error)
            tree.nodes.remove(node)
            bpy.data.images.remove(source)
            bpy.data.images.remove(result)
            look.clear_post(scene)
            assert out.inputs['Image'].is_linked
            assert out.inputs['Image'].links[0].from_node == render
    # An existing but disabled tree must actually be enabled when applying.
    scene.use_nodes = False
    scene[look._BACKUP['use_nodes']] = False
    assert look.build_post(scene, defaults) == '' and scene.use_nodes
    look.clear_post(scene)
    assert not scene.use_nodes and out.inputs['Image'].is_linked
    extra_render = tree.nodes.new('CompositorNodeRLayers')
    assert look.build_post(scene, defaults) == 'busy'
    assert not scene.use_nodes
    tree.nodes.remove(extra_render)
    # A user's connected compositor is preserved even if it is currently off.
    foreign = tree.nodes.new('CompositorNodeInvert')
    tree.links.new(render.outputs['Image'], foreign.inputs['Color'])
    tree.links.new(foreign.outputs['Color'], out.inputs['Image'])
    assert look.build_post(scene, defaults) == 'busy'
    look.clear_post(scene)
    assert not scene.use_nodes and out.inputs['Image'].links[0].from_node == foreign
    # Actual graph API: disconnected grades cannot affect exported or previewed
    # results; settings BCS and a connected grade are independent stages.
    import addon_utils
    addon_utils.enable('BlenderGameKit', default_set=False, persistent=False)
    settings = scene.gk_settings
    settings.env_enabled = True
    settings.env_adjust = True
    settings.env_brightness = 0.
    settings.env_contrast = 0.
    settings.env_saturation = 0.
    settings.env_glow = True
    settings.env_glow_strength = 0.
    settings.env_glow_threshold = 0.
    env = bpy.data.node_groups.new('Post graph regression', 'GKEnvLibraryTree')
    start = env.nodes.new('GKNodeEnvDefault')
    disconnected = env.nodes.new('GKNodeEnvGrade')
    disconnected.gk_brightness = 4.
    connected = env.nodes.new('GKNodeEnvGrade')
    connected.gk_brightness = 0.
    connected.gk_contrast = 0.
    connected.gk_saturation = 0.
    link = env.links.new(start.outputs['执行'], connected.inputs['执行'])
    values = look.post_values(scene)
    assert values['grade_on'] and values['env_adjust_on']
    assert all(values[key] == 0 for key in ('brightness', 'contrast', 'saturation',
                                          'env_brightness', 'env_contrast', 'env_saturation',
                                          'glow_strength', 'glow_threshold'))
    env.links.remove(link)
    assert not look.post_values(scene)['grade_on']
    settings.env_enabled = False
    values = look.post_values(scene)
    assert not values['env_adjust_on'] and not values['glow_on']
    bpy.data.node_groups.remove(env)
    print('POST PIXELS: all tests passed', flush=True)


if __name__ == '__main__':
    main()
