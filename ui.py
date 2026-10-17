"""VFX Layer Tools — UI panels and list widget.
Fully localized (RU / EN) with streamlined layout and per-layer FX overrides.
"""

import bpy
import os

from . import VFX_VERSION
from .core import get_project, active_layer
from .materials import _last_adjust_stats
from .occlusion import compute_composite_order, _camera_distance
from .compositor import get_matte_sequence_info
from .locales import tr


def _get_rendered_frames_badge(scene, vfx, master, is_static=False):
    """Scan folder and return (badge_text, icon) showing rendered frames."""
    if not scene or not vfx:
        return "", 'BLANK1'
    base_dir = bpy.path.abspath(vfx.output_dir) if vfx.output_dir else ""
    if not base_dir:
        return "", 'BLANK1'
    folder = os.path.join(base_dir, scene.name)
    total = 1 if is_static else max(1, master.frame_end - master.frame_start + 1)
    if not os.path.exists(folder):
        return f"0/{total}", 'BLANK1'

    fmt = getattr(vfx, "render_format", 'OPEN_EXR')
    target_ext = '.png' if fmt == 'PNG' else '.exr'
    try:
        files = [f for f in os.listdir(folder) if f.lower().endswith(target_ext)]
        if not files:
            files = [f for f in os.listdir(folder) if f.lower().endswith(('.exr', '.png'))]
        count = len(files)
    except Exception:
        count = 0

    if count >= total:
        return f"{count}/{total}", 'CHECKMARK'
    elif count > 0:
        return f"{count}/{total}", 'TIME'
    return f"0/{total}", 'BLANK1'


# ---------------------------------------------------------------------
# UI LIST
# ---------------------------------------------------------------------

class VFX_UL_layers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        has_shd = bool(item.shadow_scene)
        vfx = context.scene.vfx
        master = getattr(vfx, "master_scene", None) or context.scene

        row = layout.row(align=True)
        row.prop(item, "selected", text="")
        row.prop(item, "enabled", text="", icon='HIDE_OFF' if item.enabled else 'HIDE_ON', emboss=False)

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

        # Frame badge showing rendered frames (e.g. 76/76 or 15/76)
        b_text, b_icon = _get_rendered_frames_badge(item.scene, vfx, master, is_static=item.is_static)
        if b_text:
            badge_sub = row.row(align=True)
            badge_sub.alignment = 'RIGHT'
            badge_sub.label(text=b_text, icon=b_icon)

        if getattr(item, "use_holdout_matte", False):
            m_badge = row.row(align=True)
            m_badge.alignment = 'RIGHT'
            m_badge.label(text="", icon='MOD_MASK')

        sel_btn = row.operator(
            "vfx.select_layer_objects",
            text="",
            icon='RESTRICT_SELECT_OFF',
            emboss=False
        )
        sel_btn.layer_index = index

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

            shd_text, shd_icon = _get_rendered_frames_badge(item.shadow_scene, vfx, master)
            if shd_text:
                shd_sub = shd_row.row(align=True)
                shd_sub.alignment = 'RIGHT'
                shd_sub.label(text=shd_text, icon=shd_icon)

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
# MAIN PANEL DRAW FUNCTIONS
# ---------------------------------------------------------------------

