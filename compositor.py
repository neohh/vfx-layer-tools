"""VFX Layer Tools — compositor node tree, fog system."""

import bpy
import os

from .core import (
    ensure_root, ensure_camera_collection, link_collection_to_scene,
    create_empty_scene, sync_scene_settings,
)
from .materials import _trigger_comp
from .colormatch import get_or_create_color_match_group, apply_preset
from .grade import ensure_layer_grades, ensure_master_grade, apply_grade_values
from .occlusion import compute_composite_order, occlusion_self_check
from .lightgroups import add_light_group_output_nodes


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

    # FALLBACK: search node_groups, but skip VFX sub-groups
    # (VFX_FogGroup, VFX_Grade, VFX_ColorMatchGroup are CompositorNodeTree
    # but NOT the scene's compositor)
    tree = None
    for ng in bpy.data.node_groups:
        if ng.bl_idname == 'CompositorNodeTree':
            if ng.name.startswith("VFX_") and ng.name != "VFX_Compositor":
                continue
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
    """Fallback: load EXR sequence via image.load + SEQUENCE source."""
    img_name = f"VFX_SEQ_{scene_name}"
    abs_base = bpy.path.abspath(base_path)
    folder = os.path.join(abs_base, scene_name)

    existing = bpy.data.images.get(img_name)
    if existing is not None:
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
        return existing

    if not os.path.isdir(folder):
        return None

    files = sorted([f for f in os.listdir(folder) if f.lower().endswith('.exr')])
    if not files:
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

    return img


