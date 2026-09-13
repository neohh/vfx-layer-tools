bl_info = {
    "name": "VFX Layer Tools",
    "author": "VFX Pipeline",
    "version": (3, 6, 0),
    "blender": (5, 2, 1),
    "location": "View3D > Sidebar > VFX",
    "description": "VFX layer / scene / compositing manager",
    "category": "Compositing",
}

VFX_VERSION = "3.6.0"

import bpy
import importlib
import os
import sys
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
# CALLBACKS (defined early so PropertyGroup lambdas can reference them)
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
    """Auto-switch mask source when an effect slider is edited."""
    try:
        vfx = ctx.scene.vfx
        if vfx.use_mask:
            vfx.mask_source = source
    except Exception:
        pass


def _fog_changed(ctx):
    _auto_mask(ctx, 'FOG')
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
# MASK SOURCE ITEMS (shared by all masked effects)
# NOTE: must be a static tuple, NOT an items-callback function.
# EnumProperty(items=<function>) requires default to be an int index;
# string defaults like 'NONE' only work with a static items tuple.
# ---------------------------------------------------------------------

_MASK_SOURCE_ITEMS = (
    ('NONE', "None", "No mask: effect acts everywhere"),
    ('EXT', "Ext (node)", "External mask from a named comp node"),
    ('ALPHA', "Alpha", "Mask from the layer alpha (silhouette)"),
    ('DEPTH', "Depth", "Mask by depth (start/end in meters)"),
    ('LUMA', "Luma", "Mask by brightness (lo/hi)"),
)


# ---------------------------------------------------------------------
# PROPERTIES
# ---------------------------------------------------------------------

