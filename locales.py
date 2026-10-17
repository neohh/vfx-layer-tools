"""VFX Layer Tools — Localization dictionary and translation helper.
Supports 100% Russian and 100% English interface.
"""

import bpy

TRANSLATIONS = {
    # Headers & Panels
    "VFX Layers & Comp": {
        'RU': "VFX Слои и Композ",
        'EN': "VFX Layers & Comp",
    },
    "Master:": {
        'RU': "Мастер:",
        'EN': "Master:",
    },
    "Master scene not set:": {
        'RU': "Мастер-сцена не выбрана:",
        'EN': "Master scene not set:",
    },
    "Set Master": {
        'RU': "Сделать мастером",
        'EN': "Set Master",
    },

    # Toolbar
    "+ Layer": {
        'RU': "+ Слой",
        'EN': "+ Layer",
    },
    "Backplate": {
        'RU': "Задник",
        'EN': "Backplate",
    },
    "Matte": {
        'RU': "Маска",
        'EN': "Matte",
    },
    "Link Selected Lights": {
        'RU': "Привязать выделенный свет",
        'EN': "Link Selected Lights",
    },
    "Up (Closer)": {
        'RU': "Выше (Ближе)",
        'EN': "Up (Closer)",
    },
    "Down (Further)": {
        'RU': "Ниже (Дальше)",
        'EN': "Down (Further)",
    },

    # Backplate (BG)
    "BACKPLATE (BG)": {
        'RU': "ЗАДНИК (Фон)",
        'EN': "BACKPLATE (BG)",
    },
    "+ Add Backplate Layer": {
        'RU': "+ Добавить слой фона (задник)",
        'EN': "+ Add Backplate Layer",
    },
    "Video": {
        'RU': "Видео",
        'EN': "Video",
    },
    "Image": {
        'RU': "Фото",
        'EN': "Image",
    },
    "Clip": {
        'RU': "Клип",
        'EN': "Clip",
    },
    "3D": {
        'RU': "3D",
        'EN': "3D",
    },
    "Source:": {
        'RU': "Файл:",
        'EN': "Source:",
    },
    "Fit:": {
        'RU': "Размер:",
        'EN': "Fit:",
    },
    "Stretch": {
        'RU': "Растянуть",
        'EN': "Stretch",
    },
    "Fit": {
        'RU': "Вписать",
        'EN': "Fit",
    },
    "Crop": {
        'RU': "Заполнить",
        'EN': "Crop",
    },
    "Create 3D Scene": {
        'RU': "Создать 3D-сцену",
        'EN': "Create 3D Scene",
    },
    "Static (1 Frame)": {
        'RU': "Статика (1 кадр)",
        'EN': "Static (1 Frame)",
    },
    "Frame:": {
        'RU': "Кадр:",
        'EN': "Frame:",
    },
    "Match FPS ({fps:.2f})": {
        'RU': "Синхронизировать FPS ({fps:.2f})",
        'EN': "Sync FPS ({fps:.2f})",
    },
    "Start at frame {frame}": {
        'RU': "Поставить старт сцены на {frame} кадр",
        'EN': "Start at frame {frame}",
    },

    # Active Layer Card
    "Select": {
        'RU': "Выделить",
        'EN': "Select",
    },
    "Rename": {
        'RU': "Переименовать",
        'EN': "Rename",
    },
    "Delete": {
        'RU': "Удалить",
        'EN': "Delete",
    },

    # 3D Shadow
    "3D SHADOW": {
        'RU': "3D ТЕНЬ",
        'EN': "3D SHADOW",
    },
    "Strength": {
        'RU': "Сила",
        'EN': "Strength",
    },
    "Catcher:": {
        'RU': "Кетчер:",
        'EN': "Catcher:",
    },
    "Create Pass": {
        'RU': "Создать пасс",
        'EN': "Create Pass",
    },
    "Add Pass": {
        'RU': "Добавить пасс",
        'EN': "Add Pass",
    },
    "GI Bounce & Reflections": {
        'RU': "GI-подсветка и рефлексы",
        'EN': "GI Bounce & Reflections",
    },
    "Color:": {
        'RU': "Цвет:",
        'EN': "Color:",
    },
    "Samples:": {
        'RU': "Семплы:",
        'EN': "Samples:",
    },
    "Blur": {
        'RU': "Мягкость",
        'EN': "Blur",
    },
    "Noise Cut": {
        'RU': "Срез шума",
        'EN': "Noise Cut",
    },
    "Core": {
        'RU': "Плотность",
        'EN': "Core",
    },
    "Open ColorRamp Node": {
        'RU': "ColorRamp в нодах",
        'EN': "Open ColorRamp Node",
    },

    # Occlusion
    "Occlusion:": {
        'RU': "Перекрытие:",
        'EN': "Occlusion:",
    },

    # Holdout Matte
    "HOLDOUT MATTE": {
        'RU': "МАСКА ВЫРЕЗА",
        'EN': "HOLDOUT MATTE",
    },
    "fr.": {
        'RU': "к.",
        'EN': "fr.",
    },
    "Path:": {
        'RU': "Путь:",
        'EN': "Path:",
    },
    "Frames:": {
        'RU': "Кадров:",
        'EN': "Frames:",
    },
    "Start": {
        'RU': "Старт",
        'EN': "Start",
    },
    "Offset": {
        'RU': "Сдвиг",
        'EN': "Offset",
    },
    "Invert": {
        'RU': "Инверсия",
        'EN': "Invert",
    },
    "Channel: Luma": {
        'RU': "Канал: Яркость",
        'EN': "Channel: Luma",
    },
    "Choke (px)": {
        'RU': "Сжатие (px)",
        'EN': "Choke (px)",
    },
    "Feather": {
        'RU': "Мягкость",
        'EN': "Feather",
    },
    "Cutout from Shadow": {
        'RU': "Вырезать и из тени",
        'EN': "Cutout from Shadow",
    },
    "Refresh": {
        'RU': "Обновить",
        'EN': "Refresh",
    },
    "Folder": {
        'RU': "Папка",
        'EN': "Folder",
    },

    # Layer Overrides
    "LAYER OVERRIDES (DOF / GRADE)": {
        'RU': "ОВЕРРАЙДЫ СЛОЯ (DOF / ЦВЕТ)",
        'EN': "LAYER OVERRIDES (DOF / GRADE)",
    },
    "Blur Influence": {
        'RU': "Влияние блюра",
        'EN': "Blur Influence",
    },
    "Fixed Blur (px)": {
        'RU': "Фикс. блюр (px)",
        'EN': "Fixed Blur (px)",
    },
    "Layer Grade": {
        'RU': "Цветокоррекция слоя (Grade)",
        'EN': "Layer Grade",
    },
    "Reset Grade": {
        'RU': "Сбросить грейд",
        'EN': "Reset Grade",
    },
    "Direct Material Adjust": {
        'RU': "Прямая подстройка материалов",
        'EN': "Direct Material Adjust",
    },
    "Reset Material Lights": {
        'RU': "Сбросить свет материалов",
        'EN': "Reset Material Lights",
    },

    # Batch Render & Comp Setup
    "Enable GPU (OptiX)": {
        'RU': "⚡ Включить GPU (OptiX) для рендера",
        'EN': "⚡ Enable GPU (OptiX)",
    },
    "1-Click: Render ALL + Comp": {
        'RU': "1-Клик: Рендер ВСЕХ + Композ",
        'EN': "1-Click: Render ALL + Comp",
    },
    "Resume": {
        'RU': "Дорендер",
        'EN': "Resume",
    },
    "Selected": {
        'RU': "Выбранные",
        'EN': "Selected",
    },
    "Mist Pass": {
        'RU': "Mist (Туман)",
        'EN': "Mist Pass",
    },
    "STOP RENDER": {
        'RU': "ОСТАНОВИТЬ РЕНДЕР",
        'EN': "STOP RENDER",
    },
    "Layer Order:": {
        'RU': "Сортировка:",
        'EN': "Layer Order:",
    },
    "Viewport Comp": {
        'RU': "Вьюпорт Композ",
        'EN': "Viewport Comp",
    },
    "Rebuild Comp": {
        'RU': "Пересобрать",
        'EN': "Rebuild Comp",
    },
    "RENDER SETTINGS & PATHS": {
        'RU': "НАСТРОЙКИ РЕНДЕРА И ПУТИ",
        'EN': "RENDER SETTINGS & PATHS",
    },
    "Folder:": {
        'RU': "Папка:",
        'EN': "Folder:",
    },
    "Layers:": {
        'RU': "Слои:",
        'EN': "Layers:",
    },
    "Mist:": {
        'RU': "Mist:",
        'EN': "Mist:",
    },
    "Objects:": {
        'RU': "Объекты:",
        'EN': "Objects:",
    },
    "Shadows:": {
        'RU': "Тени:",
        'EN': "Shadows:",
    },
    "Parallel Render": {
        'RU': "Параллельный рендер",
        'EN': "Parallel Render",
    },
    "Threads:": {
        'RU': "Потоков:",
        'EN': "Threads:",
    },

    # Post Effects
    "POST EFFECTS (POST FX)": {
        'RU': "ПОСТ-ЭФФЕКТЫ (POST FX)",
        'EN': "POST EFFECTS (POST FX)",
    },
    "Target:": {
        'RU': "Цель:",
        'EN': "Target:",
    },
    "All Layers (Master)": {
        'RU': "Все слои (Master)",
        'EN': "All Layers (Master)",
    },
    "Layer Override": {
        'RU': "Оверрайд слоя",
        'EN': "Layer Override",
    },
    "Layer:": {
        'RU': "Слой:",
        'EN': "Layer:",
    },
    "ATMOSPHERIC FOG": {
        'RU': "ТУМАН (Fog)",
        'EN': "ATMOSPHERIC FOG",
    },
    "Start:": {
        'RU': "Начало:",
        'EN': "Start:",
    },
    "Full:": {
        'RU': "Максимум:",
        'EN': "Full:",
    },
    "Density:": {
        'RU': "Плотность:",
        'EN': "Density:",
    },
    "Fog Color:": {
        'RU': "Цвет тумана:",
        'EN': "Fog Color:",
    },
    "Render Mist Sequence (EEVEE)": {
        'RU': "Рендер секвенции Mist (EEVEE)",
        'EN': "Render Mist Sequence (EEVEE)",
    },
    "DEPTH BLUR (Mist / DOF)": {
        'RU': "РАЗМЫТИЕ ГЛУБИНЫ (DOF)",
        'EN': "DEPTH BLUR (Mist / DOF)",
    },
    "Blur Amount (px):": {
        'RU': "Сила размытия (px):",
        'EN': "Blur Amount (px):",
    },
    "GLOW & GLARE": {
        'RU': "СВЕЧЕНИЕ И БЛИКИ (Glow)",
        'EN': "GLOW & GLARE",
    },
    "Type:": {
        'RU': "Тип:",
        'EN': "Type:",
    },
    "Brightness:": {
        'RU': "Яркость:",
        'EN': "Brightness:",
    },
    "Threshold:": {
        'RU': "Порог:",
        'EN': "Threshold:",
    },
    "Size:": {
        'RU': "Размер:",
        'EN': "Size:",
    },
    "LENS DISTORTION": {
        'RU': "ИСКАЖЕНИЯ ЛИНЗЫ",
        'EN': "LENS DISTORTION",
    },
    "Distortion:": {
        'RU': "Дисторсия:",
        'EN': "Distortion:",
    },
    "Chromatic:": {
        'RU': "Хроматика:",
        'EN': "Chromatic:",
    },
    "COLOR GRADING (Grade)": {
        'RU': "ЦВЕТОКОРРЕКЦИЯ (Grade)",
        'EN': "COLOR GRADING (Grade)",
    },
    "Exposure": {
        'RU': "Экспозиция",
        'EN': "Exposure",
    },
    "Temperature": {
        'RU': "Температура",
        'EN': "Temperature",
    },
    "Tint": {
        'RU': "Тинт",
        'EN': "Tint",
    },
    "Saturation": {
        'RU': "Насыщенность",
        'EN': "Saturation",
    },
    "Contrast": {
        'RU': "Контраст",
        'EN': "Contrast",
    },
    "Lift (Shadows)": {
        'RU': "Lift (Тени)",
        'EN': "Lift (Shadows)",
    },
    "Gain (Highlights)": {
        'RU': "Gain (Света)",
        'EN': "Gain (Highlights)",
    },
    "Reset": {
        'RU': "Сбросить",
        'EN': "Reset",
    },
    "Copy to Layers": {
        'RU': "Скопировать на слои",
        'EN': "Copy to Layers",
    },
    "Pick Objects (Crypto)": {
        'RU': "Пипетка объектов",
        'EN': "Pick Objects",
    },

    # Final Export
    "FINAL VIDEO EXPORT": {
        'RU': "ФИНАЛЬНЫЙ ЭКСПОРТ (ВИДЕО)",
        'EN': "FINAL VIDEO EXPORT",
    },
    "Format:": {
        'RU': "Формат:",
        'EN': "Format:",
    },
    "Quality:": {
        'RU': "Кач-во:",
        'EN': "Quality:",
    },
    "Turbo Assemble (Workbench)": {
        'RU': "Турбо-сборка (Workbench)",
        'EN': "Turbo Assemble (Workbench)",
    },
    "RENDER VIDEO (ANIMATION)": {
        'RU': "РЕНДЕР ВИДЕО (АНИМАЦИЯ)",
        'EN': "RENDER VIDEO (ANIMATION)",
    },
    "Still Frame": {
        'RU': "Текущий кадр",
        'EN': "Still Frame",
    },
    "Open Folder": {
        'RU': "Папка",
        'EN': "Open Folder",
    },
}


def get_current_lang(context=None):
    """Retrieve currently active language code ('RU' or 'EN')."""
    if context:
        scene = getattr(context, "scene", None)
        if scene and hasattr(scene, "vfx"):
            return getattr(scene.vfx, "vfx_ui_lang", 'RU')
    try:
        scene = bpy.context.scene
        if scene and hasattr(scene, "vfx"):
            return getattr(scene.vfx, "vfx_ui_lang", 'RU')
    except Exception:
        pass
    return 'RU'


def tr(key, context=None, lang=None, **kwargs):
    """Translate key to the active language."""
    if lang is None:
        lang = get_current_lang(context)
    entry = TRANSLATIONS.get(key)
    if entry:
        text = entry.get(lang, entry.get('RU', key))
    else:
        text = key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            pass
    return text
