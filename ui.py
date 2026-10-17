"""VFX Layer Tools — UI panels and list widget."""

import bpy
import os

from . import VFX_VERSION
from .core import get_project, active_layer
from .materials import _last_adjust_stats
from .occlusion import compute_composite_order, _camera_distance
from .compositor import get_matte_sequence_info


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
        return f"{0}/{total}", 'BLANK1'

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
    return f"{0}/{total}", 'BLANK1'


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
# MAIN PANEL (View3D + NodeEditor)
# ---------------------------------------------------------------------

def _draw_layer_list(context, layout):
    """Shared: draw layer list + active layer box + buttons."""
    vfx, master = get_project(context, allow_write=False)

    layout.operator_context = 'INVOKE_DEFAULT'
    top_row = layout.row(align=True)
    top_row.operator("vfx.create_layer", text="Слой", icon='ADD')
    top_row.operator("vfx.add_background", text="Задник", icon='IMAGE_BACKGROUND')
    top_row.operator("vfx.assign_layer_mask_dialog", text="Маска...", icon='MOD_MASK')
    layout.operator("vfx.add_selected_lights", text="Add Selected Lights", icon='LIGHT')

    layout.template_list(
        "VFX_UL_layers",
        "",
        vfx,
        "layers",
        vfx,
        "active_layer_index"
    )

    row = layout.row(align=True)
    row.operator("vfx.move_layer_up", icon='TRIA_UP', text="Up / Forward")
    row.operator("vfx.move_layer_down", icon='TRIA_DOWN', text="Down / Back")

    # background strip
    has_bg = getattr(vfx, "use_bg", False) or (vfx.bg_scene is not None)
    if has_bg:
        bgbox = layout.box()
        r = bgbox.row(align=True)
        is_exp = getattr(vfx, "bg_expanded", True)
        r.prop(vfx, "bg_expanded", text="", icon='TRIA_DOWN' if is_exp else 'TRIA_RIGHT', emboss=False)
        r.prop(vfx, "use_bg", text="ЗАДНИК (Фон)", icon='IMAGE_BACKGROUND')
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
                v_row.prop(vfx, "bg_video_path", text="Видео")
                op = v_row.operator("vfx.browse_bg_file", text="", icon='FILE_FOLDER')
                op.file_type = 'VIDEO'
                f_row = col.row(align=True)
                f_row.prop(vfx, "bg_fit_mode", text="Размер")

            elif st == 'IMAGE':
                i_row = col.row(align=True)
                i_row.prop(vfx, "bg_image_path", text="Кадр")
                op = i_row.operator("vfx.browse_bg_file", text="", icon='FILE_FOLDER')
                op.file_type = 'IMAGE'
                f_row = col.row(align=True)
                f_row.prop(vfx, "bg_fit_mode", text="Размер")

            elif st == 'MOVIE_CLIP':
                c_row = col.row(align=True)
                c_row.prop(vfx, "bg_movie_clip", text="Клип")
                f_row = col.row(align=True)
                f_row.prop(vfx, "bg_fit_mode", text="Размер")

            elif st == 'SCENE':
                s_row = col.row(align=True)
                s_row.prop(vfx, "bg_scene", text="3D Сцена")
                if not vfx.bg_scene:
                    s_row.operator("vfx.create_background", text="Создать сцену", icon='ADD')
                st_row = col.row(align=True)
                st_row.prop(vfx, "bg_is_static", text="Статика (1 кадр)", icon='IMAGE_DATA')
                if vfx.bg_is_static:
                    st_row.prop(vfx, "bg_static_frame", text="Кадр")

            # Diagnostics: Check FPS mismatch and frame start
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
                    w_r.label(text=f"Рассинхрон FPS! Проект: {sc_fps:.1f}, Клип: {active_clip.fps:.1f} fps", icon='ERROR')
                    w_b = wbox.row()
                    w_b.operator("vfx.match_scene_fps", text=f"Синхронизировать FPS ({active_clip.fps:.2f})", icon='TIME')
            if active_clip and active_clip.frame_start > context.scene.frame_start:
                sbox = col.box()
                s_r = sbox.row()
                s_r.label(text=f"Кадр 0 пустой: клип с {active_clip.frame_start}, проект с {context.scene.frame_start}", icon='INFO')
                sbox.operator("vfx.match_frame_start", text=f"Поставить старт сцены на {active_clip.frame_start} кадр", icon='PLAY')
    else:
        layout.operator("vfx.add_background", text="+ Добавить слой фона (задник)", icon='IMAGE_BACKGROUND')

    layer = active_layer(vfx)

    if layer:
        box = layout.box()
        box.label(text=layer.layer_name, icon='SCENE_DATA')

        row = box.row(align=True)
        row.operator("vfx.select_layer_objects", text="Выделить объекты", icon='RESTRICT_SELECT_OFF')
        row.operator("vfx.add_selected_to_layer", text="+ Add", icon='PLUS')
        row.operator("vfx.remove_selected_from_layer", text="- Remove", icon='X')

        row = box.row(align=True)
        row.operator("vfx.rename_layer", text="Rename Layer")
        row.operator("vfx.delete_layer", text="Delete", icon='X')

        # Static 1-frame hold option
        st_row = box.row(align=True)
        st_row.prop(layer, "is_static", text="Статика (1 кадр)", icon='IMAGE_DATA')
        if layer.is_static:
            st_row.prop(layer, "static_frame", text="Кадр")
        if vfx.show_hints and layer.is_static:
            box.label(text="Рендерит только 1 кадр и удерживает его на всю анимацию (для Луны/фонов)", icon='HELP')

        # ── 3D SHADOW PASS & FILTER ──
        sbox = box.box()
        shead = sbox.row(align=True)
        shead.prop(layer, "shadow_expanded", text="",
                   icon='TRIA_DOWN' if layer.shadow_expanded else 'TRIA_RIGHT', emboss=False)
        if not layer.shadow_scene:
            shead.label(text="3D ТЕНЬ (PASS)", icon='LIGHT')
            if layer.shadow_catcher:
                shead.operator("vfx.create_shadow_pass", text="Создать пасс", icon='PLUS')
            else:
                shead.operator("vfx.create_shadow_pass", text="Добавить пасс", icon='PLUS')
        else:
            shead.label(text=f"3D ТЕНЬ ({layer.shadow_mode})", icon='CHECKMARK')
            shead.prop(layer, "shadow_strength", text="Сила")
            shead.operator("vfx.refresh_proxies", text="", icon='FILE_REFRESH', emboss=False)
            shead.operator("vfx.delete_shadow_pass", text="", icon='X', emboss=False)

        if layer.shadow_expanded:
            sc = sbox.column(align=True)
            sc.prop(layer, "shadow_catcher", text="Кетчер тени")
            if layer.shadow_catcher:
                b_row = sc.row(align=True)
                b_row.prop(layer, "shadow_catcher_bounce", text="Подсветка от кетчера (GI Bounce & Reflections)", icon='LIGHT_SUN')
                if vfx.show_hints and layer.shadow_catcher_bounce:
                    sc.label(text="Кетчер освещает 3D-объект снизу и отражается в глянцевых материалах.", icon='INFO')
            if not layer.shadow_scene:
                if layer.shadow_catcher:
                    r = sc.row(align=True)
                    r.alert = True
                    r.operator("vfx.create_shadow_pass", text="⚠ Нажмите: Создать 3D пасс тени", icon='LIGHT')
            else:
                sc.separator()
                r_main = sc.row(align=True)
                r_main.prop(layer, "shadow_color", text="Цвет")
                r_main.prop(layer, "shadow_samples", text="Семплы Cycles")

                r_filter = sc.row(align=True)
                r_filter.prop(layer, "shadow_use_ramp", text="ColorRamp")
                r_filter.prop(layer, "shadow_blur", text="Размытие (px)")

                if layer.shadow_use_ramp:
                    r_ramp = sc.row(align=True)
                    r_ramp.prop(layer, "shadow_ramp_black", text="Срез (шум)")
                    r_ramp.prop(layer, "shadow_ramp_white", text="Ядро (плотность)")

                if vfx.show_hints:
                    sc.label(text="Цвет и ColorRamp меняют оттенок и плотность тени в композиторе без перерендера.", icon='HELP')

                b_row = sc.row(align=True)
                b_row.operator("vfx.focus_shadow_ramp", text="Открыть ColorRamp в нодах", icon='NODETREE').layer_id = layer.id

        # ── OCCLUSION (holdout) ──
        obox = box.box()
        ohead = obox.row(align=True)
        ohead.prop(layer, "occlusion_expanded", text="", icon='TRIA_DOWN' if layer.occlusion_expanded else 'TRIA_RIGHT', emboss=False)
        ohead.label(text="ПЕРЕКРЫТИЕ (Окклюзия)", icon='MOD_BOOLEAN')
        ohead.prop(layer, "occlusion_mode", text="")
        if layer.occlusion_expanded:
            oc = obox.column(align=True)
            if vfx.show_hints:
                oc.label(text="Holdout делает вырез в слое там, где его перекрывают другие объекты в 3D.", icon='HELP')
            if layer.occlusion_mode == 'MANUAL':
                oc.label(text="Слои, вырезающие этот слой (окклюдеры):", icon='RESTRICT_RENDER_ON')
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
                    oc.label(text=f"Авто-перекрытие: все слои спереди ({len(others)})", icon='INFO')
                    oc.label(text=" | ".join(others), icon='DOT')
                else:
                    oc.label(text="Нет других слоев", icon='INFO')
            else:  # OFF
                oc.label(text="Окклюзия выключена (слой рендерится целиком без вырезов)", icon='X')
            n_prox = 0
            if layer.occlusion_collection:
                n_prox = sum(1 for o in layer.occlusion_collection.objects
                             if o.get("vfx_proxy"))
            oc.label(text=f"Holdout-прокси: {n_prox}", icon='MOD_MESHDEFORM')

        # ── HOLDOUT MATTE (PNG SEQUENCE CUTOUT) ──
        mbox = box.box()
        mhead = mbox.row(align=True)
        mhead.prop(layer, "holdout_matte_expanded", text="",
                   icon='TRIA_DOWN' if layer.holdout_matte_expanded else 'TRIA_RIGHT', emboss=False)
        mhead.prop(layer, "use_holdout_matte", text="")
        mhead.label(text="МАСКА ВЫРЕЗА (PNG СЕКВЕНЦИЯ)", icon='MOD_MASK')
        if layer.use_holdout_matte:
            count, st_f, first_f, status = get_matte_sequence_info(layer.holdout_matte_path)
            if count > 0:
                mhead.label(text=f"{count} к.", icon='CHECKMARK')

        if layer.holdout_matte_expanded and layer.use_holdout_matte:
            mc = mbox.column(align=True)
            if vfx.show_hints:
                mc.label(text="Вычитает 3D-слой по внешней PNG-маске (ротоскоп/кеинг из папки):", icon='HELP')

            p_row = mc.row(align=True)
            p_row.prop(layer, "holdout_matte_path", text="Папка/Файл")
            p_row.operator("vfx.browse_layer_matte_folder", text="", icon='FILE_FOLDER').layer_id = layer.id

            count, st_f, first_f, status = get_matte_sequence_info(layer.holdout_matte_path)
            s_row = mc.row(align=True)
            if count > 0:
                s_row.label(text=f"{status} (старт: {first_f})", icon='CHECKMARK')
            else:
                s_row.label(text=status, icon='ERROR')

            mc.separator()
            r_sync = mc.row(align=True)
            r_sync.prop(layer, "holdout_matte_frame_start", text="Старт маски")
            r_sync.prop(layer, "holdout_matte_frame_offset", text="Сдвиг кадров")

            row_cfg = mc.row(align=True)
            row_cfg.prop(layer, "holdout_matte_invert", text="Инвертировать")
            row_cfg.prop(layer, "holdout_matte_mode", text="")

            r_edge = mc.row(align=True)
            r_edge.prop(layer, "holdout_matte_choke", text="Сжатие (px)")
            r_edge.prop(layer, "holdout_matte_feather", text="Смягчение (px)")

            if vfx.show_hints:
                mc.label(text="Отрицательное сжатие (например, -3 px) убирает зазоры вокруг тонких столбов и проводов.", icon='INFO')

            if layer.shadow_scene:
                mc.prop(layer, "holdout_matte_mask_shadow", text="Вырезать также из тени слоя")

            mc.separator()
            b_row = mc.row(align=True)
            b_row.operator("vfx.reload_layer_mask", text="Обновить маску", icon='FILE_REFRESH').layer_id = layer.id
            if layer.holdout_matte_path:
                b_row.operator("vfx.open_layer_matte_folder", text="Открыть папку", icon='FOLDER_REDIRECT').layer_id = layer.id

        # ── EXTRA LAYER ADJUSTMENTS (Collapsible fine-tuning) ──
        ext_box = box.box()
        ext_head = ext_box.row(align=True)
        ext_head.prop(layer, "extra_settings_expanded", text="",
                      icon='TRIA_DOWN' if layer.extra_settings_expanded else 'TRIA_RIGHT', emboss=False)
        ext_head.label(text="ДОП. НАСТРОЙКИ СЛОЯ", icon='PREFERENCES')
        if getattr(vfx, "use_dof", False) and layer.blur_multiplier != 1.0:
            ext_head.label(text=f"Блюр {layer.blur_multiplier:.1f}x", icon='CAMERA_DATA')
        if layer.grade_enable:
            ext_head.label(text="Грейд ВКЛ", icon='COLOR')

        if layer.extra_settings_expanded:
            ext_col = ext_box.column(align=True)
            if vfx.show_hints:
                ext_col.label(text="Тонкая настройка цвета, масок и пипетки Cryptomatte ДЛЯ ЭТОГО СЛОЯ:", icon='HELP')

            # Per-layer Blur / Depth of field fine-tuning
            if getattr(vfx, "use_dof", False):
                blur_box = ext_col.box()
                blur_box.label(text="Подстройка размытия (DOF)", icon='CAMERA_DATA')
                bc = blur_box.column(align=True)
                bc.prop(layer, "blur_multiplier", text="Влияние блюра")
                if layer.blur_multiplier == 0.0:
                    bc.label(text="Слой останется идеально резким", icon='INFO')
                elif layer.blur_multiplier != 1.0:
                    bc.label(text=f"Блюр масштабирован в {layer.blur_multiplier:.1f}x", icon='INFO')
                bc.prop(layer, "blur_override", text="Фикс. блюр (px)")
                if layer.blur_override > 0.0:
                    bc.label(text=f"Переопределено: {layer.blur_override:.0f} px (игнорирует карту)", icon='INFO')
                ext_col.separator()
            gr_head = ext_col.row(align=True)
            gr_head.prop(layer, "grade_enable", text="")
            gr_head.label(text="Цветокоррекция слоя (Grade)", icon='COLOR')
            if layer.grade_enable:
                gc = ext_col.column(align=True)
                gc.prop(layer, "l_exposure")
                gc.prop(layer, "l_temp")
                gc.prop(layer, "l_tint")
                gc.prop(layer, "l_sat")
                gc.prop(layer, "l_contrast")
                row_lgg = gc.row(align=True)
                row_lgg.prop(layer, "l_lift", text="Lift")
                row_lgg.prop(layer, "l_gain", text="Gain")
                gc.operator("vfx.reset_layer_grade", text="Сбросить грейд", icon='LOOP_BACK')

                # Masking for per-layer grade
                gc.separator()
                lam = gc.row(align=True)
                lam.prop(layer, "use_alpha_mask", text="По силуэту слоя", icon='MOD_MASK')
                if not layer.use_alpha_mask:
                    gc.prop(layer, "grade_mask_source", text="Маска")
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
                        pk = gc.operator("vfx.pick_cryptomatte", text="Пипетка Cryptomatte (объект)", icon='EYEDROPPER')
                        pk.target = 'LAYER'
                    r3 = gc.row(align=True)
                    r3.prop(layer, "grade_mask_invert")
                    r3.prop(layer, "grade_mask_soft")

            ext_col.separator()
            # Material Lighting Adjust
            ext_col.prop(layer, "use_adjust", text="Прямая подстройка материалов")
            if layer.use_adjust:
                if vfx.show_hints:
                    ext_col.label(text="Меняет экспозицию/контраст прямо в Principled BSDF материалах слоя", icon='HELP')
                st = _last_adjust_stats
                if st["notes"]:
                    for n in st["notes"]:
                        ext_col.label(text=n, icon='ERROR')
                else:
                    ext_col.label(text=f"Настроено {st['applied']}/{st['materials']} материалов", icon='CHECKMARK')
                adj = ext_col.column(align=True)
                adj.prop(layer, "exposure")
                adj.prop(layer, "contrast")
                adj.prop(layer, "saturation")
                adj.prop(layer, "tint_strength")
                if layer.tint_strength > 0.0:
                    adj.prop(layer, "tint_color", text="")
                adj.operator("vfx.reset_lighting", text="Сбросить свет материалов", icon='LOOP_BACK')