class VFXOcclusionRef(bpy.types.PropertyGroup):
    """Manual occluder reference: one entry per other layer."""
    layer_id: StringProperty(default="")
    include: BoolProperty(default=True)


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
    use_alpha_mask: BoolProperty(
        name="Use Alpha Mask",
        description="Restrict the grade to the layer silhouette",
        default=True,
        update=lambda self, ctx: _trigger_comp(ctx)
    )
    grade_mask_source: EnumProperty(
        name="Grade Mask Source",
        items=_MASK_SOURCE_ITEMS,
        default='NONE',
        update=lambda self, ctx: _trigger_comp(ctx)
    )
    grade_mask_invert: BoolProperty(
        name="Invert Mask", default=False,
        update=lambda self, ctx: _trigger_comp(ctx)
    )
    grade_mask_soft: FloatProperty(
        name="Softness (px)", default=0.0, min=0.0, max=50.0,
        update=lambda self, ctx: _trigger_comp(ctx)
    )
    grade_mask_depth_start: FloatProperty(
        name="Depth Start (m)", default=0.0, min=0.0, max=1000.0,
        update=lambda self, ctx: _trigger_comp(ctx)
    )
    grade_mask_depth_end: FloatProperty(
        name="Depth End (m)", default=50.0, min=0.1, max=1000.0,
        update=lambda self, ctx: _trigger_comp(ctx)
    )
    grade_mask_luma_lo: FloatProperty(
        name="Luma Lo", default=0.0, min=0.0, max=1.0,
        update=lambda self, ctx: _trigger_comp(ctx)
    )
    grade_mask_luma_hi: FloatProperty(
        name="Luma Hi", default=1.0, min=0.0, max=1.0,
        update=lambda self, ctx: _trigger_comp(ctx)
    )
    grade_mask_ext_node: StringProperty(
        name="Ext Node", default="",
        description="Name of a node in comp whose output is used as mask",
        update=lambda self, ctx: _trigger_comp(ctx)
    )
    expanded: BoolProperty(
        name="Expanded",
        description="Show shadow pass sub-row",
        default=False
    )

    # -- Inter-layer occlusion (holdout) --
    occlusion_mode: EnumProperty(
        name="Occlusion",
        items=(
            ('AUTO', "Auto", "All other layers occlude this one (recommended)"),
            ('MANUAL', "Manual", "Pick occluder layers manually"),
            ('OFF', "Off", "No occlusion: layer always renders on top of nothing"),
        ),
        default='AUTO',
        update=lambda s, c: _trigger_comp(c),
    )
    occlusion_layers: CollectionProperty(type=VFXOcclusionRef)
    occlusion_collection: PointerProperty(type=bpy.types.Collection)
    occlusion_expanded: BoolProperty(
        name="Occlusion expanded",
        description="Show occlusion section of this layer",
        default=False,
    )

    # -- Per-layer grade --
    grade_enable: BoolProperty(
        name="Grade",
        description="Enable per-layer color grading (before FOG)",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    l_exposure: FloatProperty(
        name="Exposure", default=0.0, min=-6.0, max=6.0,
        update=lambda s, c: _trigger_comp(c)
    )
    l_temp: FloatProperty(
        name="Temperature", default=0.0, min=-1.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    l_tint: FloatProperty(
        name="Tint", default=0.0, min=-1.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    l_lift: FloatVectorProperty(
        name="Lift", subtype='COLOR', size=3,
        default=(0.0, 0.0, 0.0), min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    l_gain: FloatVectorProperty(
        name="Gain", subtype='COLOR', size=3,
        default=(1.0, 1.0, 1.0), min=0.0, max=2.0,
        update=lambda s, c: _trigger_comp(c)
    )
    l_sat: FloatProperty(
        name="Saturation", default=1.0, min=0.0, max=2.0,
        update=lambda s, c: _trigger_comp(c)
    )
    l_contrast: FloatProperty(
        name="Contrast", default=1.0, min=0.0, max=2.0,
        update=lambda s, c: _trigger_comp(c)
    )


class VFXProject(bpy.types.PropertyGroup):
    master_scene: PointerProperty(type=bpy.types.Scene)

    composite_sort_mode: EnumProperty(
        name="Comp Order",
        items=(
            ('AUTO', "Auto (by depth)", "Sort layers by camera distance: nearest renders on top"),
            ('MANUAL', "Manual (list order)", "Use the layer list order (drag to change)"),
        ),
        default='AUTO',
        description="How the compositor stacks layers",
        update=lambda s, c: _trigger_comp(c),
    )

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
        description="Overall fog density, scales the shared fog map",
        update=lambda s, c: _fog_changed(c)
    )
    fog_color: FloatVectorProperty(
        name="Fog Color", subtype='COLOR', size=4,
        default=(0.7, 0.75, 0.85, 1.0), min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    ramp_black: FloatProperty(
        name="Fog Start (m)", default=0.0, min=0.0, max=10000.0,
        unit='LENGTH',
        description="Distance from camera where fog starts (meters)",
        update=lambda s, c: _trigger_comp(c)
    )
    ramp_white: FloatProperty(
        name="Fog Full (m)", default=30.0, min=0.0, max=10000.0,
        unit='LENGTH',
        description="Distance where fog reaches full density (meters)",
        update=lambda s, c: _trigger_comp(c)
    )
    comp_order_expanded: BoolProperty(
        name="Composite order expanded",
        description="Show the computed compositing order",
        default=False
    )
    fx_mode: EnumProperty(
        name="Per-layer FX",
        items=(
            ('FOG', "Fog", "Per-layer fog density"),
            ('GRADE', "Grade", "Per-layer color grading"),
            ('SHADOW', "Shadow pass", "Shadow catcher settings"),
            ('ADJUST', "Lighting adjust", "Material exposure/contrast adjust"),
        ),
        default='FOG',
        description="Which per-layer effect settings to show"
    )
    fog_mask_source: EnumProperty(
        name="Fog Mask Source",
        items=_MASK_SOURCE_ITEMS,
        default='NONE',
        update=lambda s, c: _trigger_comp(c)
    )
    fog_mask_invert: BoolProperty(
        name="Invert Mask", default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    fog_mask_soft: FloatProperty(
        name="Softness (px)", default=0.0, min=0.0, max=50.0,
        update=lambda s, c: _trigger_comp(c)
    )
    fog_mask_depth_start: FloatProperty(
        name="Depth Start (m)", default=0.0, min=0.0, max=1000.0,
        update=lambda s, c: _trigger_comp(c)
    )
    fog_mask_depth_end: FloatProperty(
        name="Depth End (m)", default=50.0, min=0.1, max=1000.0,
        update=lambda s, c: _trigger_comp(c)
    )
    fog_mask_luma_lo: FloatProperty(
        name="Luma Lo", default=0.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    fog_mask_luma_hi: FloatProperty(
        name="Luma Hi", default=1.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    fog_mask_ext_node: StringProperty(
        name="Ext Node", default="",
        description="Name of a node in comp whose output is used as mask",
        update=lambda s, c: _trigger_comp(c)
    )
    use_mask: BoolProperty(
        name="Show Mask",
        description="Show effect mask in viewer instead of final composite",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    crypto_pick_name: StringProperty(
        name="Picked Object",
        description="Object picked with the Cryptomatte pipette",
        default=""
    )
    use_color_match: BoolProperty(
        name="Color Match / Plate",
        description="Match colors to a reference plate (presets)",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    color_match_preset: EnumProperty(
        name="Preset",
        items=(
            ('NONE', "None", "No color matching"),
            ('WARM', "Warm", "Warm golden tint"),
            ('TEAL_ORANGE', "Teal & Orange", "Cinematic teal/orange"),
            ('COOL', "Cool", "Cool blue tint"),
            ('FILM', "Film", "Filmic contrast look"),
        ),
        default='NONE',
        update=lambda s, c: _trigger_comp(c)
    )
    color_match_strength: FloatProperty(
        name="Strength", default=1.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    use_light_groups: BoolProperty(
        name="Light Groups",
        description="Auto-assign lights into Key/Fill/Rim/Env groups",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    mask_source: EnumProperty(
        name="Mask Source",
        items=(
            ('FOG', "Fog Mask", "Show fog density mask"),
            ('DOF', "DOF Mask", "Show DOF blur mask"),
            ('GLARE', "Glare Mask", "Show glare mask"),
            ('GRADE', "Grade Mask", "Show master grade mask"),
        ),
        default='FOG',
        update=lambda s, c: _trigger_comp(c)
    )
    fog_expanded: BoolProperty(
        name="Fog settings expanded",
        default=False
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
    glare_mask_source: EnumProperty(
        name="Glare Mask Source",
        items=_MASK_SOURCE_ITEMS,
        default='NONE',
        update=lambda s, c: _trigger_comp(c)
    )
    glare_mask_invert: BoolProperty(
        name="Invert Mask", default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    glare_mask_soft: FloatProperty(
        name="Softness (px)", default=0.0, min=0.0, max=50.0,
        update=lambda s, c: _trigger_comp(c)
    )
    glare_mask_depth_start: FloatProperty(
        name="Depth Start (m)", default=0.0, min=0.0, max=1000.0,
        update=lambda s, c: _trigger_comp(c)
    )
    glare_mask_depth_end: FloatProperty(
        name="Depth End (m)", default=50.0, min=0.1, max=1000.0,
        update=lambda s, c: _trigger_comp(c)
    )
    glare_mask_luma_lo: FloatProperty(
        name="Luma Lo", default=0.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    glare_mask_luma_hi: FloatProperty(
        name="Luma Hi", default=1.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    glare_mask_ext_node: StringProperty(
        name="Ext Node", default="",
        description="Name of a node in comp whose output is used as mask",
        update=lambda s, c: _trigger_comp(c)
    )
    dof_far_start: FloatProperty(
        name="Far Start (m)", default=2.0, min=0.1, max=500.0,
        description="Distance beyond focus where blur begins to grow",
        update=lambda s, c: _dof_changed(c)
    )
    dof_far_end: FloatProperty(
        name="Far End (m)", default=20.0, min=0.2, max=500.0,
        description="Distance beyond focus where blur reaches Max Blur",
        update=lambda s, c: _dof_changed(c)
    )
    use_dof: BoolProperty(
        name="Camera Focus (DOF)",
        description="Sharp at focus, blurred beyond Far Start; ramp-editable in comp",
        default=False,
        update=lambda s, c: _dof_changed(c)
    )
    dof_fstop: FloatProperty(
        name="F-Stop", default=2.8, min=0.1, max=32.0,
        update=lambda s, c: _dof_changed(c)
    )
    dof_maxblur: FloatProperty(
        name="Max Blur", default=0.5, min=0.0, max=1.0,
        description="Maximum blur amount at Far End (0 = sharp)",
        update=lambda s, c: _dof_changed(c)
    )
    dof_focus: FloatProperty(
        name="Focus Distance (m)", default=10.0, min=0.0, max=500.0,
        update=lambda s, c: _dof_changed(c)
    )
    dof_mask_source: EnumProperty(
        name="DOF Mask Source",
        items=_MASK_SOURCE_ITEMS,
        default='NONE',
        update=lambda s, c: _trigger_comp(c)
    )
    dof_mask_invert: BoolProperty(
        name="Invert Mask", default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    dof_mask_soft: FloatProperty(
        name="Softness (px)", default=0.0, min=0.0, max=50.0,
        update=lambda s, c: _trigger_comp(c)
    )
    dof_mask_depth_start: FloatProperty(
        name="Depth Start (m)", default=0.0, min=0.0, max=1000.0,
        update=lambda s, c: _trigger_comp(c)
    )
    dof_mask_depth_end: FloatProperty(
        name="Depth End (m)", default=50.0, min=0.1, max=1000.0,
        update=lambda s, c: _trigger_comp(c)
    )
    dof_mask_luma_lo: FloatProperty(
        name="Luma Lo", default=0.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    dof_mask_luma_hi: FloatProperty(
        name="Luma Hi", default=1.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    dof_mask_ext_node: StringProperty(
        name="Ext Node", default="",
        description="Name of a node in comp whose output is used as mask",
        update=lambda s, c: _trigger_comp(c)
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
    grade_mask_source: EnumProperty(
        name="Grade Mask Source",
        items=_MASK_SOURCE_ITEMS,
        default='NONE',
        update=lambda s, c: _trigger_comp(c)
    )
    grade_mask_invert: BoolProperty(
        name="Invert Mask", default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    grade_mask_soft: FloatProperty(
        name="Softness (px)", default=0.0, min=0.0, max=50.0,
        update=lambda s, c: _trigger_comp(c)
    )
    grade_mask_depth_start: FloatProperty(
        name="Depth Start (m)", default=0.0, min=0.0, max=1000.0,
        update=lambda s, c: _trigger_comp(c)
    )
    grade_mask_depth_end: FloatProperty(
        name="Depth End (m)", default=50.0, min=0.1, max=1000.0,
        update=lambda s, c: _trigger_comp(c)
    )
    grade_mask_luma_lo: FloatProperty(
        name="Luma Lo", default=0.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    grade_mask_luma_hi: FloatProperty(
        name="Luma Hi", default=1.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    grade_mask_ext_node: StringProperty(
        name="Ext Node", default="",
        description="Name of a node in comp whose output is used as mask",
        update=lambda s, c: _trigger_comp(c)
    )

    # -- Master grade --
    m_grade_enable: BoolProperty(
        name="Master Grade",
        description="Enable master color grading (after all post-effects)",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    m_exposure: FloatProperty(
        name="M: Exposure", default=0.0, min=-6.0, max=6.0,
        update=lambda s, c: _trigger_comp(c)
    )
    m_temp: FloatProperty(
        name="M: Temperature", default=0.0, min=-1.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    m_tint: FloatProperty(
        name="M: Tint", default=0.0, min=-1.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    m_lift: FloatVectorProperty(
        name="M: Lift", subtype='COLOR', size=3,
        default=(0.0, 0.0, 0.0), min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    m_gain: FloatVectorProperty(
        name="M: Gain", subtype='COLOR', size=3,
        default=(1.0, 1.0, 1.0), min=0.0, max=2.0,
        update=lambda s, c: _trigger_comp(c)
    )
    m_saturation: FloatProperty(
        name="M: Saturation", default=1.0, min=0.0, max=2.0,
        update=lambda s, c: _trigger_comp(c)
    )
    m_contrast: FloatProperty(
        name="M: Contrast", default=1.0, min=0.0, max=2.0,
        update=lambda s, c: _trigger_comp(c)
    )


# ---------------------------------------------------------------------
# SUBMODULE IMPORTS (after class definitions to avoid circular import)
# ---------------------------------------------------------------------

from .core import get_project, active_layer, sync_scene_settings, ensure_root
from .shadow import set_shadow_catcher, refresh_shadow_proxies, repair_shadow_proxies
from .occlusion import (
    rebuild_all_occlusion, rebuild_layer_occlusion, remove_occlusion_collection,
    compute_composite_order, occlusion_self_check,
)
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
    VFX_OT_refresh_occlusion,
    VFX_OT_diagnostic,
    VFX_OT_setup_light_groups, VFX_OT_apply_color_preset,
    VFX_OT_preview_this_mask, VFX_OT_pick_cryptomatte,
    VFX_OT_reset_layer_grade, VFX_OT_reset_master_grade,
    VFX_OT_copy_master_grade,
)
from .ui import (
    VFX_UL_layers, VFX_PT_main, VFX_PT_post_effects,
    VFX_PT_compositor, VFX_PT_compositor_effects,
    VFX_PT_debug_test,
)
from .colormatch import get_or_create_color_match_group, apply_preset
from .lightgroups import (
    auto_assign_light_groups, enable_light_groups_on_view_layer,
    add_light_group_output_nodes,
)


# ---------------------------------------------------------------------
# REGISTER
# ---------------------------------------------------------------------

classes = (
    VFXOcclusionRef,
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
    VFX_OT_refresh_occlusion,
    VFX_OT_diagnostic,
    VFX_OT_apply_color_preset,
    VFX_OT_preview_this_mask, VFX_OT_pick_cryptomatte,
    VFX_OT_reset_layer_grade,
    VFX_OT_reset_master_grade,
    VFX_OT_copy_master_grade,
    VFX_PT_main,
    VFX_PT_post_effects,
    VFX_PT_compositor,
    VFX_PT_compositor_effects,
    VFX_PT_debug_test,
)

CLASS_NAMES = tuple(cls.__name__ for cls in classes)


# ---------------------------------------------------------------------
# AUTO-RELOAD (dev convenience)
# ---------------------------------------------------------------------

_AUTO_RELOAD_ENABLED = False  # breaks RNA state on edit; was removed in v2.3.0 for this reason
_AUTO_RELOAD_INTERVAL = 2  # seconds
_FILE_TIMESTAMPS = {}
_AUTO_RELOAD_FIRST_RUN = True


def _auto_reload_timer():
    """Check source files for changes; reload changed modules."""
    global _AUTO_RELOAD_FIRST_RUN

    if not _AUTO_RELOAD_ENABLED:
        return None

    addon_dir = os.path.dirname(__file__)
    changed_files = []

    if _AUTO_RELOAD_FIRST_RUN:
        _AUTO_RELOAD_FIRST_RUN = False
        print(f"VFX auto-reload: watching {addon_dir}")

    for filename in os.listdir(addon_dir):
        if not filename.endswith(".py"):
            continue
        filepath = os.path.join(addon_dir, filename)
        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            continue
        if filename in _FILE_TIMESTAMPS and _FILE_TIMESTAMPS[filename] != mtime:
            changed_files.append(filename)
        _FILE_TIMESTAMPS[filename] = mtime

    if changed_files:
        print(f"VFX auto-reload: changed {', '.join(changed_files)}")
        try:
            unregister()

            # Reload every vfx_layer_tools.* module
            to_reload = [n for n in list(sys.modules) if n.startswith("vfx_layer_tools")]
            for mod_name in to_reload:
                mod = sys.modules.get(mod_name)
                if mod is not None:
                    try:
                        importlib.reload(mod)
                    except Exception as exc:
                        print(f"  reload error {mod_name}: {exc}")

            register()
            print("VFX auto-reload: done")
        except Exception as exc:
            print(f"VFX auto-reload failed: {exc}")

    return _AUTO_RELOAD_INTERVAL


def unregister():
    # stop auto-reload timer
    try:
        bpy.app.timers.unregister(_auto_reload_timer)
    except Exception:
        pass

    # Always destroy Scene.vfx so register() recreates it with fresh properties
    if hasattr(bpy.types.Scene, "vfx"):
        try:
            del bpy.types.Scene.vfx
        except Exception:
            pass

    for name in set(CLASS_NAMES):
        old = getattr(bpy.types, name, None)
        if old is not None:
            try:
                bpy.utils.unregister_class(old)
            except Exception:
                pass

    # Sweep orphaned VFX_* registrations left over from failed load attempts
    # (e.g. "already registered as a subclass" after a partial registration)
    orphans = [
        n for n in dir(bpy.types)
        if n.startswith(("VFX_OT_", "VFX_PT_", "VFX_UL_", "VFXLayer", "VFXProject"))
    ]
    for name in orphans:
        old = getattr(bpy.types, name, None)
        if old is not None:
            try:
                bpy.utils.unregister_class(old)
            except Exception:
                pass


def _purge_stale_modules():
    """Drop leftover copies of this addon (old folder / old zips) from sys.modules."""
    for name in list(sys.modules):
        if name != __name__ and (name == "vfx_layer_tools" or name.startswith("vfx_layer_tools.")):
            try:
                del sys.modules[name]
                print(f"VFX register: purged stale module '{name}'")
            except Exception:
                pass


def register():
    print("=" * 66)
    print(f"VFX Layer Tools v{VFX_VERSION} | register from: {os.path.dirname(__file__)}")
    print("=" * 66)
    unregister()

    _purge_stale_modules()

    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as exc:
            # Probably a stale RNA class with the same name is still registered:
            # unregister it and retry once.
            replaced = False
            old = getattr(bpy.types, cls.__name__, None)
            if old is not None:
                try:
                    bpy.utils.unregister_class(old)
                    bpy.utils.register_class(cls)
                    replaced = True
                    print(f"VFX register: replaced stale class {cls.__name__}")
                except Exception as exc2:
                    print(f"VFX register ERROR {cls.__name__}: retry failed: {exc2}")
            if not replaced:
                print(f"VFX register ERROR {cls.__name__}: {exc}")

    # Scene.vfx: kill any stale pointer (old build / foreign addon) and recreate
    if hasattr(bpy.types.Scene, "vfx"):
        try:
            del bpy.types.Scene.vfx
        except Exception:
            pass
    try:
        bpy.types.Scene.vfx = PointerProperty(type=VFXProject)
        print("VFX: Scene.vfx created")
    except Exception as exc:
        print(f"VFX register ERROR: could not create Scene.vfx: {exc}")

    # One-time migration: old ramp values were mist fractions (0..1),
    # new ones are meters. A stale 0.11 m "Fog Full" sits below Mist Start
    # and kills all fog. Per-scene try: one unwritable (linked) scene
    # must not abort the loop for the rest.
    for sc in bpy.data.scenes:
        try:
            v = getattr(sc, "vfx", None)
            if v is None or v.get("vfx_ramp_migrated"):
                continue
            if v.ramp_white <= 1.0:
                v.ramp_white = 30.0
                print(f"VFX: migrated Fog Full 0..1 fraction to meters in '{sc.name}'")
            if v.ramp_black > v.ramp_white:
                v.ramp_black = 0.0
            v["vfx_ramp_migrated"] = True
        except Exception:
            continue

    # kick off auto-reload timer
    if _AUTO_RELOAD_ENABLED:
        try:
            addon_dir = os.path.dirname(__file__)
            for filename in os.listdir(addon_dir):
                if filename.endswith(".py"):
                    filepath = os.path.join(addon_dir, filename)
                    _FILE_TIMESTAMPS[filename] = os.path.getmtime(filepath)
            bpy.app.timers.register(_auto_reload_timer, first_interval=_AUTO_RELOAD_INTERVAL)
            print(f"VFX auto-reload: watching for changes every {_AUTO_RELOAD_INTERVAL}s")
        except Exception as exc:
            print(f"VFX auto-reload init error: {exc}")


if __name__ == "__main__":
    register()
    try:
        repair_shadow_proxies()
        print("VFX: shadow proxies repaired")
    except Exception as e:
        print("VFX repair error:", e)
