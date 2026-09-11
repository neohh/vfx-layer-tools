"""VFX Layer Tools — inter-layer occlusion (holdout) system.

Every layer gets an occlusion collection VFX_<id>_OCCL filled with holdout
proxies of the OTHER layers' objects. A holdout renders as a hole (alpha=0)
and cuts everything behind it per-pixel by depth in the SAME render, so each
layer EXR is already clipped where another layer is closer.

Holdout proxies never affect lighting (all ray visibility off except camera),
never render as visible geometry, and are excluded from fog/grades (they are
never linked into the FOGMAP scene).
"""

import bpy
from mathutils import Vector

from .core import ensure_root, link_collection_to_scene, exclude_collection_in_master

DRAWABLE = {'MESH', 'CURVE', 'VOLUME', 'SURFACE', 'META'}
PROXY_SUFFIX = "_VFXHoldout"
COL_SUFFIX = "_OCCL"


# ---------------------------------------------------------------------
# HOLDOUT HELPERS
# ---------------------------------------------------------------------

def set_holdout(proxy):
    """Mark object as holdout and kill every light interaction.

    Camera ray visibility stays ON — that is what makes it cut a hole.
    """
    try:
        proxy.is_holdout = True
    except Exception as exc:
        print(f"VFX occlusion: cannot set is_holdout on {proxy.name}: {exc}")
        proxy["vfx_holdout"] = True
    # ray visibility: only camera (Blender 5.x top-level attrs)
    for attr in ("visible_shadow", "visible_diffuse", "visible_glossy",
                 "visible_transmission", "visible_volume_scatter"):
        try:
            setattr(proxy, attr, False)
        except Exception:
            pass
    # legacy Cycles fallback
    try:
        vis = proxy.cycles_visibility
        vis.shadow = False
        vis.diffuse = False
        vis.glossy = False
        vis.transmission = False
        vis.volume_scatter = False
    except Exception:
        pass
    # never render as normal visible geometry if holdout is unsupported
    try:
        proxy.visible_camera = True
    except Exception:
        pass
    return proxy


def _make_holdout_proxy(obj, layer_id, col):
    """Duplicate obj as holdout proxy into col (reuses make_proxy pattern)."""
    name = obj.name + PROXY_SUFFIX
    existing = col.objects.get(name)
    if existing is not None and existing.get("vfx_proxy") == layer_id:
        # reuse by name — refresh state, don't duplicate
        if getattr(obj, "data", None):
            try:
                existing.data = obj.data  # follow source mesh swaps
            except Exception:
                pass
        try:
            existing.hide_render = False
            existing.hide_viewport = False
        except Exception:
            pass
        set_holdout(existing)
        return existing

    proxy = obj.copy()
    proxy.name = name
    if getattr(obj, "data", None):
        proxy.data = obj.data
    proxy["vfx_proxy"] = layer_id
    try:
        proxy.hide_render = False
        proxy.hide_viewport = False
    except Exception:
        pass

    # keep parenting: EMPTY chains are linked directly, other parents are
    # baked into a world matrix (same approach as shadow proxies)
    chain = []
    node = obj.parent
    ok_chain = True
    while node is not None:
        if node.type == 'EMPTY':
            chain.append(node)
            node = node.parent
        else:
            ok_chain = False
            break
    if ok_chain:
        proxy.parent = None
        proxy.matrix_parent_inverse.identity()
        if obj.parent is not None:
            proxy.matrix_basis = obj.parent.matrix_world @ \
                obj.matrix_parent_inverse @ obj.matrix_basis
        for n in chain:
            if n.name not in col.objects:
                try:
                    col.objects.link(n)
                except Exception:
                    pass
    else:
        try:
            mw = obj.matrix_world.copy()
        except Exception:
            mw = None
        proxy.parent = None
        proxy.matrix_parent_inverse.identity()
        if mw is not None:
            proxy.matrix_basis = mw

    if col.objects.get(proxy.name) is None:
        col.objects.link(proxy)
    set_holdout(proxy)
    return proxy


# ---------------------------------------------------------------------
# COLLECTION MANAGEMENT
# ---------------------------------------------------------------------

def _find_occlusion_collection(layer):
    want = f"VFX_{layer.id}{COL_SUFFIX}"
    col = bpy.data.collections.get(want)
    if col is not None and col.get("vfx_pass") == "OCCLUSION" \
            and col.get("vfx_id") == layer.id:
        return col
    # lookup by custom props (name may have .001 suffix after collisions)
    for c in bpy.data.collections:
        if c.get("vfx_pass") == "OCCLUSION" and c.get("vfx_id") == layer.id:
            return c
    return None


