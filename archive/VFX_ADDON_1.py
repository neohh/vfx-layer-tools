# VFX_LAYER_TOOLS_VERSION = "1.42"

bl_info = {
    "name": "VFX Layer Tools",
    "author": "VFX Pipeline",
    "version": (1, 42, 0),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > VFX",
    "description": "VFX layer / scene / compositing manager",
    "category": "Compositing",
}


import bpy
import os
import time
import uuid
import bmesh
from mathutils import Vector
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


VFX_VERSION = "1.42"


# ---------------------------------------------------------------------
# UTILS
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# COLLECTIONS
# ---------------------------------------------------------------------

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


def link_lights_to_all_scenes(vfx, col):
    for layer in vfx.layers:
        if layer.scene:
            link_collection_to_scene(layer.scene, col)
        if layer.shadow_scene:
            link_collection_to_scene(layer.shadow_scene, col)


# ---------------------------------------------------------------------
# SCENES
# ---------------------------------------------------------------------

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


def vfx_output(context):
    try:
        return context.scene.vfx.output_dir
    except Exception:
        return "//VFX_render/"


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

    sync_engine_settings(master, scene)


# ---------------------------------------------------------------------
# SHADOW
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# COMP TREE
# ---------------------------------------------------------------------

def _find_comp_tree_attr(master):
    for attr in ("node_tree", "compositor_node_tree", "compositing_node_tree"):
        tree = getattr(master, attr, None)
        if tree is not None:
            return tree

    for attr in dir(master):
        low = attr.lower()
        if "node" in low or "comp" in low:
            try:
                val = getattr(master, attr)
            except Exception:
                continue
            if isinstance(val, bpy.types.NodeTree):
                return val

    return None


def get_comp_tree(master, create=True):
    tree = _find_comp_tree_attr(master)
    if tree is not None:
        return tree

    if hasattr(master, "use_nodes"):
        try:
            master.use_nodes = True
        except Exception:
            pass
        tree = _find_comp_tree_attr(master)
        if tree is not None:
            return tree

    if not create:
        return None

    tree = None
    for ng in bpy.data.node_groups:
        if ng.bl_idname == 'CompositorNodeTree':
            tree = ng
            break

    if tree is None:
        try:
            tree = bpy.data.node_groups.new(name="VFX_Compositor", type='CompositorNodeTree')
        except Exception:
            print("VFX: cannot create CompositorNodeTree")
            return None

    for attr in ("node_tree", "compositor_node_tree", "compositing_node_tree"):
        if hasattr(master, attr):
            try:
                setattr(master, attr, tree)
                return tree
            except Exception:
                continue

    for attr in dir(master):
        low = attr.lower()
        if "node" in low or "comp" in low:
            try:
                if getattr(master, attr) is None:
                    setattr(master, attr, tree)
                    return tree
            except Exception:
                continue

    print("VFX: warning - compositor tree not attached, using detached tree")
    return tree


# ---------------------------------------------------------------------
# COMP FROM FILES
# ---------------------------------------------------------------------


def _load_sequence_image(scene_name, base_path):
    img_name = f"VFX_SEQ_{scene_name}"

    existing = bpy.data.images.get(img_name)
    if existing is not None:
        abs_base = bpy.path.abspath(base_path)
        folder = os.path.join(abs_base, scene_name)
        if os.path.isdir(folder):
            files = [f for f in os.listdir(folder) if f.lower().endswith('.exr')]
            if files:
                existing.source = 'SEQUENCE'
                try:
                    existing.filepath_raw = os.path.join(folder, "####.exr")
                    existing.frame_duration = max(1, len(files))
                    existing.reload()
                except Exception:
                    pass
                print(f"VFX SEQ refresh: {img_name} files={len(files)}")
        return existing

    abs_base = bpy.path.abspath(base_path)
    folder = os.path.join(abs_base, scene_name)

    if not os.path.isdir(folder):
        print(f"VFX: folder not found: {folder}")
        return None

    files = sorted([f for f in os.listdir(folder) if f.lower().endswith('.exr')])
    if not files:
        print(f"VFX: no EXR in {folder}")
        return None

    first_file = os.path.join(folder, files[0])
    try:
        img = bpy.data.images.load(first_file, check_existing=False)
    except Exception as e:
        print(f"VFX: load failed {first_file}: {e}")
        return None

    start_frame = 1
    try:
        digits = "".join(ch for ch in files[0] if ch.isdigit())
        if digits:
            start_frame = int(digits)
    except Exception:
        pass

    img.name = img_name
    img.source = 'SEQUENCE'
    try:
        img.filepath_raw = os.path.join(folder, "####.exr")
        img.filepath = img.filepath_raw
        img.frame_start = start_frame
        img.frame_offset = 0
        img.frame_duration = max(1, len(files))
        img.reload()
    except Exception:
        pass

    print(f"VFX SEQ load: {img_name} <- {folder} files={len(files)} start={start_frame}")
    return img


def _load_sequence_image2(scene_name, base_path):
    img_name = f"VFX_SEQ_{scene_name}"
    abs_base = bpy.path.abspath(base_path)
    folder = os.path.join(abs_base, scene_name)

    files = []
    if os.path.isdir(folder):
        files = sorted([f for f in os.listdir(folder)
                        if f.lower().endswith('.exr')])
    if not files:
        print("VFX SEQ2: no EXR in", folder)
        return None

    start_frame = 1
    digits = "".join(ch for ch in files[0] if ch.isdigit())
    if digits:
        start_frame = int(digits)

    existing = bpy.data.images.get(img_name)
    if existing is not None:
        if getattr(existing, "frame_duration", 1) >= len(files):
            try:
                existing.reload()
            except Exception:
                pass
            return existing
        try:
            bpy.data.images.remove(existing)
        except Exception:
            pass

    win = None
    if bpy.context.window_manager.windows:
        win = bpy.context.window_manager.windows[0]

    before = set(bpy.data.images.keys())
    try:
        with bpy.context.temp_override(window=win):
            bpy.ops.image.open(
                directory=folder + os.sep,
                files=[{"name": f} for f in files],
                check_existing=False,
                relative_path=False,
            )
    except Exception as e:
        print("VFX SEQ2: ops open failed:", e)
        return _load_sequence_image(scene_name, base_path)

    new_imgs = [bpy.data.images[k]
                for k in (set(bpy.data.images.keys()) - before)]
    if not new_imgs:
        print("VFX SEQ2: no new image, fallback")
        return _load_sequence_image(scene_name, base_path)

    img = new_imgs[0]
    img.name = img_name
    try:
        img.frame_start = start_frame
        img.frame_offset = 0
    except Exception:
        pass

    print(f"VFX SEQ2: {img_name} files={len(files)} "
          f"start={start_frame} frames={getattr(img, 'frame_duration', 1)}")
    return img


def rebuild_comp_from_files(vfx, master):
    nt = get_comp_tree(master)
    if not nt:
        return

    for node in list(nt.nodes):
        if node.type == 'R_LAYERS' and node.get("vfx_id"):
            nt.nodes.remove(node)

    y = 0
    for layer in vfx.layers:
        if not layer.enabled:
            continue

        if layer.scene:
            node_name = f"VFX_RL_{layer.id}"
            img = _load_sequence_image2(layer.scene.name, vfx.output_dir)

            node = nt.nodes.get(node_name)
            if node is not None and node.type != 'IMAGE':
                nt.nodes.remove(node)
                node = None
            if node is None:
                node = nt.nodes.new("CompositorNodeImage")
                node.name = node_name
            if img is not None:
                node.image = img
            node.label = layer.layer_name
            node["vfx_id"] = layer.id
            node["vfx_pass"] = "OBJECT"
            node.location = (0, y)

        if layer.shadow_scene:
            node_name = f"VFX_RL_{layer.id}_SHD"
            img = _load_sequence_image2(layer.shadow_scene.name, vfx.output_dir)

            node = nt.nodes.get(node_name)
            if node is not None and node.type != 'IMAGE':
                nt.nodes.remove(node)
                node = None
            if node is None:
                node = nt.nodes.new("CompositorNodeImage")
                node.name = node_name
            if img is not None:
                node.image = img
            node.label = f"{layer.layer_name} SHD"
            node["vfx_id"] = layer.id
            node["vfx_pass"] = "SHADOW"
            node.location = (320, y)

        y -= 220

    valid_names = set()
    for layer in vfx.layers:
        if layer.enabled and layer.scene:
            valid_names.add(f"VFX_RL_{layer.id}")
        if layer.enabled and layer.shadow_scene:
            valid_names.add(f"VFX_RL_{layer.id}_SHD")

    bg_scene = getattr(vfx, "bg_scene", None)
    if bg_scene:
        valid_names.add("VFX_RL_BG")
        node_name = "VFX_RL_BG"
        img = _load_sequence_image2(bg_scene.name, vfx.output_dir)
        node = nt.nodes.get(node_name)
        if node is not None and node.type != 'IMAGE':
            nt.nodes.remove(node)
            node = None
        if node is None:
            node = nt.nodes.new("CompositorNodeImage")
            node.name = node_name
        if img is not None:
            node.image = img
        node.label = "BACKGROUND"
        node["vfx_id"] = "BG"
        node["vfx_pass"] = "OBJECT"
        node.location = (0, 400)

    for node in list(nt.nodes):
        if node.type == 'IMAGE' and node.name.startswith("VFX_RL_"):
            if node.name not in valid_names:
                nt.nodes.remove(node)

    for node in nt.nodes:
        if node.type == 'IMAGE' and node.name.startswith("VFX_RL_"):
            im = getattr(node, "image", None)
            print("VFX comp node:", node.name,
                  "| img:", im.name if im else None,
                  "| frames:", getattr(im, "frame_duration", 0) if im else 0,
                  "| start:", getattr(im, "frame_start", 0) if im else 0)

    try:
        nt.update_tag()
    except Exception:
        pass
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()

    build_comp_assembly(vfx, master)


