bl_info = {
    "name": "VFX Layer Tools",
    "author": "VFX Pipeline",
    "version": (2, 1, 0),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > VFX",
    "description": "VFX layer / scene / compositing manager",
    "category": "Compositing",
}

VFX_VERSION = "2.1.0"

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
        update=lambda s, c: _trigger_comp(c)
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
    fog_preview: BoolProperty(
        name="Show Mask (viewer)",
        description="Temporarily show the fog mask in the viewer instead of the scene",
        default=False,
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
        update=lambda s, c: _trigger_comp(c)
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
        update=lambda s, c: _trigger_comp(c)
    )
    dof_focus: FloatProperty(
        name="Focus Distance (m)", default=10.0, min=0.0, max=500.0,
        update=lambda s, c: _trigger_comp(c)
    )
    dof_maxblur: FloatProperty(
        name="Max Blur (px)", default=12.0, min=0.0, max=100.0,
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


# ---------------------------------------------------------------------
# SUBMODULE IMPORTS (after class definitions to avoid circular import)
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
    VFX_OT_diagnose, VFX_OT_force_enable_passes,
    VFX_OT_auto_calibrate_mist,
)
from .ui import (
    VFX_UL_layers, VFX_PT_main, VFX_PT_compositor,
)


# ---------------------------------------------------------------------
# REGISTER
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
    VFX_OT_diagnose,
    VFX_OT_force_enable_passes,
    VFX_OT_auto_calibrate_mist,
    VFX_PT_main,
    VFX_PT_compositor,
)

CLASS_NAMES = tuple(cls.__name__ for cls in classes)


# ---------------------------------------------------------------------
# AUTO-RELOAD (dev convenience)
# ---------------------------------------------------------------------

_AUTO_RELOAD_ENABLED = True
_AUTO_RELOAD_INTERVAL = 2  # seconds
_FILE_TIMESTAMPS = {}
_AUTO_RELOAD_IN_PROGRESS = False


def _auto_reload_timer():
    """Check source files for changes; reload changed modules."""
    global _AUTO_RELOAD_IN_PROGRESS

    if not _AUTO_RELOAD_ENABLED:
        return None

    if _AUTO_RELOAD_IN_PROGRESS:
        return _AUTO_RELOAD_INTERVAL

    addon_dir = os.path.dirname(__file__)
    changed_files = []

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

    if not changed_files:
        return _AUTO_RELOAD_INTERVAL

    print(f"VFX auto-reload: changed {', '.join(changed_files)}")
    _AUTO_RELOAD_IN_PROGRESS = True
    try:
        unregister()

        # Reload all modules belonging to this package
        pkg = __name__  # e.g. "vfx_layer_tools"
        to_reload = [n for n in list(sys.modules)
                     if n == pkg or n.startswith(pkg + ".")]
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
        import traceback
        traceback.print_exc()
    finally:
        _AUTO_RELOAD_IN_PROGRESS = False

    return _AUTO_RELOAD_INTERVAL


def unregister():
    # stop auto-reload timer
    try:
        bpy.app.timers.unregister(_auto_reload_timer)
    except Exception:
        pass

    if hasattr(bpy.types.Scene, "vfx"):
        try:
            del bpy.types.Scene.vfx
        except Exception:
            pass

    for name in CLASS_NAMES:
        old = getattr(bpy.types, name, None)
        if old is not None:
            try:
                bpy.utils.unregister_class(old)
            except Exception:
                pass


def register():
    unregister()

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.vfx = PointerProperty(type=VFXProject)

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
