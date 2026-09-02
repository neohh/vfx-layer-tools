"""VFX Layer Tools — UI panels and list widget."""

import bpy

from . import VFX_VERSION
from .core import get_project, active_layer
from .materials import _last_adjust_stats


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


def _draw_post_effects(context, layout):
    """Shared: draw all post-processing effects (fog, blur, DOF, glare, lensdist)."""
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

    # ── ATMOSPHERIC BLUR ──
    blurbox = layout.box()
    bbh = blurbox.row(align=True)
    bbh.prop(vfx, "use_blur", text="")
    bbh.label(text="ATMOSPHERIC BLUR", icon='FILTER')
    if vfx.use_blur:
        bc = blurbox.column(align=True)
        bc.prop(vfx, "blur_size")
        br = bc.row(align=True)
        br.prop(vfx, "blur_ramp_black")
        br.prop(vfx, "blur_ramp_white")

    # ── CAMERA DOF ──
    dofbox = layout.box()
    dh = dofbox.row(align=True)
    dh.prop(vfx, "use_dof", text="")
    dh.label(text="CAMERA FOCUS (DOF)", icon='CAMERA_DATA')
    if vfx.use_dof:
        dc = dofbox.column(align=True)
        dc.prop(vfx, "dof_fstop")
        dc.prop(vfx, "dof_focus")
        dc.prop(vfx, "dof_maxblur")

    # ── GLOW / GLARE ──
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

    # ── LENS DISTORTION ──
    ldbox = layout.box()
    lbw = ldbox.row(align=True)
    lbw.prop(vfx, "use_lensdist", text="")
    lbw.label(text="LENS DISTORTION", icon='VIEW_CAMERA')
    if vfx.use_lensdist:
        lc = ldbox.column(align=True)
        lc.prop(vfx, "lensdist_distort")
        lc.prop(vfx, "lensdist_disperse")

    # ── COLOR MATCH ──
    cmbox = layout.box()
    cmr = cmbox.row(align=True)
    cmr.prop(vfx, "use_color_match", text="")
    cmr.label(text="COLOR MATCH", icon='COLOR')
    if vfx.use_color_match:
        cmc = cmbox.column(align=True)
        cmc.prop(vfx, "color_match_preset", text="")
        cmc.prop(vfx, "color_match_strength")

    # ── CRYPTOMATTE ──
    crypto_box = layout.box()
    cryptor = crypto_box.row(align=True)
    cryptor.prop(vfx, "use_cryptomatte", text="")
    cryptor.label(text="CRYPTOMATTE", icon='NODETREE')
    if vfx.use_cryptomatte:
        cryptor.operator("vfx.enable_cryptomatte", text="", icon='FILE_REFRESH')
    else:
        cryptor.operator("vfx.enable_cryptomatte", text="Enable", icon='ADD')


def _draw_render_settings(context, layout):
    """Shared: render engines, output, rebuild buttons."""
    vfx, master = get_project(context, allow_write=False)

    layout.separator()
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
            layout.label(text="VFX props not registered!", icon='ERROR')
            layout.label(text="Remove old addon, restart Blender")
            return

        try:
            vfx, master = get_project(context, allow_write=False)
            layout.label(text=f"VFX Layer Tools v{VFX_VERSION}", icon='NODETREE')
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
            layout.label(text=f"VFX Layer Tools v{VFX_VERSION}", icon='NODETREE')
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
        if not hasattr(context.scene, "vfx"):
            return
        try:
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
            layout.label(text="VFX props not registered!", icon='ERROR')
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
        if not hasattr(context.scene, "vfx"):
            return
        try:
            _draw_post_effects(context, layout)
        except Exception as e:
            layout.label(text="Panel draw error:", icon='ERROR')
            layout.label(text=str(e))