def _load_sequence_image2(scene_name, base_path):
    """Primary: load EXR sequence via ops.image.open for proper multi-file import."""
    img_name = f"VFX_SEQ_{scene_name}"
    abs_base = bpy.path.abspath(base_path)
    folder = os.path.join(abs_base, scene_name)

    files = []
    if os.path.isdir(folder):
        files = sorted([f for f in os.listdir(folder)
                        if f.lower().endswith('.exr')])
    if not files:
        return _load_sequence_image(scene_name, base_path)

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
        return _load_sequence_image(scene_name, base_path)

    img = new_imgs[0]
    img.name = img_name
    try:
        img.frame_start = start_frame
        img.frame_offset = 0
    except Exception:
        pass

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
            node.location = (350, y)

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
        node.location = (0, 500)
    if getattr(vfx, "use_fog", False) or getattr(vfx, "use_dof", False):
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
            node.location = (-350, 600)

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
                layer.id, "SHADOW", x=350, y=y
            )

        y -= 220

    bg_scene = getattr(vfx, "bg_scene", None)
    if bg_scene:
        valid_nodes.add("VFX_RL_BG")
        ensure_render_node(master, bg_scene, "VFX_RL_BG", "BACKGROUND", "BG", "OBJECT", x=0, y=y)
    if getattr(vfx, "use_fog", False) or getattr(vfx, "use_dof", False):
        _setup_fog_passes(vfx, master, force=True)
        fm = getattr(vfx, "fog_map_scene", None)
        if fm is not None:
            valid_nodes.add("VFX_RL_FOGMAP")
            ensure_render_node(
                master, fm, "VFX_RL_FOGMAP", "FOG MAP (live)",
                "FOGMAP", "MIST", x=-350, y=600
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


def _safe_set(node, name, value):
    """Set a property: try direct attr first, then input socket (case-insensitive)."""
    try:
        setattr(node, name, value)
        return True
    except Exception:
        pass
    for sock in node.inputs:
        if sock.name.lower() == name.lower():
            try:
                sock.default_value = value
                return True
            except Exception:
                pass
    return False


def _remove_nodes(nt, *names):
    for n in names:
        node = nt.nodes.get(n)
        if node is not None:
            nt.nodes.remove(node)


def _get_mist_socket(nt):
    n = nt.nodes.get("VFX_RL_FOGMAP")
    if n is not None:
        if n.outputs.get("Mist"):
            return n.outputs["Mist"]
        if n.outputs.get("Image"):
            return n.outputs["Image"]
    return None


def _get_depth_socket(nt):
    """Z depth in meters from the live FOGMAP render layers (1 - mist fallback)."""
    n = nt.nodes.get("VFX_RL_FOGMAP")
    if n is None:
        return None
    if n.outputs.get("Depth"):
        return n.outputs["Depth"]
    if n.outputs.get("Z"):
        return n.outputs["Z"]
    return None


def _to_float(nt, sock, name_prefix, loc):
    """Convert Color socket to float (red channel) if needed."""
    if sock is None or sock.type == 'VALUE':
        return sock
    sep = nt.nodes.get(f"MASK_{name_prefix}_SEP")
    if sep is None:
        sep = _new_node(nt, "ShaderNodeSeparateColor", "CompositorNodeSeparateColor")
        if sep is None:
            return sock
        sep.name = f"MASK_{name_prefix}_SEP"
        sep.label = f"MASK {name_prefix} to float"
    sep.location = loc
    for l in list(sep.inputs[0].links):
        nt.links.remove(l)
    nt.links.new(sock, sep.inputs[0])
    return sep.outputs[0]


def _math_node(nt, op, a, b, name, loc):
    m = nt.nodes.get(name)
    if m is None:
        m = _new_node(nt, "CompositorNodeMath", "ShaderNodeMath")
        if m is None:
            return None
        m.name = name
    m.operation = op
    m.label = f"MASK {name}"
    m.location = loc
    if a is not None:
        for l in list(m.inputs[0].links):
            nt.links.remove(l)
        nt.links.new(a, m.inputs[0])
    if b is not None:
        for l in list(m.inputs[1].links):
            nt.links.remove(l)
        nt.links.new(b, m.inputs[1])
    return m


def _set_maprange(mr, lo, hi):
    for name, val in (("From Min", lo), ("From Max", hi)):
        s = mr.inputs.get(name)
        if s is not None:
            try:
                s.default_value = val
            except Exception:
                pass
    try:
        mr.interpolation_type = 'SMOOTHSTEP'
    except Exception:
        pass


def build_mask(nt, props, name_prefix, source, invert, soft,
               depth_start, depth_end, luma_lo, luma_hi,
               ext_node="", image_sock=None, depth_sock=None):
    """Build (or reuse) a mask chain and return a float socket 0..1.

    All nodes are named MASK_<name_prefix>* and reused on rebuild.
    """
    sock = None
    if source == 'EXT':
        ext = nt.nodes.get(ext_node) if ext_node else None
        if ext is not None and ext.outputs:
            sock = ext.outputs[0]
    elif source == 'ALPHA' and image_sock is not None:
        # Real alpha channel of the source (render layer / image nodes have it)
        src_node = getattr(image_sock, "node", None)
        if src_node is not None:
            alpha = src_node.outputs.get("Alpha")
            if alpha is not None:
                sock = alpha
        if sock is None:
            # Fallback: Separate Color -> alpha channel (index 3)
            sep = nt.nodes.get(f"MASK_{name_prefix}_ASEP")
            if sep is None:
                sep = _new_node(nt, "ShaderNodeSeparateColor", "CompositorNodeSeparateColor")
                if sep is not None:
                    sep.name = f"MASK_{name_prefix}_ASEP"
                    sep.label = f"MASK {name_prefix} alpha"
            if sep is not None:
                sep.location = (-2600, -400)
                for l in list(sep.inputs[0].links):
                    nt.links.remove(l)
                nt.links.new(image_sock, sep.inputs[0])
                sock = sep.outputs.get("Alpha") or sep.outputs[2]
    elif source == 'DEPTH':
        ds = depth_sock
        if ds is None:
            ds = _get_mist_socket(nt)
        if ds is not None:
            mr = nt.nodes.get(f"MASK_{name_prefix}_MR")
            if mr is None:
                mr = _new_node(nt, "ShaderNodeMapRange", "CompositorNodeMapRange")
                if mr is not None:
                    mr.name = f"MASK_{name_prefix}_MR"
                    mr.label = f"MASK {name_prefix} depth"
            if mr is not None:
                mr.location = (-2600, 300)
                _set_maprange(mr, depth_start, depth_end)
                v = mr.inputs.get("Value")
                if v is not None:
                    for l in list(v.links):
                        nt.links.remove(l)
                    nt.links.new(_to_float(nt, ds, name_prefix + "D", (-2800, 300)), v)
                sock = mr.outputs.get("Result")
    elif source == 'LUMA' and image_sock is not None:
        rgb = nt.nodes.get(f"MASK_{name_prefix}_RGB")
        if rgb is None:
            rgb = _new_node(nt, "CompositorNodeRGBToBW", "ShaderNodeRGBToBW")
            if rgb is not None:
                rgb.name = f"MASK_{name_prefix}_RGB"
                rgb.label = f"MASK {name_prefix} luma"
        if rgb is not None:
            rgb.location = (-2700, -150)
            for l in list(rgb.inputs[0].links):
                nt.links.remove(l)
            nt.links.new(image_sock, rgb.inputs[0])
            mr = nt.nodes.get(f"MASK_{name_prefix}_LMR")
            if mr is None:
                mr = _new_node(nt, "ShaderNodeMapRange", "CompositorNodeMapRange")
                if mr is not None:
                    mr.name = f"MASK_{name_prefix}_LMR"
                    mr.label = f"MASK {name_prefix} luma range"
            if mr is not None:
                mr.location = (-2600, -150)
                _set_maprange(mr, luma_lo, luma_hi)
                v = mr.inputs.get("Value")
                if v is not None:
                    for l in list(v.links):
                        nt.links.remove(l)
                    nt.links.new(rgb.outputs[0], v)
                sock = mr.outputs.get("Result")

    # Invert: 1 - x
    if invert and sock is not None:
        sock = _math_node(nt, 'SUBTRACT', None, sock, f"MASK_{name_prefix}_INV", (-2400, 0))
        if sock is not None:
            sock.node.inputs[0].default_value = 1.0

    # Softness: tiny blur rounds mask edges
    try:
        soft = float(soft or 0.0)
    except Exception:
        soft = 0.0
    if soft > 0.001 and sock is not None:
        bl = nt.nodes.get(f"MASK_{name_prefix}_SOFT")
        if bl is None:
            try:
                bl = nt.nodes.new("CompositorNodeBlur")
                if bl is not None:
                    bl.name = f"MASK_{name_prefix}_SOFT"
            except Exception:
                bl = None
        if bl is not None:
            bl.label = f"MASK {name_prefix} soft"
            bl.location = (-2300, 0)
            try:
                bl.blur_method = 'GAUSS'
            except Exception:
                pass
            try:
                bl.size_x = max(1, int(round(soft)))
                bl.size_y = max(1, int(round(soft)))
            except Exception:
                pass
            for l in list(bl.inputs[0].links):
                nt.links.remove(l)
            nt.links.new(sock, bl.inputs[0])
            try:
                bl.inputs["Size"].default_value = 1.0
            except Exception:
                pass
            sock = bl.outputs[0]
    return sock


def _apply_mask(nt, prefix, orig_sock, eff_sock, mask_sock, loc_y=0):
    """Wrap an effect: out = Mix(fac=MASK, A=orig, B=eff). Returns output socket."""
    if mask_sock is None:
        return eff_sock
    mix, fac, a, b, out = _fog_mix_node(nt, (1300, loc_y))
    if mix is None or fac is None or a is None or b is None or out is None:
        return eff_sock
    mix.name = f"VFX_MASKMIX_{prefix}"
    mix.label = f"{prefix} MASK"
    mix["vfx_maskmix"] = 1
    nt.links.new(mask_sock, fac)
    nt.links.new(orig_sock, a)
    nt.links.new(eff_sock, b)
    return out


def _cleanup_mask_nodes(nt):
    """Remove all MASK_* helper nodes and mask mix nodes not reconnected this build.
    Called at the start of build_comp_assembly; builders recreate/reuse by name."""
    for node in list(nt.nodes):
        n = node.name
        if n.startswith("MASK_") or n.startswith("VFX_MASKMIX_"):
            nt.nodes.remove(node)


def _remove_vfx_nodes(nt, *names):
    for n in names:
        node = nt.nodes.get(n)
        if node is not None:
            nt.nodes.remove(node)


def _ensure_dof_ramp_group():
    """Group: Depth (m) -> abs distance to focus -> ColorRamp (EDITABLE) -> Max Blur.
    Outputs 'Blur' 0..1 used as the variable-size factor of the DOF blur node.
    The ColorRamp node inside is user-editable in the node editor.
    """
    ng = bpy.data.node_groups.get("VFX_DofRamp")
    if ng is None:
        ng = bpy.data.node_groups.new("VFX_DofRamp", 'CompositorNodeTree')
    have = {s.name for s in ng.interface.items_tree if hasattr(s, "in_out")}
    need = {("Depth", 'INPUT', 'NodeSocketFloat'), ("Focus", 'INPUT', 'NodeSocketFloat'),
            ("Far Start", 'INPUT', 'NodeSocketFloat'), ("Far End", 'INPUT', 'NodeSocketFloat'),
            ("Max Blur", 'INPUT', 'NodeSocketFloat'), ("Blur", 'OUTPUT', 'NodeSocketFloat')}
    if not need.issubset({(s.name, s.in_out, s.socket_type) for s in ng.interface.items_tree if hasattr(s, "in_out")}):
        ng.nodes.clear()
        try:
            ng.interface.items_clear()
        except Exception:
            pass
        for name, io, typ in sorted(need, key=lambda t: t[1] != 'INPUT'):
            try:
                ng.interface.new_socket(name, in_out=io, socket_type=typ)
            except Exception:
                pass
        gin = ng.nodes.new("NodeGroupInput")
        gin.location = (-700, 0)
        gout = ng.nodes.new("NodeGroupOutput")
        gout.location = (500, 0)
        # |depth - focus|
        sub = ng.nodes.new("CompositorNodeMath")
        sub.operation = 'SUBTRACT'
        sub.name = "DR_SUB"
        sub.location = (-480, 100)
        ng.links.new(gin.outputs.get("Depth"), sub.inputs[0])
        ng.links.new(gin.outputs.get("Focus"), sub.inputs[1])
        ab = ng.nodes.new("CompositorNodeMath")
        ab.operation = 'ABSOLUTE'
        ab.name = "DR_ABS"
        ab.location = (-320, 100)
        ng.links.new(sub.outputs[0], ab.inputs[0])
        # distance -> 0..1 over far range (input for the ramp)
        mr = ng.nodes.new("ShaderNodeMapRange")
        mr.name = "DR_MR"
        mr.location = (-150, 100)
        try:
            mr.interpolation_type = 'LINEAR'
        except Exception:
            pass
        ng.links.new(ab.outputs[0], mr.inputs.get("Value"))
        ng.links.new(gin.outputs.get("Far Start"), mr.inputs.get("From Min"))
        ng.links.new(gin.outputs.get("Far End"), mr.inputs.get("From Max"))
        # EDITABLE ramp: black = sharp, white = max blur
        ramp = ng.nodes.new("CompositorNodeValToRGB")
        ramp.name = "DR_RAMP"
        ramp.label = "EDIT ME: sharp -> blurry"
        ramp.location = (50, 100)
        try:
            ramp.color_ramp.elements[0].position = 0.0
            ramp.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
            ramp.color_ramp.elements[1].position = 1.0
            ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
        except Exception:
            pass
        ng.links.new(mr.outputs.get("Result"), ramp.inputs[0])
        # ramp -> max blur px
        mul = ng.nodes.new("CompositorNodeMath")
        mul.operation = 'MULTIPLY'
        mul.name = "DR_MUL"
        mul.location = (250, 100)
        ng.links.new(ramp.outputs[0], mul.inputs[0])
        ng.links.new(gin.outputs.get("Max Blur"), mul.inputs[1])
        ng.links.new(mul.outputs[0], gout.inputs.get("Blur"))
    return ng


def _ensure_grade_group(vfx, name="VFX_MasterGrade"):
    """Create/update a grade node group (Bright/Contrast + Hue/Sat)."""
    ng = bpy.data.node_groups.get(name)
    if ng is None:
        ng = bpy.data.node_groups.new(name, 'CompositorNodeTree')
    ng.nodes.clear()
    try:
        ng.interface.items_clear()
    except Exception:
        pass
    ng.interface.new_socket("Image", in_out='INPUT', socket_type='NodeSocketColor')
    ng.interface.new_socket("Brightness", in_out='INPUT', socket_type='NodeSocketFloat')
    ng.interface.new_socket("Contrast", in_out='INPUT', socket_type='NodeSocketFloat')
    ng.interface.new_socket("Saturation", in_out='INPUT', socket_type='NodeSocketFloat')
    ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    gin = ng.nodes.new("NodeGroupInput")
    gin.location = (-500, 0)
    gout = ng.nodes.new("NodeGroupOutput")
    gout.location = (350, 0)
    cur = gin.outputs.get("Image")
    bc = _new_node(ng, "CompositorNodeBrightContrast", "ShaderNodeBrightContrast")
    if bc is not None:
        bc.name = "MG_BC"
        bc.location = (-250, 0)
        try:
            bc.use_clamp = True
        except Exception:
            pass
        for l in list(bc.inputs[0].links):
            ng.links.remove(l)
        ng.links.new(cur, bc.inputs[0])
        b = bc.inputs.get("Bright")
        c = bc.inputs.get("Contrast")
        bs = gin.outputs.get("Brightness")
        cs = gin.outputs.get("Contrast")
        if b is not None and bs is not None:
            ng.links.new(bs, b)
        elif b is not None:
            b.default_value = 0.0
        if c is not None and cs is not None:
            ng.links.new(cs, c)
        elif c is not None:
            c.default_value = 0.0
        o = [s for s in bc.outputs if s.type == 'RGBA']
        cur = o[0] if o else cur
    hs = _new_node(ng, "CompositorNodeHueSat", "ShaderNodeHueSaturation")
    if hs is not None:
        hs.name = "MG_HS"
        hs.location = (100, 0)
        i = [s for s in hs.inputs if s.type == 'RGBA']
        if i and cur is not None:
            ng.links.new(cur, i[0])
            ss = gin.outputs.get("Saturation")
            sat_in = None
            for s in hs.inputs:
                if s.name.lower() == 'saturation':
                    sat_in = s
                    break
            if sat_in is not None and ss is not None:
                ng.links.new(ss, sat_in)
            o = [s for s in hs.outputs if s.type == 'RGBA']
            cur = o[0] if o else cur
    oi = gout.inputs.get("Image")
    if oi is not None and cur is not None:
        ng.links.new(cur, oi)
    return ng


def ensure_crypto_mask_node(vfx, master, obj_name):
    """Cryptomatte mask node fed by the live FOGMAP render layer;
    matte is set to the picked object. Used as EXT mask source."""
    nt = get_comp_tree(master)
    if not nt:
        return None
    fm = getattr(vfx, "fog_map_scene", None)
    if fm is not None:
        for vl in fm.view_layers:
            try:
                vl.use_pass_cryptomatte_object = True
            except Exception:
                pass
    rl = nt.nodes.get("VFX_RL_FOGMAP")
    node = nt.nodes.get("VFX_CRYPTO_PICK")
    if node is None:
        try:
            node = nt.nodes.new("CompositorNodeCryptomatte")
        except Exception:
            return None
        node.name = "VFX_CRYPTO_PICK"
        node.label = "CRYPTO PICK (pipette)"
        node.location = (-350, -700)
    if rl is not None and node.inputs:
        img_in = node.inputs.get("Image") or node.inputs[0]
        try:
            nt.links.new(rl.outputs.get("Image") or rl.outputs[0], img_in)
        except Exception:
            pass
    try:
        node.matte_id = obj_name
    except Exception:
        pass
    try:
        node.layer_name = "CryptoObject00"
    except Exception:
        pass
    return node


def _set_socket(node, name, value):
    """Set an input socket's default_value by name."""
    for sock in node.inputs:
        if sock.name == name:
            try:
                sock.default_value = value
                return True
            except Exception:
                pass
    return False


def _set_glare_type(node, glare_type):
    """Set the Glare type via the 'Type' MENU input socket.
    The addon enum values: BLOOM, FOG_GLOW, STREAKS, GHOSTS
    Blender socket values: Bloom, Fog Glow, Streaks, Ghosts (display names)
    """
    TYPE_MAP = {
        'BLOOM': 'Bloom',
        'FOG_GLOW': 'Fog Glow',
        'STREAKS': 'Streaks',
        'GHOSTS': 'Ghosts',
    }
    socket_name = TYPE_MAP.get(glare_type, glare_type)

    for sock in node.inputs:
        if sock.name == 'Type':
            for val in (socket_name, glare_type, glare_type.lower()):
                try:
                    sock.default_value = val
                    return True
                except Exception:
                    pass
            for val in ('Bloom', 'Fog Glow', 'Streaks', 'Ghosts',
                        'BLOOM', 'FOG_GLOW', 'STREAKS', 'GHOSTS'):
                try:
                    sock.default_value = val
                    return True
                except Exception:
                    pass
            break
    return False


def _remove_nodes(nt, *names):
    """Remove nodes by name if they exist."""
    for n in names:
        node = nt.nodes.get(n)
        if node is not None:
            nt.nodes.remove(node)


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
            "FOGMAP", "MIST", x=-350, y=600
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


def _build_fog_group2(vfx):
    """VFX_FogGroup — minimal scheme (reference topology):

        Depth (m) -> MapRange(Fog Start..Fog Full, clamped)
                  -> ColorRamp (EDITABLE density curve: black=near, white=far)
                  -> [* Strength] -> [* Extra Mask] -> density
        density -> Invert -> Mix.Factor
        Mix.A = Fog Color
        Mix.B = Image input (layers assembled OUTSIDE the group)

    Outputs: Image (fogged comp), Mask (fog density, white = dense).

    The internals are built ONCE and then left alone, so the user's
    ColorRamp edits survive comp rebuilds. A rebuild happens only if the
    existing group is missing, still has the OLD socket set, or carries an
    older scheme version (ng["vfx_fog_version"] < VFX_FOG_SCHEME_VERSION).
    """
    VFX_FOG_SCHEME_VERSION = 2  # 2 = Invert wired by socket name
    ng = bpy.data.node_groups.get("VFX_FogGroup")
    needed_inputs = {"Image", "Depth", "Strength", "Extra Mask",
                     "Fog Color", "Ramp Black", "Ramp White"}
    if ng is not None:
        try:
            have = {s.name for s in ng.interface.items_tree
                    if hasattr(s, "in_out") and s.in_out == 'INPUT'}
        except Exception:
            have = set()
        try:
            scheme = int(ng.get("vfx_fog_version", 0))
        except Exception:
            scheme = 0
        if needed_inputs.issubset(have) and scheme >= VFX_FOG_SCHEME_VERSION:
            return ng  # modern group already built — keep user's ramp edits
        # old-scheme group (per-layer OBJ_/AL_/F_ sockets...) — purge it
        try:
            bpy.data.node_groups.remove(ng)
        except Exception:
            ng.use_fake_user = False
    ng = bpy.data.node_groups.new("VFX_FogGroup", 'CompositorNodeTree')
    ng.nodes.clear()
    try:
        ng.interface.items_clear()
    except Exception:
        try:
            ng.interface.clear()
        except Exception:
            pass

    ng.interface.new_socket("Image", in_out='INPUT', socket_type='NodeSocketColor')
    ng.interface.new_socket("Depth", in_out='INPUT', socket_type='NodeSocketFloat')
    ng.interface.new_socket("Strength", in_out='INPUT', socket_type='NodeSocketFloat')
    ng.interface.new_socket("Extra Mask", in_out='INPUT', socket_type='NodeSocketFloat')
    ng.interface.new_socket("Fog Color", in_out='INPUT', socket_type='NodeSocketColor')
    ng.interface.new_socket("Ramp Black", in_out='INPUT', socket_type='NodeSocketFloat')
    ng.interface.new_socket("Ramp White", in_out='INPUT', socket_type='NodeSocketFloat')
    ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    ng.interface.new_socket("Mask", in_out='OUTPUT', socket_type='NodeSocketFloat')

    gin = ng.nodes.new("NodeGroupInput")
    gin.location = (-900, 0)
    gout = ng.nodes.new("NodeGroupOutput")
    gout.location = (750, 0)

    def g_in(name):
        return gin.outputs.get(name)

    def math_node(op, loc):
        m = _new_node(ng, "CompositorNodeMath", "ShaderNodeMath")
        if m is not None:
            m.operation = op
            m.location = loc
        return m

    # meters -> 0..1 over the Fog Start..Fog Full window.
    # MapRange clamps by itself, no division anywhere -> factor can never
    # get stuck at 0 or 1 (the old bug class).
    mr = _new_node(ng, "ShaderNodeMapRange", "CompositorNodeMapRange")
    if mr is None:
        return ng
    mr.location = (-700, 250)
    try:
        mr.interpolation_type = 'LINEAR'
    except Exception:
        pass
    try:
        mr.clamp = True
    except Exception:
        pass
    ng.links.new(g_in("Depth"), mr.inputs.get("Value"))
    ng.links.new(g_in("Ramp Black"), mr.inputs.get("From Min"))
    ng.links.new(g_in("Ramp White"), mr.inputs.get("From Max"))

    # EDITABLE ramp: the fog density curve (like the reference scheme:
    # black = near/clear, white = far/dense). User can re-shape it freely.
    ramp = _new_node(ng, "CompositorNodeValToRGB", "ShaderNodeValToRGB")
    if ramp is None:
        return ng
    ramp.location = (-500, 250)
    ramp.name = "VFX_FOG_RAMP"
    ramp.label = "FOG MAP RAMP (edit me)"
    try:
        ramp.color_ramp.elements[0].position = 0.0
        ramp.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
        ramp.color_ramp.elements[1].position = 1.0
        ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
    except Exception:
        pass
    ng.links.new(mr.outputs.get("Result"), ramp.inputs[0])

    # Strength scales the DENSITY (not the inverted factor!), otherwise
    # Strength = 0 would mean "wall of fog everywhere".
    fog_fac = math_node('MULTIPLY', (-300, 250))
    if fog_fac is None:
        return ng
    ng.links.new(ramp.outputs[0], fog_fac.inputs[0])
    ng.links.new(g_in("Strength"), fog_fac.inputs[1])

    em = g_in("Extra Mask")
    if em is not None:
        fm2 = math_node('MULTIPLY', (-100, 250))
        if fm2 is not None:
            ng.links.new(fog_fac.outputs[0], fm2.inputs[0])
            ng.links.new(em, fm2.inputs[1])
            fog_fac = fm2
    density_sock = fog_fac.outputs[0]

    # invert: density -> mix factor (1 = clear layers, 0 = fog color)
    # NOTE: the compositor Invert node has sockets [Factor, Color] — wire by
    # NAME. By index, density would land in 'Factor' (the inversion amount)
    # and 'Color' would stay black: factor = 1 - 0*... = 1 everywhere, i.e.
    # the fog mix would pass the layers through untouched at every depth.
    inv = _new_node(ng, "CompositorNodeInvert", "ShaderNodeInvert")
    if inv is None:
        return ng
    inv.location = (100, 250)
    inv.name = "VFX_FOG_INV"
    inv.label = "density -> mix factor"
    try:
        inv.inputs["Fac"].default_value = 1.0  # full inversion
    except Exception:
        pass
    inv_color_in = inv.inputs.get("Color")
    inv_color_out = inv.outputs.get("Color")
    if inv_color_in is None or inv_color_out is None:
        inv_color_in = inv.inputs[-1]
        inv_color_out = inv.outputs[-1]
    ng.links.new(density_sock, inv_color_in)
    factor_sock = inv_color_out

    # ── MIX (reference scheme): Fac = inverted map, A = Fog Color,
    #    B = assembled layers ──
    mix = _new_node(ng, "CompositorNodeMixRGB", "ShaderNodeMix")
    if mix is None:
        return ng
    mix.location = (450, 0)
    try:
        mix.data_type = 'RGBA'
    except Exception:
        pass
    try:
        mix.blend_type = 'MIX'
    except Exception:
        pass
    try:
        mix.clamp_factor = True
        mix.clamp_result = False
    except Exception:
        pass
    # ShaderNodeMix has several sockets named A/B (Float, Vector, Color)
    # and its factor is called 'Factor' — pick sockets by type AND name,
    # inputs.get() would return the (wrong) Float sockets.
    a_sock = b_sock = fac_sock = None
    for s in mix.inputs:
        if s.type == 'RGBA' and s.name == 'A':
            a_sock = s
        elif s.type == 'RGBA' and s.name == 'B':
            b_sock = s
        elif s.type == 'VALUE' and s.name in ('Factor', 'Fac'):
            fac_sock = s
    if a_sock is None:
        a_sock = mix.inputs.get("Color1")
    if b_sock is None:
        b_sock = mix.inputs.get("Color2")

    fog_color_sock = g_in("Fog Color")
    if a_sock is not None and fog_color_sock is not None:
        ng.links.new(fog_color_sock, a_sock)
    img_in = g_in("Image")
    if b_sock is not None and img_in is not None:
        ng.links.new(img_in, b_sock)
    if fac_sock is not None:
        ng.links.new(factor_sock, fac_sock)

    img_out = None
    for s in mix.outputs:
        if s.type == 'RGBA':
            img_out = s
            break
    if img_out is None and mix.outputs:
        img_out = mix.outputs[-1]
    oi = gout.inputs.get("Image")
    if img_out is not None and oi is not None:
        ng.links.new(img_out, oi)
    mk_out = gout.inputs.get("Mask")
    if mk_out is not None:
        # Mask out = fog DENSITY (white = dense fog) for preview/masks
        ng.links.new(density_sock, mk_out)

    ng["vfx_fog_version"] = VFX_FOG_SCHEME_VERSION
    return ng


def build_comp_assembly(vfx, master, nt=None):
    if nt is None:
        nt = get_comp_tree(master)
    if not nt:
        return

    # Verify tree matches scene
    scene_tree = _find_comp_tree_attr(master)
    if scene_tree is not None and scene_tree is not nt:
        nt = scene_tree

    for node in list(nt.nodes):
        if node.get("vfx_mix"):
            nt.nodes.remove(node)

    if not getattr(vfx, "use_fog", False):
        fg = nt.nodes.get("VFX_FOG_GROUP")
        if fg is not None:
            nt.nodes.remove(fg)

    _cleanup_fog_nodes(nt)
    _cleanup_mask_nodes(nt)
    _remove_vfx_nodes(nt, "VFX_BLUR", "VFX_BLURRAMP", "VFX_BLURMATH")
    # Fog ramp: user values are METERS from the camera. The group maps Z
    # depth directly: near -> clear, far -> full fog color (clamped).
    # Heal garbage left from older versions before it reaches the nodes.
    ramp_black = max(0.0, float(vfx.ramp_black))
    ramp_white = float(vfx.ramp_white)
    if ramp_white <= 1.0 + 1e-6:
        # stale fraction from the old system (e.g. 0.11 or 1.0) -> meters
        ramp_white = max(0.0, float(vfx.mist_start)) + 0.5 * max(0.1, float(vfx.mist_depth))
        print(f"VFX: stale fog 'Fog Full' value converted to {ramp_white:.1f} m")
    if ramp_white <= ramp_black + 0.01:
        ramp_white = ramp_black + max(1.0, 0.5 * max(0.1, float(vfx.mist_depth)))
        print(f"VFX: healed fog ramp (Full raised to {ramp_white:.1f} m)")
    if getattr(vfx, "use_fog", False):
        print(f"[VFX fog] depth ramp: clear up to {ramp_black:.1f} m, "
              f"full fog at {ramp_white:.1f} m")
    for node in list(nt.nodes):
        if node.type == 'CRYPTOMATTE' and node.name != "VFX_CRYPTO_PICK":
            nt.nodes.remove(node)
    # ── COMPUTED STACKING ORDER (back -> front) ──
    # AUTO: by camera distance (nearest layer on top) | MANUAL: list order.
    # BACKGROUND (bg_sock) is always composited first = bottom.
    order = compute_composite_order(vfx, master, verbose=True)

    sockets = []
    for layer in order:
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
        # back-to-front within a layer: shadow pass first, object on top
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

    # ── PER-LAYER GRADES ──
    grade_nodes = ensure_layer_grades(vfx, master, nt)

    # ── ASSEMBLE THE FULL STACK (BG -> SHD -> OBJ, grades applied) ──
    # Layers are composited into ONE image outside the fog group;
    # the group then mixes this assembled stack with the fog color
    # by the shared depth-ramp map.
    if bg_sock is not None:
        stack_sock = bg_sock
        mix_list = sockets
    elif sockets:
        stack_sock = sockets[0][2]
        mix_list = sockets[1:]
    else:
        stack_sock = None
        mix_list = []

    current = stack_sock
    mix_index = 0
    for layer, kind, sock in mix_list:
        gsock = grade_nodes.get(layer.id) if kind == 'OBJ' else None
        if gsock is not None:
            sock = gsock
        mix = nt.nodes.new("CompositorNodeAlphaOver")
        mix.name = f"VFX_MIX_{mix_index:02d}"
        mix.label = f"{layer.layer_name} {kind}"
        mix["vfx_mix"] = 1
        mix.location = (800, -mix_index * 200)

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

    # ── FOG: assembled stack -> VFX_FogGroup ──
    if getattr(vfx, "use_fog", False):
        try:
            if current is None:
                raise RuntimeError("no layer stack to fog")
            _ensure_fogmap(nt, vfx, master)
            depth_sock = _get_depth_socket(nt)
            depth_is_z = depth_sock is not None
            if depth_sock is None:
                # Fallback: synthesize meters from the mist fraction
                # (Z = Mist Start + mist * Mist Depth).
                fm_node = nt.nodes.get("VFX_RL_FOGMAP")
                mist_out = fm_node.outputs.get("Mist") if fm_node else None
                if mist_out is not None:
                    fb = nt.nodes.get("VFX_FOG_DEPTH_FB")
                    if fb is not None and fb.type != 'MAP_RANGE':
                        nt.nodes.remove(fb)
                        fb = None
                    if fb is None:
                        fb = _new_node(nt, "ShaderNodeMapRange",
                                       "CompositorNodeMapRange")
                        if fb is not None:
                            fb.name = "VFX_FOG_DEPTH_FB"
                            fb.label = "mist -> meters"
                    if fb is not None:
                        fb.location = (-350, 600)
                        for name, val in (("From Min", 0.0), ("From Max", 1.0),
                                          ("To Min", float(vfx.mist_start)),
                                          ("To Max", float(vfx.mist_start)
                                           + max(0.1, float(vfx.mist_depth)))):
                            s = fb.inputs.get(name)
                            if s is not None:
                                s.default_value = val
                        for l in list(fb.inputs.get("Value").links):
                            nt.links.remove(l)
                        nt.links.new(mist_out, fb.inputs.get("Value"))
                        depth_sock = fb.outputs.get("Result")

            ng = _build_fog_group2(vfx)
            gnode = nt.nodes.get("VFX_FOG_GROUP")
            if gnode is not None and gnode.type != 'GROUP':
                nt.nodes.remove(gnode)
                gnode = None
            if gnode is None:
                gnode = _new_node(nt, "CompositorNodeGroup",
                                  "ShaderNodeGroup", "NodeGroup")
                if gnode is not None:
                    gnode.name = "VFX_FOG_GROUP"
            if gnode is None:
                raise RuntimeError("VFX fog group node not created")
            gnode.node_tree = ng
            gnode.label = "VFX FOG"
            gnode.location = (1050, 0)

            def relink(sock, out):
                for l in list(sock.links):
                    nt.links.remove(l)
                nt.links.new(out, sock)

            gi = lambda n: gnode.inputs.get(n)

            sm = gi("Image")
            if sm is not None and current is not None:
                relink(sm, current)

            sd = gi("Depth")
            if sd is not None:
                if depth_sock is not None:
                    relink(sd, depth_sock)
                else:
                    sd.default_value = 0.0

            for name, val in (("Strength", vfx.fog_strength),
                              ("Ramp Black", ramp_black),
                              ("Ramp White", ramp_white)):
                s = gi(name)
                if s is not None:
                    s.default_value = val

            # Optional extra mask on fog (from MASK_* chain)
            try:
                em = gi("Extra Mask")
                fmask = build_mask(
                    nt, vfx, "FOG", getattr(vfx, "fog_mask_source", 'NONE'),
                    vfx.fog_mask_invert, vfx.fog_mask_soft,
                    vfx.fog_mask_depth_start, vfx.fog_mask_depth_end,
                    vfx.fog_mask_luma_lo, vfx.fog_mask_luma_hi,
                    ext_node=vfx.fog_mask_ext_node, image_sock=depth_sock)
                if em is not None:
                    if fmask is not None:
                        relink(em, fmask)
                    else:
                        em.default_value = 1.0
                    if fmask is not None and getattr(vfx, "use_mask", False) and getattr(vfx, "mask_source", 'NONE') == 'FOG':
                        view_sock = fmask
            except Exception as e:
                print("VFX fog mask error:", e)

            scol = gi("Fog Color")
            if scol is not None:
                try:
                    r, g, b, a = vfx.fog_color
                    scol.default_value = (r, g, b, 1.0)
                except Exception:
                    pass

            oi = gnode.outputs.get("Image")
            if oi is not None:
                current = oi
                fog_done = True
                print(f"[VFX fog] assembled stack ({mix_index} over-nodes) "
                      f"-> fog group, depth source: "
                      f"{'Z' if depth_is_z else 'mist fallback'}")
            om = gnode.outputs.get("Mask")
            if om is not None and getattr(vfx, "use_mask", False) and getattr(vfx, "mask_source", 'NONE') == 'FOG':
                view_sock = om
        except Exception as e:
            import traceback
            print("VFX fog apply error:", e)
            traceback.print_exc()


    _PX_DOF = 1400
    _PX_GLARE = 1700
    _PX_LD = 2000
    _PX_GRADE = 2300
    _PX_OUT = 2600
    _PY = 0

    # -------------------------------------------------------------
    # CAMERA FOCUS (DOF): depth -> editable ColorRamp -> variable blur
    # Sharp at focus distance, smooth blur growth up to Far End.
    # -------------------------------------------------------------
    if getattr(vfx, "use_dof", False):
        try:
            _ensure_fogmap(nt, vfx, master)
            depth_b = _get_depth_socket(nt)
            if depth_b is None:
                _remove_nodes(nt, "VFX_DOF", "VFX_DOF_PRE")
            if depth_b is not None:
                pre = nt.nodes.get("VFX_DOF_PRE")
                if pre is not None and pre.type != 'GROUP':
                    nt.nodes.remove(pre)
                    pre = None
                if pre is None:
                    _ensure_dof_ramp_group()
                    for bid in ("CompositorNodeGroup", "ShaderNodeGroup", "NodeGroup"):
                        try:
                            pre = nt.nodes.new(bid)
                            break
                        except Exception:
                            continue
                    if pre is not None:
                        pre.name = "VFX_DOF_PRE"
                if pre is not None:
                    pre.node_tree = _ensure_dof_ramp_group()
                    pre.label = "DOF FOCUS RAMP (edit ramp!)"
                    pre.location = (_PX_DOF - 400, _PY + 240)
                    din = pre.inputs.get("Depth")
                    if din is not None:
                        for l in list(din.links):
                            nt.links.remove(l)
                        nt.links.new(depth_b, din)
                    far0 = vfx.dof_focus + max(vfx.dof_far_start, 0.01)
                    far1 = vfx.dof_focus + max(vfx.dof_far_end, far0 + 0.01)
                    for name, val in (("Focus", vfx.dof_focus), ("Far Start", far0),
                                      ("Far End", far1), ("Max Blur", vfx.dof_maxblur)):
                        s = pre.inputs.get(name)
                        if s is not None:
                            try:
                                s.default_value = val
                            except Exception:
                                pass
                    blur = nt.nodes.get("VFX_DOF")
                    if blur is not None and blur.type != 'BLUR':
                        nt.nodes.remove(blur)
                        blur = None
                    if blur is None:
                        try:
                            blur = nt.nodes.new("CompositorNodeBlur")
                            blur.name = "VFX_DOF"
                            blur.label = "DOF BLUR"
                        except Exception:
                            blur = None
                    if blur is not None:
                        blur.location = (_PX_DOF, _PY)
                        try:
                            blur.use_variable_size = True
                        except Exception:
                            pass
                        for attr in ("blur_method", "filter_type"):
                            _safe_set(blur, attr, 'GAUSS')
                        try:
                            blur.size_x = 64
                            blur.size_y = 64
                        except Exception:
                            pass
                        orig_in = current
                        img_in = blur.inputs.get("Image")
                        if img_in is None and blur.inputs:
                            img_in = blur.inputs[0]
                        if img_in is not None:
                            for l in list(img_in.links):
                                nt.links.remove(l)
                            nt.links.new(orig_in, img_in)
                        sz = blur.inputs.get("Size")
                        if sz is not None:
                            for l in list(sz.links):
                                nt.links.remove(l)
                            nt.links.new(pre.outputs.get("Blur"), sz)
                        blurred = blur.outputs[0] if blur.outputs else orig_in
                        msock = build_mask(
                            nt, vfx, "DOF", getattr(vfx, "dof_mask_source", 'NONE'),
                            vfx.dof_mask_invert, vfx.dof_mask_soft,
                            vfx.dof_mask_depth_start, vfx.dof_mask_depth_end,
                            vfx.dof_mask_luma_lo, vfx.dof_mask_luma_hi,
                            ext_node=vfx.dof_mask_ext_node, image_sock=orig_in,
                            depth_sock=depth_b)
                        current = _apply_mask(nt, "DOF", orig_in, blurred, msock, _PY)
                        if msock is not None and getattr(vfx, "use_mask", False) and getattr(vfx, "mask_source", 'NONE') == 'DOF':
                            view_sock = msock
        except Exception as e:
            print("VFX dof error:", e)
    else:
        _remove_nodes(nt, "VFX_DOF", "VFX_DOF_PRE")

    # ── GLOW / GLARE ──
    if getattr(vfx, "use_glare", False):
        try:
            gl = nt.nodes.get("VFX_GLARE")
            if gl is not None and gl.type != 'GLARE':
                nt.nodes.remove(gl)
                gl = None
            if gl is None:
                gl = _new_node(nt, "CompositorNodeGlare")
            if gl is not None:
                gl.name = "VFX_GLARE"
                gl.label = "GLARE"
                gl.location = (_PX_GLARE, _PY)

                # Glare TYPE is a MENU input socket, not a node property
                _set_glare_type(gl, vfx.glare_type)

                # Threshold, Size, Strength — all via input sockets
                _set_socket(gl, "Threshold", vfx.glare_threshold)
                _set_socket(gl, "Size", vfx.glare_size)
                _set_socket(gl, "Strength", vfx.glare_strength)

                orig_in = current
                if gl.inputs:
                    for l in list(gl.inputs[0].links):
                        nt.links.remove(l)
                    nt.links.new(orig_in, gl.inputs[0])
                glowed = gl.outputs[0] if gl.outputs else orig_in
                msock = build_mask(
                    nt, vfx, "GLARE", getattr(vfx, "glare_mask_source", 'NONE'),
                    vfx.glare_mask_invert, vfx.glare_mask_soft,
                    vfx.glare_mask_depth_start, vfx.glare_mask_depth_end,
                    vfx.glare_mask_luma_lo, vfx.glare_mask_luma_hi,
                    ext_node=vfx.glare_mask_ext_node, image_sock=orig_in)
                current = _apply_mask(nt, "GLARE", orig_in, glowed, msock, _PY - 150)
                if msock is not None and getattr(vfx, "use_mask", False) and getattr(vfx, "mask_source", 'NONE') == 'GLARE':
                    view_sock = msock
        except Exception as e:
            print("VFX glare error:", e)
    else:
        _remove_nodes(nt, "VFX_GLARE")

    # ── LENS DISTORTION ──
    if getattr(vfx, "use_lensdist", False):
        ld = nt.nodes.get("VFX_LENSDIST")
        if ld is not None and ld.type != 'LENSDIST':
            nt.nodes.remove(ld)
            ld = None
        if ld is None:
            try:
                ld = nt.nodes.new("CompositorNodeLensdist")
                ld.name = "VFX_LENSDIST"
                ld.label = "LENS DIST"
            except Exception:
                ld = None
        if ld is not None:
            ld.location = (_PX_LD, _PY)
            # Set by exact socket name so panel values always match the node
            for name, val in (("Distort", vfx.lensdist_distort), ("Dispersion", vfx.lensdist_disperse)):
                s = ld.inputs.get(name)
                if s is None:
                    for si in ld.inputs:
                        if si.name.lower() == name.lower():
                            s = si
                            break
                if s is not None:
                    try:
                        s.default_value = val
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
    else:
        _remove_nodes(nt, "VFX_LENSDIST")

    # -------------------------------------------------------------
    # MASTER GRADE: final color grade over the whole comp + mask
    # Uses the VFX_Grade engine (grade.py): m_exposure/temp/lift/gain/...
    # -------------------------------------------------------------
    if getattr(vfx, "m_grade_enable", False):
        try:
            gnode = ensure_master_grade(vfx, master, nt)
            if gnode is not None:
                gnode.label = "MASTER GRADE"
                gnode.location = (_PX_GRADE, _PY)
                orig_in = current
                img_in = None
                for s in gnode.inputs:
                    if s.type == 'RGBA' and s.name == "Image":
                        img_in = s
                        break
                if img_in is None:
                    for s in gnode.inputs:
                        if s.type == 'RGBA':
                            img_in = s
                            break
                if img_in is not None:
                    for l in list(img_in.links):
                        nt.links.remove(l)
                    nt.links.new(orig_in, img_in)
                graded = None
                for s in gnode.outputs:
                    if s.type == 'RGBA':
                        graded = s
                        break
                if graded is None:
                    graded = orig_in
                msock = build_mask(
                    nt, vfx, "GRADE", getattr(vfx, "grade_mask_source", 'NONE'),
                    vfx.grade_mask_invert, vfx.grade_mask_soft,
                    vfx.grade_mask_depth_start, vfx.grade_mask_depth_end,
                    vfx.grade_mask_luma_lo, vfx.grade_mask_luma_hi,
                    ext_node=vfx.grade_mask_ext_node, image_sock=orig_in)
                current = _apply_mask(nt, "GRADE", orig_in, graded, msock, _PY - 250)
                if msock is not None and getattr(vfx, "use_mask", False) and getattr(vfx, "mask_source", 'NONE') == 'GRADE':
                    view_sock = msock
        except Exception as e:
            print("VFX master grade error:", e)
    else:
        _remove_nodes(nt, "VFX_GRADE_MASTER")

    # Light Groups: combine LG outputs
    if getattr(vfx, "use_light_groups", False):
        try:
            add_light_group_output_nodes(vfx, master, nt)
        except Exception as e:
            print("VFX light groups error:", e)

    # Cryptomatte: feature removed

    # Color Match: plate matching node group
    if getattr(vfx, "use_color_match", False):
        try:
            from .colormatch import get_or_create_color_match_group, apply_preset
            cm_ng = get_or_create_color_match_group()
            preset = getattr(vfx, "color_match_preset", "NONE")
            strength = getattr(vfx, "color_match_strength", 1.0)
            if preset != 'NONE':
                apply_preset(cm_ng, preset, strength)

            cm_node = nt.nodes.get("VFX_COLORMATCH")
            if cm_node is not None and cm_node.type != 'GROUP':
                nt.nodes.remove(cm_node)
                cm_node = None
            if cm_node is None:
                for bid in ("CompositorNodeGroup", "ShaderNodeGroup", "NodeGroup"):
                    try:
                        cm_node = nt.nodes.new(bid)
                        break
                    except Exception:
                        continue
                if cm_node is not None:
                    cm_node.label = "COLOR MATCH"
                    cm_node["vfx_colormatch"] = 1
            if cm_node is not None:
                cm_node.node_tree = cm_ng
                cm_node.location = (_PX_GRADE - 300, _PY - 100)
                img_in = None
                for s in cm_node.inputs:
                    if s.type == 'RGBA':
                        img_in = s
                        break
                if img_in is not None:
                    for l in list(img_in.links):
                        nt.links.remove(l)
                    nt.links.new(current, img_in)
                for s in cm_node.outputs:
                    if s.type == 'RGBA':
                        current = s
                        break
        except Exception as e:
            print("VFX color match error:", e)

    # ── COMPOSITE OUTPUT ──
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
                comp.location = (_PX_OUT, _PY)
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
            gout.location = (_PX_OUT, _PY - 250)
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
    try:
        _self_check_masks(nt, vfx)
        occlusion_self_check(vfx, master)
    except Exception:
        pass


def _self_check_masks(nt, vfx):
    """Post-build self-check (printed to console):
    every masked effect has Mix(fac=mask), no MASK_* node hangs in the air."""
    problems = []
    lines = ["VFX self-check:"]
    for prefix, on, src in (("DOF", getattr(vfx, "use_dof", False), getattr(vfx, "dof_mask_source", 'NONE')),
                            ("GLARE", getattr(vfx, "use_glare", False), getattr(vfx, "glare_mask_source", 'NONE')),
                            ("GRADE", getattr(vfx, "m_grade_enable", False), getattr(vfx, "grade_mask_source", 'NONE'))):
        if not on:
            continue
        node = nt.nodes.get(f"VFX_MASKMIX_{prefix}")
        if src == 'NONE':
            ok = node is None
            lines.append(f"  {prefix}: no mask (ok={ok})")
            if not ok:
                problems.append(f"{prefix}: mask source NONE but VFX_MASKMIX_{prefix} still present")
        else:
            ok = node is not None and node.inputs[0].is_linked
            lines.append(f"  {prefix}: Mix(fac=MASK) linked={ok}")
            if not ok:
                problems.append(f"{prefix}: mask mix missing or unlinked")
    if getattr(vfx, "use_fog", False) and getattr(vfx, "fog_mask_source", 'NONE') != 'NONE':
        fg = nt.nodes.get("VFX_FOG_GROUP")
        em = fg.inputs.get("Extra Mask") if fg is not None else None
        ok = em is not None and em.is_linked
        lines.append(f"  FOG: Extra Mask linked={ok}")
        if not ok:
            problems.append("FOG: Extra Mask input unlinked")
    for layer in getattr(vfx, "layers", []):
        if getattr(layer, "grade_enable", False):
            src = getattr(layer, "grade_mask_source", 'NONE')
            use_am = getattr(layer, "use_alpha_mask", False)
            if src == 'NONE' and not use_am:
                continue
            node = nt.nodes.get(f"VFX_MASKMIX_L{layer.id}")
            ok = node is not None and node.inputs[0].is_linked
            lines.append(f"  L{layer.layer_name}: grade mask Mix linked={ok}")
            if not ok:
                problems.append(f"layer {layer.layer_name}: grade mask mix missing/unlinked")
    hanging = [n.name for n in nt.nodes
               if n.name.startswith("MASK_") and n.outputs
               and not any(o.is_linked for o in n.outputs)]
    if hanging:
        problems.append("hanging MASK nodes: " + ", ".join(hanging))
    if problems:
        for p in problems:
            lines.append("  PROBLEM: " + p)
    else:
        lines.append("  all mask chains OK")
    print("\n".join(lines))

    try:
        nt.update_tag()
    except Exception:
        pass
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()




# ---------------------------------------------------------------------
# FOG
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
        try:
            vl.use_pass_z = True
        except Exception:
            pass
    try:
        sc.render.film_transparent = True
    except Exception:
        pass
    try:
        sc.world.mist_settings.falloff = 'INVERSE_QUADRATIC'
    except Exception:
        pass