def _draw_layer_list(context, layout):
    """Draw layer list + active layer card."""
    vfx, master = get_project(context, allow_write=False)

    layout.operator_context = 'INVOKE_DEFAULT'

    # Toolbar row: + Layer | Backplate | Matte in one clean row
    top_row = layout.row(align=True)
    top_row.operator("vfx.create_layer", text=tr("+ Layer", context), icon='ADD')
    top_row.operator("vfx.add_background", text=tr("Backplate", context), icon='IMAGE_BACKGROUND')
    top_row.operator("vfx.assign_layer_mask_dialog", text=tr("Matte", context), icon='MOD_MASK')
    layout.operator("vfx.add_selected_lights", text=tr("Link Selected Lights", context), icon='LIGHT')

    # Layers UIList
    layout.template_list(
        "VFX_UL_layers",
        "",
        vfx,
        "layers",
        vfx,
        "active_layer_index"
    )

    # Reorder buttons: Up (Closer) | Down (Further)
    row = layout.row(align=True)
    row.operator("vfx.move_layer_up", icon='TRIA_UP', text=tr("Up (Closer)", context))
    row.operator("vfx.move_layer_down", icon='TRIA_DOWN', text=tr("Down (Further)", context))

    # ── BACKPLATE (BACKGROUND) ──
    has_bg = getattr(vfx, "use_bg", False) or (vfx.bg_scene is not None)
    if has_bg:
        bgbox = layout.box()
        r = bgbox.row(align=True)
        is_exp = getattr(vfx, "bg_expanded", True)
        r.prop(vfx, "bg_expanded", text="", icon='TRIA_DOWN' if is_exp else 'TRIA_RIGHT', emboss=False)
        r.prop(vfx, "use_bg", text=tr("BACKPLATE (BG)", context), icon='IMAGE_BACKGROUND')
        if getattr(vfx, "bg_source_type", 'VIDEO') == 'SCENE' and vfx.bg_scene is not None:
            rbg = r.operator("vfx.render_all_layers", text="", icon='RENDER_STILL', emboss=False)
            rbg.only_background = True
            rbg.refresh_after = True
        r.operator("vfx.rebuild_comp", text="", icon='FILE_REFRESH', emboss=False)
        r.operator("vfx.delete_background", text="", icon='X', emboss=False)

        if is_exp:
            col = bgbox.column(align=True)
            src_row = col.row(align=True)
            src_row.prop(vfx, "bg_source_type", expand=True)

            st = getattr(vfx, "bg_source_type", 'VIDEO')
            if st == 'VIDEO':
                v_row = col.row(align=True)
                v_row.prop(vfx, "bg_video_path", text=tr("Source:", context))
                op = v_row.operator("vfx.browse_bg_file", text="", icon='FILE_FOLDER')
                op.file_type = 'VIDEO'
                f_row = col.row(align=True)
                f_row.prop(vfx, "bg_fit_mode", text=tr("Fit:", context))

            elif st == 'IMAGE':
                i_row = col.row(align=True)
                i_row.prop(vfx, "bg_image_path", text=tr("Source:", context))
                op = i_row.operator("vfx.browse_bg_file", text="", icon='FILE_FOLDER')
                op.file_type = 'IMAGE'
                f_row = col.row(align=True)
                f_row.prop(vfx, "bg_fit_mode", text=tr("Fit:", context))

            elif st == 'MOVIE_CLIP':
                c_row = col.row(align=True)
                c_row.prop(vfx, "bg_movie_clip", text=tr("Source:", context))
                f_row = col.row(align=True)
                f_row.prop(vfx, "bg_fit_mode", text=tr("Fit:", context))

            elif st == 'SCENE':
                s_row = col.row(align=True)
                s_row.prop(vfx, "bg_scene", text=tr("Source:", context))
                if not vfx.bg_scene:
                    s_row.operator("vfx.create_background", text=tr("Create 3D Scene", context), icon='ADD')
                st_row = col.row(align=True)
                st_row.prop(vfx, "bg_is_static", text=tr("Static (1 Frame)", context), icon='IMAGE_DATA')
                if vfx.bg_is_static:
                    st_row.prop(vfx, "bg_static_frame", text=tr("Frame:", context))

            # FPS / frame mismatch diagnostics (compact)
            active_clip = getattr(vfx, "bg_movie_clip", None)
            if active_clip is None and bpy.data.movieclips:
                for mc in bpy.data.movieclips:
                    if "matte" not in mc.name.lower() and mc.fps > 0:
                        active_clip = mc
                        break
            if active_clip and active_clip.fps > 0:
                sc_fps = context.scene.render.fps / (context.scene.render.fps_base or 1.0)
                if abs(active_clip.fps - sc_fps) > 0.05:
                    wbox = col.box()
                    wbox.alert = True
                    w_r = wbox.row()
                    w_r.label(text=f"FPS: {sc_fps:.1f} vs {active_clip.fps:.1f}", icon='ERROR')
                    w_r.operator("vfx.match_scene_fps", text=tr("Match FPS ({fps:.2f})", context, fps=active_clip.fps), icon='TIME')
            if active_clip and active_clip.frame_start > context.scene.frame_start:
                sbox = col.box()
                s_r = sbox.row()
                s_r.label(text=f"Frame 0: clip from {active_clip.frame_start}", icon='INFO')
                s_r.operator("vfx.match_frame_start", text=tr("Start at frame {frame}", context, frame=active_clip.frame_start), icon='PLAY')
    else:
        layout.operator("vfx.add_background", text=tr("+ Add Backplate Layer", context), icon='IMAGE_BACKGROUND')

    # ── ACTIVE LAYER CARD ──
    layer = active_layer(vfx)

    if layer:
        box = layout.box()
        box.label(text=layer.layer_name, icon='SCENE_DATA')

        # Select | + | -  (icons for + and - to save horizontal space)
        row = box.row(align=True)
        row.operator("vfx.select_layer_objects", text=tr("Select", context), icon='RESTRICT_SELECT_OFF')
        row.operator("vfx.add_selected_to_layer", text="", icon='ADD')
        row.operator("vfx.remove_selected_from_layer", text="", icon='REMOVE')

        row = box.row(align=True)
        row.operator("vfx.rename_layer", text=tr("Rename", context))
        row.operator("vfx.delete_layer", text=tr("Delete", context), icon='X')

        # Static 1-frame hold option
        st_row = box.row(align=True)
        st_row.prop(layer, "is_static", text=tr("Static (1 Frame)", context), icon='IMAGE_DATA')
        if layer.is_static:
            st_row.prop(layer, "static_frame", text=tr("Frame:", context))

        # ── 3D SHADOW PASS & FILTER ──
        sbox = box.box()
        shead = sbox.row(align=True)
        shead.prop(layer, "shadow_expanded", text="",
                   icon='TRIA_DOWN' if layer.shadow_expanded else 'TRIA_RIGHT', emboss=False)
        if not layer.shadow_scene:
            shead.label(text=tr("3D SHADOW", context), icon='LIGHT')
            if layer.shadow_catcher:
                shead.operator("vfx.create_shadow_pass", text=tr("Create Pass", context), icon='PLUS')
            else:
                shead.operator("vfx.create_shadow_pass", text=tr("Add Pass", context), icon='PLUS')
        else:
            shead.label(text=tr("3D SHADOW", context), icon='CHECKMARK')
            shead.prop(layer, "shadow_strength", text=tr("Strength", context))
            shead.operator("vfx.refresh_proxies", text="", icon='FILE_REFRESH', emboss=False)
            shead.operator("vfx.delete_shadow_pass", text="", icon='X', emboss=False)

        if layer.shadow_expanded:
            sc = sbox.column(align=True)
            sp_cat = sc.split(factor=0.32)
            sp_cat.label(text=tr("Catcher:", context))
            sp_cat.prop(layer, "shadow_catcher", text="")

            if layer.shadow_catcher:
                b_row = sc.row(align=True)
                b_row.prop(layer, "shadow_catcher_bounce", text=tr("GI Bounce & Reflections", context), icon='LIGHT_SUN')

            if not layer.shadow_scene:
                if layer.shadow_catcher:
                    r = sc.row(align=True)
                    r.alert = True
                    r.operator("vfx.create_shadow_pass", text=f"⚠ {tr('Create Pass', context)}", icon='LIGHT')
            else:
                sc.separator()
                r_main = sc.row(align=True)
                r_main.prop(layer, "shadow_color", text=tr("Color:", context))
                r_main.prop(layer, "shadow_samples", text=tr("Samples:", context))

                r_filter = sc.row(align=True)
                r_filter.prop(layer, "shadow_use_ramp", text="ColorRamp")
                r_filter.prop(layer, "shadow_blur", text=tr("Blur", context))

                if layer.shadow_use_ramp:
                    r_ramp = sc.row(align=True)
                    r_ramp.prop(layer, "shadow_ramp_black", text=tr("Noise Cut", context))
                    r_ramp.prop(layer, "shadow_ramp_white", text=tr("Core", context))

                b_row = sc.row(align=True)
                b_row.operator("vfx.focus_shadow_ramp", text=tr("Open ColorRamp Node", context), icon='NODETREE').layer_id = layer.id

        # ── OCCLUSION (holdout) ──
        obox = box.box()
        ohead = obox.row(align=True)
        ohead.prop(layer, "occlusion_expanded", text="", icon='TRIA_DOWN' if layer.occlusion_expanded else 'TRIA_RIGHT', emboss=False)
        sp_occ = ohead.split(factor=0.4)
        sp_occ.label(text=tr("Occlusion:", context), icon='MOD_BOOLEAN')
        sp_occ.prop(layer, "occlusion_mode", text="")
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
                others = [l.layer_name for l in vfx.layers if l.id != layer.id and l.enabled]
                if others:
                    oc.label(text=f"Auto occluders: {len(others)}", icon='INFO')
            n_prox = 0
            if layer.occlusion_collection:
                n_prox = sum(1 for o in layer.occlusion_collection.objects if o.get("vfx_proxy"))
            oc.label(text=f"Holdout proxies: {n_prox}", icon='MOD_MESHDEFORM')

        # ── HOLDOUT MATTE (PNG SEQUENCE CUTOUT) ──
        mbox = box.box()
        mhead = mbox.row(align=True)
        mhead.prop(layer, "holdout_matte_expanded", text="",
                   icon='TRIA_DOWN' if layer.holdout_matte_expanded else 'TRIA_RIGHT', emboss=False)
        mhead.prop(layer, "use_holdout_matte", text="")
        mhead.label(text=tr("HOLDOUT MATTE", context), icon='MOD_MASK')
        if layer.use_holdout_matte:
            count, st_f, first_f, status = get_matte_sequence_info(layer.holdout_matte_path)
            if count > 0:
                mhead.label(text=f"{count} {tr('fr.', context)}", icon='CHECKMARK')

        if layer.holdout_matte_expanded and layer.use_holdout_matte:
            mc = mbox.column(align=True)
            p_row = mc.row(align=True)
            p_row.prop(layer, "holdout_matte_path", text=tr("Path:", context))
            p_row.operator("vfx.browse_layer_matte_folder", text="", icon='FILE_FOLDER').layer_id = layer.id

            count, st_f, first_f, status = get_matte_sequence_info(layer.holdout_matte_path)
            s_row = mc.row(align=True)
            if count > 0:
                s_row.label(text=f"{tr('Frames:', context)} {count} (st: {first_f})", icon='CHECKMARK')
            else:
                s_row.label(text=status, icon='ERROR')

            mc.separator()
            r_sync = mc.row(align=True)
            r_sync.prop(layer, "holdout_matte_frame_start", text=tr("Start", context))
            r_sync.prop(layer, "holdout_matte_frame_offset", text=tr("Offset", context))

            row_cfg = mc.row(align=True)
            row_cfg.prop(layer, "holdout_matte_invert", text=tr("Invert", context))
            row_cfg.prop(layer, "holdout_matte_mode", text="")

            r_edge = mc.row(align=True)
            r_edge.prop(layer, "holdout_matte_choke", text=tr("Choke (px)", context))
            r_edge.prop(layer, "holdout_matte_feather", text=tr("Feather", context))

            if layer.shadow_scene:
                mc.prop(layer, "holdout_matte_mask_shadow", text=tr("Cutout from Shadow", context))

            mc.separator()
            b_row = mc.row(align=True)
            b_row.operator("vfx.reload_layer_mask", text=tr("Refresh", context), icon='FILE_REFRESH').layer_id = layer.id
            if layer.holdout_matte_path:
                b_row.operator("vfx.open_layer_matte_folder", text=tr("Folder", context), icon='FOLDER_REDIRECT').layer_id = layer.id

        # ── LAYER OVERRIDES (DOF / GRADE) ──
        ext_box = box.box()
        ext_head = ext_box.row(align=True)
        ext_head.prop(layer, "extra_settings_expanded", text="",
                      icon='TRIA_DOWN' if layer.extra_settings_expanded else 'TRIA_RIGHT', emboss=False)
        ext_head.label(text=tr("LAYER OVERRIDES (DOF / GRADE)", context), icon='PREFERENCES')
        if getattr(vfx, "use_dof", False) and layer.blur_multiplier != 1.0:
            ext_head.label(text=f"DOF {layer.blur_multiplier:.1f}x", icon='CAMERA_DATA')
        if layer.grade_enable:
            ext_head.label(text="Grade ON", icon='COLOR')

        if layer.extra_settings_expanded:
            ext_col = ext_box.column(align=True)

            if getattr(vfx, "use_dof", False):
                blur_box = ext_col.box()
                blur_box.label(text=tr("Blur Influence", context), icon='CAMERA_DATA')
                bc = blur_box.column(align=True)
                bc.prop(layer, "blur_multiplier", text=tr("Blur Influence", context))
                bc.prop(layer, "blur_override", text=tr("Fixed Blur (px)", context))
                ext_col.separator()

            gr_head = ext_col.row(align=True)
            gr_head.prop(layer, "grade_enable", text="")
            gr_head.label(text=tr("Layer Grade", context), icon='COLOR')
            if layer.grade_enable:
                gc = ext_col.column(align=True)
                gc.prop(layer, "l_exposure", text=tr("Exposure", context))
                gc.prop(layer, "l_contrast", text=tr("Contrast", context))
                row_lgg = gc.row(align=True)
                row_lgg.prop(layer, "l_lift", text=tr("Lift (Shadows)", context))
                row_lgg.prop(layer, "l_gain", text=tr("Gain (Highlights)", context))
                gc.operator("vfx.reset_layer_grade", text=tr("Reset Grade", context), icon='LOOP_BACK')

            ext_col.separator()
            ext_col.prop(layer, "use_adjust", text=tr("Direct Material Adjust", context))
            if layer.use_adjust:
                adj = ext_col.column(align=True)
                adj.prop(layer, "exposure")
                adj.prop(layer, "contrast")
                adj.prop(layer, "saturation")
                adj.operator("vfx.reset_lighting", text=tr("Reset Material Lights", context), icon='LOOP_BACK')


