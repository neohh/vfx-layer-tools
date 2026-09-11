"""VFX Layer Tools — UI panels and list widget."""

import bpy

from . import VFX_VERSION
from .core import get_project, active_layer
from .materials import _last_adjust_stats
from .occlusion import compute_composite_order, _camera_distance


# ---------------------------------------------------------------------
# UI LIST
# ---------------------------------------------------------------------

class VFX_UL_layers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        has_shd = bool(item.shadow_scene)

        row = layout.row(align=True)
        row.prop(item, "enabled", text="")

        if has_shd:
            tog = row.operator(
                "vfx.toggle_layer_expand",
                text="",
                icon='TRIA_DOWN' if item.expanded else 'TRIA_RIGHT',
                emboss=False
            )
            tog.index = index
        else:
            row.label(text="", icon='BLANK1')

        if item.scene:
            row.label(text=item.layer_name, icon='SCENE_DATA')
        else:
            row.label(text=item.layer_name, icon='ERROR')

        rr = row.operator(
            "vfx.render_all_layers",
            text="",
            icon='RENDER_STILL',
            emboss=False
        )
        rr.only_layer = index
        rr.refresh_after = True

        drag = row.operator(
            "vfx.drag_layer",
            text="",
            icon='GRIP',
            emboss=False
        )
        drag.index = index

        if has_shd and item.expanded:
            shd_row = layout.row(align=True)
            shd_row.scale_y = 0.9
            shd_row.label(text="", icon='BLANK1')
            shd_row.label(text="", icon='BLANK1')
            shd_row.label(text="shadow", icon='LIGHT')
            rr2 = shd_row.operator(
                "vfx.render_all_layers",
                text="",
                icon='RENDER_STILL',
                emboss=False
            )
            rr2.only_shadow_for_layer = index
            rr2.refresh_after = True
            shd_row.label(text="", icon='BLANK1')


# ---------------------------------------------------------------------
# MAIN PANEL (View3D + NodeEditor)
# ---------------------------------------------------------------------

