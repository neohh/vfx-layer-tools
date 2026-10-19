bl_info = {
    "name": "VFX Layer Tools",
    "author": "VFX Pipeline",
    "version": (3, 24, 0),
    "blender": (5, 2, 1),
    "location": "View3D > Sidebar > VFX",
    "description": "VFX layer / scene / compositing manager",
    "category": "Compositing",
}

VFX_VERSION = "3.24.0"

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


def _dof_changed(ctx):
    _auto_mask(ctx, 'DOF')
    try:
        sc = ctx.scene
        cam_obj = sc.camera or bpy.data.objects.get("VFX_Camera")
        if cam_obj and cam_obj.data and hasattr(cam_obj.data, "dof"):
            cam_obj.data.dof.focus_distance = sc.vfx.dof_focus
            cam_obj.data.show_limits = True
    except Exception:
        pass
    _trigger_comp(ctx)


def _on_shadow_catcher_update(layer, context):
    try:
        from .core import get_project
        from .shadow import refresh_shadow_proxies, sync_layer_bounce_proxy
        vfx, master = get_project(context, allow_write=True)
        if getattr(layer, "shadow_scene", None):
            refresh_shadow_proxies(vfx, master)
        else:
            sync_layer_bounce_proxy(layer, master)
    except Exception:
        pass


def _on_shadow_catcher_bounce_update(layer, context):
    try:
        from .core import get_project
        from .shadow import sync_layer_bounce_proxy
        vfx, master = get_project(context, allow_write=True)
        sync_layer_bounce_proxy(layer, master)
    except Exception:
        pass


def _on_shadow_samples_update(layer, context):
    if getattr(layer, "shadow_scene", None) and hasattr(layer.shadow_scene, "cycles"):
        try:
            layer.shadow_scene.cycles.samples = max(1, getattr(layer, "shadow_samples", 8))
        except Exception:
            pass


# ---------------------------------------------------------------------
# ENGINE ITEMS
# ---------------------------------------------------------------------

def _engine_label(i):
    return i.replace("BLENDER_", "").replace("_", " ").title()


_ENGINE_EEVEE_CACHE = []
_ENGINE_CYCLES_CACHE = []


def _engine_items_eevee_first(self, context):
    global _ENGINE_EEVEE_CACHE
    ids = [i.identifier for i in bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items]
    if "CYCLES" not in ids:
        ids = ["CYCLES"] + ids
    pref = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in ids else (ids[0] if ids else "CYCLES")
    ordered = [pref] + [i for i in ids if i != pref]
    _ENGINE_EEVEE_CACHE = [(i, _engine_label(i), "") for i in ordered]
    return _ENGINE_EEVEE_CACHE


