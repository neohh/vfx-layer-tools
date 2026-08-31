# AGENT.md — Правила работы с GitHub

## Репозиторий

- **URL:** https://github.com/neohh/vfx-layer-tools
- **Владелец:** neohh
- **Ветка:** `master`

## Коммиты

- Все коммиты через `git commit` с HEREDOC-форматом
- В конце каждого коммита добавлять:

```
🤖 Generated with Codebuff
Co-Authored-By: Codebuff <noreply@codebuff.com>
```

- Не пушить без явного запроса пользователя
- Не делать `git push`, `git reset`, `git rebase` без разрешения

## Релизы

### Версионирование

- Формат: `vX.Y.Z` (например `v2.0.0`)
- Версия указывается в `vfx_layer_tools/__init__.py`:
  - `bl_info["version"] = (X, Y, 0)`
  - `VFX_VERSION = "X.Y.Z"`

### Процесс создания релиза

1. Обновить версию в `__init__.py` (bl_info + VFX_VERSION)
2. Обновить CHANGELOG.md
3. Закоммитить изменения
4. Создать аннотированный тег: `git tag -a vX.Y.Z -m "VFX Layer Tools vX.Y.Z"`
5. Запушить коммиты и тег: `git push origin master --tags`
6. **Создать zip-архив** (обязательно!)
7. Создать релиз через `gh release create`
8. **Прикрепить zip** через `gh release upload`

### Создание zip-архива

**Важно:** GitHub Source ZIP содержит лишнюю папку `repo-name-tag/`, Blender не находит `__init__.py`. Всегда создавать свой zip.

Структура zip:
```
vfx_layer_tools/        ← в корне zip, без лишних папок
├── __init__.py
├── core.py
├── shadow.py
├── compositor.py
├── materials.py
├── operators.py
└── ui.py
```

Скрипт создания:
```bash
python -c "
import zipfile, os
name = 'vfx_layer_tools_vX.Y.Z.zip'
with zipfile.ZipFile(name, 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk('vfx_layer_tools'):
        for f in files:
            fpath = os.path.join(root, f)
            zf.write(fpath, fpath.replace(os.sep, '/'))
"
```

Имя файла: `vfx_layer_tools_vX.Y.Z.zip`

### Публикация релиза

```bash
# Загрузить zip к релизу
gh release upload vX.Y.Z vfx_layer_tools_vX.Y.Z.zip --clobber
```

### Шаблон релиза

```bash
# 1. Коммит
git add -A && git commit -m "Release vX.Y.Z"

# 2. Тег
git tag -a vX.Y.Z -m "VFX Layer Tools vX.Y.Z"

# 3. Пуш
git push origin master --tags

# 4. Создать zip
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

# 5. Создать релиз + прикрепить zip
gh release create vX.Y.Z \
  --title "VFX Layer Tools vX.Y.Z" \
  --notes "описание..."
gh release upload vX.Y.Z vfx_layer_tools_vX.Y.Z.zip --clobber
```

## Структура проекта

```
vfx-layer-tools/
├── vfx_layer_tools/          ← пакет аддона (устанавливается в Blender)
│   ├── __init__.py           ← bl_info, Properties, register/unregister
│   ├── core.py               ← утилиты, коллекции, сцены
│   ├── shadow.py             ← shadow catcher, прокси
│   ├── compositor.py         ← композитор, fog, blur, DOF, glare
│   ├── materials.py          ← редактирование материалов
│   ├── operators.py          ← все операторы
│   └── ui.py                 ← панели, UIList
├── archive/                  ← исходные 5 версий (архив)
├── AGENT.md                  ← этот файл
├── CHANGELOG.md
├── README.md
└── .gitignore
```

## Запрещено

- `git push` без разрешения
- `git reset --hard`, `git rebase`, `git push -f`
- Удалять файлы из `archive/`
- Менять структуру пакета `vfx_layer_tools/` без согласования
- Делать релиз без прикреплённого zip-архива