# ---------------------------------------------------------------------
# BATCH RENDER & COMP SETUP
# ---------------------------------------------------------------------

def _draw_composite_order(context, layout):
    """Global composite sort mode + live display of the computed order."""
    vfx, master = get_project(context, allow_write=False)

    cbox = layout.box()
    head = cbox.row(align=True)
    head.prop(vfx, "comp_order_expanded", text="", emboss=False,
              icon='TRIA_DOWN' if vfx.comp_order_expanded else 'TRIA_RIGHT')
    sp_ord = head.split(factor=0.4)
    sp_ord.label(text=tr("Layer Order:", context), icon='SORTSIZE')
    sp_ord.prop(vfx, "composite_sort_mode", text="")
    if not vfx.comp_order_expanded:
        return
    seq = compute_composite_order(vfx, master, verbose=False)
    col = cbox.column(align=True)
    col.label(text="bottom -> top:", icon='TRIA_DOWN')
    if vfx.bg_scene is not None:
        col.label(text="  1. BACKGROUND", icon='WORLD')
    for idx, l in enumerate(seq, start=2 if vfx.bg_scene else 1):
        d = _camera_distance(master, l)
        suffix = f"  ({d:.1f}m)" if (d is not None and vfx.composite_sort_mode == 'AUTO') else ""
        col.label(text=f"  {idx}. {l.layer_name}{suffix}", icon='SCENE_DATA')


