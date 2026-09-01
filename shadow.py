"""VFX Layer Tools — shadow pass system."""

import bpy
import bmesh
from mathutils import Vector

from .core import ensure_root, ensure_camera_collection, ensure_light_collection


def set_shadow_catcher(obj, state=True):
    if hasattr(obj, "is_shadow_catcher"):
        try:
            obj.is_shadow_catcher = state
            return
        except Exception:
            pass
    try:
        if hasattr(obj, "cycles") and hasattr(obj.cycles, "is_shadow_catcher"):
            obj.cycles.is_shadow_catcher = state
        elif hasattr(obj, "cycles") and hasattr(obj.cycles, "shadow_catcher"):
            obj.cycles.shadow_catcher = state
        else:
            obj["vfx_shadow_catcher"] = state
    except Exception:
        obj["vfx_shadow_catcher"] = state


def set_only_shadow_caster(obj):
    if hasattr(obj, "visible_camera"):
        try:
            obj.visible_camera = False
            obj.visible_shadow = True
            obj.visible_diffuse = False
            obj.visible_glossy = False
            obj.visible_transmission = False
            obj.visible_volume_scatter = False
            return
        except Exception:
            pass
    try:
        if hasattr(obj, "cycles_visibility"):
            vis = obj.cycles_visibility
            vis.camera = False
            vis.shadow = True
            for attr in ("diffuse", "glossy", "transmission", "volume_scatter"):
                try:
                    setattr(vis, attr, False)
                except Exception:
                    pass
    except Exception:
        pass


def refresh_shadow_proxies(vfx, master):
    for layer in vfx.layers:
        if not layer.shadow_scene:
            continue
        cast_col = layer.shadow_cast_collection
        catch_col = layer.shadow_catch_collection
        if not cast_col or not catch_col:
            continue
        for col in (cast_col, catch_col):
            for obj in list(col.objects):
                if obj.get("vfx_proxy") == layer.id:
                    try:
                        bpy.data.objects.remove(obj, do_unlink=True)
                    except Exception:
                        pass
        drawable = {'MESH', 'CURVE', 'VOLUME', 'SURFACE', 'META'}

        def make_proxy(obj, suffix, col):
            proxy = obj.copy()
            proxy.name = obj.name + suffix
            if getattr(obj, "data", None):
                proxy.data = obj.data
            proxy["vfx_proxy"] = layer.id
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
            return proxy

        if layer.shadow_mode == 'RECEIVE':
            for obj in layer.collection.objects:
                if obj.type not in drawable:
                    continue
                p = make_proxy(obj, "_VFXCatch", catch_col)
                set_shadow_catcher(p, True)
            for other in vfx.layers:
                if other.id == layer.id or not other.enabled or not other.collection:
                    continue
                for obj in other.collection.objects:
                    if obj.type not in drawable:
                        continue
                    p = make_proxy(obj, "_VFXShadowCaster", cast_col)
                    set_only_shadow_caster(p)
        else:
            catcher = layer.shadow_catcher
            for obj in layer.collection.objects:
                if catcher and obj == catcher:
                    continue
                if obj.type not in drawable:
                    continue
                p = make_proxy(obj, "_VFXShadowCaster", cast_col)
                set_only_shadow_caster(p)
            if catcher and catch_col.objects.get(catcher.name) is None:
                catch_col.objects.link(catcher)


def repair_shadow_proxies():
    for col in bpy.data.collections:
        vp = col.get("vfx_pass", "")
        if vp == "SHADOW_CAST":
            for obj in col.objects:
                if obj.get("vfx_proxy"):
                    set_only_shadow_caster(obj)
        elif vp == "SHADOW_CATCH":
            for obj in col.objects:
                if obj.get("vfx_proxy"):
                    set_shadow_catcher(obj, True)


def create_default_catcher(layer, master):
    name = f"VFX_{layer.layer_name}_ShadowCatcher"
    existing = bpy.data.objects.get(name)
    if existing:
        return existing
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=50.0)
    bm.to_mesh(mesh)
    bm.free()
    catcher = bpy.data.objects.new(name, mesh)
    master.collection.objects.link(catcher)
    min_z = None
    if layer.collection:
        for obj in layer.collection.objects:
            if obj.type in {'MESH', 'CURVE', 'VOLUME', 'SURFACE', 'META'} and obj.bound_box:
                for corner in obj.bound_box:
                    world_corner = obj.matrix_world @ Vector(corner)
                    z = world_corner.z
                    min_z = z if min_z is None else min(min_z, z)
    if min_z is not None:
        catcher.location.z = min_z - 0.05
    set_shadow_catcher(catcher, True)
    return catcher