_CRYPTO_UI = {
    # ui prop_name: (fx_key, amount_prop, objects_prop)
    'crypto_on_fog': ('FOG', 'fog_crypto_amount', 'fog_crypto_objects'),
    'crypto_on_dof': ('DOF', 'dof_crypto_amount', 'dof_crypto_objects'),
    'crypto_on_glare': ('GLARE', 'glare_crypto_amount', 'glare_crypto_objects'),
    'crypto_on_grade': ('GRADE', 'grade_crypto_amount', 'grade_crypto_objects'),
    'crypto_on_colormatch': ('CM', 'cm_crypto_amount', 'cm_crypto_objects'),
}


def _draw_crypto_influence(context, layout, vfx, prop_name):
    """Compact per-effect crypto influence: own toggle, own amount and
    its own picked object set (independent of every other effect)."""
    fx_key, amt_prop, objs_prop = _CRYPTO_UI[prop_name]
    active = bool(getattr(vfx, prop_name, False))
    amt = float(getattr(vfx, amt_prop, 0.0) or 0.0)
    if not active and abs(amt) <= 0.001:
        row = layout.row(align=True)
        row.prop(vfx, prop_name, text="", icon='EYEDROPPER', toggle=True)
        row.label(text="Crypto Influence", icon='BLANK1')
        return
    box = layout.box()
    box.prop(vfx, prop_name, icon='EYEDROPPER', toggle=True)
    box.prop(vfx, amt_prop)
    pk = box.row(align=True)
    op = pk.operator("vfx.pick_cryptomatte", text="Pick Objects", icon='EYEDROPPER')
    op.target = 'INFLUENCE'
    op.fx_key = fx_key
    names = [n for n in (getattr(vfx, objs_prop, "") or "").split("|") if n.strip()]
    if names:
        shown = ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")
        box.label(text=shown, icon='OBJECT_DATA')
    else:
        box.label(text="nothing picked yet", icon='INFO')


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
    fr.label(text="ТУМАН (Fog)", icon='FORCE_WIND')
    fr.prop(vfx, "use_fog", text="")
    if vfx.fog_expanded:
        fb = fogbox.column(align=True)
        if getattr(vfx, "show_hints", False):
            fb.label(text="Атмосферный туман по глубине камеры (в метрах):", icon='HELP')
        fr2 = fb.row(align=True)
        fr2.prop(vfx, "ramp_black", text="Начало")
        fr2.prop(vfx, "ramp_white", text="Максимум")
        fb.prop(vfx, "fog_density", text="Плотность")
        fb.prop(vfx, "fog_color", text="Цвет тумана")
        _draw_crypto_influence(context, fb, vfx, "crypto_on_fog")
        row_rf = fb.row(align=True)
        row_rf.operator("vfx.render_fog_map", text="Рендер секвенции Mist (EEVEE)", icon='RENDER_ANIMATION')
        row_rf.prop(vfx, "fog_map_format", text="")
    dofbox = layout.box()
    dh = dofbox.row(align=True)
    dh.prop(vfx, "use_dof", text="")
    dh.label(text="РАЗМЫТИЕ ПО ГЛУБИНЕ (Mist / DOF)", icon='CAMERA_DATA')
    if vfx.use_dof:
        dc = dofbox.column(align=True)
        if getattr(vfx, "show_hints", False):
            dc.label(text="Размытие заднего плана по карте Mist (редактируйте ColorRamp в группе VFX_DOF_PRE)", icon='HELP')
        dr = dc.row(align=True)
        dr.prop(vfx, "dof_far_start", text="Начало")
        dr.prop(vfx, "dof_far_end", text="Максимум")
        dc.prop(vfx, "dof_maxblur", text="Сила размытия (px)")
        row_rd = dc.row(align=True)
        row_rd.operator("vfx.render_fog_map", text="Рендер секвенции Mist (EEVEE)", icon='RENDER_ANIMATION')
        row_rd.prop(vfx, "fog_map_format", text="")
        _draw_crypto_influence(context, dc, vfx, "crypto_on_dof")
        _draw_mask_section(context, dc, vfx, "dof", ('ALPHA', 'DEPTH', 'LUMA', 'EXT'))
    glowbox = layout.box()
    gr = glowbox.row(align=True)
    gr.prop(vfx, "use_glare", text="")
    gr.label(text="СВЕЧЕНИЕ / БЛИКИ (Glow)", icon='LIGHT_SUN')
    if vfx.use_glare:
        gc = glowbox.column(align=True)
        if getattr(vfx, "show_hints", False):
            gc.label(text="Оптическое свечение (Bloom) вокруг ярких источников света и магии", icon='HELP')
        gc.prop(vfx, "glare_type", text="Тип")
        gc.prop(vfx, "glare_strength", text="Яркость")
        gc.prop(vfx, "glare_threshold", text="Порог яркости")
        gc.prop(vfx, "glare_size", text="Размер ореола")
        _draw_crypto_influence(context, gc, vfx, "crypto_on_glare")
        _draw_mask_section(context, gc, vfx, "glare", ('ALPHA', 'DEPTH', 'LUMA', 'EXT'))
    ldbox = layout.box()
    lbw = ldbox.row(align=True)
    lbw.prop(vfx, "use_lensdist", text="")
    lbw.label(text="ИСКАЖЕНИЯ ЛИНЗЫ (Distortion)", icon='VIEW_CAMERA')
    if vfx.use_lensdist:
        lc = ldbox.column(align=True)
        if getattr(vfx, "show_hints", False):
            lc.label(text="Дисторсия объектива и радужная кайма (хроматика) по краям кадра", icon='HELP')
        lc.prop(vfx, "lensdist_distort", text="Дисторсия")
        lc.prop(vfx, "lensdist_disperse", text="Хроматика")
    # ── MASTER GRADE (primary correction, applied first in chain) ──
    mgbox = layout.box()
    mgr = mgbox.row(align=True)
    mgr.prop(vfx, "m_grade_enable", text="")
    mgr.label(text="ОБЩИЙ ЦВЕТОКОР (Master Grade)", icon='COLOR')
    if vfx.m_grade_enable:
        mgc = mgbox.column(align=True)
        if getattr(vfx, "show_hints", False):
            mgc.label(text="Финальная общая цветокоррекция поверх всего собранного кадра", icon='HELP')
        mgc.prop(vfx, "m_exposure", text="Экспозиция")
        mgc.prop(vfx, "m_temp", text="Температура")
        mgc.prop(vfx, "m_tint", text="Тинт")
        mgc.prop(vfx, "m_saturation", text="Насыщенность")
        mgc.prop(vfx, "m_contrast", text="Контраст")
        row_lgg = mgc.row(align=True)
        row_lgg.prop(vfx, "m_lift", text="Lift (Тени)")
        row_lgg.prop(vfx, "m_gain", text="Gain (Света)")
        row_btn = mgbox.row(align=True)
        row_btn.operator("vfx.reset_master_grade", text="Сбросить", icon='LOOP_BACK')
        row_btn.operator("vfx.copy_master_grade", text="Скопировать на слои", icon='COPYDOWN')
        _draw_crypto_influence(context, mgc, vfx, "crypto_on_grade")
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
        _draw_crypto_influence(context, cmc, vfx, "crypto_on_colormatch")

    # Light Groups
    box = layout.box()
    bh = box.row(align=True)
    bh.prop(vfx, "use_light_groups", text="")
    bh.label(text="LIGHT GROUPS", icon='LIGHT')
    if vfx.use_light_groups:
        box.operator("vfx.setup_light_groups", icon='LIGHT')
        box.label(text="Auto-assigns Key/Fill/Rim/Env", icon='INFO')

    # Quick export shortcut
    layout.separator()
    pe_box = layout.box()
    pe_row = pe_box.row(align=True)
    pe_row.scale_y = 1.3
    op_v = pe_row.operator("vfx.export_final_video", text="РЕНДЕР ВИДЕО", icon='PLAY')
    op_v.animation = True
    pe_row.operator("vfx.open_output_folder", text="", icon='FILE_FOLDER')


