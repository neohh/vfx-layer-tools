bl_info = {
    "name": "VFX Layer Tools",
    "author": "VFX Pipeline",
    "version": (2, 4, 0),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > VFX",
    "description": "VFX layer / scene / compositing manager",
    "category": "Compositing",
}

VFX_VERSION = "2.4.0"

import bpy
import gc
import os
import shutil
from bpy.props import (
    StringProperty,
    BoolProperty,
    IntProperty,
    FloatProperty,
    FloatVectorProperty,
    EnumProperty,
    CollectionProperty,
    PointerProperty,
)


# ---------------------------------------------------------------------
# CALLBACKS
# ---------------------------------------------------------------------

def _trigger_rebuild(context):
    try:
        from .materials import _trigger_rebuild as _impl
        _impl(context)
    except Exception:
        pass


def _trigger_comp(context):
    try:
        from .materials import _trigger_comp as _impl
        _impl(context)
    except Exception:
        pass


def _update_mist(context):
    try:
        from .compositor import _update_mist as _impl
        _impl(context)
    except Exception:
        pass


def _auto_mask(ctx, source):
    try:
        vfx = ctx.scene.vfx
        if vfx.use_mask:
            vfx.mask_source = source
    except Exception:
        pass


def _fog_changed(ctx):
    _auto_mask(ctx, 'FOG')
    _trigger_comp(ctx)


def _blur_changed(ctx):
    _auto_mask(ctx, 'BLUR')
    _trigger_comp(ctx)


def _dof_changed(ctx):
    _auto_mask(ctx, 'DOF')
    _trigger_comp(ctx)


# ---------------------------------------------------------------------
# ENGINE ITEMS
# ---------------------------------------------------------------------

def _engine_label(i):
    return i.replace("BLENDER_", "").replace("_", " ").title()


def _engine_items_eevee_first(self, context):
    ids = [i.identifier for i in bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items]
    if "CYCLES" not in ids:
        ids = ["CYCLES"] + ids
    pref = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in ids else (ids[0] if ids else "CYCLES")
    ordered = [pref] + [i for i in ids if i != pref]
    return [(i, _engine_label(i), "") for i in ordered]


def _engine_items_cycles_first(self, context):
    ids = [i.identifier for i in bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items]
    if "CYCLES" not in ids:
        ids = ["CYCLES"] + ids
    ordered = ["CYCLES"] + [i for i in ids if i != "CYCLES"]
    return [(i, _engine_label(i), "") for i in ordered]


# ---------------------------------------------------------------------
# PROPERTIES
# ---------------------------------------------------------------------

class VFXLayer(bpy.types.PropertyGroup):
    id: StringProperty(default="")
    layer_name: StringProperty(default="Layer")
    enabled: BoolProperty(default=True)

    collection: PointerProperty(type=bpy.types.Collection)
    scene: PointerProperty(type=bpy.types.Scene)

    use_shadow: BoolProperty(default=False)
    shadow_mode: EnumProperty(
        name="Shadow Mode",
        items=(
            ('CAST', "Cast", "Layer objects cast shadow onto catcher"),
            ('RECEIVE', "Receive", "Layer receives shadows from other layers"),
        ),
        default='CAST'
    )
    shadow_scene: PointerProperty(type=bpy.types.Scene)
    shadow_catcher: PointerProperty(type=bpy.types.Object)

    shadow_cast_collection: PointerProperty(type=bpy.types.Collection)
    shadow_catch_collection: PointerProperty(type=bpy.types.Collection)

    use_adjust: BoolProperty(
        name="Adjust Material (viewport)",
        default=False,
        update=lambda self, ctx: _trigger_rebuild(ctx)
    )
    exposure: FloatProperty(
        name="Exposure", default=1.0, min=0.0, max=5.0,
        update=lambda self, ctx: _trigger_rebuild(ctx)
    )
    contrast: FloatProperty(
        name="Contrast", default=1.0, min=0.0, max=3.0,
        update=lambda self, ctx: _trigger_rebuild(ctx)
    )
    saturation: FloatProperty(
        name="Saturation", default=1.0, min=0.0, max=3.0,
        update=lambda self, ctx: _trigger_rebuild(ctx)
    )
    tint_strength: FloatProperty(
        name="Tint Strength", default=0.0, min=0.0, max=1.0,
        update=lambda self, ctx: _trigger_rebuild(ctx)
    )
    tint_color: FloatVectorProperty(
        name="Tint", subtype='COLOR', size=4,
        default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0,
        update=lambda self, ctx: _trigger_rebuild(ctx)
    )
    shadow_strength: FloatProperty(
        name="Shadow Strength", default=1.0, min=0.0, max=1.0,
        update=lambda self, ctx: _trigger_comp(ctx)
    )
    fog_factor: FloatProperty(
        name="Fog x(layer)", default=1.0, min=0.0, max=2.0,
        description="Per-layer multiplier: 0 = off, 1 = normal, 2 = double",
        update=lambda self, ctx: _trigger_comp(ctx)
    )
    expanded: BoolProperty(
        name="Expanded",
        description="Show shadow pass sub-row",
        default=False
    )