def _draw_layer_list(context, layout):
    """Shared: draw layer list + active layer box + buttons."""
    vfx, master = get_project(context, allow_write=False)

    layout.operator_context = 'INVOKE_DEFAULT'
    layout.operator("vfx.create_layer", icon='ADD')
    layout.operator("vfx.add_selected_lights", text="Add Selected Lights", icon='LIGHT')

    layout.template_list(
        "VFX_UL_layers",
        "",
        vfx,
        "layers",
        vfx,
        "active_layer_index"
    )

    # background strip
    if vfx.bg_scene is not None:
        bgbox = layout.box()
        r = bgbox.row(align=True)
        r.label(text="", icon='WORLD')
        r.label(text="BACKGROUND (bottom)")
        rbg = r.operator(
            "vfx.render_all_layers",
            text="",
            icon='RENDER_STILL',
            emboss=False
        )
        rbg.only_background = True
        rbg.refresh_after = True
        r.operator("vfx.delete_background", text="", icon='X')
        if vfx.use_fog:
            r.prop(vfx, "bg_fog_factor", text="Fog")

    row = layout.row(align=True)
    row.operator("vfx.move_layer_up", icon='TRIA_UP', text="Up / Forward")
    row.operator("vfx.move_layer_down", icon='TRIA_DOWN', text="Down / Back")

    layer = active_layer(vfx)

    if layer:
        box = layout.box()
        box.label(text=layer.layer_name, icon='SCENE_DATA')

        row = box.row(align=True)
        row.operator("vfx.add_selected_to_layer", text="Add Sel")
        row.operator("vfx.remove_selected_from_layer", text="Remove Sel")

        row = box.row(align=True)
        row.operator("vfx.rename_layer", text="Rename Layer")
        row.operator("vfx.delete_layer", text="Delete", icon='X')

        box.prop(layer, "shadow_catcher", text="Catcher")

        row = box.row(align=True)
        if not layer.shadow_scene:
            row.operator("vfx.create_shadow_pass", icon='LIGHT')
        else:
            row.label(text=f"Shadow pass: {layer.shadow_mode}", icon='CHECKMARK')
            row.operator("vfx.refresh_proxies", text="", icon='FILE_REFRESH')
            row.operator("vfx.delete_shadow_pass", text="", icon='X')
            box.prop(layer, "shadow_strength")

        if vfx.use_fog:
            box.prop(layer, "fog_factor")

        # ── OCCLUSION (holdout) ──
        obox = box.box()
        ohead = obox.row(align=True)
        ohead.prop(layer, "occlusion_expanded", text="", icon='TRIA_DOWN' if layer.occlusion_expanded else 'TRIA_RIGHT', emboss=False)
        ohead.label(text="OCCLUSION", icon='MOD_BOOLEAN')
        ohead.prop(layer, "occlusion_mode", text="")
        if layer.occlusion_expanded:
            oc = obox.column(align=True)
            if layer.occlusion_mode == 'MANUAL':
                oc.label(text="Occluder layers:", icon='RESTRICT_RENDER_ON')
                for ref in layer.occlusion_layers:
                    other = next((l for l in vfx.layers if l.id == ref.layer_id), None)
                    if other is None:
                        continue
                    r = oc.row(align=True)
                    r.prop(ref, "include", text="")
                    r.label(text=other.layer_name, icon='SCENE_DATA')
                oc.operator("vfx.refresh_occlusion", icon='FILE_REFRESH')
            elif layer.occlusion_mode == 'AUTO':
                others = [l.layer_name for l in vfx.layers
                          if l.id != layer.id and l.enabled]
                if others:
                    oc.label(text=f"Occluders: all other layers ({len(others)})", icon='INFO')
                    oc.label(text=" | ".join(others), icon='DOT')
                else:
                    oc.label(text="No other layers yet", icon='INFO')
            else:  # OFF
                oc.label(text="Occlusion disabled", icon='X')
            n_prox = 0
            if layer.occlusion_collection:
                n_prox = sum(1 for o in layer.occlusion_collection.objects
                             if o.get("vfx_proxy"))
            oc.label(text=f"Holdout proxies: {n_prox}", icon='MOD_MESHDEFORM')

        box.separator()
        box.prop(layer, "use_adjust")
        if layer.use_adjust:
            st = _last_adjust_stats
            if st["notes"]:
                for n in st["notes"]:
                    box.label(text=n, icon='ERROR')
            else:
                box.label(text=f"{st['applied']}/{st['materials']} materials adjusted", icon='CHECKMARK')
            adj = box.column(align=True)
            adj.prop(layer, "exposure")
            adj.prop(layer, "contrast")
            adj.prop(layer, "saturation")
            adj.prop(layer, "tint_strength")
            if layer.tint_strength > 0.0:
                adj.prop(layer, "tint_color", text="")
            adj.operator("vfx.reset_lighting", icon='LOOP_BACK')

        # ── Per-layer GRADE ──
        box.separator()
        gbox = box.box()
        gr = gbox.row(align=True)
        gr.prop(layer, "grade_enable", text="")
        gr.label(text="GRADE", icon='COLOR')
        if layer.grade_enable:
            gc = gbox.column(align=True)
            gc.prop(layer, "l_exposure")
            gc.prop(layer, "l_temp")
            gc.prop(layer, "l_tint")
            gc.prop(layer, "l_sat")
            gc.prop(layer, "l_contrast")
            row_lgg = gc.row(align=True)
            row_lgg.prop(layer, "l_lift", text="Lift")
            row_lgg.prop(layer, "l_gain", text="Gain")
            gbox.operator("vfx.reset_layer_grade", icon='LOOP_BACK')
            # MASK for per-layer grade (default: alpha silhouette)
            lam = gc.row(align=True)
            lam.prop(layer, "use_alpha_mask", icon='MOD_MASK')
            if not layer.use_alpha_mask:
                gc.prop(layer, "grade_mask_source", text="Mask")
                if layer.grade_mask_source == 'DEPTH':
                    lr = gc.row(align=True)
                    lr.prop(layer, "grade_mask_depth_start")
                    lr.prop(layer, "grade_mask_depth_end")
                elif layer.grade_mask_source == 'LUMA':
                    lr = gc.row(align=True)
                    lr.prop(layer, "grade_mask_luma_lo")
                    lr.prop(layer, "grade_mask_luma_hi")
                elif layer.grade_mask_source == 'EXT':
                    gc.prop(layer, "grade_mask_ext_node")
                    pk = gc.operator("vfx.pick_cryptomatte", text="Pick Object (pipette)", icon='EYEDROPPER')
                    pk.target = 'LAYER'
                r3 = gc.row(align=True)
                r3.prop(layer, "grade_mask_invert")
                r3.prop(layer, "grade_mask_soft")