def _draw_render_settings(context, layout):
    """Batch render controls and settings."""
    vfx, master = get_project(context, allow_write=False)

    layout.separator()

    cprefs = bpy.context.preferences.addons.get('cycles')
    if cprefs and hasattr(cprefs, 'preferences') and getattr(cprefs.preferences, 'compute_device_type', 'NONE') == 'NONE':
        r_gpu = layout.row(align=True)
        r_gpu.alert = True
        r_gpu.operator("vfx.enable_gpu_compute", text=tr("Enable GPU (OptiX)", context), icon='LIGHT_SUN')

    # Two-tier render button hierarchy
    box_rnd = layout.box()
    col_rnd = box_rnd.column(align=True)

    # 1. Primary big render button (scale_y = 1.3)
    col_rnd.scale_y = 1.3
    col_rnd.operator(
        "vfx.one_click_exr",
        text=tr("1-Click: Render ALL + Comp", context),
        icon='FILE_IMAGE'
    )

    # 2. Secondary compact row: Resume | Selected | Mist Pass
    row_sub = col_rnd.row(align=True)
    row_sub.scale_y = 0.8
    row_sub.prop(vfx, "resume_render", text=tr("Resume", context), toggle=True)
    op_sel = row_sub.operator("vfx.one_click_exr_single", text=tr("Selected", context), icon='RESTRICT_SELECT_OFF')
    op_sel.only_selected = True
    row_sub.operator("vfx.render_fog_map", text=tr("Mist Pass", context), icon='FORCE_WIND')

    if vfx.render_running:
        bar = layout.column(align=True)
        bar.scale_y = 2.0
        bar.prop(vfx, "render_progress", slider=True, text="")
        layout.label(text=vfx.render_status, icon='RENDER_ANIMATION')
        layout.operator("vfx.stop_render", text=tr("STOP RENDER", context), icon='CANCEL')

    _draw_composite_order(context, layout)

    # Action buttons: Viewport Comp, Rebuild Comp, Diagnostics
    act_row = layout.row(align=True)
    act_row.operator("vfx.toggle_viewport_compositor", text=tr("Viewport Comp", context), icon='HIDE_OFF')
    act_row.operator("vfx.rebuild_comp", text=tr("Rebuild Comp", context), icon='FILE_REFRESH')
    act_row.operator("vfx.diagnostic", text="", icon='CONSOLE')

    # Collapsible Render Settings & Output
    set_box = layout.box()
    set_head = set_box.row(align=True)
    set_head.prop(vfx, "render_settings_expanded", text="",
                  icon='TRIA_DOWN' if vfx.render_settings_expanded else 'TRIA_RIGHT', emboss=False)
    set_head.label(text=tr("RENDER SETTINGS & PATHS", context), icon='PREFERENCES')

    if vfx.render_settings_expanded:
        set_col = set_box.column(align=True)
        set_col.prop(vfx, "output_dir", text=tr("Folder:", context))
        fmt_row = set_col.row(align=True)
        fmt_row.prop(vfx, "render_format", text=tr("Layers:", context))
        fmt_row.prop(vfx, "fog_map_format", text=tr("Mist:", context))
        eng_row = set_col.row(align=True)
        eng_row.prop(vfx, "objects_engine", text=tr("Objects:", context))
        eng_row.prop(vfx, "shadows_engine", text=tr("Shadows:", context))

        set_col.separator()
        p_row = set_col.row(align=True)
        p_row.prop(vfx, "use_parallel_render", text=tr("Parallel Render", context), icon='RESTRICT_RENDER_OFF')
        if vfx.use_parallel_render:
            p_row.prop(vfx, "parallel_instances", text=tr("Threads:", context))


