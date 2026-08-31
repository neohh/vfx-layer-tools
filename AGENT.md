# AGENT.md — VFX Layer Tools: полная информация

## Репозиторий

- **URL:** https://github.com/neohh/vfx-layer-tools
- **Владелец:** neohh
- **Ветка:** `main`
- **gh CLI:** залогинен как `neohh` (авторизация через keyring)
- **Текущая версия:** v2.1.0

## Где лежат файлы

**Репозиторий (GitHub):**
```
https://github.com/neohh/vfx-layer-tools/
├── vfx_layer_tools/          ← пакет аддона
│   ├── __init__.py
│   ├── core.py
│   ├── shadow.py
│   ├── compositor.py
│   ├── materials.py
│   ├── operators.py
│   ├── diagnostic.py
│   └── ui.py
├── archive/                  ← исходные 5 версий (архив, не трогать!)
├── AGENT.md
├── CHANGELOG.md
├── README.md
└── .gitignore
```

**Установка в Blender (на компе пользователя):**
```
C:\Users\maxim\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons\vfx_layer_tools\
├── __init__.py
├── core.py
├── shadow.py
├── compositor.py
├── materials.py
├── operators.py
├── diagnostic.py
└── ui.py
```

**ВАЖНО:** Рабочая директория Agents — это установочная папка аддона в Blender. git-репозиторий инициализирован ВНУТРИ этой папки, но `.git` ссылается на `https://github.com/neohh/vfx-layer-tools.git`.

## Git workflow

```bash
# Коммит с HEREDOC
git commit -m "$(cat <<'EOF'
Описание изменений

🤖 Generated with Codebuff
Co-Authored-By: Codebuff <noreply@codebuff.com>
EOF
)"

# Пуш
git push origin main

# Создание релиза
git tag -a vX.Y.Z -m "VFX Layer Tools vX.Y.Z"
git push origin main --tags
```

### Правила гита

- Не пушить без явного запроса пользователя
- Не делать `git push`, `git reset`, `git rebase` без разрешения
- Не удалять файлы из `archive/`
- Не менять структуру пакета без согласования

## Создание релиза

1. Обновить версию в `vfx_layer_tools/__init__.py`:
   - `bl_info["version"] = (X, Y, 0)`
   - `VFX_VERSION = "X.Y.Z"`
2. Закоммитить + запушить + создать тег
3. Создать zip-архив:
```bash
python -c "
import zipfile, os
name = 'vfx_layer_tools_vX.Y.Z.zip'
with zipfile.ZipFile(name, 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk('vfx_layer_tools'):
        for f in files:
            fpath = os.path.join(root, f)
            zf.write(fpath, fpath.replace(os.sep, '/'))
print('Created:', name)
"
```
4. Создать релиз + прикрепить zip:
```bash
gh release create vX.Y.Z --title "VFX Layer Tools vX.Y.Z" --notes "описание..."
gh release upload vX.Y.Z vfx_layer_tools_vX.Y.Z.zip --clobber
```

**ВАЖНО:** GitHub Source ZIP содержит лишнюю папку — Blender не находит `__init__.py`. Всегда создавать свой zip через скрипт выше.

## Архитектура аддона

### Основные модули

| Файл | Назначение |
|------|-----------|
| `__init__.py` | bl_info, Properties (VFXProject, VFXLayer), register/unregister, auto-reload |
| `core.py` | Утилиты: ensure_root, ensure_camera_collection, create_empty_scene, sync_scene_settings |
| `compositor.py` | Композитор: build_comp_assembly, rebuild_comp_from_files, fog system, blur, DOF, glare, auto_calibrate_mist |
| `operators.py` | Все операторы: render, rebuild comp, shadow pass, diagnostics, auto-calibrate |
| `materials.py` | _trigger_comp, _trigger_rebuild, material editing (adjust viewport materials) |
| `shadow.py` | Shadow catcher, proxy objects |
| `diagnostic.py` | Полная диагностика: сцены, pass'ы, EXR файлы, композитор ноды |
| `ui.py` | Панели VFX, Compositor, UIList слоёв |

### Поток данных

1. **Создание слоёв:** Пользователь создаёт слои через UI → создаются отдельные сцены в Blender
2. **Рендер:** `Render All Layers` → рендерит каждую сцену как EXR с pass'ами (Mist, Z, Normal)
3. **Композитор:** `Rebuild Comp` → строит ноды в VFX_Compositor сцене из EXR файлов
4. **Fog:** VFX_FOGMAP сцена рендерит live mist-маску через EEVEE
5. **Auto-calibrate:** Анализирует реальную геометрию сцены → выставляет Mist Start/Depth

### Ключевые функции

- `build_comp_assembly(vfx, master)` — строит весь граф композитора
- `rebuild_comp_from_files(vfx, master)` — загружает EXR файлы в композитор
- `auto_calibrate_mist(vfx, master)` — анализ геометрии через depsgraph, калибровка mist
- `_setup_fog_passes(vfx, master)` — создаёт/настраивает VFX_FOGMAP сцену
- `_trigger_comp(context)` — пересборка компа + авто-калибровка mist
- `_update_mist(context)` — обновление mist настроек в world

### Auto-reload

Аддон автоматически перезагружается при изменении .py файлов (таймер каждые 2 секунды). Отключить: `_AUTO_RELOAD_ENABLED = False` в `__init__.py`.

## Blender compatibility

- **Минимальная версия:** Blender 5.1+
- **Протестировано на:** Blender 5.2
- **Движок для FOGMAP:** EEVEE (BLENDER_EEVEE_NEXT)
- **Формат рендера:** OPEN_EXR, 32-bit, RGBA

## Диагностика

Кнопка **Diagnostics** в панели VFX → выводит полный отчёт в System Console:
- Сцены и их pass'ы
- EXR файлы на диске
- Композитор ноды и их подключения
- Статус mist/depth/normal pass'ов

Кнопка **Enable Passes** → принудительно включает Mist/Z/Normal на всех VFX сценах.

Кнопка **Auto-Calibrate from Scene** → анализирует геометрию из камеры и выставляет Mist Start/Depth.

## Известные особенности

- FOGMAP рендерится отдельно от layer scenes (EEVEE vs Cycles)
- Mist pass использует `world.mist_settings.start/depth` — все сцены делят один World через `sync_scene_settings`
- `view_frame()` в Blender 5.2 возвращает точки в camera-local пространстве — нужно трансформировать через `matrix_world`
- `scene.ray_cast()` ненадёжен в Blender 5.2 — используем прямой перебор вершин через depsgraph

## Запрещено

- `git push` без разрешения
- `git reset --hard`, `git rebase`, `git push -f`
- Удалять файлы из `archive/`
- Менять структуру пакета `vfx_layer_tools/` без согласования
- Делать релиз без прикреплённого zip-архива