def _draw_mask_section(context, layout, vfx, prefix, sources):
    """Unified MASK sub-section for a masked effect (props: <prefix>_mask_*)."""
    src = getattr(vfx, prefix + "_mask_source", 'NONE')
    box = layout.box()
    hdr = box.row(align=True)
    hdr.label(text="MASK", icon='MOD_MASK')
    hdr.prop(vfx, prefix + "_mask_source", text="")
    if src == 'NONE':
        return
    col = box.column(align=True)
    col.prop(vfx, prefix + "_mask_invert")
    col.prop(vfx, prefix + "_mask_soft")
    if src == 'DEPTH':
        cr = col.row(align=True)
        cr.prop(vfx, prefix + "_mask_depth_start")
        cr.prop(vfx, prefix + "_mask_depth_end")
    elif src == 'LUMA':
        lr = col.row(align=True)
        lr.prop(vfx, prefix + "_mask_luma_lo")
        lr.prop(vfx, prefix + "_mask_luma_hi")
    elif src == 'EXT':
        col.prop(vfx, prefix + "_mask_ext_node")
        if prefix == 'grade' and src == 'EXT':
            pk = col.row(align=True)
            pk.operator("vfx.pick_cryptomatte", text="Pick Object (pipette)", icon='EYEDROPPER')
            if getattr(vfx, "crypto_pick_name", ""):
                pk.label(text=vfx.crypto_pick_name, icon='OBJECT_DATA')
    pr = col.row(align=True)
    pr.prop(vfx, "use_mask", text="Preview Mask", icon='HIDE_OFF', toggle=True)
    if vfx.use_mask:
        pr.prop(vfx, "mask_source", text="")
        if vfx.mask_source != prefix.upper():
            row2 = col.row(align=True)
            row2.operator("vfx.preview_this_mask", text="Show This Mask").source = prefix.upper()


