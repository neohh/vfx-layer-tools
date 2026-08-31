# VFX_LAYER_TOOLS_VERSION = "1.77"

bl_info = {
    "name": "VFX Layer Tools",
    "author": "VFX Pipeline",
    "version": (1, 77, 0),
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


VFX_VERSION = "1.77"


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
        return None

    new_imgs = [bpy.data.images[k]
                for k in (set(bpy.data.images.keys()) - before)]
    if not new_imgs:
        print("VFX SEQ2: no new image")
        return None

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
        if node.type == 'R_LAYERS' and node.get("vfx_id") \
                and node.name != "VFX_RL_FOGMAP":
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

    # единая live-карта глубины на всю сцену
    if getattr(vfx, "use_fog", False) or getattr(vfx, "use_blur", False) \
            or getattr(vfx, "use_dof", False):
        _setup_fog_passes(vfx, master, force=True)
        fm = getattr(vfx, "fog_map_scene", None)
        if fm is not None:
            mn = "VFX_RL_FOGMAP"
            valid_names.add(mn)
            node = nt.nodes.get(mn)
            if node is not None and node.type != 'R_LAYERS':
                nt.nodes.remove(node)
                node = None
            if node is None:
                node = nt.nodes.new("CompositorNodeRLayers")
                node.name = mn
            node.scene = fm
            try:
                if fm.view_layers:
                    node.layer = fm.view_layers[0].name
            except Exception:
                pass
            node.label = "FOG MAP (live)"
            node["vfx_id"] = "FOGMAP"
            node["vfx_pass"] = "MIST"
            node.location = (-500, 600)

    for node in list(nt.nodes):
        if node.type == 'IMAGE' and node.name.startswith("VFX_RL_"):
            if node.name not in valid_names:
                nt.nodes.remove(node)

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
    if getattr(vfx, "comp_mode", 'LIVE') == 'FILES':
        rebuild_comp_from_files(vfx, master)
        return

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

    if getattr(vfx, "use_fog", False) or getattr(vfx, "use_blur", False) \
            or getattr(vfx, "use_dof", False):
        _setup_fog_passes(vfx, master, force=True)
        fm = getattr(vfx, "fog_map_scene", None)
        if fm is not None:
            valid_nodes.add("VFX_RL_FOGMAP")
            ensure_render_node(
                master, fm, "VFX_RL_FOGMAP", "FOG MAP (live)",
                "FOGMAP", "MIST", x=-500, y=600
            )

    for node in list(nt.nodes):
        if node.type == 'IMAGE' and node.get("vfx_id"):
            nt.nodes.remove(node)

    for node in list(nt.nodes):
        if node.type == 'R_LAYERS' and node.get("vfx_id") \
                and node.name not in valid_nodes:
            nt.nodes.remove(node)

    build_comp_assembly(vfx, master)


def _new_node(nt, *ids):
    for i in ids:
        try:
            return nt.nodes.new(i)
        except Exception:
            continue
    print("VFX: no valid node type among", ids)
    return None


def _get_mist_socket(nt):
    n = nt.nodes.get("VFX_RL_FOGMAP")
    if n is not None:
        if n.outputs.get("Mist"):
            return n.outputs["Mist"]
        if n.outputs.get("Image"):
            return n.outputs["Image"]
    return None


def _cleanup_fog_nodes(nt):
    for node in list(nt.nodes):
        n = node.name
        if n == "VFX_FOG_GROUP":
            continue
        if n.startswith("VFX_FOG") or node.get("vfx_fog"):
            nt.nodes.remove(node)


def _ensure_fogmap(nt, vfx, master):
    _setup_fog_passes(vfx, master, force=True)
    fm = getattr(vfx, "fog_map_scene", None)
    if fm is not None:
        ensure_render_node(
            master, fm, "VFX_RL_FOGMAP", "FOG MAP (live)",
            "FOGMAP", "MIST", x=-500, y=600
        )
    return nt.nodes.get("VFX_RL_FOGMAP")


def _fog_mix_node(ng, loc):
    mix = _new_node(ng, "CompositorNodeMixRGB", "ShaderNodeMix")
    if mix is None:
        return None, None, None, None, None
    mix.location = loc
    if mix.bl_idname == 'ShaderNodeMix':
        try:
            mix.data_type = 'RGBA'
        except Exception:
            pass
        fac_in = a_in = b_in = out_s = None
        for s in mix.inputs:
            if fac_in is None and s.type == 'VALUE' and s.name == 'Factor':
                fac_in = s
            if s.type == 'RGBA' and s.name == 'A':
                a_in = s
            if s.type == 'RGBA' and s.name == 'B':
                b_in = s
        for s in mix.outputs:
            if s.type == 'RGBA':
                out_s = s
                break
        return mix, fac_in, a_in, b_in, out_s
    return (mix, mix.inputs.get("Fac"), mix.inputs.get("Color1"),
            mix.inputs.get("Color2"),
            mix.outputs[0] if len(mix.outputs) else None)


def _build_fog_group2(vfx, has_bg=True):
    """Вся сборка сцены внутри группы; туман каждого слоя применяется
    ДО альфа-композитинга - поэтому нет каймы на краях."""
    ng = bpy.data.node_groups.get("VFX_FogGroup")
    if ng is None:
        ng = bpy.data.node_groups.new("VFX_FogGroup", 'CompositorNodeTree')
    ng.nodes.clear()
    try:
        ng.interface.items_clear()
    except Exception:
        try:
            ng.interface.clear()
        except Exception:
            pass

    ng.interface.new_socket("Mist", in_out='INPUT', socket_type='NodeSocketColor')
    ng.interface.new_socket("Strength", in_out='INPUT', socket_type='NodeSocketFloat')
    ng.interface.new_socket("Fog Color", in_out='INPUT', socket_type='NodeSocketColor')
    ng.interface.new_socket("Ramp Black", in_out='INPUT', socket_type='NodeSocketFloat')
    ng.interface.new_socket("Ramp White", in_out='INPUT', socket_type='NodeSocketFloat')
    if has_bg:
        ng.interface.new_socket("BG Image", in_out='INPUT', socket_type='NodeSocketColor')
        ng.interface.new_socket("F_BG", in_out='INPUT', socket_type='NodeSocketFloat')

    meta = []
    for layer in reversed(vfx.layers):
        if not (layer.enabled and layer.scene):
            continue
        meta.append({"id": layer.id, "layer": layer,
                     "shd": bool(layer.shadow_scene)})
        ng.interface.new_socket(f"OBJ_{layer.id}", in_out='INPUT',
                                socket_type='NodeSocketColor')
        ng.interface.new_socket(f"AL_{layer.id}", in_out='INPUT',
                                socket_type='NodeSocketFloat')
        ng.interface.new_socket(f"F_{layer.id}", in_out='INPUT',
                                socket_type='NodeSocketFloat')
        if layer.shadow_scene:
            ng.interface.new_socket(f"SHD_{layer.id}", in_out='INPUT',
                                    socket_type='NodeSocketColor')
            ng.interface.new_socket(f"SS_{layer.id}", in_out='INPUT',
                                    socket_type='NodeSocketFloat')

    ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    ng.interface.new_socket("Mask", in_out='OUTPUT', socket_type='NodeSocketFloat')

    gin = ng.nodes.new("NodeGroupInput")
    gin.location = (-1100, 0)
    gout = ng.nodes.new("NodeGroupOutput")
    gout.location = (900, 0)

    def g_in(name):
        return gin.outputs.get(name)

    def math_node(op, loc):
        m = _new_node(ng, "CompositorNodeMath", "ShaderNodeMath")
        if m is not None:
            m.operation = op
            m.location = loc
        return m

    mr = _new_node(ng, "ShaderNodeMapRange", "CompositorNodeMapRange")
    if mr is None:
        return ng, meta
    mr.location = (-800, 300)
    try:
        mr.interpolation_type = 'SMOOTHSTEP'
    except Exception:
        pass
    ng.links.new(g_in("Mist"), mr.inputs.get("Value"))
    ng.links.new(g_in("Ramp Black"), mr.inputs.get("From Min"))
    ng.links.new(g_in("Ramp White"), mr.inputs.get("From Max"))
    mstr = math_node('MULTIPLY', (-550, 300))
    if mstr is None:
        return ng, meta
    ng.links.new(mr.outputs.get("Result"), mstr.inputs[0])
    ng.links.new(g_in("Strength"), mstr.inputs[1])
    mask_out = mstr.outputs[0]

    sep = _new_node(ng, "ShaderNodeSeparateColor", "CompositorNodeSeparateColor")
    if sep is not None:
        sep.location = (-800, 0)

    def fogged(img_sock, f_sock, alpha_sock, y):
        fm = math_node('MULTIPLY', (-550, y))
        if fm is None:
            return img_sock
        ng.links.new(mask_out, fm.inputs[0])
        ng.links.new(f_sock, fm.inputs[1])
        mix, fac_in, a_in, b_in, out_s = _fog_mix_node(ng, (-150, y))
        if mix is None or fac_in is None or out_s is None:
            return img_sock
        comb = _new_node(ng, "CompositorNodeCombineColor", "ShaderNodeCombineColor")
        if comb is not None and sep is not None:
            comb.location = (-350, y - 150)
            ng.links.new(g_in("Fog Color"), sep.inputs[0])
            ng.links.new(sep.outputs[0], comb.inputs[0])
            ng.links.new(sep.outputs[1], comb.inputs[1])
            ng.links.new(sep.outputs[2], comb.inputs[2])
            if alpha_sock is not None:
                ng.links.new(alpha_sock, comb.inputs[3])
            else:
                comb.inputs[3].default_value = 1.0
            ng.links.new(comb.outputs[0], b_in)
        else:
            ng.links.new(g_in("Fog Color"), b_in)
        ng.links.new(fm.outputs[0], fac_in)
        ng.links.new(img_sock, a_in)
        return out_s

    def alpha_over(bg_sock, fg_sock, fac_sock=None, fac_value=None, y=0):
        ao = _new_node(ng, "CompositorNodeAlphaOver")
        if ao is None:
            return bg_sock
        ao.location = (400, y)
        img = [s for s in ao.inputs if s.type == 'RGBA']
        if len(img) >= 2:
            ng.links.new(bg_sock, img[0])
            ng.links.new(fg_sock, img[1])
        vs = [s for s in ao.inputs if s.type == 'VALUE']
        if vs:
            if fac_sock is not None:
                ng.links.new(fac_sock, vs[0])
            elif fac_value is not None:
                vs[0].default_value = fac_value
        outs = [s for s in ao.outputs if s.type == 'RGBA']
        return outs[0] if outs else bg_sock

    y = 700
    cur = None
    if has_bg:
        cur = fogged(g_in("BG Image"), g_in("F_BG"), None, y)
        y -= 250

    for entry in meta:
        lid = entry["id"]
        fog_obj = fogged(g_in(f"OBJ_{lid}"), g_in(f"F_{lid}"),
                         g_in(f"AL_{lid}"), y)
        y -= 250
        if cur is None:
            cur = fog_obj
        else:
            cur = alpha_over(cur, fog_obj, y=y)
        if entry["shd"]:
            cur = alpha_over(cur, g_in(f"SHD_{lid}"),
                             fac_sock=g_in(f"SS_{lid}"), y=y)
        y -= 250

    if cur is None:
        return ng, meta

    oi = gout.inputs.get("Image")
    if oi is not None:
        ng.links.new(cur, oi)
    mk = gout.inputs.get("Mask")
    if mk is not None:
        ng.links.new(mask_out, mk)

    return ng, meta


def build_comp_assembly(vfx, master, nt=None):
    if nt is None:
        nt = get_comp_tree(master)
    if not nt:
        return

    for node in list(nt.nodes):
        if node.get("vfx_mix"):
            nt.nodes.remove(node)

    _cleanup_fog_nodes(nt)

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

    view_sock = None
    fog_done = False

    # туман: вся сборка (фог слоев ДО альфа-оверов) внутри группы
    if getattr(vfx, "use_fog", False):
        try:
            _ensure_fogmap(nt, vfx, master)
            mist = _get_mist_socket(nt)
            if mist is not None:
                ng, meta = _build_fog_group2(vfx, has_bg=bg_sock is not None)
                gnode = nt.nodes.get("VFX_FOG_GROUP")
                if gnode is None:
                    gnode = None
                    for bid in ("CompositorNodeGroup",
                                "ShaderNodeGroup",
                                "NodeGroup"):
                        try:
                            gnode = nt.nodes.new(bid)
                            break
                        except Exception:
                            continue
                    if gnode is None:
                        raise RuntimeError("no group node id")
                    gnode.name = "VFX_FOG_GROUP"
                    gnode.label = "FOG"
                    gnode.location = (700, 500)
                gnode.node_tree = ng

                def relink(sock, out):
                    for l in list(sock.links):
                        nt.links.remove(l)
                    nt.links.new(out, sock)

                gi = lambda n: gnode.inputs.get(n)

                sm = gi("Mist")
                if sm is not None:
                    relink(sm, mist)

                for name, val in (("Strength", vfx.fog_strength),
                                  ("Ramp Black", vfx.ramp_black),
                                  ("Ramp White", vfx.ramp_white),
                                  ("F_BG", vfx.bg_fog_factor)):
                    s = gi(name)
                    if s is not None:
                        s.default_value = val
                scol = gi("Fog Color")
                if scol is not None:
                    try:
                        r, g, b, a = vfx.fog_color
                        scol.default_value = (r, g, b, 1.0)
                    except Exception:
                        pass
                sbg = gi("BG Image")
                if sbg is not None and bgn is not None \
                        and bgn.outputs.get("Image"):
                    relink(sbg, bgn.outputs["Image"])

                for entry in meta:
                    lid = entry["id"]
                    lay = entry["layer"]
                    ln = nt.nodes.get(f"VFX_RL_{lid}")
                    if ln is None:
                        continue
                    s = gi(f"OBJ_{lid}")
                    if s is not None and ln.outputs.get("Image"):
                        relink(s, ln.outputs["Image"])
                    s = gi(f"AL_{lid}")
                    if s is not None and ln.outputs.get("Alpha"):
                        relink(s, ln.outputs["Alpha"])
                    s = gi(f"F_{lid}")
                    if s is not None:
                        s.default_value = lay.fog_factor
                    if entry["shd"]:
                        shn = nt.nodes.get(f"VFX_RL_{lid}_SHD")
                        s = gi(f"SHD_{lid}")
                        if s is not None and shn is not None \
                                and shn.outputs.get("Image"):
                            relink(s, shn.outputs["Image"])
                        s = gi(f"SS_{lid}")
                        if s is not None:
                            s.default_value = lay.shadow_strength

                oi = gnode.outputs.get("Image")
                if oi is not None:
                    current = oi
                    fog_done = True
                if getattr(vfx, "fog_preview", False):
                    om = gnode.outputs.get("Mask")
                    if om is not None:
                        view_sock = om
        except Exception as e:
            import traceback
            print("VFX fog apply error:", e)
            traceback.print_exc()

    # без тумана (или если группа не собралась): старая цепочка миксов
    if not fog_done:
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

    # атмосферный blur по mist со своей рампой
    if getattr(vfx, "use_blur", False):
        try:
            fmn = _ensure_fogmap(nt, vfx, master)
            mist_b = _get_mist_socket(nt)
            if mist_b is not None:
                mr = nt.nodes.get("VFX_BLURRAMP")
                if mr is None:
                    mr = _new_node(nt, "ShaderNodeMapRange",
                                   "CompositorNodeMapRange")
                    if mr is not None:
                        mr.name = "VFX_BLURRAMP"
                        mr.label = "BLUR RAMP"
                        mr.location = (300, -700)
                mask_src = mist_b
                if mr is not None:
                    try:
                        mr.interpolation_type = 'SMOOTHSTEP'
                    except Exception:
                        pass
                    v_in = mr.inputs.get("Value")
                    if v_in is not None:
                        for l in list(v_in.links):
                            nt.links.remove(l)
                        nt.links.new(mist_b, v_in)
                    fmin = mr.inputs.get("From Min")
                    fmax = mr.inputs.get("From Max")
                    if fmin is not None:
                        fmin.default_value = vfx.blur_ramp_black
                    if fmax is not None:
                        fmax.default_value = vfx.blur_ramp_white
                    res = mr.outputs.get("Result")
                    if res is not None:
                        mask_src = res

                bm = nt.nodes.get("VFX_BLURMATH")
                if bm is None:
                    bm = _new_node(nt, "CompositorNodeMath", "ShaderNodeMath")
                    if bm is not None:
                        bm.name = "VFX_BLURMATH"
                        bm.location = (500, -700)
                if bm is not None:
                    try:
                        bm.operation = 'MULTIPLY'
                    except Exception:
                        pass
                    bm.inputs[1].default_value = vfx.blur_size
                    for l in list(bm.inputs[0].links):
                        nt.links.remove(l)
                    nt.links.new(mask_src, bm.inputs[0])

                    bl = nt.nodes.get("VFX_BLUR")
                    if bl is None:
                        try:
                            bl = nt.nodes.new("CompositorNodeBlur")
                            bl.name = "VFX_BLUR"
                            bl.label = "ATMO BLUR"
                            bl.location = (700, -700)
                        except Exception:
                            bl = None
                    if bl is not None:
                        try:
                            bl.use_variable_size = True
                        except Exception:
                            pass
                        for attr in ("blur_method", "filter_type"):
                            try:
                                setattr(bl, attr, 'GAUSS')
                            except Exception:
                                pass
                        img_in = bl.inputs.get("Image")
                        if img_in is None and bl.inputs:
                            img_in = bl.inputs[0]
                        if img_in is not None:
                            for l in list(img_in.links):
                                nt.links.remove(l)
                            nt.links.new(current, img_in)
                        size_in = bl.inputs.get("Size")
                        if size_in is not None:
                            for l in list(size_in.links):
                                nt.links.remove(l)
                            nt.links.new(bm.outputs[0], size_in)
                        if bl.outputs:
                            current = bl.outputs[0]
        except Exception as e:
            print("VFX blur error:", e)

    # камерный DOF по Z (Defocus)
    if getattr(vfx, "use_dof", False):
        try:
            fmn = _ensure_fogmap(nt, vfx, master)
            if fmn is not None and fmn.outputs.get("Depth"):
                df = nt.nodes.get("VFX_DOF")
                if df is None:
                    try:
                        df = nt.nodes.new("CompositorNodeDefocus")
                        df.name = "VFX_DOF"
                        df.label = "CAMERA DOF"
                        df.location = (700, -900)
                    except Exception:
                        df = None
                if df is not None:
                    for attr, val in (("fstop", vfx.dof_fstop),
                                      ("f_stop", vfx.dof_fstop)):
                        try:
                            setattr(df, attr, val)
                        except Exception:
                            pass
                    for attr, val in (("focal_distance", vfx.dof_focus),
                                      ("focus_distance", vfx.dof_focus),
                                      ("distance", vfx.dof_focus)):
                        try:
                            setattr(df, attr, val)
                        except Exception:
                            pass
                    for attr, val in (("blur_max", vfx.dof_maxblur),
                                      ("max_blur", vfx.dof_maxblur)):
                        try:
                            setattr(df, attr, val)
                        except Exception:
                            pass
                    img_in = df.inputs.get("Image")
                    z_in = df.inputs.get("Z")
                    if img_in is not None:
                        for l in list(img_in.links):
                            nt.links.remove(l)
                        nt.links.new(current, img_in)
                    if z_in is not None:
                        for l in list(z_in.links):
                            nt.links.remove(l)
                        nt.links.new(fmn.outputs["Depth"], z_in)
                    if df.outputs:
                        current = df.outputs[0]
        except Exception as e:
            print("VFX dof error:", e)

    # блики/блум
    if getattr(vfx, "use_glare", False):
        gl = nt.nodes.get("VFX_GLARE")
        if gl is None:
            try:
                gl = nt.nodes.new("CompositorNodeGlare")
                gl.name = "VFX_GLARE"
                gl.label = "GLARE"
                gl.location = (700, -500)
            except Exception:
                gl = None
        if gl is not None:
            for attr, val in (("glare_type", vfx.glare_type),
                              ("mode", vfx.glare_type)):
                try:
                    setattr(gl, attr, val)
                except Exception:
                    pass
            for attr, val in (("strength", vfx.glare_strength),
                              ("threshold", vfx.glare_threshold),
                              ("size", vfx.glare_size),
                              ("mix", 0.0)):
                try:
                    setattr(gl, attr, val)
                except Exception:
                    pass
            if gl.inputs:
                gin_s = gl.inputs[0]
                for l in list(gin_s.links):
                    nt.links.remove(l)
                nt.links.new(current, gin_s)
            if gl.outputs:
                current = gl.outputs[0]

    # дисторсия объектива
    if getattr(vfx, "use_lensdist", False):
        ld = nt.nodes.get("VFX_LENSDIST")
        if ld is None:
            try:
                ld = nt.nodes.new("CompositorNodeLensdist")
                ld.name = "VFX_LENSDIST"
                ld.label = "LENS DIST"
                ld.location = (900, -700)
            except Exception:
                ld = None
        if ld is not None:
            for attr, val in (("distort", vfx.lensdist_distort),
                              ("dispersion", vfx.lensdist_disperse)):
                try:
                    setattr(ld, attr, val)
                except Exception:
                    pass
            img_in = ld.inputs.get("Image")
            if img_in is None and ld.inputs:
                img_in = ld.inputs[0]
            if img_in is not None:
                for l in list(img_in.links):
                    nt.links.remove(l)
                nt.links.new(current, img_in)
            if ld.outputs:
                current = ld.outputs[0]

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

    if view_sock is None:
        view_sock = current

    for node in nt.nodes:
        if node.type == 'VIEWER' and len(node.inputs) > 0:
            vsock = node.inputs.get("Image") or node.inputs[0]
            nt.links.new(view_sock, vsock)
            break


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
    shadow_strength: FloatProperty(
        name="Shadow Strength", default=1.0, min=0.0, max=1.0,
        update=lambda self, ctx: _trigger_comp(ctx)
    )
    fog_factor: FloatProperty(
        name="Fog x(layer)", default=1.0, min=0.0, max=2.0,
        description="Per-layer multiplier: 0 = off, 1 = normal, 2 = double",
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
    bg_scene: PointerProperty(
        type=bpy.types.Scene,
        name="Background scene",
        description="World-only background pass, always bottom of comp"
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
    fog_strength: FloatProperty(
        name="Density (global)", default=0.0, min=0.0, max=1.0,
        description="Overall fog density, multiplies every layer",
        update=lambda s, c: _trigger_comp(c)
    )
    fog_color: FloatVectorProperty(
        name="Fog Color", subtype='COLOR', size=4,
        default=(0.7, 0.75, 0.85, 1.0), min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    ramp_black: FloatProperty(
        name="Ramp Black", default=0.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    ramp_white: FloatProperty(
        name="Ramp White", default=1.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    fog_preview: BoolProperty(
        name="Show Mask (viewer)",
        description="Temporarily show the fog mask in the viewer instead of the scene",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    fog_expanded: BoolProperty(
        name="Fog settings expanded",
        default=False
    )
    bg_fog_factor: FloatProperty(
        name="BG Fog", default=1.0, min=0.0, max=2.0,
        description="Fog amount on the background layer",
        update=lambda s, c: _trigger_comp(c)
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
    use_blur: BoolProperty(
        name="Atmospheric Blur (mist)",
        description="Far = more blur, artistic depth haze",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    blur_size: FloatProperty(
        name="Blur Size (px)", default=8.0, min=0.0, max=100.0,
        update=lambda s, c: _trigger_comp(c)
    )
    blur_ramp_black: FloatProperty(
        name="Blur Black", default=0.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    blur_ramp_white: FloatProperty(
        name="Blur White", default=1.0, min=0.0, max=1.0,
        update=lambda s, c: _trigger_comp(c)
    )
    use_dof: BoolProperty(
        name="Camera Focus (DOF)",
        description="Physical depth of field: sharp at focus, blurred near and far",
        default=False,
        update=lambda s, c: _trigger_comp(c)
    )
    dof_fstop: FloatProperty(
        name="F-Stop", default=2.8, min=0.1, max=32.0,
        update=lambda s, c: _trigger_comp(c)
    )
    dof_focus: FloatProperty(
        name="Focus Distance (m)", default=10.0, min=0.0, max=500.0,
        update=lambda s, c: _trigger_comp(c)
    )
    dof_maxblur: FloatProperty(
        name="Max Blur (px)", default=12.0, min=0.0, max=100.0,
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
# FOG (единая live-карта)
# ---------------------------------------------------------------------

def _update_mist(context):
    try:
        vfx = context.scene.vfx
        master = vfx.master_scene or context.scene
        w = master.world
        if w is not None:
            w.mist_settings.start = vfx.mist_start
            w.mist_settings.depth = vfx.mist_depth
        _trigger_comp(context)
    except Exception:
        pass


def _setup_fog_passes(vfx, master, force=False):
    """Сцена VFX_FOGMAP со всеми объектами для live mist-маски."""
    if not force and not getattr(vfx, "use_fog", False):
        return

    w = master.world
    if w is not None:
        try:
            w.mist_settings.start = vfx.mist_start
            w.mist_settings.depth = vfx.mist_depth
        except Exception:
            pass

    sc = getattr(vfx, "fog_map_scene", None) or bpy.data.scenes.get("VFX_FOGMAP")
    if sc is None:
        sc = create_empty_scene("VFX_FOGMAP", master)
        sc["vfx_pass"] = "FOGMAP"
        try:
            sc.vfx.master_scene = master
        except Exception:
            pass
    vfx.fog_map_scene = sc

    root = ensure_root(master)
    cam_col = ensure_camera_collection(master, root)
    link_collection_to_scene(sc, cam_col)
    if master.camera:
        sc.camera = master.camera
    for layer in vfx.layers:
        if layer.collection:
            link_collection_to_scene(sc, layer.collection)

    sync_scene_settings(master, sc)
    try:
        sc.render.engine = 'BLENDER_EEVEE_NEXT'
    except Exception:
        try:
            sc.render.engine = 'BLENDER_EEVEE'
        except Exception:
            pass

    for vl in sc.view_layers:
        try:
            vl.use_pass_mist = True
        except Exception:
            pass


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


class VFX_OT_toggle_fog_expand(bpy.types.Operator):
    bl_idname = "vfx.toggle_fog_expand"
    bl_label = "Toggle Fog Settings"
    bl_options = set()

    def execute(self, context):
        vfx, master = get_project(context)
        vfx.fog_expanded = not vfx.fog_expanded
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

        self.report({'INFO'}, f"Layer renamed to: {name}")
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

        auto_sync_settings(vfx, master)

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

        self.mode = 'ANIMATION'

        try:
            refresh_shadow_proxies(vfx, master)
        except Exception as e:
            print("VFX proxy refresh error:", e)

        self.steps = []
        total_frames = 0
        for sc in scenes:
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
        vfx.render_status = f"{self.frames_done}/{self.total_frames}  •  {name}  •  frames {sc.frame_start}-{sc.frame_end}"

        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()

        win = context.window
        if win is None and bpy.context.window_manager.windows:
            win = bpy.context.window_manager.windows[0]
        try:
            sc.render.filepath = f"{vfx.output_dir}{sc.name}/"
            print("VFX: rendering", name, "->",
                  bpy.path.abspath(sc.render.filepath))
        except Exception:
            pass

        try:
            with bpy.context.temp_override(window=win):
                bpy.ops.render.render('INVOKE_DEFAULT', animation=True, scene=name)
        except Exception as e:
            print("VFX render start error:", name, e)
            try:
                bpy.ops.render.render(animation=True, scene=name)
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

        if not stopped and (getattr(self, "post_files", False) or getattr(self, "refresh_after", False)):
            try:
                master = vfx.master_scene or context.scene
                vfx.comp_mode = 'FILES'
                rebuild_comp_from_files(vfx, master)
                vfx.render_status = "Done + comp from EXR"
            except Exception as e:
                print("VFX post-files error:", e)

        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()


class VFX_OT_one_click_exr(bpy.types.Operator):
    bl_idname = "vfx.one_click_exr"
    bl_label = "Render EXR + Comp From Files"
    bl_description = "Render all layer scenes as EXR animation, then build comp from files"

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

        rebuild_comp_from_files(vfx, master)

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

        rebuild_comp(vfx, master)

        self.report({'INFO'}, "Background removed")
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

        rebuild_comp(vfx, master)

        self.report({'INFO'}, f"Shadow pass removed from {layer.layer_name}")
        return {'FINISHED'}


def auto_sync_settings(vfx, master):
    """Перед рендером: тянет настройки из master во все слой-сцены."""
    for layer in vfx.layers:
        if layer.scene:
            try:
                sync_scene_settings(master, layer.scene)
                layer.scene.render.engine = vfx.objects_engine
            except Exception:
                pass
        if layer.shadow_scene:
            try:
                sync_scene_settings(master, layer.shadow_scene)
                layer.shadow_scene.render.engine = vfx.shadows_engine
            except Exception:
                pass
        for c in (layer.shadow_cast_collection, layer.shadow_catch_collection):
            if c:
                exclude_collection_in_master(master, c)

    bg = getattr(vfx, "bg_scene", None)
    if bg:
        try:
            sync_scene_settings(master, bg)
            bg.render.engine = vfx.objects_engine
            bg.render.film_transparent = False
        except Exception:
            pass


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
        import traceback
        vfx, master = get_project(context, allow_write=True)
        try:
            if getattr(vfx, "comp_mode", 'LIVE') == 'FILES':
                rebuild_comp_from_files(vfx, master)
                self.report({'INFO'}, "Comp rebuilt from EXR files")
            else:
                rebuild_comp(vfx, master)
                self.report({'INFO'}, "VFX compositing nodes rebuilt")
        except Exception as e:
            print("VFX rebuild error:", e)
            traceback.print_exc()
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

        layout.template_list(
            "VFX_UL_layers",
            "",
            vfx,
            "layers",
            vfx,
            "active_layer_index"
        )

        # закрепленная строка фона
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
            if vfx.use_fog:
                r.prop(vfx, "bg_fog_factor", text="Fog")

        # закрепленная строка тумана
        fogbox = layout.box()
        fr = fogbox.row(align=True)
        fr.operator(
            "vfx.toggle_fog_expand",
            text="",
            icon='TRIA_DOWN' if vfx.fog_expanded else 'TRIA_RIGHT',
            emboss=False
        )
        fr.label(text="FOG", icon='FORCE_WIND')
        fr.prop(vfx, "use_fog", text="")
        if vfx.fog_expanded:
            fb = fogbox.column(align=True)
            fb.prop(vfx, "mist_start")
            fb.prop(vfx, "mist_depth")
            fr2 = fb.row(align=True)
            fr2.prop(vfx, "ramp_black")
            fr2.prop(vfx, "ramp_white")
            fb.prop(vfx, "fog_strength")
            if vfx.fog_strength > 0.0:
                fb.prop(vfx, "fog_color", text="")
            fb.prop(vfx, "fog_preview")

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

            if vfx.use_fog:
                box.prop(layer, "fog_factor")

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

        layout.separator()
        layout.prop(vfx, "output_dir", text="Output")

        layout.operator(
            "vfx.one_click_exr",
            text="1-Click: Render EXR + Comp",
            icon='FILE_IMAGE'
        )

        if vfx.render_running:
            bar = layout.column(align=True)
            bar.scale_y = 2.5
            bar.prop(vfx, "render_progress", slider=True, text="")
            layout.label(text=vfx.render_status, icon='RENDER_ANIMATION')
            layout.label(text="ESC - stop render", icon='INFO')

        layout.separator(factor=1.5)

        row = layout.row(align=True)
        row.prop(vfx, "objects_engine", text="Obj")
        row.prop(vfx, "shadows_engine", text="Shd")

        layout.operator("vfx.rebuild_comp", text="Rebuild Comp", icon='FILE_REFRESH')

        # GLOW / GLARE
        glowbox = layout.box()
        gr = glowbox.row(align=True)
        gr.prop(vfx, "use_glare", text="")
        gr.label(text="GLOW / GLARE", icon='LIGHT_SUN')
        if vfx.use_glare:
            gc = glowbox.column(align=True)
            gc.prop(vfx, "glare_type", text="")
            gc.prop(vfx, "glare_strength")
            gc.prop(vfx, "glare_threshold")
            gc.prop(vfx, "glare_size")

        # DEPTH / BLUR: атмосферный + камерный
        depthbox = layout.box()
        depthbox.label(text="DEPTH / BLUR", icon='CAMERA_DATA')

        da = depthbox.column(align=True)
        da.prop(vfx, "use_blur")
        if vfx.use_blur:
            da.prop(vfx, "blur_size")
            dr = da.row(align=True)
            dr.prop(vfx, "blur_ramp_black")
            dr.prop(vfx, "blur_ramp_white")

        depthbox.separator()

        dd = depthbox.column(align=True)
        dd.prop(vfx, "use_dof")
        if vfx.use_dof:
            dd.prop(vfx, "dof_fstop")
            dd.prop(vfx, "dof_focus")
            dd.prop(vfx, "dof_maxblur")

        # LENS DISTORTION
        ldbox = layout.box()
        lbw = ldbox.row(align=True)
        lbw.prop(vfx, "use_lensdist", text="")
        lbw.label(text="LENS DISTORTION", icon='VIEW_CAMERA')
        if vfx.use_lensdist:
            lc = ldbox.column(align=True)
            lc.prop(vfx, "lensdist_distort")
            lc.prop(vfx, "lensdist_disperse")


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
    VFX_OT_create_shadow_pass,
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
    VFX_OT_create_background,
    VFX_OT_delete_background,
    VFX_OT_delete_shadow_pass,
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