def _engine_items_cycles_first(self, context):
    global _ENGINE_CYCLES_CACHE
    ids = [i.identifier for i in bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items]
    if "CYCLES" not in ids:
        ids = ["CYCLES"] + ids
    ordered = ["CYCLES"] + [i for i in ids if i != "CYCLES"]
    _ENGINE_CYCLES_CACHE = [(i, _engine_label(i), "") for i in ordered]
    return _ENGINE_CYCLES_CACHE


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
    selected: BoolProperty(
        name="Selected",
        description="Select layer for batch operations (e.g. Render Selected)",
        default=False
    )

    collection: PointerProperty(type=bpy.types.Collection)
    scene: PointerProperty(type=bpy.types.Scene)

    use_shadow: BoolProperty(default=False)
    shadow_tech: EnumProperty(
        name="Технология тени",
        items=(
            ('3D', "3D Catcher (Cycles)", "Классическая честная 3D-тень через Shadow Catcher на геометрии сцены"),
            ('AI', "Нейротень (ComfyUI)", "Сгенерированная нейросетью реалистичная тень (идеально для 2D-плейнов/персонажей)"),
        ),
        default='3D',
        description="Тип тени: честная 3D тень Cycles или сгенерированная нейросетью AI Shadow"
    )
    shadow_mode: EnumProperty(
        name="Shadow Mode",
        items=(
            ('CAST', "Cast", "Layer objects cast shadow onto catcher"),
            ('RECEIVE', "Receive", "Layer receives shadows from other layers"),
        ),
        default='CAST'
    )
    shadow_scene: PointerProperty(type=bpy.types.Scene)
    shadow_catcher: PointerProperty(
        type=bpy.types.Object,
        name="Shadow Catcher",
        description="Object that catches shadows from this layer",
        update=_on_shadow_catcher_update,
    )
    shadow_catcher_bounce: BoolProperty(
        name="Подсветка и рефлексы (GI Bounce)",
        description="Кетчер освещает 3D-объекты слоя снизу (диффузный отскок света, рефлексы в глянцевых материалах, контактный AO), оставаясь невидимым для камеры",
        default=True,
        update=_on_shadow_catcher_bounce_update,
    )
    shadow_samples: IntProperty(
        name="Семплы тени",
        default=8,
        min=1,
        max=4096,
        description="Количество семплов Cycles для пасса тени (для прямого света и мягкой тени обычно достаточно 3-8)",
        update=_on_shadow_samples_update,
    )

    # -- AI Shadow settings --
    ai_shadow_blend_mode: EnumProperty(
        name="Режим наложения AI",
        items=(
            ('MULTIPLY', "Multiply", "Классическое умножение — сохраняет текстуру подложки и цвет фона"),
            ('DARKEN', "Darken", "Затемнение — предотвращает излишне глубокую черноту"),
        ),
        default='MULTIPLY',
        update=lambda self, ctx: _trigger_comp(ctx)
    )
    ai_shadow_use_depth_clip: BoolProperty(
        name="Отсекать по глубине (Z)",
        description="Не накладывать тень поверх объектов, находящихся ближе к камере",
        default=True,
        update=lambda self, ctx: _trigger_comp(ctx)
    )

    shadow_cast_collection: PointerProperty(type=bpy.types.Collection)
    shadow_catch_collection: PointerProperty(type=bpy.types.Collection)
    shadow_bounce_collection: PointerProperty(type=bpy.types.Collection)

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
    shadow_expanded: BoolProperty(
        name="Настройки тени",
        description="Развернуть блок тонкой настройки тени (ColorRamp, цвет, срез, размытие)",
        default=True,
    )
    shadow_color: FloatVectorProperty(
        name="Цвет тени",
        subtype='COLOR',
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
        description="Цвет и оттенок тени (позволяет подкрасить тень под цвет окружения/неба)",
        update=lambda self, ctx: _trigger_comp(ctx),
    )
    shadow_use_ramp: BoolProperty(
        name="ColorRamp",
        description="Фильтровать тень через ColorRamp (срез шума, плотность ядра, точный цвет)",
        default=True,
        update=lambda self, ctx: _trigger_comp(ctx),
    )
    shadow_ramp_black: FloatProperty(
        name="Срез (шум)",
        description="Отсечение слабой тени и краевого шума (положение черной точки в ColorRamp)",
        default=0.0,
        min=0.0,
        max=0.99,
        update=lambda self, ctx: _trigger_comp(ctx),
    )
    shadow_ramp_white: FloatProperty(
        name="Ядро (плотность)",
        description="Плотность ядра тени (положение белой точки в ColorRamp: меньшие значения делают тень темнее и плотнее)",
        default=1.0,
        min=0.01,
        max=1.0,
        update=lambda self, ctx: _trigger_comp(ctx),
    )
    shadow_blur: IntProperty(
        name="Размытие (px)",
        description="Быстрое размытие тени в композиторе (смягчение краев без необходимости перерендеривать Cycles с большими углами света)",
        default=0,
        min=0,
        max=100,
        update=lambda self, ctx: _trigger_comp(ctx),
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

    # -- Static (single frame) render & hold --
    is_static: BoolProperty(
        name="Static (1 Frame)",
        description="Render only a single frame and hold it across the whole project duration",
        default=False,
        update=lambda self, ctx: _trigger_comp(ctx)
    )
    static_frame: IntProperty(
        name="Static Frame",
        description="Frame number to render and hold",
        default=1,
        min=0,
        update=lambda self, ctx: _trigger_comp(ctx)
    )

    # -- Layer Blend Mode & Target --
    blend_mode: EnumProperty(
        name="Blend Mode",
        description="How this layer blends with underlying layers or background",
        items=(
            ('ALPHA_OVER', "Alpha Over (Normal)", "Normal over compositing (default)"),
            ('OVERLAY', "Overlay", "Overlay: lightens highlights, darkens shadows (ideal for decals/graffiti on ground/walls)"),
            ('SOFT_LIGHT', "Soft Light", "Soft Light: gentler contrast blend"),
            ('MULTIPLY', "Multiply", "Multiply: darkens (ideal for dirt, stains, shadows)"),
            ('SCREEN', "Screen", "Screen: lightens (ideal for fire, sparks, light beams)"),
            ('ADD', "Add", "Add: glow and energy effects"),
            ('DARKEN', "Darken", "Darken: keeps darker colors"),
            ('LIGHTEN', "Lighten", "Lighten: keeps lighter colors"),
            ('COLOR_DODGE', "Color Dodge", "Color Dodge: bright dodge effect"),
            ('DIFFERENCE', "Difference", "Difference"),
            ('DIVIDE', "Divide", "Divide"),
        ),
        default='ALPHA_OVER',
        update=lambda s, c: _trigger_comp(c),
    )
    blend_target: EnumProperty(
        name="Blend Target",
        description="Target stream to blend this layer onto",
        items=(
            ('STACK', "Stack (Below)", "Blend onto everything underneath this layer in the stack"),
            ('BACKGROUND', "Background (Backplate)", "Blend directly onto the background plate"),
        ),
        default='STACK',
        update=lambda s, c: _trigger_comp(c),
    )
    blend_opacity: FloatProperty(
        name="Blend Opacity",
        description="Strength / opacity of the blend effect (0.0 to 1.0)",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
        update=lambda s, c: _trigger_comp(c),
    )
    blend_influence: FloatProperty(
        name="Background Influence",
        description="How much background lighting and shadows modulate the decal (0.0 = pure original decal color, 1.0 = full blend math)",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
        update=lambda s, c: _trigger_comp(c),
    )
    blend_exposure: FloatProperty(
        name="Decal Exposure",
        description="Exposure / brightness trim for the decal before blending (EV): allows brightening dark decal textures",
        default=0.0,
        min=-5.0,
        max=5.0,
        update=lambda s, c: _trigger_comp(c),
    )
    blend_expanded: BoolProperty(
        name="Blend Expanded",
        description="Show detailed blend mode settings",
        default=False,
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

    # -- External PNG Holdout Matte (Layer Cutout) --
    use_holdout_matte: BoolProperty(
        name="Маска выреза (PNG)",
        description="Вырезать 3D-слой по внешней PNG-маске / секвенции кадров (ротоскоп / кеинг / matte)",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    holdout_matte_path: StringProperty(
        name="Папка / Файл маски",
        description="Путь к папке с PNG-секвенцией маски или к файлу маски",
        subtype='FILE_PATH',
        default="",
        update=lambda s, c: _trigger_comp(c)
    )
    holdout_matte_invert: BoolProperty(
        name="Инвертировать маску",
        description="Инвертировать: по умолчанию белые участки (1.0) вырезают 3D-слой, черные (0.0) оставляют видимым",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    holdout_matte_mode: EnumProperty(
        name="Канал маски",
        items=(
            ('LUMA', "Яркость (RGB / ч/б)", "Использовать яркость (Luma) изображения как маску выреза"),
            ('ALPHA', "Альфа (PNG Alpha)", "Использовать альфа-канал PNG как маску выреза"),
        ),
        default='LUMA',
        description="Откуда брать маску выреза",
        update=lambda s, c: _trigger_comp(c)
    )
    holdout_matte_feather: FloatProperty(
        name="Смягчение (px)",
        description="Размытие / смягчение краев маски выреза в пикселях",
        default=0.0,
        min=0.0,
        max=100.0,
        update=lambda s, c: _trigger_comp(c)
    )
    holdout_matte_choke: IntProperty(
        name="Сжатие маски (px)",
        description="Сжатие (отрицательные значения, например -2 px) или расширение (положительные значения) маски выреза в пикселях",
        default=-2,
        min=-100,
        max=100,
        update=lambda s, c: _trigger_comp(c)
    )
    holdout_matte_frame_start: IntProperty(
        name="Старт маски",
        description="Номер кадра сцены, с которого начинается воспроизведение маски (по умолчанию 1)",
        default=1,
        min=-10000,
        max=10000,
        update=lambda s, c: _trigger_comp(c)
    )
    holdout_matte_frame_offset: IntProperty(
        name="Сдвиг кадров",
        description="Смещение кадров маски относительно таймлайна (например, +1 или -1 кадр для синхронизации)",
        default=0,
        min=-1000,
        max=1000,
        update=lambda s, c: _trigger_comp(c)
    )
    holdout_matte_mask_shadow: BoolProperty(
        name="Вырезать также из тени",
        description="Применять маску выреза не только к объектам слоя, но и к пассу его тени (SHD)",
        default=True,
        update=lambda s, c: _trigger_comp(c)
    )
    holdout_matte_expanded: BoolProperty(
        name="Развернуть маску",
        description="Развернуть блок настроек внешней маски выреза",
        default=True
    )
    extra_settings_expanded: BoolProperty(
        name="Extra Layer Adjustments",
        description="Expand fine-tuning for this layer (grade, cryptomatte mask, custom holdout)",
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

    # -- Per-layer Depth of Field / Blur fine-tuning --
    blur_multiplier: FloatProperty(
        name="Множитель блюра",
        description="Множитель размытия для этого слоя (1.0 = по общей карте Mist, 0.0 = оставить резким, >1.0 = размыть сильнее)",
        default=1.0, min=0.0, max=5.0,
        update=lambda s, c: _trigger_comp(c)
    )
    blur_override: FloatProperty(
        name="Фиксированный блюр (px)",
        description="Принудительный радиус блюра в пикселях (если > 0, переопределяет карту Mist для этого слоя)",
        default=0.0, min=0.0, max=300.0,
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
    render_settings_expanded: BoolProperty(
        name="Render Settings Expanded",
        description="Show engines and output directory settings",
        default=False
    )
    vfx_ui_lang: EnumProperty(
        name="Language",
        description="Select UI language / Выберите язык интерфейса",
        items=(
            ('RU', "Русский", "Русский интерфейс"),
            ('EN', "English", "English interface"),
        ),
        default='RU'
    )
    post_fx_expanded: BoolProperty(
        name="Post Effects Expanded",
        description="Expand Post Effects section / Развернуть блок пост-эффектов",
        default=True
    )
    fx_target_mode: EnumProperty(
        name="FX Target",
        description="Apply effects globally to all layers or fine-tune specific layer override",
        items=(
            ('ALL', "Все слои (Master)", "Global post effects for entire composite"),
            ('LAYER', "Оверрайд слоя", "Per-layer fine-tuning overrides"),
        ),
        default='ALL'
    )
    fx_override_layer_idx: IntProperty(
        name="Override Layer Index",
        description="Index of layer to override",
        default=0
    )
    use_parallel_render: BoolProperty(
        name="Параллельный рендер",
        description="Рендерить кадры параллельными фоновыми процессами Blender (сильно ускоряет рендер секвенций на RTX 5080)",
        default=False
    )
    parallel_instances: IntProperty(
        name="Потоков",
        description="Количество параллельных фоновых процессов Blender",
        default=2,
        min=2,
        max=4
    )
    resume_render: BoolProperty(
        name="Дорендеривать (не начинать с нуля)",
        description="Проверять готовые кадры на диске и продолжать рендер с места остановки, пропуская уже отрендеренное",
        default=True
    )
    render_format: EnumProperty(
        name="Формат файлов",
        items=(
            ('OPEN_EXR', "OpenEXR (.exr)", "32-bit float OpenEXR — стандарт для VFX и композитинга"),
            ('PNG', "PNG (.png)", "16-bit RGBA PNG — компактный формат без потерь"),
        ),
        default='OPEN_EXR',
        description="Формат файлов для рендера слоев и тени"
    )
    fog_map_format: EnumProperty(
        name="Формат Mist",
        items=(
            ('PNG', "PNG (.png)", "16-bit RGBA PNG — компактный и быстрый формат, идеально для карты глубины Mist"),
            ('OPEN_EXR', "OpenEXR (.exr)", "32-bit float OpenEXR"),
        ),
        default='PNG',
        description="Формат файлов для рендера карты Mist / глубины"
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
    final_export_expanded: BoolProperty(
        name="Финальный экспорт",
        description="Развернуть блок настроек финального экспорта видео",
        default=True,
    )
    final_output_path: StringProperty(
        name="Файл вывода",
        description="Путь и имя файла для сохранения итогового видео или секвенции",
        default="//final_render/output.mp4",
        subtype='FILE_PATH',
    )
    final_format: EnumProperty(
        name="Формат видео",
        items=(
            ('MP4', "MP4 (H.264 Video)", "Готовое видео MP4 (H.264, универсальный формат высокого качества)"),
            ('PNG', "PNG Секвенция", "Последовательность кадров PNG (с альфа-каналом без потерь)"),
            ('PRORES', "ProRes (MOV)", "Apple ProRes QuickTime — профессиональный стандарт для монтажа"),
            ('EXR', "OpenEXR Секвенция", "32-bit Float Master EXR — максимальное качество без потерь"),
        ),
        default='MP4',
        description="Формат итогового файла видео"
    )
    final_quality: EnumProperty(
        name="Качество H.264",
        items=(
            ('HIGH', "Высокое (Perceptually Lossless)", "Отличное визуальное качество без видимых артефактов"),
            ('MEDIUM', "Среднее (Компактный размер)", "Баланс между качеством и размером файла"),
            ('LOSSLESS', "Lossless (Без сжатия)", "Идеальное качество 1:1, большой размер файла"),
        ),
        default='HIGH'
    )
    final_speedup: BoolProperty(
        name="Турбо-сборка (Workbench)",
        description="Временно собирает кадры через быстрый Workbench (0.1–0.2с на кадр), минуя повторный 3D-просчет сцены",
        default=True
    )
    # -- ComfyUI AI Shadow Pipeline --
    ai_shadow_expanded: BoolProperty(
        name="Нейросети & ComfyUI (Тени)",
        description="Развернуть блок настроек генерации теней через ComfyUI",
        default=True
    )
    comfy_url: StringProperty(
        name="ComfyUI URL",
        description="Адрес локального сервера ComfyUI REST API",
        default="http://127.0.0.1:8188"
    )
    comfy_workflow_file: StringProperty(
        name="Воркфлоу (JSON)",
        description="Путь к файлу workflow API (экспортированному из ComfyUI в формате API JSON)",
        default="",
        subtype='FILE_PATH'
    )
    comfy_status: StringProperty(
        name="Статус ComfyUI",
        default=""
    )
    use_bg: BoolProperty(
        name="Использовать фон",
        description="Включить фоновый слой (видео, картинка, Movie Clip или 3D-рендер), расположенный под всеми слоями",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    bg_source_type: EnumProperty(
        name="Тип фона",
        description="Источник для фонового слоя (задника)",
        items=(
            ('VIDEO', "Видео / Секвенция", "Видеофайл (.mp4, .mov...) или папка секвенции кадров", 'FILE_MOVIE', 0),
            ('IMAGE', "Картинка", "Одиночное статичное изображение (.png, .jpg, .exr...)", 'FILE_IMAGE', 1),
            ('MOVIE_CLIP', "Movie Clip", "Клип из Blender Movie Clip Editor (трекинг)", 'TRACKER', 2),
            ('SCENE', "3D Рендер", "3D-сцена задника (мир, небо, окружение)", 'SCENE_DATA', 3),
        ),
        default='VIDEO',
        update=lambda s, c: _trigger_comp(c)
    )
    bg_video_path: StringProperty(
        name="Видео / Секвенция",
        description="Путь к видеофайлу (.mp4, .mov...) или папке секвенции",
        subtype='FILE_PATH',
        default="",
        update=lambda s, c: _trigger_comp(c)
    )
    bg_image_path: StringProperty(
        name="Файл картинки",
        description="Путь к статичному изображению фона (.png, .jpg, .exr...)",
        subtype='FILE_PATH',
        default="",
        update=lambda s, c: _trigger_comp(c)
    )
    bg_movie_clip: PointerProperty(
        type=bpy.types.MovieClip,
        name="Movie Clip",
        description="Выбрать Movie Clip из проекта Blender",
        update=lambda s, c: _trigger_comp(c)
    )
    bg_fit_mode: EnumProperty(
        name="Масштаб фона",
        description="Масштабирование фона под разрешение рендера",
        items=(
            ('STRETCH', "Растянуть (Stretch)", "Растянуть под пропорции кадра холста", 0),
            ('FIT', "Вписать (Fit)", "Вписать с сохранением пропорций (черные полосы по краям)", 1),
            ('CROP', "Заполнить (Crop)", "Заполнить кадр с обрезкой краев", 2),
        ),
        default='STRETCH',
        update=lambda s, c: _trigger_comp(c)
    )
    bg_expanded: BoolProperty(
        name="Развернуть настройки фона",
        description="Развернуть блок настроек фона",
        default=True
    )
    bg_scene: PointerProperty(
        type=bpy.types.Scene,
        name="Background scene",
        description="World-only background pass, always bottom of comp"
    )
    bg_is_static: BoolProperty(
        name="BG Static (1 Frame)",
        description="Render only a single frame for background and hold it",
        default=True,
        update=lambda s, c: _trigger_comp(c)
    )
    bg_static_frame: IntProperty(
        name="BG Static Frame",
        description="Frame number to render and hold for background",
        default=1,
        min=0,
        update=lambda s, c: _trigger_comp(c)
    )
    show_hints: BoolProperty(
        name="Show Help & Guides",
        description="Show helpful explanations, tips and workflow guides in the UI",
        default=True
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
    fog_density: FloatProperty(
        name="Fog Density", default=1.0, min=0.0, max=1.0,
        description="Global fog density: scales the whole fog map "
                    "(0 = no fog, 1 = full ramp density)",
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

    # --- Per-effect crypto influence: each effect has its OWN picked set
    #     of objects and its own strength. Nothing is shared. ---
    crypto_on_fog: BoolProperty(
        name="Crypto Influence",
        description="Boost/reduce fog on picked objects",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    fog_crypto_amount: FloatProperty(
        name="Fog Amount",
        description="Fog strength change on picked objects: "
                    "+ = denser fog, − = fog clears, 0 = no change",
        default=0.0, min=-1.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    fog_crypto_objects: StringProperty(
        name="Fog Objects",
        description="Objects whose fog is boosted/reduced (| separated)",
        default=""
    )
    crypto_on_dof: BoolProperty(
        name="Crypto Influence",
        description="Boost/reduce DOF blur on picked objects",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    dof_crypto_amount: FloatProperty(
        name="DOF Amount",
        description="DOF blur change on picked objects: "
                    "+ = more blur, − = less blur, 0 = no change",
        default=0.0, min=-1.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    dof_crypto_objects: StringProperty(
        name="DOF Objects",
        description="Objects whose DOF blur is boosted/reduced (| separated)",
        default=""
    )
    crypto_on_glare: BoolProperty(
        name="Crypto Influence",
        description="Boost/reduce glare on picked objects",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    glare_crypto_amount: FloatProperty(
        name="Glare Amount",
        description="Glare change on picked objects: "
                    "+ = brighter glow, − = dimmer, 0 = no change",
        default=0.0, min=-1.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    glare_crypto_objects: StringProperty(
        name="Glare Objects",
        description="Objects whose glare is boosted/reduced (| separated)",
        default=""
    )
    crypto_on_grade: BoolProperty(
        name="Crypto Influence",
        description="Boost/reduce master grade on picked objects",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    grade_crypto_amount: FloatProperty(
        name="Grade Amount",
        description="Master grade change on picked objects: "
                    "+ = stronger grade, − = weaker, 0 = no change",
        default=0.0, min=-1.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    grade_crypto_objects: StringProperty(
        name="Grade Objects",
        description="Objects whose master grade is boosted/reduced (| separated)",
        default=""
    )
    crypto_on_colormatch: BoolProperty(
        name="Crypto Influence",
        description="Boost/reduce color match on picked objects",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    cm_crypto_amount: FloatProperty(
        name="Color Match Amount",
        description="Color match change on picked objects: "
                    "+ = stronger, − = weaker, 0 = no change",
        default=0.0, min=-1.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    cm_crypto_objects: StringProperty(
        name="Color Match Objects",
        description="Objects whose color match is boosted/reduced (| separated)",
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
        name="Max Blur (px)", default=25.0, min=0.0, max=300.0, soft_max=100.0,
        description="Maximum blur radius in pixels at Far End (0 = sharp)",
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
    VFX_OT_add_selected_lights, VFX_OT_create_shadow_pass, VFX_OT_enable_gpu_compute,
    VFX_OT_delete_layer, VFX_OT_rename_layer, VFX_OT_reset_lighting,
    VFX_OT_toggle_layer_expand, VFX_OT_toggle_fog_expand,
    VFX_OT_drag_layer, VFX_OT_move_layer_up, VFX_OT_move_layer_down,
    VFX_OT_debug_layer, VFX_OT_rebuild_comp,
    VFX_OT_render_all_layers, VFX_OT_one_click_exr,
    VFX_OT_one_click_exr_single, VFX_OT_render_fog_map, VFX_OT_stop_render,
    VFX_OT_create_background, VFX_OT_delete_background,
    VFX_OT_delete_shadow_pass, VFX_OT_refresh_proxies,
    VFX_OT_focus_shadow_ramp,
    VFX_OT_refresh_occlusion,
    VFX_OT_diagnostic,
    VFX_OT_setup_light_groups, VFX_OT_apply_color_preset,
    VFX_OT_preview_this_mask, VFX_OT_pick_cryptomatte,
    VFX_OT_reset_layer_grade, VFX_OT_reset_master_grade,
    VFX_OT_copy_master_grade, VFX_OT_toggle_viewport_compositor,
    VFX_OT_select_layer_objects,
    VFX_OT_export_final_video, VFX_OT_open_output_folder,
    VFX_OT_comfy_test_connection, VFX_OT_export_comfy_inputs,
    VFX_OT_open_ai_shadow_folder,
    VFX_OT_browse_layer_matte_folder, VFX_OT_reload_layer_mask,
    VFX_OT_open_layer_matte_folder, VFX_OT_assign_layer_mask_dialog,
    VFX_OT_copy_matte_from_layer,
    VFX_OT_add_background, VFX_OT_browse_bg_file,
    VFX_OT_match_scene_fps, VFX_OT_match_frame_start,
    VFX_OT_select_override_layer,
    VFX_OT_set_active_layer,
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
    VFX_OT_select_layer_objects,
    VFX_OT_add_selected_lights,
    VFX_OT_create_shadow_pass,
    VFX_OT_enable_gpu_compute,
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
    VFX_OT_one_click_exr_single,
    VFX_OT_render_fog_map,
    VFX_OT_stop_render,
    VFX_OT_create_background,
    VFX_OT_delete_background,
    VFX_OT_delete_shadow_pass,
    VFX_OT_refresh_proxies,
    VFX_OT_focus_shadow_ramp,
    VFX_OT_refresh_occlusion,
    VFX_OT_diagnostic,
    VFX_OT_apply_color_preset,
    VFX_OT_preview_this_mask, VFX_OT_pick_cryptomatte,
    VFX_OT_reset_layer_grade,
    VFX_OT_reset_master_grade,
    VFX_OT_copy_master_grade,
    VFX_OT_toggle_viewport_compositor,
    VFX_OT_export_final_video,
    VFX_OT_open_output_folder,
    VFX_OT_comfy_test_connection,
    VFX_OT_export_comfy_inputs,
    VFX_OT_open_ai_shadow_folder,
    VFX_OT_browse_layer_matte_folder,
    VFX_OT_reload_layer_mask,
    VFX_OT_open_layer_matte_folder,
    VFX_OT_assign_layer_mask_dialog,
    VFX_OT_copy_matte_from_layer,
    VFX_OT_add_background,
    VFX_OT_browse_bg_file,
    VFX_OT_match_scene_fps,
    VFX_OT_match_frame_start,
    VFX_OT_select_override_layer,
    VFX_OT_set_active_layer,
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


def _migrate_ramp_values():
    """One-time fog ramp migration (fractions -> meters), runs after file load.

    Registered from register() as a timer: timers fire only once Blender has
    a loaded file, so bpy.data.scenes is safe to touch here.
    """
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
    return None  # don't repeat


def unregister():
    # stop auto-reload timer
    try:
        bpy.app.timers.unregister(_auto_reload_timer)
    except Exception:
        pass
    # stop a pending migration timer (disable/re-enable before it fired)
    try:
        bpy.app.timers.unregister(_migrate_ramp_values)
    except Exception:
        pass

    # Always destroy Scene.vfx so register() recreates it with fresh properties
    if hasattr(bpy.types.Scene, "vfx"):
        try:
            del bpy.types.Scene.vfx
        except Exception:
            pass

    # Unregister classes directly in reverse order (essential for PropertyGroups)
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
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
    orphans = [
        n for n in dir(bpy.types)
        if n.startswith(("VFX_OT_", "VFX_PT_", "VFX_UL_", "VFXLayer", "VFXProject", "VFXOcclusion"))
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
            # Stale RNA class or PropertyGroup subclass: unregister and retry
            try:
                bpy.utils.unregister_class(cls)
                bpy.utils.register_class(cls)
            except Exception:
                old = getattr(bpy.types, cls.__name__, None)
                if old is not None:
                    try:
                        bpy.utils.unregister_class(old)
                        bpy.utils.register_class(cls)
                    except Exception as exc2:
                        print(f"VFX register ERROR {cls.__name__}: {exc2}")
                else:
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
    # and kills all fog. DEFERRED to a timer: while the addon is being
    # enabled, bpy.data is a restricted _RestrictData object (it becomes
    # usable only after the .blend file has loaded) and touching it raises
    # "'_RestrictData' object has no attribute 'scenes'" — which aborted
    # register() so the addon checkbox never stuck across restarts.
    try:
        bpy.app.timers.register(_migrate_ramp_values, first_interval=0.1)
    except Exception:
        pass

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