def _draw_post_effects(context, layout):
    """Shared: draw all post-processing effects (fog, blur, DOF, glare, lensdist)."""
    if not hasattr(context.scene, "vfx"):
        layout.label(text="Scene.vfx not registered — restart Blender", icon='ERROR')
        return
    vfx, master = get_project(context, allow_write=False)

    # ── Mask toggle + selector (top bar) ──
    mask_row = layout.row(align=True)
    mask_row.prop(vfx, "use_mask", text="", icon='HIDE_OFF')
    mask_row.prop(vfx, "mask_source", text="")

    # ── FOG ──
    fogbox = layout.box()
    fr = fogbox.row(align=True)
    fr.operator(
        "vfx.toggle_fog_expand",
        text="",
        icon='TRIA_DOWN' if vfx.fog_expanded else 'TRIA_RIGHT',
        emboss=False
    )
    fr.label(text="FOG", icon='FORCE_WIND')
    fr.prop(vfx, "use_fog", text="")
    if vfx.fog_expanded:
        fb = fogbox.column(align=True)
        fb.prop(vfx, "mist_start")
        fb.prop(vfx, "mist_depth")
        fr2 = fb.row(align=True)
        fr2.prop(vfx, "ramp_black")
        fr2.prop(vfx, "ramp_white")
        fb.prop(vfx, "fog_strength")
        if vfx.fog_strength > 0.0:
            fb.prop(vfx, "fog_color", text="")
        _draw_mask_section(context, fb, vfx, "fog", ('DEPTH', 'LUMA', 'EXT'))
    dofbox = layout.box()
    dh = dofbox.row(align=True)
    dh.prop(vfx, "use_dof", text="")
    dh.label(text="CAMERA FOCUS (DOF)", icon='CAMERA_DATA')
    if vfx.use_dof:
        dc = dofbox.column(align=True)
        dc.prop(vfx, "dof_focus")
        dr = dc.row(align=True)
        dr.prop(vfx, "dof_far_start")
        dr.prop(vfx, "dof_far_end")
        dc.prop(vfx, "dof_maxblur")
        dc.label(text="Ramp: edit DOF FOCUS RAMP node in comp", icon='INFO')
        _draw_mask_section(context, dc, vfx, "dof", ('ALPHA', 'DEPTH', 'LUMA', 'EXT'))
    glowbox = layout.box()
    gr = glowbox.row(align=True)
    gr.prop(vfx, "use_glare", text="")
    gr.label(text="GLOW / GLARE", icon='LIGHT_SUN')
    if vfx.use_glare:
        gc = glowbox.column(align=True)
        gc.prop(vfx, "glare_type", text="")
        gc.prop(vfx, "glare_strength")
        gc.prop(vfx, "glare_threshold")
        gc.prop(vfx, "glare_size")
        _draw_mask_section(context, gc, vfx, "glare", ('ALPHA', 'DEPTH', 'LUMA', 'EXT'))
    ldbox = layout.box()
    lbw = ldbox.row(align=True)
    lbw.prop(vfx, "use_lensdist", text="")
    lbw.label(text="LENS DISTORTION", icon='VIEW_CAMERA')
    if vfx.use_lensdist:
        lc = ldbox.column(align=True)
        lc.prop(vfx, "lensdist_distort")
        lc.prop(vfx, "lensdist_disperse")
    # ── MASTER GRADE (primary correction, applied first in chain) ──
    mgbox = layout.box()
    mgr = mgbox.row(align=True)
    mgr.prop(vfx, "m_grade_enable", text="")
    mgr.label(text="MASTER GRADE", icon='COLOR')
    if vfx.m_grade_enable:
        mgc = mgbox.column(align=True)
        mgc.prop(vfx, "m_exposure")
        mgc.prop(vfx, "m_temp")
        mgc.prop(vfx, "m_tint")
        mgc.prop(vfx, "m_saturation")
        mgc.prop(vfx, "m_contrast")
        row_lgg = mgc.row(align=True)
        row_lgg.prop(vfx, "m_lift", text="Lift")
        row_lgg.prop(vfx, "m_gain", text="Gain")
        row_btn = mgbox.row(align=True)
        row_btn.operator("vfx.reset_master_grade", icon='LOOP_BACK')
        row_btn.operator("vfx.copy_master_grade", icon='COPYDOWN')
        _draw_mask_section(context, mgc, vfx, "grade", ('ALPHA', 'DEPTH', 'LUMA', 'EXT'))

def _draw_advanced_features(context, layout):
    """Draw advanced features: Color Match, Light Groups."""
    vfx, master = get_project(context, allow_write=False)

    # Color Match
    cmbox = layout.box()
    bh = cmbox.row(align=True)
    bh.prop(vfx, "use_color_match", text="")
    bh.label(text="COLOR MATCH / PLATE", icon='COLOR')
    if vfx.use_color_match:
        cmc = cmbox.column(align=True)
        cmc.prop(vfx, "color_match_preset", text="Preset")
        cmc.prop(vfx, "color_match_strength", text="Strength")
        crow = cmc.row(align=True)
        for preset_id in ('WARM', 'TEAL_ORANGE', 'COOL', 'FILM'):
            op = crow.operator("vfx.apply_color_preset", text=preset_id.replace('_', ' ').title())
            op.preset = preset_id

    # Light Groups
    box = layout.box()
    bh = box.row(align=True)
    bh.prop(vfx, "use_light_groups", text="")
    bh.label(text="LIGHT GROUPS", icon='LIGHT')
    if vfx.use_light_groups:
        box.operator("vfx.setup_light_groups", icon='LIGHT')
        box.label(text="Auto-assigns Key/Fill/Rim/Env", icon='INFO')


