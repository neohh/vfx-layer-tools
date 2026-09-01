"""VFX Layer Tools — core utilities, collections, scenes, sync."""

import bpy
import os
import uuid

from mathutils import Vector


def uid():
    return uuid.uuid4().hex[:8]


def get_project(context, allow_write=False):
    scene = context.scene
    vfx = scene.vfx
    master = vfx.master_scene
    if master:
        if master != scene:
            return master.vfx, master
        return vfx, scene
    if allow_write:
        vfx.master_scene = scene
        return vfx, scene
    return vfx, scene


def active_layer(vfx):
    if 0 <= vfx.active_layer_index < len(vfx.layers):
        return vfx.layers[vfx.active_layer_index]
    return None


def collect_objects(objects, include_children=False):
    result = []
    seen = set()
    for obj in objects:
        if obj.name not in seen:
            seen.add(obj.name)
            result.append(obj)
        if include_children:
            for child in obj.children_recursive:
                if child.name not in seen:
                    seen.add(child.name)
                    result.append(child)
    return result


def default_layer_name(context):
    obj = context.active_object
    if obj is None and context.selected_objects:
        obj = context.selected_objects[0]
    if obj is not None:
        return obj.name.split(".")[0].strip()
    return ""


def ensure_root(master):
    root = bpy.data.collections.get("VFX")
    if not root:
        root = bpy.data.collections.new("VFX")
    if master.collection.children.get(root.name) is None:
        master.collection.children.link(root)
    return root


def ensure_camera(master):
    cam_obj = master.camera
    if not cam_obj:
        cam_data = bpy.data.cameras.new("VFX_Camera")
        cam_obj = bpy.data.objects.new("VFX_Camera", cam_data)
        master.collection.objects.link(cam_obj)
        master.camera = cam_obj
    return cam_obj


def ensure_camera_collection(master, root):
    col = bpy.data.collections.get("VFX_Camera")
    if not col:
        col = bpy.data.collections.new("VFX_Camera")
    if root.children.get(col.name) is None:
        root.children.link(col)
    cam_obj = ensure_camera(master)
    if col.objects.get(cam_obj.name) is None:
        col.objects.link(cam_obj)
    return col


def ensure_light_collection(master, root):
    col = bpy.data.collections.get("VFX_Lights")
    if not col:
        col = bpy.data.collections.new("VFX_Lights")
    if root.children.get(col.name) is None:
        root.children.link(col)
    return col


def link_lights_to_all_scenes(vfx, col):
    for layer in vfx.layers:
        if layer.scene:
            link_collection_to_scene(layer.scene, col)
        if layer.shadow_scene:
            link_collection_to_scene(layer.shadow_scene, col)


def sync_master_lights(master, root=None):
    if root is None:
        root = ensure_root(master)
    col = ensure_light_collection(master, root)
    count = 0
    for obj in master.objects:
        if obj.type == 'LIGHT':
            if col.objects.get(obj.name) is None:
                col.objects.link(obj)
                count += 1
    return count


def create_empty_scene(name, restore_scene=None):
    old_scene = bpy.context.window.scene if bpy.context.window else None
    scene = None
    try:
        bpy.ops.scene.new(type='EMPTY')
        scene = bpy.context.scene
    except Exception:
        scene = bpy.data.scenes.new(name)
    scene.name = name
    if bpy.context.window:
        if restore_scene:
            bpy.context.window.scene = restore_scene
        elif old_scene:
            bpy.context.window.scene = old_scene
    return scene


def exclude_collection_in_master(master, col):
    def find(lc):
        if lc.collection == col:
            return lc
        for ch in lc.children:
            r = find(ch)
            if r:
                return r
        return None
    for vl in master.view_layers:
        lc = find(vl.layer_collection)
        if lc:
            try:
                lc.exclude = True
            except Exception:
                pass


def link_collection_to_scene(scene, collection):
    if collection is None:
        return
    if scene.collection.children.get(collection.name) is None:
        scene.collection.children.link(collection)