# ---------------------------------------------------------------------
# POST EFFECTS (GLOBAL & PER-LAYER OVERRIDES)
# ---------------------------------------------------------------------

_CRYPTO_UI = {
    'crypto_on_fog': ('FOG', 'fog_crypto_amount', 'fog_crypto_objects'),
    'crypto_on_dof': ('DOF', 'dof_crypto_amount', 'dof_crypto_objects'),
    'crypto_on_glare': ('GLARE', 'glare_crypto_amount', 'glare_crypto_objects'),
    'crypto_on_grade': ('GRADE', 'grade_crypto_amount', 'grade_crypto_objects'),
    'crypto_on_colormatch': ('CM', 'cm_crypto_amount', 'cm_crypto_objects'),
}


def _draw_crypto_influence(context, layout, vfx, prop_name):
    """Compact per-effect crypto influence."""
    fx_key, amt_prop, objs_prop = _CRYPTO_UI[prop_name]
    active = bool(getattr(vfx, prop_name, False))
    amt = float(getattr(vfx, amt_prop, 0.0) or 0.0)
    if not active and abs(amt) <= 0.001:
        row = layout.row(align=True)
        row.prop(vfx, prop_name, text="", icon='EYEDROPPER', toggle=True)
        row.label(text=tr("Pick Objects (Crypto)", context), icon='BLANK1')
        return
    box = layout.box()
    box.prop(vfx, prop_name, text=tr("Pick Objects (Crypto)", context), icon='EYEDROPPER', toggle=True)
    box.prop(vfx, amt_prop)
    pk = box.row(align=True)
    op = pk.operator("vfx.pick_cryptomatte", text=tr("Pick Objects (Crypto)", context), icon='EYEDROPPER')
    op.target = 'INFLUENCE'
    op.fx_key = fx_key
    names = [n for n in (getattr(vfx, objs_prop, "") or "").split("|") if n.strip()]
    if names:
        shown = ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")
        box.label(text=shown, icon='OBJECT_DATA')