def _draw_composite_order(context, layout):
    """Global composite sort mode + live display of the computed order."""
    vfx, master = get_project(context, allow_write=False)

    cbox = layout.box()
    cbox.label(text="COMPOSITING ORDER", icon='SORTSIZE')
    cbox.prop(vfx, "composite_sort_mode", text="Sort")
    seq = compute_composite_order(vfx, master, verbose=False)
    col = cbox.column(align=True)
    col.label(text="bottom -> top:", icon='TRIA_DOWN')
    if vfx.bg_scene is not None:
        col.label(text="  BACKGROUND", icon='WORLD')
    for l in seq:
        d = _camera_distance(master, l)
        suffix = f"  ({d:.1f}m)" if (d is not None and vfx.composite_sort_mode == 'AUTO') else ""
        col.label(text=f"  {l.layer_name}{suffix}", icon='SCENE_DATA')
    if vfx.composite_sort_mode == 'AUTO':
        col.label(text="nearest = on top", icon='INFO')


def _draw_render_settings(context, layout):
    """Shared: render engines, output, rebuild buttons."""
    vfx, master = get_project(context, allow_write=False)

    layout.separator()
    _draw_composite_order(context, layout)
    layout.prop(vfx, "output_dir", text="Output")

    layout.operator(
        "vfx.one_click_exr",
        text="1-Click: Render EXR + Comp",
        icon='FILE_IMAGE'
    )

    if vfx.render_running:
        bar = layout.column(align=True)
        bar.scale_y = 2.5
        bar.prop(vfx, "render_progress", slider=True, text="")
        layout.label(text=vfx.render_status, icon='RENDER_ANIMATION')
        layout.label(text="ESC - stop render", icon='INFO')

    layout.separator(factor=1.5)

    row = layout.row(align=True)
    row.prop(vfx, "objects_engine", text="Obj")
    row.prop(vfx, "shadows_engine", text="Shd")

    row = layout.row(align=True)
    row.operator("vfx.rebuild_comp", text="Rebuild Comp", icon='FILE_REFRESH')
    row.operator("vfx.diagnostic", text="Diagnostic", icon='CONSOLE')


# ---------------------------------------------------------------------
# PANEL — MAIN (View3D sidebar)
# ---------------------------------------------------------------------

