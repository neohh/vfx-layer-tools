import bpy

PATCH_VERSION = "1.66"
MARKER = "VFX_LAYER_TOOLS_VERSION"
PATCH_SIG = "PATCH_VERSION"

target = None
for text in bpy.data.texts:
    body = text.as_string()
    if PATCH_SIG in body:
        continue
    if MARKER in body or "vfx.create_layer" in body:
        target = text
        break

if target is None:
    raise RuntimeError("VFX patcher: main script not found")

print("VFX patcher: target =", target.name)
src = target.as_string()

REPLACEMENTS = []

REPLACEMENTS.append((
'''# VFX_LAYER_TOOLS_VERSION = "1.65"''',
'''# VFX_LAYER_TOOLS_VERSION = "1.66"'''
))

REPLACEMENTS.append((
'''VFX_VERSION = "1.65"''',
'''VFX_VERSION = "1.66"'''
))

REPLACEMENTS.append((
'''    "version": (1, 65, 0),''',
'''    "version": (1, 66, 0),'''
))

REPLACEMENTS.append((
'''def rebuild_comp(vfx, master):
    nt = get_comp_tree(master)
    if not nt:
        return''',
'''def rebuild_comp(vfx, master):
    # в режиме From Files любая "пересборка" собирается из EXR,
    # чтобы перестановка слоев не превращала комп в live-рендер
    if getattr(vfx, "comp_mode", 'LIVE') == 'FILES':
        rebuild_comp_from_files(vfx, master)
        return

    nt = get_comp_tree(master)
    if not nt:
        return'''
))

if f'# VFX_LAYER_TOOLS_VERSION = "{PATCH_VERSION}"' in src:
    print("VFX patcher: already at version", PATCH_VERSION)
else:
    missing = []
    for old, new in REPLACEMENTS:
        if old in src:
            src = src.replace(old, new, 1)
        else:
            missing.append(old.split("\n")[0][:70])

    if missing and len(missing) == len(REPLACEMENTS):
        print("VFX patcher: ERROR - nothing matched, file not written")
        for m in missing:
            print("  -", m)
    else:
        if missing:
            print("VFX patcher: WARNING, blocks not found:")
            for m in missing:
                print("  -", m)
        target.clear()
        target.write(src)
        print("VFX patcher: updated to", PATCH_VERSION)

exec(compile(src, target.name, 'exec'), {"__name__": "__main__", "__file__": target.name})
print("VFX patcher done")