def _ensure_occlusion_collection(layer, master):
    col = _find_occlusion_collection(layer)
    if col is None:
        col = bpy.data.collections.new(f"VFX_{layer.id}{COL_SUFFIX}")
        col["vfx_id"] = layer.id
        col["vfx_pass"] = "OCCLUSION"
    root = ensure_root(master)
    if root.children.get(col.name) is None:
        try:
            root.children.link(col)
        except Exception:
            pass
    exclude_collection_in_master(master, col)
    try:
        col.name = f"VFX_{layer.id}{COL_SUFFIX}"
    except Exception:
        pass
    return col


def remove_occlusion_collection(layer):
    """Remove the layer's occlusion collection and all its proxies."""
    col = _find_occlusion_collection(layer)
    if col is None:
        return
    for obj in list(col.objects):
        if obj.get("vfx_proxy"):
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass
    try:
        bpy.data.collections.remove(col)
    except Exception:
        pass


def _occluder_layers(vfx, layer):
    """Layers whose objects should occlude `layer` (never itself)."""
    mode = getattr(layer, "occlusion_mode", 'AUTO')
    if mode == 'OFF':
        return []
    if mode == 'MANUAL':
        include = {r.layer_id for r in layer.occlusion_layers if r.include}
        return [o for o in vfx.layers
                if o.id != layer.id and o.enabled and o.id in include]
    return [o for o in vfx.layers
            if o.id != layer.id and o.enabled]


# ---------------------------------------------------------------------
# REBUILD
# ---------------------------------------------------------------------

def _sync_manual_refs(vfx, layer):
    """Keep occlusion_layers refs in sync with the real layer list."""
    valid_ids = [o.id for o in vfx.layers if o.id != layer.id]
    have = {r.layer_id for r in layer.occlusion_layers}
    for lid in valid_ids:
        if lid not in have:
            r = layer.occlusion_layers.add()
            r.layer_id = lid
            r.include = True
    for i in range(len(layer.occlusion_layers) - 1, -1, -1):
        if layer.occlusion_layers[i].layer_id not in valid_ids:
            layer.occlusion_layers.remove(i)


def rebuild_layer_occlusion(vfx, master, layer):
    """(Re)build holdout proxies for one layer. Returns proxy count."""
    if not layer.scene:
        return 0
    _sync_manual_refs(vfx, layer)

    occl = getattr(layer, "occlusion_collection", None)
    if occl is not None and occl.get("vfx_pass") != "OCCLUSION":
        layer.occlusion_collection = None
        occl = None

    mode = getattr(layer, "occlusion_mode", 'AUTO')
    if mode == 'OFF':
        if occl is not None:
            remove_occlusion_collection(layer)
            layer.occlusion_collection = None
        return 0

    col = _ensure_occlusion_collection(layer, master)
    layer.occlusion_collection = col

    # expected proxy names from current occluders
    expected = {}
    for other in _occluder_layers(vfx, layer):
        if not other.collection:
            continue
        for obj in other.collection.objects:
            if obj.type not in DRAWABLE:
                continue
            if getattr(obj, "hide_render", False):
                continue  # invisible source can't occlude anything
            expected[obj.name] = obj

    # drop proxies that no longer match any source
    for obj in list(col.objects):
        if not obj.get("vfx_proxy"):
            continue  # linked EMPTY parent chains are kept
        base = obj.name[:-len(PROXY_SUFFIX)] if obj.name.endswith(PROXY_SUFFIX) else obj.name
        if base not in expected:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass

    # create/refresh proxies
    count = 0
    for name, obj in expected.items():
        _make_holdout_proxy(obj, layer.id, col)
        count += 1

    # collection linked into the layer scene (and nothing else)
    link_collection_to_scene(layer.scene, col)
    exclude_collection_in_master(master, col)
    return count


def rebuild_all_occlusion(vfx, master, report=False):
    """Rebuild occlusion for every layer (create/delete/content changes)."""
    stats = {}
    for layer in vfx.layers:
        try:
            stats[layer.layer_name] = rebuild_layer_occlusion(vfx, master, layer)
        except Exception as exc:
            print(f"VFX occlusion: rebuild error on '{layer.layer_name}': {exc}")
            stats[layer.layer_name] = -1
    if report:
        occlusion_self_check(vfx, master)
    return stats


# ---------------------------------------------------------------------
# COMPOSITE ORDER
# ---------------------------------------------------------------------

