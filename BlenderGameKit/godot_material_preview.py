"""Reversible EEVEE approximation of the exported scene-lit toon materials.

The exporter replaces ordinary Principled materials with ``gk_toon_lit``.
That shader has no PBR metallic reflection, roughness or implicit emission.
Use the same exported base colour and texture for a diffuse-only preview.
EEVEE cannot expose each light's N.L to material nodes: the per-light toon
bands and half-vector specular remain runtime details of this approximation.
The view-dependent rim uses a separate diffuse lobe, which lets EEVEE apply
each light's direction, attenuation and shadow to the same Godot rim formula.
Godot's constant ambient term is added explicitly, since EEVEE does not light
diffuse materials from the world without an irradiance volume. While using
these materials, the preview world must supply no additional ambient light.

Only object material overrides are changed. Mesh materials and source node
trees remain untouched. Save and glTF export callers must use ``suspend``.
"""

from contextlib import contextmanager
from array import array

import bpy

from . import npr_preview


SOURCE_KEY = "gk_look_source_material"
PREVIEW_KEY = "gk_look_material_preview"
IMAGE_KEY = "gk_look_image_preview"
_SLOTS = globals().get("_SLOTS", {})
_SCENES = globals().get("_SCENES", {})
_MATERIALS = globals().get("_MATERIALS", [])
_IMAGES = globals().get("_IMAGES", [])
_SUSPEND_DEPTH = globals().get("_SUSPEND_DEPTH", 0)


def resolve_material(material):
    """Return the authored material even while an object shows its preview."""
    if material is None:
        return None
    try:
        original = material.get(SOURCE_KEY)
        if isinstance(original, bpy.types.Material):
            return original
    except (ReferenceError, TypeError):
        pass
    return material


source_material = resolve_material


def _pointer(data):
    try:
        return data.as_pointer()
    except ReferenceError:
        return 0


def _enabled(scene):
    settings = getattr(scene, "gk_settings", None)
    return bool(settings and getattr(settings, "toon_materials", True)
                and getattr(settings, "toon_follow_light", False))


def _srgb(value):
    value = max(0.0, float(value))
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _texture_image(image):
    """Godot's source_color sampler decodes sRGB, independently of Blender."""
    if image.colorspace_settings.name == "sRGB":
        return image
    pixels = None
    if image.source == "GENERATED" or image.is_dirty:
        # Blender's Image.copy() does not retain a generated/painted pixel
        # buffer, and changing its colour space may reload an empty image.
        pixels = array("f", [0.0]) * len(image.pixels)
        image.pixels.foreach_get(pixels)
    duplicate = image.copy()
    duplicate.name = "__GK Look Texture · " + image.name
    duplicate[IMAGE_KEY] = True
    duplicate.use_fake_user = False
    duplicate.colorspace_settings.name = "sRGB"
    if pixels is not None:
        duplicate.pixels.foreach_set(pixels)
        duplicate.update()
    _IMAGES.append(duplicate)
    return duplicate


def _add_rim(nodes, links, shader, amount):
    """Godot rim(N.V) * light's diffuse irradiance, without albedo tinting."""
    facing = nodes.new("ShaderNodeLayerWeight")
    facing.name = "Godot 边缘视角"
    facing.location = (-760, -460)
    # At 0.5 Facing is exactly 1 - abs(N.V), with no power remapping.
    facing.inputs["Blend"].default_value = 0.5
    band = nodes.new("ShaderNodeMapRange")
    band.location = (-540, -460)
    band.interpolation_type = "SMOOTHSTEP"
    band.clamp = True
    band.inputs["From Min"].default_value = 0.0
    band.inputs["From Max"].default_value = 1.0 - 0.55  # shader rim_width
    band.inputs["To Min"].default_value = 0.0
    band.inputs["To Max"].default_value = 1.0
    links.new(facing.outputs["Facing"], band.inputs["Value"])
    front = nodes.new("ShaderNodeMath")
    front.operation = "MULTIPLY"
    front.location = (-540, -680)
    front.inputs[1].default_value = 0.15  # shader rim_front
    links.new(facing.outputs["Facing"], front.inputs[0])
    rim = nodes.new("ShaderNodeMath")
    rim.operation = "ADD"
    rim.use_clamp = True
    rim.location = (-320, -460)
    links.new(band.outputs["Result"], rim.inputs[0])
    links.new(front.outputs[0], rim.inputs[1])
    tint = nodes.new("ShaderNodeMixRGB")
    tint.blend_type = "MULTIPLY"
    tint.location = (-100, -460)
    tint.inputs[0].default_value = 1.0
    tint.inputs[2].default_value = tuple(_srgb(v) * amount
                                       for v in (0.75, 0.85, 1.0)) + (1.0,)
    links.new(rim.outputs[0], tint.inputs[1])
    diffuse = nodes.new("ShaderNodeBsdfDiffuse")
    diffuse.name = "Godot 受光边缘光"
    diffuse.location = (180, -460)
    diffuse.inputs["Roughness"].default_value = 0.0
    links.new(tint.outputs[0], diffuse.inputs["Color"])
    add = nodes.new("ShaderNodeAddShader")
    add.location = (400, -240)
    links.new(shader, add.inputs[0])
    links.new(diffuse.outputs["BSDF"], add.inputs[1])
    return add.outputs[0]