# ---------------------------------------------------------------------
# COMP NODES
# ---------------------------------------------------------------------

def remove_comp_node(master, node_name):
    nt = get_comp_tree(master, create=False)
    if not nt:
        return

    node = nt.nodes.get(node_name)
    if node:
        nt.nodes.remove(node)


def ensure_render_node(master, scene, node_name, label, layer_id, pass_type, x=0, y=0):
    nt = get_comp_tree(master)
    if not nt:
        return None

    node = nt.nodes.get(node_name)

    if node is not None and node.type != 'R_LAYERS':
        nt.nodes.remove(node)
        node = None

    if not node:
        node = nt.nodes.new("CompositorNodeRLayers")
        node.name = node_name

    node.label = label

    if scene:
        node.scene = scene
        if scene.view_layers:
            node.layer = scene.view_layers[0].name

    node["vfx_id"] = layer_id
    node["vfx_pass"] = pass_type
    node.location = (x, y)

    return node


def rebuild_comp(vfx, master):
    nt = get_comp_tree(master)
    if not nt:
        return

    valid_nodes = set()
    y = 0

    for i, layer in enumerate(vfx.layers):
        if layer.enabled and layer.scene:
            node_name = f"VFX_RL_{layer.id}"
            valid_nodes.add(node_name)
            ensure_render_node(
                master, layer.scene, node_name, layer.layer_name,
                layer.id, "OBJECT", x=0, y=y
            )

        if layer.enabled and layer.shadow_scene:
            node_name = f"VFX_RL_{layer.id}_SHD"
            valid_nodes.add(node_name)
            ensure_render_node(
                master, layer.shadow_scene, node_name, f"{layer.layer_name} SHD",
                layer.id, "SHADOW", x=320, y=y
            )

        y -= 220

    bg_scene = getattr(vfx, "bg_scene", None)
    if bg_scene:
        valid_nodes.add("VFX_RL_BG")
        ensure_render_node(
            master, bg_scene, "VFX_RL_BG", "BACKGROUND",
            "BG", "OBJECT", x=0, y=y
        )

    for node in list(nt.nodes):
        if node.type == 'IMAGE' and node.get("vfx_id"):
            nt.nodes.remove(node)

    for node in list(nt.nodes):
        if node.type == 'R_LAYERS' and node.get("vfx_id") and node.name not in valid_nodes:
            nt.nodes.remove(node)

    build_comp_assembly(vfx, master)


def build_comp_assembly(vfx, master, nt=None):
    if nt is None:
        nt = get_comp_tree(master)
    if not nt:
        return

    for node in list(nt.nodes):
        if node.get("vfx_mix"):
            nt.nodes.remove(node)

    sockets = []
    for layer in reversed(vfx.layers):
        if not layer.enabled:
            continue

        sh_sock = None
        ob_sock = None

        if layer.shadow_scene:
            sh = nt.nodes.get(f"VFX_RL_{layer.id}_SHD")
            if sh and sh.outputs.get("Image"):
                sh_sock = sh.outputs["Image"]

        if layer.scene:
            ob = nt.nodes.get(f"VFX_RL_{layer.id}")
            if ob and ob.outputs.get("Image"):
                ob_sock = ob.outputs["Image"]
                ob_sock = _apply_fog(nt, layer, ob_sock)

        if getattr(layer, "shadow_mode", "CAST") == 'RECEIVE':
            if ob_sock:
                sockets.append((layer, "OBJ", ob_sock))
            if sh_sock:
                sockets.append((layer, "SHD", sh_sock))
        else:
            if sh_sock:
                sockets.append((layer, "SHD", sh_sock))
            if ob_sock:
                sockets.append((layer, "OBJ", ob_sock))

    bg_sock = None
    bgn = nt.nodes.get("VFX_RL_BG")
    if bgn is not None and bgn.outputs.get("Image"):
        bg_sock = bgn.outputs["Image"]

    if not sockets and bg_sock is None:
        return

    if bg_sock is not None:
        current = bg_sock
        mix_list = sockets
    else:
        current = sockets[0][2]
        mix_list = sockets[1:]

    mix_index = 0
    for layer, kind, sock in mix_list:
        mix = nt.nodes.new("CompositorNodeAlphaOver")
        mix.name = f"VFX_MIX_{mix_index:02d}"
        mix.label = f"{layer.layer_name} {kind}"
        mix["vfx_mix"] = 1
        mix.location = (600, -mix_index * 180)

        img = [s for s in mix.inputs if s.type == 'RGBA']
        fac = [s for s in mix.inputs if s.type == 'VALUE']

        if len(img) >= 2:
            bg, fg = img[0], img[1]
        else:
            bg, fg = mix.inputs[1], mix.inputs[2]

        mix_fac = 1.0
        if kind == 'SHD':
            mix_fac = getattr(layer, "shadow_strength", 1.0)
        for f in fac:
            try:
                f.default_value = mix_fac
            except Exception:
                pass

        nt.links.new(current, bg)
        nt.links.new(sock, fg)

        outs = [s for s in mix.outputs if s.type == 'RGBA']
        current = outs[0] if outs else mix.outputs[0]
        mix_index += 1

    comp = None
    for node in nt.nodes:
        if node.type == 'COMPOSITE':
            comp = node
            break

    if comp is None:
        for bid in ("CompositorNodeComposite",
                    "CompositorNodeOutput",
                    "NodeComposite"):
            try:
                comp = nt.nodes.new(bid)
                comp.location = (900, 0)
                break
            except Exception:
                comp = None
                continue

    if comp is not None and len(comp.inputs) > 0:
        target_sock = comp.inputs.get("Image") or comp.inputs[0]
        nt.links.new(current, target_sock)
    else:
        print("VFX: warning - no composite output node available")

    try:
        gout = None
        for node in nt.nodes:
            if node.type == 'GROUP_OUTPUT':
                gout = node
                break
        if gout is None:
            try:
                nt.interface.new_socket(
                    "Image", in_out='OUTPUT', socket_type='NodeSocketColor'
                )
            except Exception:
                pass
            gout = nt.nodes.new("NodeGroupOutput")
            gout.location = (900, -250)
        if gout is not None and len(gout.inputs) > 0:
            nt.links.new(current, gout.inputs[0])
    except Exception:
        pass

    for node in nt.nodes:
        if node.type == 'VIEWER' and len(node.inputs) > 0:
            vsock = node.inputs.get("Image") or node.inputs[0]
            nt.links.new(current, vsock)
            break


def _get_mist_socket(nt, layer):
    n = nt.nodes.get(f"VFX_RL_{layer.id}_MIST")
    if n is not None and n.outputs.get("Image"):
        return n.outputs["Image"]
    rl = nt.nodes.get(f"VFX_RL_{layer.id}")
    if rl is not None and rl.outputs.get("Mist"):
        return rl.outputs["Mist"]
    return None


def _apply_fog(nt, layer, sock):
    strength = getattr(layer, "fog_strength", 0.0)
    if strength <= 0.001 or sock is None:
        return sock

    mist = _get_mist_socket(nt, layer)
    if mist is None:
        return sock

    math_n = nt.nodes.get(f"VFX_FOGMATH_{layer.id}")
    if math_n is None:
        math_n = nt.nodes.new("CompositorNodeMath")
        math_n.name = f"VFX_FOGMATH_{layer.id}"
        math_n.operation = 'MULTIPLY'
        math_n.location = (100, 400)
    math_n.inputs[1].default_value = strength
    for l in list(math_n.inputs[0].links):
        nt.links.remove(l)
    nt.links.new(mist, math_n.inputs[0])

    comb = nt.nodes.get(f"VFX_FOGC_{layer.id}")
    if comb is None:
        comb = nt.nodes.new("CompositorNodeCombineColor")
        comb.name = f"VFX_FOGC_{layer.id}"
        comb.location = (260, 400)
    try:
        r, g, b, a = layer.fog_color
        comb.inputs[0].default_value = r
        comb.inputs[1].default_value = g
        comb.inputs[2].default_value = b
    except Exception:
        pass
    for l in list(comb.inputs[3].links):
        nt.links.remove(l)
    nt.links.new(math_n.outputs[0], comb.inputs[3])

    over = nt.nodes.get(f"VFX_FOG_{layer.id}")
    if over is None:
        over = nt.nodes.new("CompositorNodeAlphaOver")
        over.name = f"VFX_FOG_{layer.id}"
        over.location = (420, 400)
    img_ins = [s for s in over.inputs if s.type == 'RGBA']
    if len(img_ins) >= 2:
        for l in list(img_ins[0].links):
            nt.links.remove(l)
        for l in list(img_ins[1].links):
            nt.links.remove(l)
        nt.links.new(sock, img_ins[0])
        nt.links.new(comb.outputs[0], img_ins[1])
        outs = [s for s in over.outputs if s.type == 'RGBA']
        if outs:
            return outs[0]
    return sock