class VFXProject(bpy.types.PropertyGroup):
    master_scene: PointerProperty(type=bpy.types.Scene)

    layers: CollectionProperty(type=VFXLayer)
    active_layer_index: IntProperty(default=0)

    new_layer_name: StringProperty(default="Layer")
    include_children: BoolProperty(default=True, name="Include Children")

    output_dir: StringProperty(default="//VFX_render/", subtype='DIR_PATH')

    objects_engine: EnumProperty(
        name="Objects engine",
        items=_engine_items_eevee_first,
        description="Engine for object layer scenes"
    )
    shadows_engine: EnumProperty(
        name="Shadows engine",
        items=_engine_items_cycles_first,
        description="Engine for shadow pass scenes"
    )
    sync_world: BoolProperty(
        name="World as master",
        description="Use master scene world in layer scenes by default",
        default=True
    )
    render_running: BoolProperty(default=False)
    render_progress: FloatProperty(default=0.0, min=0.0, max=1.0)
    render_status: StringProperty(default="")
    comp_mode: EnumProperty(
        name="Comp Source",
        items=(
            ('LIVE', "Live (Render Layers)", "Comp uses Render Layers nodes"),
            ('FILES', "From Files (EXR)", "Comp uses Image Sequence nodes from EXR"),
        ),
        default='FILES'
    )
    bg_scene: PointerProperty(
        type=bpy.types.Scene,
        name="Background scene",
        description="World-only background pass, always bottom of comp"
    )
    fog_map_scene: PointerProperty(
        type=bpy.types.Scene,
        name="Fog map scene",
        description="Unified live mist map of the whole scene"
    )
    use_fog: BoolProperty(
        name="Fog (Mist pass)",
        description="Unified live fog over the whole scene",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    mist_start: FloatProperty(
        name="Mist Start", default=5.0, min=0.0,
        update=lambda s, c: _update_mist(c)
    )
    mist_depth: FloatProperty(
        name="Mist Depth", default=50.0, min=0.1,
        update=lambda s, c: _update_mist(c)
    )
    fog_strength: FloatProperty(
        name="Density (global)", default=0.0, min=0.0, max=1.0,
        description="Overall fog density, multiplies every layer",
        update=lambda s, c: _fog_changed(c)
    )
    fog_color: FloatVectorProperty(
        name="Fog Color", subtype='COLOR', size=4,
        default=(0.7, 0.75, 0.85, 1.0), min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    ramp_black: FloatProperty(
        name="Ramp Black", default=0.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    ramp_white: FloatProperty(
        name="Ramp White", default=1.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    use_mask: BoolProperty(
        name="Show Mask",
        description="Show effect mask in viewer instead of final composite",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    mask_source: EnumProperty(
        name="Mask Source",
        items=(
            ('FOG', "Fog Mask", "Show fog density mask"),
            ('BLUR', "Blur Mask", "Show atmospheric blur mask"),
            ('DOF', "Depth Mask", "Show depth / Z pass"),
        ),
        default='FOG',
        update=lambda s, c: _trigger_comp(c)
    )
    fog_expanded: BoolProperty(
        name="Fog settings expanded",
        default=False
    )
    bg_fog_factor: FloatProperty(
        name="BG Fog", default=1.0, min=0.0, max=2.0,
        description="Fog amount on the background layer",
        update=lambda s, c: _trigger_comp(c)
    )
    use_glare: BoolProperty(
        name="Glare / Bloom",
        description="Add glare (bloom, streaks...) after fog",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    glare_type: EnumProperty(
        name="Glare Type",
        items=(
            ('BLOOM', "Bloom", "Soft bloom"),
            ('FOG_GLOW', "Fog Glow", "Wide soft glow"),
            ('STREAKS', "Streaks", "Anamorphic streaks"),
            ('GHOSTS', "Ghosts", "Lens ghosts"),
        ),
        default='BLOOM',
        update=lambda s, c: _trigger_comp(c)
    )
    glare_strength: FloatProperty(
        name="Glare Strength", default=0.3, min=0.0, max=5.0,
        update=lambda s, c: _trigger_comp(c)
    )
    glare_threshold: FloatProperty(
        name="Threshold", default=1.0, min=0.0, max=10.0,
        description="Only pixels brighter than this glow (HDR)",
        update=lambda s, c: _trigger_comp(c)
    )
    glare_size: FloatProperty(
        name="Glare Size", default=0.5, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    use_blur: BoolProperty(
        name="Atmospheric Blur (mist)",
        description="Far = more blur, artistic depth haze",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    blur_size: FloatProperty(
        name="Blur Size (px)", default=8.0, min=0.0, max=100.0,
        update=lambda s, c: _blur_changed(c)
    )
    blur_ramp_black: FloatProperty(
        name="Blur Black", default=0.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    blur_ramp_white: FloatProperty(
        name="Blur White", default=1.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    use_dof: BoolProperty(
        name="Camera Focus (DOF)",
        description="Physical depth of field: sharp at focus, blurred near and far",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    dof_fstop: FloatProperty(
        name="F-Stop", default=2.8, min=0.1, max=32.0,
        update=lambda s, c: _dof_changed(c)
    )
    dof_focus: FloatProperty(
        name="Focus Distance (m)", default=10.0, min=0.0, max=500.0,
        update=lambda s, c: _dof_changed(c)
    )
    dof_maxblur: FloatProperty(
        name="Max Blur (px)", default=12.0, min=0.0, max=100.0,
        update=lambda s, c: _dof_changed(c)
    )
    use_lensdist: BoolProperty(
        name="Lens Distortion",
        description="Barrel/pincushion like a real lens",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    lensdist_distort: FloatProperty(
        name="Distort", default=0.02, min=-1.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    lensdist_disperse: FloatProperty(
        name="Disperse", default=0.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )

    # Cryptomatte
    use_cryptomatte: BoolProperty(
        name="Cryptomatte",
        description="Enable Cryptomatte Object + Material passes for masking",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )

    # Color Match / Plate Matching
    use_color_match: BoolProperty(
        name="Color Match",
        description="Enable color correction (plate matching)",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    color_match_preset: EnumProperty(
        name="Preset",
        items=(
            ('NONE', "Off", "No color correction"),
            ('WARM', "Warm", "Add warmth to the image"),
            ('TEAL_ORANGE', "Teal & Orange", "Cinematic teal/orange look"),
            ('COOL', "Cool", "Cool blue tones"),
            ('FILM', "Film", "Desaturated film look"),
        ),
        default='NONE',
        update=lambda s, c: _trigger_comp(c)
    )
    color_match_strength: FloatProperty(
        name="Strength", default=1.0, min=0.0, max=2.0,
        description="Blend between original and color-corrected",
        update=lambda s, c: _trigger_comp(c)
    )

    # Light Groups
    use_light_groups: BoolProperty(
        name="Light Groups",
        description="Enable light group passes for per-light control in comp",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )

    # Cryptomatte
    use_cryptomatte: BoolProperty(
        name="Cryptomatte",
        description="Enable Cryptomatte Object + Material passes for masking",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )

    # Color Match / Plate Matching
    use_color_match: BoolProperty(
        name="Color Match",
        description="Enable color correction (plate matching)",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    color_match_preset: EnumProperty(
        name="Preset",
        items=(
            ('NONE', "Off", "No color correction"),
            ('WARM', "Warm", "Add warmth to the image"),
            ('TEAL_ORANGE', "Teal & Orange", "Cinematic teal/orange look"),
            ('COOL', "Cool", "Cool blue tones"),
            ('FILM', "Film", "Desaturated film look"),
        ),
        default='NONE',
        update=lambda s, c: _trigger_comp(c)
    )
    color_match_strength: FloatProperty(
        name="Strength", default=1.0, min=0.0, max=2.0,
        description="Blend between original and color-corrected",
        update=lambda s, c: _trigger_comp(c)
    )

    # Light Groups
    use_light_groups: BoolProperty(
        name="Light Groups",
        description="Enable light group passes for per-light control in comp",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )


# ---------------------------------------------------------------------
# SUBMODULE IMPORTS
# ---------------------------------------------------------------------

from .core import get_project, active_layer, sync_scene_settings, ensure_root
from .shadow import set_shadow_catcher, refresh_shadow_proxies, repair_shadow_proxies
from .compositor import (
    get_comp_tree, rebuild_comp, rebuild_comp_from_files,
    build_comp_assembly, _setup_fog_passes, _ensure_fogmap,
    _cleanup_fog_nodes, _get_mist_socket,
)
from .materials import _last_adjust_stats
from .operators import (
    VFX_OT_set_master, VFX_OT_create_layer,
    VFX_OT_add_selected_to_layer, VFX_OT_remove_selected_from_layer,
    VFX_OT_add_selected_lights, VFX_OT_create_shadow_pass,
    VFX_OT_delete_layer, VFX_OT_rename_layer, VFX_OT_reset_lighting,
    VFX_OT_toggle_layer_expand, VFX_OT_toggle_fog_expand,
    VFX_OT_drag_layer, VFX_OT_move_layer_up, VFX_OT_move_layer_down,
    VFX_OT_debug_layer, VFX_OT_rebuild_comp,
    VFX_OT_render_all_layers, VFX_OT_one_click_exr,
    VFX_OT_create_background, VFX_OT_delete_background,
    VFX_OT_delete_shadow_pass, VFX_OT_refresh_proxies,
    VFX_OT_diagnostic,
)
from .ui import (
    VFX_UL_layers, VFX_PT_main, VFX_PT_post_effects,
    VFX_PT_compositor, VFX_PT_compositor_effects,
)
from .cryptomatte import setup_cryptomatte_for_layers, add_cryptomatte_nodes
from .colormatch import get_or_create_color_match_group, apply_preset
from .lightgroups import (
    auto_assign_light_groups, enable_light_groups_on_view_layer,
    add_light_group_output_nodes,
)
from .cryptomatte import setup_cryptomatte_for_layers, add_cryptomatte_nodes
from .colormatch import get_or_create_color_match_group, apply_preset
from .lightgroups import (
    auto_assign_light_groups, enable_light_groups_on_view_layer,
    add_light_group_output_nodes,
)


# ---------------------------------------------------------------------
# REGISTER / UNREGISTER
# ---------------------------------------------------------------------

classes = (
    VFXLayer,
    VFXProject,
    VFX_UL_layers,
    VFX_OT_set_master,
    VFX_OT_create_layer,
    VFX_OT_add_selected_to_layer,
    VFX_OT_remove_selected_from_layer,
    VFX_OT_add_selected_lights,
    VFX_OT_create_shadow_pass,
    VFX_OT_delete_layer,
    VFX_OT_rename_layer,
    VFX_OT_reset_lighting,
    VFX_OT_toggle_layer_expand,
    VFX_OT_toggle_fog_expand,
    VFX_OT_drag_layer,
    VFX_OT_move_layer_up,
    VFX_OT_move_layer_down,
    VFX_OT_debug_layer,
    VFX_OT_rebuild_comp,
    VFX_OT_render_all_layers,
    VFX_OT_one_click_exr,
    VFX_OT_create_background,
    VFX_OT_delete_background,
    VFX_OT_delete_shadow_pass,
    VFX_OT_refresh_proxies,
    VFX_OT_diagnostic,
    VFX_OT_setup_light_groups,
    VFX_OT_apply_color_preset,
    VFX_OT_setup_light_groups,
    VFX_OT_apply_color_preset,
    VFX_PT_main,
    VFX_PT_post_effects,
    VFX_PT_compositor,
    VFX_PT_compositor_effects,
)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as exc:
            print(f"VFX register ERROR {cls.__name__}: {exc}")

    bpy.types.Scene.vfx = PointerProperty(type=VFXProject)
    print(f"VFX Layer Tools v{VFX_VERSION} registered ({len(classes)} classes)")


def unregister():
    # Remove Scene.vfx
    if hasattr(bpy.types.Scene, "vfx"):
        try:
            del bpy.types.Scene.vfx
        except Exception:
            pass

    # Unregister classes in reverse order
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

    # Clean up __pycache__ so Windows can update the extension directory
    gc.collect()
    try:
        pycache = os.path.join(os.path.dirname(__file__), "__pycache__")
        if os.path.isdir(pycache):
            shutil.rmtree(pycache, ignore_errors=True)
    except Exception:
        pass

    print("VFX Layer Tools unregistered")


if __name__ == "__main__":
    register()