def _build_material(source, settings):
    # Import lazily: asset_export already imports godot_look at module load.
    from . import asset_export

    base = asset_export._base_color_rgb(source)
    texture_name = asset_export._base_color_texture(source)
    style = asset_export._scene_toon_style_fields(settings)
    material = source.copy()
    material.name = "__GK Look Material · " + source.name
    material[PREVIEW_KEY] = True
    material[SOURCE_KEY] = source
    material["gk_look_style"] = tuple(float(style[key]) for key in
                                     ("shadow_step", "three_tone", "specular", "rim"))
    material.use_fake_user = False
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (620, 0)
    diffuse = nodes.new("ShaderNodeBsdfDiffuse")
    diffuse.name = "Godot 漫反射近似"
    diffuse.label = "Godot 漫反射（逐灯色阶在运行时显示）"
    diffuse.location = (180, 0)
    diffuse.inputs["Roughness"].default_value = 0.0

    # The ordinary-material template uses this fixed source_color lit_tint.
    # base is already linear; write_materials converts it to sRGB on export
    # and Godot decodes it again, so a second conversion here would darken it.
    lit_tint = tuple(_srgb(value) for value in (1.0, 0.976, 0.914))
    colour = tuple(float(base[index]) * lit_tint[index] for index in range(3))
    diffuse.inputs["Color"].default_value = colour + (1.0,)
    material.diffuse_color = colour + (1.0,)
    shader = diffuse.outputs["BSDF"]
    texture = None

    image = bpy.data.images.get(texture_name) if texture_name else None
    if image is not None:
        uv = nodes.new("ShaderNodeTexCoord")
        uv.location = (-760, 80)
        texture = nodes.new("ShaderNodeTexImage")
        texture.location = (-560, 80)
        texture.image = _texture_image(image)
        texture.interpolation = "Linear"
        texture.extension = "REPEAT"
        links.new(uv.outputs["UV"], texture.inputs["Vector"])
        multiply = nodes.new("ShaderNodeMixRGB")
        multiply.location = (-100, 80)
        multiply.blend_type = "MULTIPLY"
        multiply.inputs[0].default_value = 1.0
        multiply.inputs[2].default_value = colour + (1.0,)
        links.new(texture.outputs["Color"], multiply.inputs[1])
        links.new(multiply.outputs[0], diffuse.inputs["Color"])

    ambient_energy = (max(0.0, float(getattr(settings, "env_ambient_energy", 0.0)))
                      if getattr(settings, "env_enabled", False) else 0.0)
    if ambient_energy > 0.0:
        # Godot ambient = ALBEDO * environment colour * environment energy.
        # Both inputs were converted to sRGB when exported and are decoded by
        # Godot, so use their original linear values here. lit_tint belongs to
        # light() and must not tint this independent ambient contribution.
        ambient_rgb = getattr(settings, "env_ambient_color", (0.0, 0.0, 0.0))
        ambient_colour = tuple(float(base[index]) * float(ambient_rgb[index])
                               * ambient_energy for index in range(3)) + (1.0,)
        ambient = nodes.new("ShaderNodeEmission")
        ambient.name = "Godot 环境补光"
        ambient.label = "与 Godot 一致的恒定环境补光"
        ambient.location = (180, 240)
        ambient.inputs["Color"].default_value = ambient_colour
        ambient.inputs["Strength"].default_value = 1.0
        if texture is not None:
            ambient_texture = nodes.new("ShaderNodeMixRGB")
            ambient_texture.location = (-100, 320)
            ambient_texture.blend_type = "MULTIPLY"
            ambient_texture.inputs[0].default_value = 1.0
            ambient_texture.inputs[2].default_value = ambient_colour
            links.new(texture.outputs["Color"], ambient_texture.inputs[1])
            links.new(ambient_texture.outputs[0], ambient.inputs["Color"])
        add = nodes.new("ShaderNodeAddShader")
        add.location = (400, 120)
        links.new(shader, add.inputs[0])
        links.new(ambient.outputs["Emission"], add.inputs[1])
        shader = add.outputs[0]

    rim_amount = max(0.0, float(style["rim"]))
    if rim_amount > 0.001:
        shader = _add_rim(nodes, links, shader, rim_amount)

    if texture is not None:
        # gk_toon_lit discards texture alpha below the template's 0.5 cutoff.
        cutout = nodes.new("ShaderNodeMath")
        cutout.operation = "LESS_THAN"
        cutout.location = (-100, -200)
        cutout.inputs[1].default_value = 0.5
        links.new(texture.outputs["Alpha"], cutout.inputs[0])
        transparent = nodes.new("ShaderNodeBsdfTransparent")
        transparent.location = (180, -200)
        mix = nodes.new("ShaderNodeMixShader")
        mix.location = (400, 0)
        links.new(cutout.outputs[0], mix.inputs[0])
        links.new(shader, mix.inputs[1])
        links.new(transparent.outputs[0], mix.inputs[2])
        shader = mix.outputs[0]
        if hasattr(material, "surface_render_method"):
            material.surface_render_method = "DITHERED"
        elif hasattr(material, "blend_method"):
            material.blend_method = "CLIP"
            material.alpha_threshold = 0.5

    links.new(shader, output.inputs["Surface"])
    _MATERIALS.append(material)
    return material