def ensure_file_output(master, vfx):
    nt = get_comp_tree(master)
    if not nt:
        return

    node = nt.nodes.get("VFX_FileOutput")

    if not node:
        node = nt.nodes.new("CompositorNodeOutputFile")
        node.name = "VFX_FileOutput"
        node.label = "VFX Output"

    try:
        node.base_path = vfx.output_dir
    except Exception:
        pass

    try:
        if hasattr(node, "format"):
            node.format.file_format = 'OPEN_EXR'
    except Exception:
        pass

    for i, layer in enumerate(vfx.layers):
        if not layer.enabled:
            continue

        pairs = []

        if layer.scene:
            pairs.append((f"VFX_RL_{layer.id}", f"{i+1:02d}_{layer.layer_name}_OBJ"))

        if layer.shadow_scene:
            pairs.append((f"VFX_RL_{layer.id}_SHD", f"{i+1:02d}_{layer.layer_name}_SHD"))

        for rl_name, slot_name in pairs:
            rl_node = nt.nodes.get(rl_name)
            if not rl_node:
                continue

            try:
                if node.file_slots.get(slot_name) is None:
                    node.file_slots.new(slot_name)

                inp = node.inputs.get(slot_name)
                if inp is None and len(node.inputs) > 0:
                    inp = node.inputs[-1]

                out = rl_node.outputs.get("Image")

                if inp and out:
                    already_linked = any(
                        l.from_socket == out and l.to_socket == inp for l in nt.links
                    )
                    if not already_linked:
                        nt.links.new(out, inp)

            except Exception as e:
                print("VFX file output error:", e)


def rebuild_file_output(master, vfx):
    nt = get_comp_tree(master, create=False)
    if not nt:
        return

    node = nt.nodes.get("VFX_FileOutput")
    if not node:
        return

    for link in list(nt.links):
        if link.to_node == node:
            nt.links.remove(link)

    try:
        node.file_slots.clear()
    except Exception:
        pass

    ensure_file_output(master, vfx)


# ---------------------------------------------------------------------
# CLEANUP
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# PROPERTIES
# ---------------------------------------------------------------------

class VFXLayer(bpy.types.PropertyGroup):
    id: StringProperty(default="")
    layer_name: StringProperty(default="Layer")
    enabled: BoolProperty(default=True)

    collection: PointerProperty(type=bpy.types.Collection)
    scene: PointerProperty(type=bpy.types.Scene)

    use_shadow: BoolProperty(default=False)
    shadow_mode: EnumProperty(
        name="Shadow Mode",
        items=(
            ('CAST', "Cast", "Layer objects cast shadow onto catcher"),
            ('RECEIVE', "Receive", "Layer receives shadows from other layers"),
        ),
        default='CAST'
    )
    shadow_scene: PointerProperty(type=bpy.types.Scene)
    shadow_catcher: PointerProperty(type=bpy.types.Object)

    shadow_cast_collection: PointerProperty(type=bpy.types.Collection)
    shadow_catch_collection: PointerProperty(type=bpy.types.Collection)

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
    fog_strength: FloatProperty(
        name="Fog Strength", default=0.0, min=0.0, max=1.0,
        update=lambda self, ctx: _trigger_comp(ctx)
    )
    fog_color: FloatVectorProperty(
        name="Fog Color", subtype='COLOR', size=4,
        default=(0.7, 0.75, 0.85, 1.0), min=0.0, max=1.0,
        update=lambda self, ctx: _trigger_comp(ctx)
    )
    expanded: BoolProperty(
        name="Expanded",
        description="Show shadow pass sub-row",
        default=False
    )


def _engine_label(i):
    return i.replace("BLENDER_", "").replace("_", " ").title()


def _engine_items_eevee_first(self, context):
    ids = [i.identifier for i in bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items]
    if "CYCLES" not in ids:
        ids = ["CYCLES"] + ids
    pref = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in ids else (ids[0] if ids else "CYCLES")
    ordered = [pref] + [i for i in ids if i != pref]
    return [(i, _engine_label(i), "") for i in ordered]


def _engine_items_cycles_first(self, context):
    ids = [i.identifier for i in bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items]
    if "CYCLES" not in ids:
        ids = ["CYCLES"] + ids
    ordered = ["CYCLES"] + [i for i in ids if i != "CYCLES"]
    return [(i, _engine_label(i), "") for i in ordered]


class VFXProject(bpy.types.PropertyGroup):
    master_scene: PointerProperty(type=bpy.types.Scene)

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
    shadow_strength: FloatProperty(
        name="Shadow Strength",
        description="How strong the shadow pass is blended over the layer",
        default=1.0, min=0.0, max=1.0,
        update=lambda self, ctx: _trigger_rebuild(ctx)
    )
    render_running: BoolProperty(default=False)
    render_progress: FloatProperty(default=0.0, min=0.0, max=1.0)
    render_status: StringProperty(default="")
    render_mode: EnumProperty(
        name="Render Mode",
        items=(
            ('PREVIEW', "Preview", "Current frame of each layer scene"),
            ('ANIMATION', "Animation", "Full animation to EXR"),
        ),
        default='PREVIEW'
    )
    comp_mode: EnumProperty(
        name="Comp Source",
        description="How compositing reads layers: live from 3D or from rendered EXR files",
        items=(
            ('LIVE', "Live (Render Layers)", "Comp uses Render Layers nodes"),
            ('FILES', "From Files (EXR)", "Comp uses Image Sequence nodes from EXR"),
        ),
        default='LIVE'
    )
    bg_scene: PointerProperty(
        type=bpy.types.Scene,
        name="Background scene",
        description="World-only background pass, always bottom of comp"
    )


# ---------------------------------------------------------------------
# UI LIST
# ---------------------------------------------------------------------

class VFX_UL_layers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        has_shd = bool(item.shadow_scene)

        row = layout.row(align=True)
        row.prop(item, "enabled", text="")

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
# MATERIAL ADJUST
# ---------------------------------------------------------------------

ADJ_PREFIX = "VFX_ADJ_"

_last_adjust_stats = {"objects": 0, "materials": 0, "applied": 0, "notes": []}


def _trigger_rebuild(context):
    try:
        scene = context.scene
        vfx = scene.vfx
        for layer in vfx.layers:
            update_layer_material_adjust(layer)
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    except Exception as e:
        print("VFX trigger_rebuild error:", e)


def _trigger_comp(context):
    try:
        scene = context.scene
        vfx = scene.vfx
        master = vfx.master_scene or scene
        build_comp_assembly(vfx, master)
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    except Exception:
        pass


def _find_bsdf(mat):
    nt = mat.node_tree
    for n in nt.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            return n
    for n in nt.nodes:
        if n.type.startswith('BSDF'):
            return n
    for n in nt.nodes:
        if n.type == 'OUTPUT_MATERIAL':
            for inp in n.inputs:
                if inp.is_linked and inp.links[0].from_node:
                    return inp.links[0].from_node
    return None


def _find_base_color_input(bsdf):
    for name in ("Base Color", "Color", "Albedo", "Diffuse"):
        s = bsdf.inputs.get(name)
        if s:
            return s
    for s in bsdf.inputs:
        if s.type == 'RGBA':
            return s
    return None


def _ensure_material(obj):
    if not obj.material_slots:
        mat = bpy.data.materials.new(name=obj.name + "_Mat")
        obj.data.materials.append(mat)
        return mat
    if obj.material_slots[0].material is None:
        mat = bpy.data.materials.new(name=obj.name + "_Mat")
        obj.material_slots[0].material = mat
        return mat
    return obj.material_slots[0].material


def _ensure_nodes(mat):
    if not mat.use_nodes:
        mat.use_nodes = True
    nt = mat.node_tree

    bsdf = None
    for n in nt.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            bsdf = n
            break

    output = None
    for n in nt.nodes:
        if n.type == 'OUTPUT_MATERIAL':
            output = n
            break

    if bsdf is None:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (0, 0)

    if output is None:
        output = nt.nodes.new("ShaderNodeOutputMaterial")
        output.location = (300, 0)

    if not output.inputs[0].is_linked:
        nt.links.new(bsdf.outputs[0], output.inputs[0])

    return bsdf


def _remove_adjust_nodes(mat, layer_id):
    nt = mat.node_tree
    prefix = ADJ_PREFIX + layer_id + "_"
    for n in list(nt.nodes):
        if n.name.startswith(prefix):
            nt.nodes.remove(n)


def _restore_base_color_link(mat, layer_id):
    nt = mat.node_tree
    bsdf = _find_bsdf(mat)
    if not bsdf:
        return
    base_in = _find_base_color_input(bsdf)
    if not base_in:
        return

    orig_node = mat.get("vfx_adj_orig_node", "")
    orig_sock = mat.get("vfx_adj_orig_socket", "")

    if orig_node and orig_sock:
        n = nt.nodes.get(orig_node)
        if n:
            for s in n.outputs:
                if s.identifier == orig_sock or s.name == orig_sock:
                    nt.links.new(s, base_in)
                    return

    if "vfx_adj_orig_color" in mat:
        try:
            base_in.default_value = mat["vfx_adj_orig_color"]
        except Exception:
            pass


def update_layer_material_adjust(layer):
    global _last_adjust_stats
    stats = {"objects": 0, "materials": 0, "applied": 0, "notes": []}

    if not layer.collection:
        _last_adjust_stats = stats
        return

    objs = list(layer.collection.objects)
    if layer.shadow_cast_collection:
        objs += list(layer.shadow_cast_collection.objects)

    seen_mats = set()
    for obj in objs:
        if obj.type not in {'MESH', 'CURVE', 'SURFACE', 'META', 'VOLUME'}:
            continue
        stats["objects"] += 1

        if layer.use_adjust:
            if not obj.material_slots or all(s.material is None for s in obj.material_slots):
                _ensure_material(obj)

        for slot in obj.material_slots:
            mat = slot.material
            if mat is None:
                continue
            if mat.name in seen_mats:
                continue
            seen_mats.add(mat.name)

            if layer.use_adjust:
                if not mat.use_nodes:
                    mat.use_nodes = True
                _ensure_nodes(mat)
                ok = _apply_adjust_to_material(mat, layer)
                stats["materials"] += 1
                if ok:
                    stats["applied"] += 1
            else:
                _remove_adjust_nodes(mat, layer.id)
                _restore_base_color_link(mat, layer.id)
                stats["materials"] += 1

    if stats["objects"] == 0:
        stats["notes"].append("No mesh objects in layer collection")
    elif layer.use_adjust and stats["applied"] == 0:
        stats["notes"].append("No compatible materials found")

    _last_adjust_stats = stats