class VFX_PT_main(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "VFX"
    bl_label = "VFX Layers"

    def draw(self, context):
        layout = self.layout

        if not hasattr(context.scene, "vfx"):
            layout.label(text="Scene.vfx not registered — restart Blender", icon='ERROR')
            return

        try:
            vfx, master = get_project(context, allow_write=False)
            layout.label(text=f"VFX Layer Tools build {VFX_VERSION}", icon='NODETREE')
            layout.separator()
            layout.prop(vfx, "master_scene", text="Master")

            if vfx.master_scene is None:
                layout.label(text="Master scene not set:", icon='INFO')
                layout.operator("vfx.set_master")

            _draw_layer_list(context, layout)
            _draw_render_settings(context, layout)
        except Exception as e:
            layout.label(text="Panel draw error:", icon='ERROR')
            layout.label(text=str(e))

    # Keep draw_main for compositor panel compatibility
    def draw_main(self, context, layout):
        try:
            vfx, master = get_project(context, allow_write=False)
            layout.label(text=f"VFX Layer Tools build {VFX_VERSION}", icon='NODETREE')
            layout.separator()
            layout.prop(vfx, "master_scene", text="Master")

            if vfx.master_scene is None:
                layout.label(text="Master scene not set:", icon='INFO')
                layout.operator("vfx.set_master")

            _draw_layer_list(context, layout)
            _draw_render_settings(context, layout)
        except Exception as e:
            layout.label(text="Panel draw error:", icon='ERROR')
            layout.label(text=str(e))


# ---------------------------------------------------------------------
# SUB-PANEL — POST EFFECTS (View3D sidebar)
# ---------------------------------------------------------------------

class VFX_PT_post_effects(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "VFX"
    bl_label = "Post Effects"
    bl_parent_id = "VFX_PT_main"

    def draw(self, context):
        layout = self.layout
        try:
            layout.label(text=f"build {VFX_VERSION}", icon='INFO')
            if not hasattr(context.scene, "vfx"):
                layout.label(text="Scene.vfx not registered — restart Blender", icon='ERROR')
                return
            _draw_post_effects(context, layout)
        except Exception as e:
            layout.label(text="Panel draw error:", icon='ERROR')
            layout.label(text=str(e))


# ---------------------------------------------------------------------
# PANEL — COMPOSITOR (Node Editor sidebar)
# ---------------------------------------------------------------------

class VFX_PT_compositor(bpy.types.Panel):
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "VFX"
    bl_label = "VFX Layers"

    @classmethod
    def poll(cls, context):
        sd = context.space_data
        return sd is not None and getattr(sd, "tree_type", "") == 'CompositorNodeTree'

    def draw(self, context):
        layout = self.layout

        if not hasattr(context.scene, "vfx"):
            layout.label(text="Scene.vfx not registered — restart Blender", icon='ERROR')
            return

        try:
            VFX_PT_main.draw_main(self, context, layout)
        except Exception as e:
            layout.label(text="Panel draw error:", icon='ERROR')
            layout.label(text=str(e))


# ---------------------------------------------------------------------
# SUB-PANEL — POST EFFECTS (Node Editor sidebar)
# ---------------------------------------------------------------------

class VFX_PT_compositor_effects(bpy.types.Panel):
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "VFX"
    bl_label = "Post Effects"
    bl_parent_id = "VFX_PT_compositor"

    @classmethod
    def poll(cls, context):
        sd = context.space_data
        return sd is not None and getattr(sd, "tree_type", "") == 'CompositorNodeTree'

    def draw(self, context):
        layout = self.layout
        try:
            layout.label(text=f"build {VFX_VERSION}", icon='INFO')
            if not hasattr(context.scene, "vfx"):
                layout.label(text="Scene.vfx not registered — restart Blender", icon='ERROR')
                return
            _draw_post_effects(context, layout)
        except Exception as e:
            layout.label(text="Panel draw error:", icon='ERROR')
            layout.label(text=str(e))


# ---------------------------------------------------------------------
# PANEL — DEBUG / TEST (View3D sidebar)
# Простая тестовая панель: если она рисуется — аддон загружен и RNA в порядке.
# ---------------------------------------------------------------------

class VFX_PT_debug_test(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "VFX"
    bl_label = "Debug / Test"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"Build {VFX_VERSION}", icon='SEQ_CHROMA_SCOPE')

        box = layout.box()
        box.label(text="Smoke test", icon='CHECKMARK')
        col = box.column(align=True)
        col.operator("vfx.diagnostic", text="Run Diagnostics", icon='SYSTEM')
        col.operator("vfx.rebuild_comp", text="Rebuild Comp", icon='NODE_COMPOSITING')

        info = layout.box()
        vfx_ok = hasattr(context.scene, "vfx")
        info.label(text=f"Scene.vfx: {'OK' if vfx_ok else 'MISSING'}",
                   icon='CHECKMARK' if vfx_ok else 'ERROR')
        try:
            import sys as _sys
            mods = [m for m in _sys.modules if m.startswith("vfx_layer_tools")]
            info.label(text=f"modules loaded: {len(mods)}")
        except Exception:
            pass