def _restore_slot(key):
    record = _SLOTS.pop(key, None)
    if record is None:
        return
    obj, index, old_link, old_override, preview, _scene = record
    try:
        if index >= len(obj.material_slots):
            return
        slot = obj.material_slots[index]
        # If the user deliberately replaced the displayed material, preserve it.
        if slot.link != "OBJECT" or slot.material != preview:
            return
        slot.material = old_override
        slot.link = old_link
    except (ReferenceError, RuntimeError):
        pass


def _cleanup():
    # Object/material/image duplication and undo can create datablocks that
    # were not registered in these Python lists. The explicit marker, rather
    # than a name prefix, identifies copies owned by this preview feature.
    for collection, marker, tracked in (
            (getattr(bpy.data, "materials", ()), PREVIEW_KEY, _MATERIALS),
            (getattr(bpy.data, "images", ()), IMAGE_KEY, _IMAGES)):
        try:
            candidates = list(collection)
        except TypeError:
            # Offline export tests expose only the collection methods their
            # path needs; they do not contain real preview datablocks.
            candidates = ()
        for data in candidates:
            if data.get(marker) and not data.users:
                collection.remove(data)
        tracked[:] = [data for data in tracked if _pointer(data)]


def _restore_untracked(objects):
    """Recover temporary overrides inherited by Shift+D / object.copy()."""
    for obj in objects:
        for index, slot in enumerate(obj.material_slots):
            if (_pointer(obj), index) in _SLOTS:
                continue
            material = slot.material
            if material is None or not material.get(PREVIEW_KEY):
                continue
            source = resolve_material(material)
            if source == material:
                continue
            data_materials = getattr(getattr(obj, "data", None), "materials", ())
            uses_data = index < len(data_materials) and data_materials[index] == source
            # Never write through a DATA slot into shared mesh materials.
            slot.link = "OBJECT"
            slot.material = None if uses_data else source
            if uses_data:
                slot.link = "DATA"


def restore(scene):
    """Restore this scene's object slots without writing any mesh material."""
    scene_key = _pointer(scene)
    for key, record in list(_SLOTS.items()):
        if _pointer(record[-1]) == scene_key:
            _restore_slot(key)
    _restore_untracked(scene.objects)
    _SCENES.pop(scene_key, None)
    _cleanup()


def restore_all():
    """Restore all authored slots before saving, exporting or unregistering."""
    for key in list(_SLOTS):
        _restore_slot(key)
    _restore_untracked(getattr(bpy.data, "objects", ()))
    _SCENES.clear()
    _cleanup()


def apply(scene):
    """Apply temporary overrides; return the number of adapted material slots."""
    if _SUSPEND_DEPTH:
        return 0
    restore(scene)
    if not _enabled(scene):
        return 0
    from . import asset_export

    scene_key = _pointer(scene)
    settings = scene.gk_settings
    cache = {}
    count = 0
    try:
        for obj in scene.objects:
            if obj.type != "MESH" or asset_export.is_preview_object(obj):
                continue
            for index in range(len(obj.material_slots)):
                key = (_pointer(obj), index)
                # The same object can belong to multiple scenes. It can show
                # only one scene's preview at a time; always recover its source.
                _restore_slot(key)
                slot = obj.material_slots[index]
                source = resolve_material(slot.material)
                if (source is None or source.name.startswith("__GK")
                        or npr_preview.has_any_param(source)):
                    continue
                source_key = _pointer(source)
                preview = cache.get(source_key)
                if preview is None:
                    preview = _build_material(source, settings)
                    cache[source_key] = preview
                old_link = slot.link
                slot.link = "OBJECT"
                # Preserve even a dormant object override under a DATA slot.
                old_override = slot.material
                _SLOTS[key] = (obj, index, old_link, old_override, preview, scene)
                slot.material = preview
                count += 1
    except Exception:
        restore(scene)
        raise
    _SCENES[scene_key] = scene
    _cleanup()
    return count


@contextmanager
def suspend():
    """Expose authored materials during save/glTF export, then resume preview."""
    global _SUSPEND_DEPTH
    scenes = list(_SCENES.values()) if not _SUSPEND_DEPTH else []
    _SUSPEND_DEPTH += 1
    if _SUSPEND_DEPTH == 1:
        restore_all()
    try:
        yield
    finally:
        _SUSPEND_DEPTH -= 1
        if not _SUSPEND_DEPTH:
            for scene in scenes:
                try:
                    if _pointer(scene):
                        apply(scene)
                except (ReferenceError, RuntimeError):
                    pass