def _apply_adjust_to_material(mat, layer):
    nt = mat.node_tree
    bsdf = _find_bsdf(mat)
    if not bsdf:
        return False

    base_in = _find_base_color_input(bsdf)
    if not base_in:
        return False

    prefix = ADJ_PREFIX + layer.id + "_"
    hs_name = prefix + "HS"
    bc_name = prefix + "BC"
    tm_name = prefix + "TINT"

    hs = nt.nodes.get(hs_name)
    bc = nt.nodes.get(bc_name)
    tm = nt.nodes.get(tm_name)

    created = hs is None

    if created:
        orig_sock = None
        if base_in.is_linked:
            orig_sock = base_in.links[0].from_socket
            mat["vfx_adj_orig_node"] = orig_sock.node.name
            mat["vfx_adj_orig_socket"] = orig_sock.identifier
        else:
            mat["vfx_adj_orig_node"] = ""
            mat["vfx_adj_orig_socket"] = ""
            try:
                mat["vfx_adj_orig_color"] = list(base_in.default_value)
            except Exception:
                pass

        hs = nt.nodes.new("ShaderNodeHueSaturation")
        hs.name = hs_name
        hs.label = "VFX HS"
        hs.location = (bsdf.location.x - 620, bsdf.location.y + 200)

        bc = nt.nodes.new("ShaderNodeBrightContrast")
        bc.name = bc_name
        bc.label = "VFX BC"
        bc.location = (bsdf.location.x - 420, bsdf.location.y + 200)

        tm = nt.nodes.new("ShaderNodeMixRGB")
        tm.name = tm_name
        tm.label = "VFX Tint"
        try:
            tm.blend_type = 'MULTIPLY'
        except Exception:
            pass
        tm.location = (bsdf.location.x - 220, bsdf.location.y + 200)

        hs_color_in = None
        for s in hs.inputs:
            if s.type == 'RGBA':
                hs_color_in = s
                break
        bc_color_in = None
        for s in bc.inputs:
            if s.type == 'RGBA':
                bc_color_in = s
                break
        tm_img_ins = [s for s in tm.inputs if s.type == 'RGBA']

        if orig_sock and hs_color_in:
            nt.links.new(orig_sock, hs_color_in)
        elif hs_color_in:
            try:
                hs_color_in.default_value = mat.get(
                    "vfx_adj_orig_color", (0.8, 0.8, 0.8, 1.0)
                )
            except Exception:
                pass

        if hs_color_in and bc_color_in:
            hs_color_out = [s for s in hs.outputs if s.type == 'RGBA']
            if hs_color_out:
                nt.links.new(hs_color_out[0], bc_color_in)

        bc_color_out = [s for s in bc.outputs if s.type == 'RGBA']
        if bc_color_out and len(tm_img_ins) >= 1:
            nt.links.new(bc_color_out[0], tm_img_ins[0])

        tm_color_out = [s for s in tm.outputs if s.type == 'RGBA']
        if tm_color_out:
            nt.links.new(tm_color_out[0], base_in)

    def set_val(node, name, value):
        s = node.inputs.get(name)
        if s is None:
            for si in node.inputs:
                if si.type == 'VALUE' and si.name.lower() == name.lower():
                    s = si
                    break
        if s is not None:
            try:
                s.default_value = value
            except Exception:
                pass

    set_val(hs, "Saturation", layer.saturation)
    set_val(hs, "Value", layer.exposure)
    set_val(bc, "Bright", 0.0)
    set_val(bc, "Contrast", (layer.contrast - 1.0) * 0.5)
    set_val(tm, "Fac", layer.tint_strength)

    tm_c2 = tm.inputs.get("Color2")
    if tm_c2 is None:
        rgba = [s for s in tm.inputs if s.type == 'RGBA']
        if len(rgba) >= 2:
            tm_c2 = rgba[1]
    if tm_c2 is not None:
        try:
            tm_c2.default_value = tuple(layer.tint_color)
        except Exception:
            pass

    return True


# ---------------------------------------------------------------------
# OPERATORS
# ---------------------------------------------------------------------

class VFX_OT_reset_lighting(bpy.types.Operator):
    bl_idname = "vfx.reset_lighting"
    bl_label = "Reset Lighting"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = active_layer(vfx)

        if not layer:
            self.report({'ERROR'}, "No active layer")
            return {'CANCELLED'}

        layer.exposure = 1.0
        layer.contrast = 1.0
        layer.saturation = 1.0
        layer.tint_color = (1.0, 1.0, 1.0, 1.0)
        layer.tint_strength = 0.0

        update_layer_material_adjust(layer)
        self.report({'INFO'}, "Lighting reset")
        return {'FINISHED'}


class VFX_OT_set_master(bpy.types.Operator):
    bl_idname = "vfx.set_master"
    bl_label = "Use Current Scene As Master"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        context.scene.vfx.master_scene = context.scene
        self.report({'INFO'}, f"Master scene set to {context.scene.name}")
        return {'FINISHED'}


class VFX_OT_create_layer(bpy.types.Operator):
    bl_idname = "vfx.create_layer"
    bl_label = "Create Layer From Selected"
    bl_options = {'REGISTER', 'UNDO'}

    layer_name: StringProperty(name="Layer Name", default="")
    include_children: BoolProperty(name="Include Children", default=True)

    def invoke(self, context, event):
        vfx, master = get_project(context)

        if not context.selected_objects:
            self.report({'ERROR'}, "No selected objects")
            return {'CANCELLED'}

        if not self.layer_name:
            self.layer_name = default_layer_name(context) or vfx.new_layer_name

        self.include_children = vfx.include_children

        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)

        name = self.layer_name.strip()
        if not name:
            self.report({'ERROR'}, "Layer name is empty")
            return {'CANCELLED'}

        vfx.new_layer_name = name
        vfx.include_children = self.include_children

        selected = collect_objects(context.selected_objects or [], self.include_children)
        if not selected:
            self.report({'ERROR'}, "No selected objects")
            return {'CANCELLED'}

        root = ensure_root(master)
        cam_col = ensure_camera_collection(master, root)
        light_col = ensure_light_collection(master, root)

        col = bpy.data.collections.new(f"VFX_{name}")
        root.children.link(col)

        layer_id = uid()
        col["vfx_id"] = layer_id
        col["vfx_pass"] = "OBJECT"

        for obj in selected:
            if col.objects.get(obj.name) is None:
                col.objects.link(obj)

        scene = create_empty_scene(f"VFX_{name}", master)
        scene["vfx_id"] = layer_id
        scene["vfx_pass"] = "OBJECT"
        scene.vfx.master_scene = master

        link_collection_to_scene(scene, col)
        link_collection_to_scene(scene, cam_col)
        link_collection_to_scene(scene, light_col)

        if master.camera:
            scene.camera = master.camera

        sync_scene_settings(master, scene)
        try:
            scene.render.engine = vfx.objects_engine
        except Exception:
            pass

        item = vfx.layers.add()
        item.id = layer_id
        item.layer_name = name
        item.collection = col
        item.scene = scene
        item.enabled = True

        vfx.layers.move(len(vfx.layers) - 1, 0)
        vfx.active_layer_index = 0

        rebuild_comp(vfx, master)

        self.report({'INFO'}, f"Created VFX layer: {name}")
        return {'FINISHED'}


class VFX_OT_add_selected_to_layer(bpy.types.Operator):
    bl_idname = "vfx.add_selected_to_layer"
    bl_label = "Add Selected To Active Layer"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = active_layer(vfx)

        if not layer or not layer.collection:
            self.report({'ERROR'}, "Active layer has no collection")
            return {'CANCELLED'}

        selected = collect_objects(context.selected_objects or [], vfx.include_children)
        if not selected:
            self.report({'ERROR'}, "No selected objects")
            return {'CANCELLED'}

        count = 0
        for obj in selected:
            if layer.collection.objects.get(obj.name) is None:
                layer.collection.objects.link(obj)
                count += 1

        self.report({'INFO'}, f"Added {count} object(s) to {layer.layer_name}")
        return {'FINISHED'}


class VFX_OT_remove_selected_from_layer(bpy.types.Operator):
    bl_idname = "vfx.remove_selected_from_layer"
    bl_label = "Remove Selected From Active Layer"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = active_layer(vfx)

        if not layer or not layer.collection:
            self.report({'ERROR'}, "Active layer has no collection")
            return {'CANCELLED'}

        selected = collect_objects(context.selected_objects or [], vfx.include_children)
        if not selected:
            self.report({'ERROR'}, "No selected objects")
            return {'CANCELLED'}

        count = 0
        for obj in selected:
            if layer.collection.objects.get(obj.name) is not None:
                layer.collection.objects.unlink(obj)
                count += 1

        self.report({'INFO'}, f"Removed {count} object(s) from {layer.layer_name}")
        return {'FINISHED'}