def sync_engine_settings(master, scene):
    for attr in ("cycles", "eevee"):
        src = getattr(master, attr, None)
        dst = getattr(scene, attr, None)
        if src is None or dst is None:
            continue
        for prop in src.bl_rna.properties:
            if prop.is_readonly:
                continue
            if getattr(prop, "is_array", False):
                continue
            name = prop.identifier
            if name == "rna_type":
                continue
            if prop.type not in {'BOOLEAN', 'INT', 'FLOAT', 'ENUM', 'STRING'}:
                continue
            try:
                setattr(dst, name, getattr(src, name))
            except Exception:
                pass


def sync_scene_settings(master, scene):
    try:
        scene.frame_start = master.frame_start
        scene.frame_end = master.frame_end
    except Exception:
        pass
    try:
        scene.render.engine = master.render.engine
    except Exception:
        pass
    try:
        scene.render.resolution_x = master.render.resolution_x
        scene.render.resolution_y = master.render.resolution_y
        scene.render.resolution_percentage = master.render.resolution_percentage
    except Exception:
        pass
    try:
        scene.render.film_transparent = True
    except Exception:
        pass
    try:
        scene.render.filepath = f"//VFX/{scene.name}/"
    except Exception:
        pass
    try:
        scene.render.image_settings.file_format = master.render.image_settings.file_format
    except Exception:
        pass
    try:
        scene.render.image_settings.color_depth = master.render.image_settings.color_depth
    except Exception:
        pass
    try:
        if getattr(master.vfx, "sync_world", True):
            scene.world = master.world
    except Exception:
        pass
    try:
        scene.use_nodes = False
    except Exception:
        pass
    for vl in scene.view_layers:
        try:
            vl.use_pass_mist = True
        except Exception:
            pass
        try:
            vl.use_pass_z = True
        except Exception:
            pass
        try:
            vl.use_pass_normal = True
        except Exception:
            pass
        try:
            vl.use_pass_cryptomatte_object = True
        except Exception:
            pass
        try:
            vl.use_pass_cryptomatte_material = True
        except Exception:
            pass
    sync_engine_settings(master, scene)


def remove_scene_safe(context, scene, master):
    if not scene:
        return
    try:
        if context.window and context.window.scene == scene:
            context.window.scene = master
    except Exception:
        pass
    try:
        bpy.data.scenes.remove(scene)
    except Exception as e:
        print("VFX remove scene error:", e)


def remove_shadow_collections(layer):
    if layer.shadow_cast_collection:
        for obj in list(layer.shadow_cast_collection.objects):
            if obj.get("vfx_proxy") == layer.id:
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except Exception:
                    pass
        try:
            bpy.data.collections.remove(layer.shadow_cast_collection)
        except Exception:
            pass
    if layer.shadow_catch_collection:
        for obj in list(layer.shadow_catch_collection.objects):
            if obj.get("vfx_proxy") == layer.id:
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except Exception:
                    pass
        try:
            bpy.data.collections.remove(layer.shadow_catch_collection)
        except Exception:
            pass


def rename_layer(layer, name):
    if layer.layer_name == name:
        return
    layer.layer_name = name
    if layer.collection:
        layer.collection.name = f"VFX_{name}"
    if layer.scene:
        layer.scene.name = f"VFX_{name}"
    if layer.shadow_scene:
        layer.shadow_scene.name = f"VFX_{name}_SHD"
    if layer.shadow_cast_collection:
        layer.shadow_cast_collection.name = f"VFX_{name}_SHD_CAST"
    if layer.shadow_catch_collection:
        layer.shadow_catch_collection.name = f"VFX_{name}_SHD_CATCH"
    if layer.shadow_catcher and layer.shadow_catcher.name.startswith("VFX_"):
        layer.shadow_catcher.name = f"VFX_{name}_ShadowCatcher"