def _draw_composite_order(context, layout):
    """Global composite sort mode + live display of the computed order."""
    vfx, master = get_project(context, allow_write=False)

    cbox = layout.box()
    head = cbox.row(align=True)
    head.prop(vfx, "comp_order_expanded", text="", emboss=False,
              icon='TRIA_DOWN' if vfx.comp_order_expanded else 'TRIA_RIGHT')
    head.label(text="ПОРЯДОК НАЛОЖЕНИЯ СЛОЕВ", icon='SORTSIZE')
    head.prop(vfx, "composite_sort_mode", text="")
    if not vfx.comp_order_expanded:
        return
    seq = compute_composite_order(vfx, master, verbose=False)
    col = cbox.column(align=True)
    if getattr(vfx, "show_hints", False):
        col.label(text="Порядок наложения в композиторе (снизу вверх):", icon='HELP')
    col.label(text="снизу -> вверх:", icon='TRIA_DOWN')
    if vfx.bg_scene is not None:
        col.label(text="  1. ФОН (BACKGROUND)", icon='WORLD')
    for idx, l in enumerate(seq, start=2 if vfx.bg_scene else 1):
        d = _camera_distance(master, l)
        suffix = f"  ({d:.1f}м)" if (d is not None and vfx.composite_sort_mode == 'AUTO') else ""
        col.label(text=f"  {idx}. {l.layer_name}{suffix}", icon='SCENE_DATA')
    if vfx.composite_sort_mode == 'AUTO':
        col.label(text="ближайшие объекты сверху", icon='INFO')