class VFX_OT_add_selected_lights(bpy.types.Operator):
    bl_idname = "vfx.add_selected_lights"
    bl_label = "Add Selected Lights"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        root = ensure_root(master)
        col = ensure_light_collection(master, root)

        lights = [o for o in (context.selected_objects or []) if o.type == 'LIGHT']
        if not lights:
            self.report({'ERROR'}, "No selected lights")
            return {'CANCELLED'}

        count = 0
        for obj in lights:
            for c in list(obj.users_collection):
                try:
                    c.objects.unlink(obj)
                except Exception:
                    pass

            if col.objects.get(obj.name) is None:
                col.objects.link(obj)
                count += 1

        link_lights_to_all_scenes(vfx, col)

        self.report({'INFO'}, f"Moved {count} light(s) into VFX_Lights")
        return {'FINISHED'}


class VFX_OT_create_shadow_pass(bpy.types.Operator):
    bl_idname = "vfx.create_shadow_pass"
    bl_label = "Create Shadow Pass"
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(
        name="Mode",
        items=(
            ('CAST', "Cast shadow",
             "Layer objects cast shadow onto a catcher"),
            ('RECEIVE', "Receive shadow",
             "Layer receives shadows from other layers (separate pass)"),
        ),
        default='CAST'
    )

    def invoke(self, context, event):
        vfx, master = get_project(context)
        layer = active_layer(vfx)

        if not layer:
            self.report({'ERROR'}, "No active layer")
            return {'CANCELLED'}

        if layer.shadow_scene:
            self.report({'WARNING'}, "Shadow pass already exists")
            return {'CANCELLED'}

        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = active_layer(vfx)

        if not layer:
            self.report({'ERROR'}, "No active layer")
            return {'CANCELLED'}

        if not layer.collection or len(layer.collection.objects) == 0:
            self.report({'ERROR'}, "Active layer has no objects")
            return {'CANCELLED'}

        if layer.shadow_scene:
            self.report({'WARNING'}, "Shadow pass already exists")
            return {'CANCELLED'}

        root = ensure_root(master)
        cam_col = ensure_camera_collection(master, root)
        light_col = ensure_light_collection(master, root)

        cast_col = bpy.data.collections.new(f"VFX_{layer.layer_name}_SHD_CAST")
        catch_col = bpy.data.collections.new(f"VFX_{layer.layer_name}_SHD_CATCH")

        root.children.link(cast_col)
        root.children.link(catch_col)

        layer.shadow_cast_collection = cast_col
        layer.shadow_catch_collection = catch_col
        layer.shadow_mode = self.mode

        cast_col["vfx_id"] = layer.id
        cast_col["vfx_pass"] = "SHADOW_CAST"
        catch_col["vfx_id"] = layer.id
        catch_col["vfx_pass"] = "SHADOW_CATCH"

        exclude_collection_in_master(master, cast_col)
        exclude_collection_in_master(master, catch_col)

        drawable = {'MESH', 'CURVE', 'VOLUME', 'SURFACE', 'META'}
        made_cast = 0
        made_catch = 0

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

        if self.mode == 'RECEIVE':
            for obj in layer.collection.objects:
                if obj.type not in drawable:
                    continue
                p = make_proxy(obj, "_VFXCatch", catch_col)
                set_shadow_catcher(p, True)
                made_catch += 1

            for other in vfx.layers:
                if other.id == layer.id or not other.enabled or not other.collection:
                    continue
                for obj in other.collection.objects:
                    if obj.type not in drawable:
                        continue
                    p = make_proxy(obj, "_VFXShadowCaster", cast_col)
                    set_only_shadow_caster(p)
                    made_cast += 1
        else:
            catcher = layer.shadow_catcher
            if not catcher:
                catcher = create_default_catcher(layer, master)
                layer.shadow_catcher = catcher

            for obj in layer.collection.objects:
                if catcher and obj == catcher:
                    continue
                if obj.type not in drawable:
                    continue
                p = make_proxy(obj, "_VFXShadowCaster", cast_col)
                set_only_shadow_caster(p)
                made_cast += 1

            if catch_col.objects.get(catcher.name) is None:
                catch_col.objects.link(catcher)
            set_shadow_catcher(catcher, True)
            made_catch += 1

        scene = create_empty_scene(f"VFX_{layer.layer_name}_SHD", master)
        scene["vfx_id"] = layer.id
        scene["vfx_pass"] = "SHADOW"
        scene.vfx.master_scene = master

        link_collection_to_scene(scene, cast_col)
        link_collection_to_scene(scene, catch_col)
        link_collection_to_scene(scene, cam_col)
        link_collection_to_scene(scene, light_col)

        if master.camera:
            scene.camera = master.camera

        sync_scene_settings(master, scene)

        try:
            scene.render.engine = vfx.shadows_engine
        except Exception:
            pass
        try:
            if scene.render.engine == 'CYCLES':
                scene.cycles.samples = 32
        except Exception:
            pass

        layer.shadow_scene = scene
        layer.use_shadow = True

        rebuild_comp(vfx, master)

        self.report({'INFO'}, f"Shadow pass ({self.mode}): {made_cast} casters, {made_catch} catchers")
        return {'FINISHED'}


class VFX_OT_delete_layer(bpy.types.Operator):
    bl_idname = "vfx.delete_layer"
    bl_label = "Delete Active Layer"
    bl_options = {'REGISTER', 'UNDO'}

    confirm: BoolProperty(
        name="Delete layer, its scenes and collections?",
        default=True
    )

    def invoke(self, context, event):
        vfx, master = get_project(context)
        layer = active_layer(vfx)
        if not layer:
            self.report({'WARNING'}, "No active layer")
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = active_layer(vfx)

        if not layer:
            self.report({'ERROR'}, "No active layer")
            return {'CANCELLED'}

        if not self.confirm:
            return {'CANCELLED'}

        remove_comp_node(master, f"VFX_RL_{layer.id}")
        remove_comp_node(master, f"VFX_RL_{layer.id}_SHD")

        if layer.scene and layer.scene != master:
            remove_scene_safe(context, layer.scene, master)

        if layer.shadow_scene and layer.shadow_scene != master:
            remove_scene_safe(context, layer.shadow_scene, master)

        remove_shadow_collections(layer)

        if layer.collection:
            for obj in list(layer.collection.objects):
                if len(obj.users_collection) <= 1:
                    try:
                        master.collection.objects.link(obj)
                    except Exception:
                        pass
            try:
                bpy.data.collections.remove(layer.collection)
            except Exception:
                pass

        idx = vfx.active_layer_index
        vfx.layers.remove(idx)
        vfx.active_layer_index = max(0, min(idx, len(vfx.layers) - 1))

        rebuild_comp(vfx, master)

        self.report({'INFO'}, f"Deleted layer: {layer.layer_name}")
        return {'FINISHED'}


class VFX_OT_toggle_layer_expand(bpy.types.Operator):
    bl_idname = "vfx.toggle_layer_expand"
    bl_label = "Toggle Expand"
    bl_options = set()

    index: IntProperty(default=0)

    def execute(self, context):
        vfx, master = get_project(context)
        if 0 <= self.index < len(vfx.layers):
            vfx.layers[self.index].expanded = not vfx.layers[self.index].expanded
        return {'FINISHED'}


class VFX_OT_drag_layer(bpy.types.Operator):
    bl_idname = "vfx.drag_layer"
    bl_label = "Drag Layer"

    index: IntProperty(default=0)

    def invoke(self, context, event):
        vfx, master = get_project(context)

        if self.index < 0 or self.index >= len(vfx.layers):
            return {'CANCELLED'}

        vfx.active_layer_index = self.index
        self.start_index = self.index
        self.current_index = self.index
        self.start_y = event.mouse_y

        try:
            ui_scale = context.preferences.system.ui_scale
        except Exception:
            ui_scale = 1.0
        self.row_height = max(16, 24 * ui_scale)

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        vfx, master = get_project(context)

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.finish(context)
            return {'FINISHED'}

        if event.type in {'ESC', 'RIGHTMOUSE'}:
            self.finish(context)
            return {'CANCELLED'}

        if event.type == 'MOUSEMOVE':
            dy = event.mouse_y - self.start_y
            steps = int(dy / self.row_height)
            target = self.start_index - steps
            target = max(0, min(len(vfx.layers) - 1, target))

            if target != self.current_index:
                vfx.layers.move(self.current_index, target)
                self.current_index = target
                vfx.active_layer_index = target
                if context.area:
                    context.area.tag_redraw()

        return {'RUNNING_MODAL'}

    def finish(self, context):
        vfx, master = get_project(context, allow_write=True)
        rebuild_comp(vfx, master)
        rebuild_file_output(master, vfx)


class VFX_OT_move_layer_up(bpy.types.Operator):
    bl_idname = "vfx.move_layer_up"
    bl_label = "Move Layer Up (Forward)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        i = vfx.active_layer_index

        if i <= 0 or i >= len(vfx.layers):
            return {'CANCELLED'}

        vfx.layers.move(i, i - 1)
        vfx.active_layer_index = i - 1

        rebuild_comp(vfx, master)
        rebuild_file_output(master, vfx)
        return {'FINISHED'}


class VFX_OT_move_layer_down(bpy.types.Operator):
    bl_idname = "vfx.move_layer_down"
    bl_label = "Move Layer Down (Back)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        i = vfx.active_layer_index

        if i < 0 or i >= len(vfx.layers) - 1:
            return {'CANCELLED'}

        vfx.layers.move(i, i + 1)
        vfx.active_layer_index = i + 1

        rebuild_comp(vfx, master)
        rebuild_file_output(master, vfx)
        return {'FINISHED'}