def _draw_mask_section(context, layout, vfx, prefix, sources):
    """Unified MASK sub-section for a masked effect."""
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
            pk.operator("vfx.pick_cryptomatte", text="Pick Object", icon='EYEDROPPER')
            if getattr(vfx, "crypto_pick_name", ""):
                pk.label(text=vfx.crypto_pick_name, icon='OBJECT_DATA')


def _draw_post_effects(context, layout):
    """Shared: draw post-processing effects and per-layer override system."""
    vfx, master = get_project(context, allow_write=False)

    box_fx = layout.box()
    head = box_fx.row(align=True)
    head.prop(vfx, "post_fx_expanded", text="",
              icon='TRIA_DOWN' if vfx.post_fx_expanded else 'TRIA_RIGHT', emboss=False)
    head.label(text=tr("POST EFFECTS (POST FX)", context), icon='COLOR')

    if not vfx.post_fx_expanded:
        return

    # Target Selector: [ All Layers (Master) | Layer Override ]
    t_box = box_fx.box()
    t_row = t_box.row(align=True)
    t_row.prop(vfx, "fx_target_mode", expand=True)

    # ── PER-LAYER OVERRIDES MODE ──
    if getattr(vfx, "fx_target_mode", 'ALL') == 'LAYER':
        if not vfx.layers:
            t_box.label(text="No layers created yet", icon='INFO')
            return

        # Selector buttons for layers
        lr = t_box.row(align=True)
        lr.label(text=tr("Layer:", context), icon='SCENE_DATA')
        for idx, l in enumerate(vfx.layers):
            is_active = (idx == vfx.active_layer_index)
            btn = lr.operator("vfx.select_override_layer", text=l.layer_name, depress=is_active)
            btn.layer_index = idx

        target_layer = active_layer(vfx)
        if target_layer:
            ov_box = box_fx.box()
            ov_box.label(text=f"{tr('LAYER OVERRIDES (DOF / GRADE)', context)}: {target_layer.layer_name}", icon='PREFERENCES')

            # Per-layer Blur / Depth of field
            b_row = ov_box.row(align=True)
            b_row.prop(target_layer, "blur_multiplier", text=tr("Blur Influence", context))
            b_row.prop(target_layer, "blur_override", text=tr("Fixed Blur (px)", context))

            ov_box.separator()

            # Per-layer Color Grade
            g_row = ov_box.row(align=True)
            g_row.prop(target_layer, "grade_enable", text="")
            g_row.label(text=tr("Layer Grade", context), icon='COLOR')
            if target_layer.grade_enable:
                gc = ov_box.column(align=True)
                gc.prop(target_layer, "l_exposure", text=tr("Exposure", context))
                gc.prop(target_layer, "l_contrast", text=tr("Contrast", context))
                row_lgg = gc.row(align=True)
                row_lgg.prop(target_layer, "l_lift", text=tr("Lift (Shadows)", context))
                row_lgg.prop(target_layer, "l_gain", text=tr("Gain (Highlights)", context))
                gc.operator("vfx.reset_layer_grade", text=tr("Reset Grade", context), icon='LOOP_BACK')

        return

    # ── GLOBAL MASTER EFFECTS MODE ──
    # ── FOG ──
    fogbox = box_fx.box()
    fr = fogbox.row(align=True)
    fr.operator("vfx.toggle_fog_expand", text="", icon='TRIA_DOWN' if vfx.fog_expanded else 'TRIA_RIGHT', emboss=False)
    fr.label(text=tr("ATMOSPHERIC FOG", context), icon='FORCE_WIND')
    fr.prop(vfx, "use_fog", text="")
    if vfx.fog_expanded:
        fb = fogbox.column(align=True)
        fr2 = fb.row(align=True)
        fr2.prop(vfx, "ramp_black", text=tr("Start:", context))
        fr2.prop(vfx, "ramp_white", text=tr("Full:", context))
        fb.prop(vfx, "fog_density", text=tr("Density:", context))
        fb.prop(vfx, "fog_color", text=tr("Fog Color:", context))
        _draw_crypto_influence(context, fb, vfx, "crypto_on_fog")
        row_rf = fb.row(align=True)
        row_rf.operator("vfx.render_fog_map", text=tr("Render Mist Sequence (EEVEE)", context), icon='RENDER_ANIMATION')
        row_rf.prop(vfx, "fog_map_format", text="")

    # ── DEPTH BLUR (DOF) ──
    dofbox = box_fx.box()
    dh = dofbox.row(align=True)
    dh.prop(vfx, "use_dof", text="")
    dh.label(text=tr("DEPTH BLUR (Mist / DOF)", context), icon='CAMERA_DATA')
    if vfx.use_dof:
        dc = dofbox.column(align=True)
        dr = dc.row(align=True)
        dr.prop(vfx, "dof_far_start", text=tr("Start:", context))
        dr.prop(vfx, "dof_far_end", text=tr("Full:", context))
        dc.prop(vfx, "dof_maxblur", text=tr("Blur Amount (px):", context))
        row_rd = dc.row(align=True)
        row_rd.operator("vfx.render_fog_map", text=tr("Render Mist Sequence (EEVEE)", context), icon='RENDER_ANIMATION')
        row_rd.prop(vfx, "fog_map_format", text="")
        _draw_crypto_influence(context, dc, vfx, "crypto_on_dof")
        _draw_mask_section(context, dc, vfx, "dof", ('ALPHA', 'DEPTH', 'LUMA', 'EXT'))

    # ── GLOW & GLARE ──
    glowbox = box_fx.box()
    gr = glowbox.row(align=True)
    gr.prop(vfx, "use_glare", text="")
    gr.label(text=tr("GLOW & GLARE", context), icon='LIGHT_SUN')
    if vfx.use_glare:
        gc = glowbox.column(align=True)
        gc.prop(vfx, "glare_type", text=tr("Type:", context))
        gc.prop(vfx, "glare_strength", text=tr("Brightness:", context))
        gc.prop(vfx, "glare_threshold", text=tr("Threshold:", context))
        gc.prop(vfx, "glare_size", text=tr("Size:", context))
        _draw_crypto_influence(context, gc, vfx, "crypto_on_glare")
        _draw_mask_section(context, gc, vfx, "glare", ('ALPHA', 'DEPTH', 'LUMA', 'EXT'))

    # ── LENS DISTORTION ──
    ldbox = box_fx.box()
    lbw = ldbox.row(align=True)
    lbw.prop(vfx, "use_lensdist", text="")
    lbw.label(text=tr("LENS DISTORTION", context), icon='VIEW_CAMERA')
    if vfx.use_lensdist:
        lc = ldbox.column(align=True)
        lc.prop(vfx, "lensdist_distort", text=tr("Distortion:", context))
        lc.prop(vfx, "lensdist_disperse", text=tr("Chromatic:", context))

    # ── COLOR GRADING (GRADE) ──
    mgbox = box_fx.box()
    mgr = mgbox.row(align=True)
    mgr.prop(vfx, "m_grade_enable", text="")
    mgr.label(text=tr("COLOR GRADING (Grade)", context), icon='COLOR')
    if vfx.m_grade_enable:
        mgc = mgbox.column(align=True)
        mgc.prop(vfx, "m_exposure", text=tr("Exposure", context))
        mgc.prop(vfx, "m_temp", text=tr("Temperature", context))
        mgc.prop(vfx, "m_tint", text=tr("Tint", context))
        mgc.prop(vfx, "m_saturation", text=tr("Saturation", context))
        mgc.prop(vfx, "m_contrast", text=tr("Contrast", context))
        row_lgg = mgc.row(align=True)
        row_lgg.prop(vfx, "m_lift", text=tr("Lift (Shadows)", context))
        row_lgg.prop(vfx, "m_gain", text=tr("Gain (Highlights)", context))
        row_btn = mgbox.row(align=True)
        row_btn.operator("vfx.reset_master_grade", text=tr("Reset", context), icon='LOOP_BACK')
        row_btn.operator("vfx.copy_master_grade", text=tr("Copy to Layers", context), icon='COPYDOWN')
        _draw_crypto_influence(context, mgc, vfx, "crypto_on_grade")
        _draw_mask_section(context, mgc, vfx, "grade", ('ALPHA', 'DEPTH', 'LUMA', 'EXT'))

    # ── ADVANCED COLOR MATCH & LIGHT GROUPS ──
    cmbox = box_fx.box()
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
        _draw_crypto_influence(context, cmc, vfx, "crypto_on_colormatch")

    box_lg = box_fx.box()
    bh = box_lg.row(align=True)
    bh.prop(vfx, "use_light_groups", text="")
    bh.label(text="LIGHT GROUPS", icon='LIGHT')
    if vfx.use_light_groups:
        box_lg.operator("vfx.setup_light_groups", icon='LIGHT')


