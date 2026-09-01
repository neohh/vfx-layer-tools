# VFX Layer Tools

**Версия:** 2.1.0  
**Blender:** 5.1+  
**Категория:** Compositing

Послойный менеджер композитинга для Blender. Каждый слой рендерится отдельно, композитор собирается автоматически — с поддержкой тумана, глубины, блюра и пост-эффектов.

---

## Возможности

### 🎬 Слои рендера
- Создание слоёв из выделенных объектов (каждый слой — отдельная сцена)
- Каждая сцена рендерится отдельно, прозрачный фон (film transparent)
- Слои переупорядочиваются перетаскиванием (drag & drop) или кнопками вверх/вниз
- Автоматическое подключение камеры и света из master-сцены

### 🖥️ Композитинг
- **Live режим** — композитор читает данные напрямую из Render Layers
- **From Files режим** — композитор читает EXR-файлы с диска
- Ноды AlphaOver собираются автоматически с правильным порядком
- Поддержка Background-сцены (world-only pass)

### 🌑 Тени
- **Cast** — объекты слоя отбрасывают тени на catcher
- **Receive** — слой получает тени от других слоёв
- Автоматическое создание прокси-объектов для shadow pass
- Настройка интенсивности теней (shadow strength)

### 🌫️ Туман (Fog)
- Единая live mist-маска на всю сцену (VFX_FOGMAP)
- Контроль глубины тумана: Mist Start / Mist Depth
- Ramp Black / Ramp White для настройки градиента
- Per-layer multiplier (fog factor) — разная плотность тумана на разных слоях
- Цвет тумана (Fog Color)
- Режим просмотра маски (fog preview)

### 📷 Глубина и блюр
- **Atmospheric Blur** — блюр по глубине через mist-маску (дальние объекты размываются)
- **Camera DOF** — физическая глубина резкости (Defocus) с настройками F-Stop, фокусного расстояния и максимального блюра

### ✨ Пост-эффекты
- **Glare / Bloom** — блики и свечение (Bloom, Fog Glow, Streaks, Ghosts)
- **Lens Distortion** — дисторсия и хроматическая аберрация объектива

### 🎨 Редактирование материалов
- **Material Adjust** — экспозиция, контраст, насыщенность, тонирование
- Работает в viewport для быстрой настройки внешнего вида

### ⚡ Рендер
- **1-Click EXR** — рендер всех слоёв как EXR-анимация + автоматическая сборка компа
- Прогресс-бар с возможностью отмены (ESC)
- Автоматическая синхронизация настроек рендера из master-сцены

---

## Структура

```
vfx_layer_tools/            ← пакет аддона (устанавливается как zip)
├── __init__.py             ← bl_info, Properties, register/unregister
├── blender_manifest.toml   ← Extension Platform manifest (Blender 5.2+)
├── core.py                 ← утилиты, коллекции, сцены, синхронизация (337 строк)
├── shadow.py               ← shadow catcher, прокси-объекты
├── compositor.py           ← дерево композитора, сборка нод, fog
├── materials.py            ← редактирование материалов
├── diagnostic.py           ← диагностика
├── operators.py            ← все операторы
└── ui.py                   ← панели и UIList
archive/                    ← исходные 5 версий (архив)
```

### Зависимости между модулями

```
__init__.py  ──→  core, shadow, compositor, materials, operators, ui
operators.py ──→  core, shadow, compositor, materials
ui.py         ──→  core, materials
compositor.py ──→  core, materials
shadow.py     ──→  core
materials.py  ──→  (чистый модуль)
core.py       ──→  (чистый модуль)
```

---

## Установка

### Из zip-архива (рекомендуется)

1. Скачайте репозиторий как zip (Code → Download ZIP)
2. В Blender: **Edit → Preferences → Add-ons → Install**
3. Выберите скачанный zip-файл
4. Включите аддон — в 3D Viewport появится панель **VFX** в sidebar (N)

### Из папки

1. Скопируйте папку `vfx_layer_tools/` в директорию аддонов Blender:
   - Windows: `%APPDATA%\Blender Foundation\Blender\5.1\scripts\addons\`
   - macOS: `~/Library/Application Support/Blender/5.1/scripts/addons/`
   - Linux: `~/.config/blender/5.1/scripts/addons/`
2. В Blender: **Edit → Preferences → Add-ons** → найдите "VFX Layer Tools" и включите

---

## Использование

### Быстрый старт

1. **Настройте Master Scene** — выберите основную сцену и нажмите «Use Current Scene As Master»
2. **Создайте слой** — выделите объекты и нажмите «Create Layer From Selected»
3. **Добавьте тени** — на слое нажмите «Create Shadow Pass» (Cast или Receive)
4. **Настройте композитор** — нажмите «Rebuild Comp» или переключите режим comp_mode
5. **Рендер** — нажмите «1-Click: Render EXR + Comp»

### Порядок слоёв

Слои внизу списка рендерятся первыми ( фон ). Порядок влияет на композитинг:
- Перетаскивайте за иконку **⠿** (drag & drop)
- Или используйте кнопки **Up / Down**

### Туман

1. Включите **Fog (Mist pass)**
2. Настройте Mist Start / Mist Depth
3. Подберите Ramp Black / Ramp White
4. Увеличьте Density (global) для появления тумана
5. Отрегулируйте per-layer Fog Factor на отдельных слоях

---

## Требования

- **Blender 5.1** и выше
- Для теней рекомендуется **Cycles**
- Для тумана рекомендуется **EEVEE** (Fog Map сцена)

---

## Лицензия

MIT