class VFX_OT_rename_layer(bpy.types.Operator):
    bl_idname = "vfx.rename_layer"
    bl_label = "Rename Layer"
    bl_options = {'REGISTER', 'UNDO'}

    new_name: StringProperty(name="New Name", default="")

    def invoke(self, context, event):
        vfx, master = get_project(context)
        layer = active_layer(vfx)

        if not layer:
            self.report({'ERROR'}, "No active layer")
            return {'CANCELLED'}

        if not self.new_name:
            self.new_name = layer.layer_name

        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = active_layer(vfx)

        if not layer:
            self.report({'ERROR'}, "No active layer")
            return {'CANCELLED'}

        name = self.new_name.strip()
        if not name:
            self.report({'ERROR'}, "Empty name")
            return {'CANCELLED'}

        rename_layer(layer, name)
        rebuild_comp(vfx, master)
        rebuild_file_output(master, vfx)

        self.report({'INFO'}, f"Layer renamed to: {name}")
        return {'FINISHED'}


class VFX_OT_sync_lights(bpy.types.Operator):
    bl_idname = "vfx.sync_lights"
    bl_label = "Sync All Master Lights"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        root = ensure_root(master)
        count = sync_master_lights(master, root)

        light_col = bpy.data.collections.get("VFX_Lights")
        if light_col:
            link_lights_to_all_scenes(vfx, light_col)

        self.report({'INFO'}, f"Synced {count} light(s)")
        return {'FINISHED'}


class VFX_OT_debug_layer(bpy.types.Operator):
    bl_idname = "vfx.debug_layer"
    bl_label = "Debug Active Layer Scene"

    def execute(self, context):
        vfx, master = get_project(context)
        layer = active_layer(vfx)
        if not layer or not layer.scene:
            self.report({'ERROR'}, "No active layer scene")
            return {'CANCELLED'}

        sc = layer.scene
        print("=" * 60)
        print("VFX DEBUG layer:", layer.layer_name)
        print("scene:", sc.name)
        print("engine:", sc.render.engine)
        print("film_transparent:", sc.render.film_transparent)
        print("camera:", sc.camera.name if sc.camera else None)
        print("world:", sc.world.name if sc.world else None)

        for vl in sc.view_layers:
            print("view_layer:", vl.name)

            def walk(lc, depth=0):
                print("   " * (depth + 1), lc.collection.name,
                      "exclude=", lc.exclude,
                      "holdout=", lc.holdout,
                      "indirect_only=", lc.indirect_only)
                for ch in lc.children:
                    walk(ch, depth + 1)

            walk(vl.layer_collection)

        print("objects in scene:")
        for obj in sc.objects:
            print("  ", obj.name, obj.type,
                  "hide_render=", obj.hide_render)
        print("=" * 60)

        self.report({'INFO'}, "Debug info printed to console")
        return {'FINISHED'}


_RENDER_STATE = {"op": None}


def _vfx_render_post(scene, *args):
    op = _RENDER_STATE["op"]
    if op is not None:
        op.frames_done += 1


def _vfx_render_complete(scene, *args):
    op = _RENDER_STATE["op"]
    if op is not None:
        op.job_done = True


def _vfx_render_cancel(scene, *args):
    op = _RENDER_STATE["op"]
    if op is not None:
        op.cancelled = True
        op.job_done = True


class VFX_OT_render_all_layers(bpy.types.Operator):
    bl_idname = "vfx.render_all_layers"
    bl_label = "Render All Layers"

    post_files: BoolProperty(
        name="Then build comp from EXR",
        default=False
    )
    only_layer: IntProperty(
        name="Only layer index",
        default=-1
    )
    only_shadow_for_layer: IntProperty(
        name="Only shadow of layer index",
        default=-1
    )
    only_background: BoolProperty(
        name="Only background",
        default=False
    )
    refresh_after: BoolProperty(
        name="Refresh EXR comp after",
        default=False
    )

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)

        only = getattr(self, "only_layer", -1)
        only_shd = getattr(self, "only_shadow_for_layer", -1)
        only_bg = getattr(self, "only_background", False)

        if vfx.render_running:
            job_alive = False
            try:
                job_alive = bpy.app.is_job_running('RENDER')
            except Exception:
                job_alive = _RENDER_STATE["op"] is not None

            if job_alive:
                self.report({'WARNING'}, "Render already running")
                return {'CANCELLED'}

            print("VFX: stale render flag detected, resetting")
            vfx.render_running = False
            _RENDER_STATE["op"] = None
            for h in (_vfx_render_post, _vfx_render_complete, _vfx_render_cancel):
                try:
                    bpy.app.handlers.render_post.remove(h)
                except Exception:
                    pass
            try:
                bpy.app.handlers.render_complete.remove(_vfx_render_complete)
                bpy.app.handlers.render_cancel.remove(_vfx_render_cancel)
            except Exception:
                pass

        scenes = []
        if only_bg and getattr(vfx, "bg_scene", None) is not None:
            scenes.append(vfx.bg_scene)
        elif only_shd >= 0 and only_shd < len(vfx.layers):
            layer = vfx.layers[only_shd]
            if layer.shadow_scene:
                scenes.append(layer.shadow_scene)
        elif only >= 0 and only < len(vfx.layers):
            layer = vfx.layers[only]
            if layer.scene:
                scenes.append(layer.scene)
        else:
            for layer in vfx.layers:
                if not layer.enabled:
                    continue
                if layer.scene:
                    scenes.append(layer.scene)
                if layer.shadow_scene:
                    scenes.append(layer.shadow_scene)
            bg = getattr(vfx, "bg_scene", None)
            if bg is not None:
                scenes.insert(0, bg)

        if not scenes:
            self.report({'ERROR'}, "No enabled layer scenes")
            return {'CANCELLED'}

        if only >= 0 or only_shd >= 0 or only_bg or getattr(self, "post_files", False):
            mode = 'ANIMATION'
        else:
            mode = getattr(vfx, "render_mode", 'PREVIEW')
        self.mode = mode

        try:
            refresh_shadow_proxies(vfx, master)
        except Exception as e:
            print("VFX proxy refresh error:", e)

        self.steps = []
        total_frames = 0
        for sc in scenes:
            if mode == 'ANIMATION':
                try:
                    sc.frame_start = master.frame_start
                    sc.frame_end = master.frame_end
                except Exception:
                    pass
                try:
                    sc.render.image_settings.file_format = 'OPEN_EXR'
                    sc.render.image_settings.color_mode = 'RGBA'
                    sc.render.image_settings.color_depth = '32'
                    sc.render.filepath = f"{vfx.output_dir}{sc.name}/"
                except Exception:
                    pass
                n = (sc.frame_end - sc.frame_start + 1)
            else:
                n = 1
            total_frames += n
            self.steps.append((sc.name, n))

        self.total_frames = max(1, total_frames)
        self.step_index = 0
        self.frames_done = 0
        self.user_cancel = False
        self.cancelled = False
        self.job_done = False

        vfx.render_running = True
        vfx.render_progress = 0.0
        vfx.render_status = "Starting..."

        _RENDER_STATE["op"] = self
        bpy.app.handlers.render_post.append(_vfx_render_post)
        bpy.app.handlers.render_complete.append(_vfx_render_complete)
        bpy.app.handlers.render_cancel.append(_vfx_render_cancel)

        self._timer = context.window_manager.event_timer_add(0.25, window=context.window)
        context.window_manager.modal_handler_add(self)

        self._start_step(context)
        return {'RUNNING_MODAL'}

    def _start_step(self, context):
        vfx = context.scene.vfx
        name, n = self.steps[self.step_index]
        sc = bpy.data.scenes.get(name)
        if sc is None:
            self.job_done = True
            return

        try:
            sc.render.use_persistent_data = True
        except Exception:
            pass

        self.job_done = False
        self.step_start = time.time()
        vfx.render_progress = min(1.0, self.frames_done / self.total_frames)
        if self.mode == 'ANIMATION':
            vfx.render_status = f"{self.frames_done}/{self.total_frames}  •  {name}  •  frames {sc.frame_start}-{sc.frame_end}"
        else:
            vfx.render_status = f"{self.frames_done}/{self.total_frames}  •  {name}"

        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()

        win = context.window
        if win is None and bpy.context.window_manager.windows:
            win = bpy.context.window_manager.windows[0]
        if self.mode == 'ANIMATION':
            try:
                sc.render.filepath = f"{vfx.output_dir}{sc.name}/"
                print("VFX: rendering", name, "->",
                      bpy.path.abspath(sc.render.filepath))
            except Exception:
                pass

        try:
            with bpy.context.temp_override(window=win):
                if self.mode == 'ANIMATION':
                    bpy.ops.render.render('INVOKE_DEFAULT', animation=True, scene=name)
                else:
                    bpy.ops.render.render('INVOKE_DEFAULT', scene=name)
        except Exception as e:
            print("VFX render start error:", name, e)
            try:
                if self.mode == 'ANIMATION':
                    bpy.ops.render.render(animation=True, scene=name)
                else:
                    bpy.ops.render.render(scene=name)
            except Exception as e2:
                print("VFX render fallback error:", name, e2)
                self.job_done = True

    def modal(self, context, event):
        vfx = context.scene.vfx

        if event.type == 'ESC' and event.value == 'PRESS':
            self.user_cancel = True
            try:
                bpy.ops.render.view_cancel()
            except Exception:
                pass

        if event.type == 'TIMER':
            prog = min(1.0, self.frames_done / self.total_frames)
            if abs(prog - vfx.render_progress) > 0.001:
                vfx.render_progress = prog
                for window in bpy.context.window_manager.windows:
                    for area in window.screen.areas:
                        area.tag_redraw()

            try:
                alive = bpy.app.is_job_running('RENDER')
            except Exception:
                alive = True
            if (not alive) and (not self.job_done) and \
                    (time.time() - getattr(self, "step_start", 0)) > 3.0:
                print("VFX: render job died without event, skipping step")
                self.job_done = True

            if self.job_done:
                self.job_done = False
                self.step_index += 1

                if self.user_cancel or self.cancelled or self.step_index >= len(self.steps):
                    self._finish(context)
                    return {'FINISHED'}

                self._start_step(context)

        return {'PASS_THROUGH'}

    def _finish(self, context):
        vfx = context.scene.vfx
        stopped = self.user_cancel or self.cancelled

        _RENDER_STATE["op"] = None
        try:
            bpy.app.handlers.render_post.remove(_vfx_render_post)
            bpy.app.handlers.render_complete.remove(_vfx_render_complete)
            bpy.app.handlers.render_cancel.remove(_vfx_render_cancel)
        except Exception:
            pass
        try:
            context.window_manager.event_timer_remove(self._timer)
        except Exception:
            pass

        vfx.render_running = False
        if not stopped:
            vfx.render_progress = 1.0
        vfx.render_status = "Stopped (ESC)" if stopped else "Done"

        if not stopped and getattr(self, "post_files", False):
            try:
                master = vfx.master_scene or context.scene
                vfx.comp_mode = 'FILES'
                rebuild_comp_from_files(vfx, master)
                vfx.render_status = "Done + comp from EXR"
            except Exception as e:
                print("VFX post-files error:", e)
        elif not stopped and getattr(self, "refresh_after", False):
            try:
                if vfx.comp_mode == 'FILES':
                    master = vfx.master_scene or context.scene
                    rebuild_comp_from_files(vfx, master)
                    vfx.render_status = "Done + EXR refreshed"
            except Exception as e:
                print("VFX refresh error:", e)

        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()