# ---------------------------------------------------------------------
# FINAL EXPORT (VIDEO)
# ---------------------------------------------------------------------

def _draw_final_export(context, layout):
    """Draw Final Video Export controls."""
    vfx, master = get_project(context, allow_write=False)

    exp_box = layout.box()
    exp_head = exp_box.row(align=True)
    exp_head.prop(vfx, "final_export_expanded", text="",
                  icon='TRIA_DOWN' if vfx.final_export_expanded else 'TRIA_RIGHT', emboss=False)
    exp_head.label(text=tr("FINAL VIDEO EXPORT", context), icon='RENDER_ANIMATION')

    if exp_box and vfx.final_export_expanded:
        exp_col = exp_box.column(align=True)

        # Path with split layout
        r_p = exp_col.split(factor=0.25)
        r_p.label(text=tr("Path:", context))
        r_p.prop(vfx, "final_output_path", text="")

        # Format with split layout
        row_fmt = exp_col.split(factor=0.25)
        row_fmt.label(text=tr("Format:", context))
        row_fmt.prop(vfx, "final_format", text="")

        # Quality with split layout
        if vfx.final_format in ('MP4', 'PRORES'):
            row_q = exp_col.split(factor=0.25)
            row_q.label(text=tr("Quality:", context))
            row_q.prop(vfx, "final_quality", text="")

        exp_col.separator()
        row_turbo = exp_col.row(align=True)
        row_turbo.prop(vfx, "final_speedup", text=tr("Turbo Assemble (Workbench)", context), icon='FORWARD')

        exp_col.separator()
        btn_anim = exp_col.operator(
            "vfx.export_final_video",
            text=tr("RENDER VIDEO (ANIMATION)", context),
            icon='RENDER_ANIMATION'
        )
        btn_anim.animation = True

        sub_row = exp_col.row(align=True)
        btn_still = sub_row.operator(
            "vfx.export_final_video",
            text=tr("Still Frame", context),
            icon='IMAGE_DATA'
        )
        btn_still.animation = False
        sub_row.operator("vfx.open_output_folder", text=tr("Open Folder", context), icon='FILE_FOLDER')