def _draw_render_settings(context, layout):
    """Shared: render/output first (primary workflow), then engines/rebuild."""
    vfx, master = get_project(context, allow_write=False)

    layout.separator()

    cprefs = bpy.context.preferences.addons.get('cycles')
    if cprefs and hasattr(cprefs, 'preferences') and getattr(cprefs.preferences, 'compute_device_type', 'NONE') == 'NONE':
        r_gpu = layout.row(align=True)
        r_gpu.alert = True
        r_gpu.operator("vfx.enable_gpu_compute", text="⚡ Включить GPU (OptiX) для рендера", icon='LIGHT_SUN')

    layout.prop(vfx, "resume_render", icon='RECOVER_LAST')

    layout.operator(
        "vfx.one_click_exr",
        text="1-Клик: Рендер ВСЕХ слоев + Композ",
        icon='FILE_IMAGE'
    )

    row = layout.row(align=True)
    row.operator(
        "vfx.one_click_exr_single",
        text="1-Клик: ВЫБРАННЫЕ слои",
        icon='IMAGE_ZDEPTH'
    )
    row.operator(
        "vfx.render_fog_map",
        text="Рендер Mist (EEVEE)",
        icon='FORCE_WIND'
    )

    if vfx.render_running:
        bar = layout.column(align=True)
        bar.scale_y = 2.0
        bar.prop(vfx, "render_progress", slider=True, text="")
        layout.label(text=vfx.render_status, icon='RENDER_ANIMATION')
        layout.operator("vfx.stop_render", text="ОСТАНОВИТЬ РЕНДЕР", icon='CANCEL')

    _draw_composite_order(context, layout)

    # Action buttons: Rebuild, Viewport, Diagnostic
    act_row = layout.row(align=True)
    act_row.operator("vfx.viewport_render" if False else "vfx.toggle_viewport_compositor", text="Композ во вьюпорте", icon='HIDE_OFF')
    act_row.operator("vfx.rebuild_comp", text="Пересобрать композ", icon='FILE_REFRESH')
    act_row.operator("vfx.diagnostic", text="", icon='CONSOLE')

    # Collapsible Render Settings & Output
    set_box = layout.box()
    set_head = set_box.row(align=True)
    set_head.prop(vfx, "render_settings_expanded", text="",
                  icon='TRIA_DOWN' if vfx.render_settings_expanded else 'TRIA_RIGHT', emboss=False)
    set_head.label(text="НАСТРОЙКИ РЕНДЕРА И ПАПКА", icon='PREFERENCES')

    if vfx.render_settings_expanded:
        set_col = set_box.column(align=True)
        if getattr(vfx, "show_hints", False):
            set_col.label(text="Выбор движков рендера отдельно для объектов и теней:", icon='HELP')
        set_col.prop(vfx, "output_dir", text="Папка")
        fmt_row = set_col.row(align=True)
        fmt_row.prop(vfx, "render_format", text="Слои")
        fmt_row.prop(vfx, "fog_map_format", text="Mist")
        eng_row = set_col.row(align=True)
        eng_row.prop(vfx, "objects_engine", text="Объекты")
        eng_row.prop(vfx, "shadows_engine", text="Тени")

        set_col.separator()
        p_row = set_col.row(align=True)
        p_row.prop(vfx, "use_parallel_render", icon='RESTRICT_RENDER_OFF')
        if vfx.use_parallel_render:
            p_row.prop(vfx, "parallel_instances", text="Потоков")

    # Collapsible Final Export (Video / Animation)
    exp_box = layout.box()
    exp_head = exp_box.row(align=True)
    exp_head.prop(vfx, "final_export_expanded", text="",
                  icon='TRIA_DOWN' if vfx.final_export_expanded else 'TRIA_RIGHT', emboss=False)
    exp_head.label(text="ФИНАЛЬНЫЙ ЭКСПОРТ (ВИДЕО)", icon='RENDER_ANIMATION')

    if vfx.final_export_expanded:
        exp_col = exp_box.column(align=True)
        if getattr(vfx, "show_hints", False):
            exp_col.label(text="Быстрая сборка итогового видео из композитора со всеми эффектами:", icon='HELP')
        exp_col.prop(vfx, "final_output_path", text="Куда")
        row_fmt = exp_col.row(align=True)
        row_fmt.prop(vfx, "final_format", text="Формат")
        if vfx.final_format in ('MP4', 'PRORES'):
            row_fmt.prop(vfx, "final_quality", text="Качество")

        exp_col.separator()
        row_turbo = exp_col.row(align=True)
        row_turbo.prop(vfx, "final_speedup", text="Турбо-сборка (Workbench)", icon='FORWARD')
        if vfx.final_speedup and getattr(vfx, "show_hints", False):
            exp_col.label(text="0.1-0.2 сек/кадр: читает готовые EXR слои без 3D-трассировки Cycles", icon='INFO')

        exp_col.separator()
        btn_anim = exp_col.operator("vfx.export_final_video", text="РЕНДЕР ВИДЕО (АНИМАЦИЯ)", icon='PLAY')
        btn_anim.animation = True

        sub_row = exp_col.row(align=True)
        btn_still = sub_row.operator("vfx.export_final_video", text="Текущий кадр", icon='IMAGE_DATA')
        btn_still.animation = False
        sub_row.operator("vfx.open_output_folder", text="Открыть папку", icon='FILE_FOLDER')



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
            top_row = layout.row(align=True)
            top_row.label(text=f"VFX Layer Tools v{VFX_VERSION}", icon='NODETREE')
            top_row.prop(vfx, "show_hints", text="Подсказки", icon='HELP', toggle=True)
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