class VFX_OT_one_click_exr(bpy.types.Operator):
    bl_idname = "vfx.one_click_exr"
    bl_label = "Render EXR + Comp From Files"

    def execute(self, context):
        bpy.ops.vfx.render_all_layers('INVOKE_DEFAULT', post_files=True)
        return {'FINISHED'}


class VFX_OT_create_background(bpy.types.Operator):
    bl_idname = "vfx.create_background"
    bl_label = "Bake Background (world only)"

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)

        if vfx.bg_scene:
            self.report({'WARNING'}, "Background already exists")
            return {'CANCELLED'}

        scene = create_empty_scene("VFX_BG", master)
        scene["vfx_pass"] = "BACKGROUND"
        scene.vfx.master_scene = master

        root = ensure_root(master)
        cam_col = ensure_camera_collection(master, root)
        link_collection_to_scene(scene, cam_col)
        if master.camera:
            scene.camera = master.camera

        sync_scene_settings(master, scene)
        try:
            scene.render.engine = vfx.objects_engine
        except Exception:
            pass
        try:
            scene.render.film_transparent = False
        except Exception:
            pass
        try:
            scene.world = master.world
        except Exception:
            pass

        vfx.bg_scene = scene

        if getattr(vfx, "comp_mode", 'LIVE') == 'FILES':
            rebuild_comp_from_files(vfx, master)
        else:
            rebuild_comp(vfx, master)

        self.report({'INFO'}, "Background scene created: VFX_BG")
        return {'FINISHED'}


class VFX_OT_delete_background(bpy.types.Operator):
    bl_idname = "vfx.delete_background"
    bl_label = "Delete Background"

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        if not vfx.bg_scene:
            return {'CANCELLED'}

        remove_comp_node(master, "VFX_RL_BG")
        sc = vfx.bg_scene
        vfx.bg_scene = None
        if sc and sc != master:
            remove_scene_safe(context, sc, master)

        if getattr(vfx, "comp_mode", 'LIVE') == 'FILES':
            rebuild_comp_from_files(vfx, master)
        else:
            rebuild_comp(vfx, master)

        self.report({'INFO'}, "Background removed")
        return {'FINISHED'}


class VFX_OT_sync_render_settings(bpy.types.Operator):
    bl_idname = "vfx.sync_render_settings"
    bl_label = "Sync Render Settings"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        count = 0
        for layer in vfx.layers:
            for sc in (layer.scene, layer.shadow_scene):
                if sc:
                    sync_scene_settings(master, sc)
                    count += 1
        self.report({'INFO'}, f"Synced render settings for {count} scene(s)")
        return {'FINISHED'}


class VFX_OT_prepare_exr_export(bpy.types.Operator):
    bl_idname = "vfx.prepare_exr_export"
    bl_label = "Prepare EXR Export"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)

        count = 0
        for layer in vfx.layers:
            scenes = []
            if layer.scene:
                scenes.append(layer.scene)
            if layer.shadow_scene:
                scenes.append(layer.shadow_scene)

            for sc in scenes:
                try:
                    sc.render.image_settings.file_format = 'OPEN_EXR'
                    sc.render.image_settings.color_mode = 'RGBA'
                    sc.render.image_settings.color_depth = '32'
                    try:
                        sc.render.image_settings.exr_codec = 'ZIP'
                    except Exception:
                        pass
                    sc.render.filepath = f"{vfx.output_dir}{sc.name}/"
                    count += 1
                except Exception as e:
                    print("VFX exr prep error:", sc.name, e)

        ensure_file_output(master, vfx)
        self.report({'INFO'}, f"Prepared {count} scene(s) for EXR export")
        return {'FINISHED'}


class VFX_OT_shadow_scenes_cycles(bpy.types.Operator):
    bl_idname = "vfx.shadow_scenes_cycles"
    bl_label = "Set Shadow Scenes To Cycles"

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        count = 0
        for layer in vfx.layers:
            if layer.shadow_scene:
                try:
                    layer.shadow_scene.render.engine = 'CYCLES'
                    layer.shadow_scene.cycles.samples = 32
                    count += 1
                except Exception:
                    pass
        self.report({'INFO'}, f"{count} shadow scene(s) set to Cycles")
        return {'FINISHED'}


class VFX_OT_delete_shadow_pass(bpy.types.Operator):
    bl_idname = "vfx.delete_shadow_pass"
    bl_label = "Delete Shadow Pass"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = active_layer(vfx)

        if not layer or not layer.shadow_scene:
            self.report({'WARNING'}, "No shadow pass on active layer")
            return {'CANCELLED'}

        remove_comp_node(master, f"VFX_RL_{layer.id}_SHD")

        if layer.shadow_scene and layer.shadow_scene != master:
            remove_scene_safe(context, layer.shadow_scene, master)

        remove_shadow_collections(layer)

        layer.shadow_scene = None
        layer.use_shadow = False
        layer.shadow_cast_collection = None
        layer.shadow_catch_collection = None

        if getattr(vfx, "comp_mode", 'LIVE') == 'FILES':
            rebuild_comp_from_files(vfx, master)
        else:
            rebuild_comp(vfx, master)

        self.report({'INFO'}, f"Shadow pass removed from {layer.layer_name}")
        return {'FINISHED'}


class VFX_OT_apply_engines(bpy.types.Operator):
    bl_idname = "vfx.apply_engines"
    bl_label = "Apply Settings To All Scenes"

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        count = 0

        for layer in vfx.layers:
            if layer.scene:
                try:
                    sync_scene_settings(master, layer.scene)
                    layer.scene.render.engine = vfx.objects_engine
                    count += 1
                except Exception:
                    pass
            if layer.shadow_scene:
                try:
                    sync_scene_settings(master, layer.shadow_scene)
                    layer.shadow_scene.render.engine = vfx.shadows_engine
                    count += 1
                except Exception:
                    pass

            for c in (layer.shadow_cast_collection, layer.shadow_catch_collection):
                if c:
                    exclude_collection_in_master(master, c)

        self.report({'INFO'}, f"Render settings applied to {count} scene(s)")
        return {'FINISHED'}


def _apply_engines_now(vfx, master):
    count = 0
    for layer in vfx.layers:
        if layer.scene:
            try:
                layer.scene.render.engine = vfx.objects_engine
                count += 1
            except Exception:
                pass
        if layer.shadow_scene:
            try:
                layer.shadow_scene.render.engine = vfx.shadows_engine
                count += 1
            except Exception:
                pass
    return count


class VFX_OT_preset_preview(bpy.types.Operator):
    bl_idname = "vfx.preset_preview"
    bl_label = "Preview (Obj EEVEE / Shd Cycles)"

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        try:
            vfx.objects_engine = 'BLENDER_EEVEE_NEXT'
        except Exception:
            try:
                vfx.objects_engine = 'BLENDER_EEVEE'
            except Exception:
                pass
        try:
            vfx.shadows_engine = 'CYCLES'
        except Exception:
            pass
        n = _apply_engines_now(vfx, master)
        self.report({'INFO'}, f"Preview preset applied ({n} scenes)")
        return {'FINISHED'}


class VFX_OT_preset_final(bpy.types.Operator):
    bl_idname = "vfx.preset_final"
    bl_label = "Final (Cycles / Cycles)"

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        try:
            vfx.objects_engine = 'CYCLES'
        except Exception:
            pass
        try:
            vfx.shadows_engine = 'CYCLES'
        except Exception:
            pass
        n = _apply_engines_now(vfx, master)
        self.report({'INFO'}, f"Final preset applied ({n} scenes)")
        return {'FINISHED'}


class VFX_OT_refresh_proxies(bpy.types.Operator):
    bl_idname = "vfx.refresh_proxies"
    bl_label = "Refresh Shadow Proxies"

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        refresh_shadow_proxies(vfx, master)
        self.report({'INFO'}, "Shadow proxies refreshed")
        return {'FINISHED'}