def _camera_distance(master, layer):
    """Distance from camera to the NEAREST drawable object of the layer."""
    cam_obj = getattr(master, "camera", None) if master else None
    if cam_obj is None:
        return None
    cam_pos = cam_obj.matrix_world.translation
    best = None
    if layer.collection:
        for obj in layer.collection.objects:
            if obj.type not in DRAWABLE or getattr(obj, "hide_render", False):
                continue
            try:
                corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
                d = min((p - cam_pos).length for p in corners)
            except Exception:
                continue
            if best is None or d < best:
                best = d
    return best


def compute_composite_order(vfx, master, verbose=True):
    """Composite sequence of layers, BACK to FRONT (index 0 composited first,
    i.e. bottom-most). Background is handled separately by the assembler.

    AUTO  — sort by camera distance of nearest object (farthest = bottom).
    MANUAL — current list order (list index 0 = top of stack = composited last).
    """
    mode = getattr(vfx, "composite_sort_mode", 'AUTO')
    layers = [l for l in vfx.layers if l.enabled and (l.scene or l.shadow_scene)]

    if mode == 'MANUAL' or master is None or master.camera is None:
        if verbose and mode == 'AUTO' and master is not None and master.camera is None:
            print("VFX order: no camera — falling back to MANUAL (list order)")
        # list index 0 = top; back-to-front = reversed list
        seq = list(reversed(layers))
        dists = {l.id: None for l in seq}
    else:
        decorated = []
        for l in layers:
            d = _camera_distance(master, l)
            decorated.append((d, l))
        # nearest first (top of stack); None (no objects) = farthest = bottom
        decorated.sort(key=lambda t: (1 if t[0] is None else 0, t[0] if t[0] is not None else 0.0))
        nearest_first = [l for d, l in decorated]
        dists = {l.id: d for d, l in decorated}
        seq = list(reversed(nearest_first))  # back-to-front

    if verbose:
        def fmt(d):
            return f"{d:.2f}m" if d is not None else "—"
        chain = " | ".join(
            f"{l.layer_name}({fmt(dists.get(l.id))})" for l in seq
        ) or "(no layers)"
        print(f"[VFX order] bottom -> top: BACKGROUND | {chain}")
    return seq


# ---------------------------------------------------------------------
# SELF-CHECK
# ---------------------------------------------------------------------

def occlusion_self_check(vfx, master):
    """Print occlusion state per layer + final composite order (console)."""
    lines = ["[VFX occlusion] self-check:"]
    problems = []
    for layer in vfx.layers:
        mode = getattr(layer, "occlusion_mode", 'AUTO')
        occl = getattr(layer, "occlusion_collection", None)
        occluders = [o.layer_name for o in _occluder_layers(vfx, layer)]
        if layer.id in [o.id for o in _occluder_layers(vfx, layer)]:
            problems.append(f"layer {layer.layer_name}: occludes ITSELF")
        if mode == 'OFF':
            ok = occl is None or len(occl.objects) == 0
            lines.append(f"  {layer.layer_name}: OFF (clean={ok})")
            continue
        if occl is None:
            lines.append(f"  {layer.layer_name}: {mode} — NO COLLECTION (scene missing?)")
            continue
        linked = layer.scene is not None and occl.name in layer.scene.collection.children
        proxies = [o for o in occl.objects if o.get("vfx_proxy")]
        holdout_ok = 0
        for p in proxies:
            if getattr(p, "is_holdout", False) or p.get("vfx_holdout"):
                holdout_ok += 1
            if hasattr(p, "visible_shadow") and p.visible_shadow:
                problems.append(f"proxy {p.name}: visible_shadow is ON (must be OFF)")
        lines.append(
            f"  {layer.layer_name}: mode={mode} occluders={occluders or '[]'} "
            f"proxies={len(proxies)} holdout={holdout_ok}/{len(proxies)} "
            f"linked_to_scene={linked}"
        )
        if not linked and layer.scene:
            problems.append(f"layer {layer.layer_name}: occlusion collection not linked to its scene")

    bg = getattr(vfx, "bg_scene", None)
    lines.append(f"  background: {'VFX_BG (always bottom) OK' if bg else 'not created'}")

    for layer in vfx.layers:
        eng = getattr(layer.scene.render, "engine", "") if layer.scene else ""
        if eng and "CYCLES" not in eng and eng != "":
            lines.append(f"  NOTE: layer '{layer.layer_name}' uses {eng} — "
                         f"holdout is guaranteed in Cycles; in EEVEE Next it depends "
                         f"on Blender version (else use Z-glue fallback)")
            break

    seq = compute_composite_order(vfx, master)
    lines.append("  composite order (bottom->top): BACKGROUND -> "
                 + " -> ".join(l.layer_name for l in seq))
    if problems:
        for p in problems:
            lines.append("  PROBLEM: " + p)
    else:
        lines.append("  occlusion OK")
    print("\n".join(lines))
    return not problems