# ---------------------------------------------------------------------
# PANEL — MAIN (View3D sidebar)
# ---------------------------------------------------------------------

class VFX_PT_main(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "VFX"
    bl_label = "VFX Layers & Comp"

    def draw(self, context):
        layout = self.layout

        if not hasattr(context.scene, "vfx"):
            layout.label(text="Scene.vfx not registered — restart Blender", icon='ERROR')
            return

        try:
            vfx, master = get_project(context, allow_write=False)

            # Master Scene selector
            top_row = layout.row(align=True)
            top_row.prop(vfx, "master_scene", text=tr("Master:", context))

            if vfx.master_scene is None:
                layout.label(text=tr("Master scene not set:", context), icon='INFO')
                layout.operator("vfx.set_master", text=tr("Set Master", context))

            # 1. Layers & Active Layer card
            _draw_layer_list(context, layout)

            # 2. Batch render & Comp controls
            _draw_render_settings(context, layout)

            # 3. Post Effects (Post FX) — located ABOVE final export!
            _draw_post_effects(context, layout)

            # 4. Final Export (Video)
            _draw_final_export(context, layout)

            # 5. Bottom Footer: Language Switcher [ Русский | English ] + Version
            layout.separator()
            foot = layout.row(align=True)
            foot.label(text="", icon='WORLD')
            foot.prop(vfx, "vfx_ui_lang", expand=True)
            foot.label(text=f"v{VFX_VERSION}")

        except Exception as e:
            layout.label(text="Panel draw error:", icon='ERROR')
            layout.label(text=str(e))

    # Keep draw_main for compositor panel compatibility
    def draw_main(self, context, layout):
        self.draw(context)


# ---------------------------------------------------------------------
# SUB-PANEL — POST EFFECTS (View3D sidebar)
# (Kept for compatibility, delegates to _draw_post_effects)
# ---------------------------------------------------------------------

class VFX_PT_post_effects(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "VFX"
    bl_label = "Post Effects"

    @classmethod
    def poll(cls, context):
        # We now render post effects directly inside the main unified pipeline panel!
        return False

    def draw(self, context):
        pass


# ---------------------------------------------------------------------
# PANEL — COMPOSITOR (Node Editor sidebar)
# ---------------------------------------------------------------------

class VFX_PT_compositor(bpy.types.Panel):
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "VFX"
    bl_label = "VFX Layers & Comp"

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


class VFX_PT_compositor_effects(bpy.types.Panel):
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "VFX"
    bl_label = "Post Effects"
    bl_parent_id = "VFX_PT_compositor"

    @classmethod
    def poll(cls, context):
        return False

    def draw(self, context):
        pass


# ---------------------------------------------------------------------
# PANEL — DEBUG / TEST (View3D sidebar)
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