class VFX_OT_rebuild_comp(bpy.types.Operator):
    bl_idname = "vfx.rebuild_comp"
    bl_label = "Rebuild Comp Nodes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        if getattr(vfx, "comp_mode", 'LIVE') == 'FILES':
            rebuild_comp_from_files(vfx, master)
            self.report({'INFO'}, "Comp rebuilt from EXR files")
        else:
            rebuild_comp(vfx, master)
            self.report({'INFO'}, "VFX compositing nodes rebuilt")
        return {'FINISHED'}


class VFX_OT_switch_comp_mode(bpy.types.Operator):
    bl_idname = "vfx.switch_comp_mode"
    bl_label = "Apply Comp Mode"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        mode = vfx.comp_mode
        if mode == 'FILES':
            rebuild_comp_from_files(vfx, master)
            self.report({'INFO'}, "Comp now reads EXR from " + vfx.output_dir)
        else:
            rebuild_comp(vfx, master)
            self.report({'INFO'}, "Comp uses live Render Layers")
        return {'FINISHED'}


class VFX_OT_setup_file_output(bpy.types.Operator):
    bl_idname = "vfx.setup_file_output"
    bl_label = "Setup File Output"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        ensure_file_output(master, vfx)
        self.report({'INFO'}, "VFX File Output node updated")
        return {'FINISHED'}


# ---------------------------------------------------------------------
# PANEL
# ---------------------------------------------------------------------

class VFX_PT_main(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "VFX"
    bl_label = "VFX Layers"

    def draw(self, context):
        layout = self.layout

        if not hasattr(context.scene, "vfx"):
            layout.label(text="VFX props not registered!", icon='ERROR')
            layout.label(text="Remove old addon, restart Blender")
            return

        try:
            self.draw_main(context, layout)
        except Exception as e:
            layout.label(text="Panel draw error:", icon='ERROR')
            layout.label(text=str(e))

    def draw_main(self, context, layout):
        vfx, master = get_project(context, allow_write=False)

        layout.label(text=f"VFX Layer Tools v{VFX_VERSION}", icon='NODETREE')
        layout.separator()
        layout.prop(vfx, "master_scene", text="Master")

        if vfx.master_scene is None:
            layout.label(text="Master scene not set:", icon='INFO')
            layout.operator("vfx.set_master")

        layout.operator_context = 'INVOKE_DEFAULT'
        layout.operator("vfx.create_layer", icon='ADD')

        layout.operator("vfx.add_selected_lights", text="Add Selected Lights", icon='LIGHT')

        layout.template_list(
            "VFX_UL_layers",
            "",
            vfx,
            "layers",
            vfx,
            "active_layer_index"
        )

        # закрепленная строка фона снизу списка
        if vfx.bg_scene is not None:
            bgbox = layout.box()
            r = bgbox.row(align=True)
            r.label(text="", icon='WORLD')
            r.label(text="BACKGROUND (bottom)")
            rbg = r.operator(
                "vfx.render_all_layers",
                text="",
                icon='RENDER_STILL',
                emboss=False
            )
            rbg.only_background = True
            rbg.refresh_after = True
            r.operator("vfx.delete_background", text="", icon='X')

        row = layout.row(align=True)
        row.operator("vfx.move_layer_up", icon='TRIA_UP', text="Up / Forward")
        row.operator("vfx.move_layer_down", icon='TRIA_DOWN', text="Down / Back")

        layer = active_layer(vfx)

        if layer:
            box = layout.box()
            box.label(text=layer.layer_name, icon='SCENE_DATA')

            row = box.row(align=True)
            row.operator("vfx.add_selected_to_layer", text="Add Sel")
            row.operator("vfx.remove_selected_from_layer", text="Remove Sel")

            row = box.row(align=True)
            row.operator("vfx.rename_layer", text="Rename Layer")
            row.operator("vfx.delete_layer", text="Delete", icon='X')

            box.prop(layer, "shadow_catcher", text="Catcher")

            row = box.row(align=True)
            if not layer.shadow_scene:
                row.operator("vfx.create_shadow_pass", icon='LIGHT')
            else:
                row.label(text=f"Shadow pass: {layer.shadow_mode}", icon='CHECKMARK')
                row.operator("vfx.refresh_proxies", text="", icon='FILE_REFRESH')
                row.operator("vfx.delete_shadow_pass", text="", icon='X')
                box.prop(layer, "shadow_strength")

            box.separator()
            box.prop(layer, "use_adjust")
            if layer.use_adjust:
                st = _last_adjust_stats
                if st["notes"]:
                    for n in st["notes"]:
                        box.label(text=n, icon='ERROR')
                else:
                    box.label(text=f"{st['applied']}/{st['materials']} materials adjusted", icon='CHECKMARK')
                adj = box.column(align=True)
                adj.prop(layer, "exposure")
                adj.prop(layer, "contrast")
                adj.prop(layer, "saturation")
                adj.prop(layer, "tint_strength")
                if layer.tint_strength > 0.0:
                    adj.prop(layer, "tint_color", text="")
                adj.operator("vfx.reset_lighting", icon='LOOP_BACK')

            if vfx.use_pass_mist if hasattr(vfx, "use_pass_mist") else False:
                box.separator()
                box.prop(layer, "fog_strength")
                if layer.fog_strength > 0.0:
                    box.prop(layer, "fog_color", text="")

        layout.separator()
        layout.prop(vfx, "output_dir", text="Output")

        layout.operator(
            "vfx.one_click_exr",
            text="1-Click: Render EXR + Comp",
            icon='FILE_IMAGE'
        )

        layout.prop(vfx, "render_mode", expand=True)
        layout.operator("vfx.render_all_layers", icon='RENDER_ANIMATION')
        if vfx.render_mode == 'PREVIEW':
            layout.label(text="Preview не пишет EXR. Для файлов: Animation", icon='INFO')

        if vfx.render_running:
            bar = layout.column(align=True)
            bar.scale_y = 2.5
            bar.prop(vfx, "render_progress", slider=True, text="")
            layout.label(text=vfx.render_status, icon='RENDER_ANIMATION')
            layout.label(text="ESC - stop render", icon='INFO')

        layout.separator(factor=1.5)
        layout.label(text="VFX Comp Pipeline", icon='NODETREE')
        layout.prop(vfx, "comp_mode", expand=True)

        if vfx.bg_scene is None:
            layout.operator("vfx.create_background", icon='WORLD')

        row = layout.row(align=True)
        row.operator("vfx.switch_comp_mode", icon='NODETREE')
        row.operator("vfx.rebuild_comp", text="Refresh", icon='FILE_REFRESH')

        if vfx.comp_mode == 'FILES':
            info_box = layout.box()
            info_box.scale_y = 0.9
            info_box.label(text="Reading EXR from:", icon='IMAGE_DATA')
            info_box.label(text=vfx.output_dir)
            info_box.label(text="1) Render All (Animation)  2) Apply Comp Mode")

        layout.separator(factor=1.5)

        row = layout.row(align=True)
        row.prop(vfx, "objects_engine", text="Obj")
        row.prop(vfx, "shadows_engine", text="Shd")
        layout.prop(vfx, "sync_world", text="World as master")
        layout.operator("vfx.apply_engines", icon='SHADING_RENDERED')


# ---------------------------------------------------------------------
# PANEL (COMPOSITOR)
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
            layout.label(text="VFX props not registered!", icon='ERROR')
            return

        try:
            VFX_PT_main.draw_main(self, context, layout)
        except Exception as e:
            layout.label(text="Panel draw error:", icon='ERROR')
            layout.label(text=str(e))


# ---------------------------------------------------------------------
# REGISTER
# ---------------------------------------------------------------------

classes = (
    VFXLayer,
    VFXProject,
    VFX_UL_layers,
    VFX_OT_set_master,
    VFX_OT_create_layer,
    VFX_OT_add_selected_to_layer,
    VFX_OT_remove_selected_from_layer,
    VFX_OT_add_selected_lights,
    VFX_OT_create_shadow_pass,
    VFX_OT_delete_layer,
    VFX_OT_rename_layer,
    VFX_OT_reset_lighting,
    VFX_OT_toggle_layer_expand,
    VFX_OT_drag_layer,
    VFX_OT_move_layer_up,
    VFX_OT_move_layer_down,
    VFX_OT_sync_lights,
    VFX_OT_debug_layer,
    VFX_OT_rebuild_comp,
    VFX_OT_switch_comp_mode,
    VFX_OT_setup_file_output,
    VFX_OT_render_all_layers,
    VFX_OT_one_click_exr,
    VFX_OT_create_background,
    VFX_OT_delete_background,
    VFX_OT_sync_render_settings,
    VFX_OT_prepare_exr_export,
    VFX_OT_shadow_scenes_cycles,
    VFX_OT_delete_shadow_pass,
    VFX_OT_apply_engines,
    VFX_OT_preset_preview,
    VFX_OT_preset_final,
    VFX_OT_refresh_proxies,
    VFX_PT_main,
    VFX_PT_compositor,
)

CLASS_NAMES = tuple(cls.__name__ for cls in classes)


def unregister():
    if hasattr(bpy.types.Scene, "vfx"):
        try:
            del bpy.types.Scene.vfx
        except Exception:
            pass

    for name in CLASS_NAMES:
        old = getattr(bpy.types, name, None)
        if old is not None:
            try:
                bpy.utils.unregister_class(old)
            except Exception:
                pass


def register():
    unregister()

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.vfx = PointerProperty(type=VFXProject)


if __name__ == "__main__":
    register()
    try:
        repair_shadow_proxies()
        print("VFX: shadow proxies repaired")
    except Exception as e:
        print("VFX repair error:", e)