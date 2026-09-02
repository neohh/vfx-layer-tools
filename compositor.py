"""VFX Layer Tools — compositor node tree, fog system."""

import bpy
import os

from .core import (
    ensure_root, ensure_camera_collection, link_collection_to_scene,
    create_empty_scene, sync_scene_settings,
)
from .materials import _trigger_comp
from .grade import ensure_layer_grades, ensure_master_grade


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
                print(f"VFX SEQ refresh: {img_name} files={len(files)}")
        return existing

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
    """Primary: load EXR sequence via ops.image.open for proper multi-file import."""
    img_name = f"VFX_SEQ_{scene_name}"
    abs_base = bpy.path.abspath(base_path)
    folder = os.path.join(abs_base, scene_name)

    files = []
    if os.path.isdir(folder):
        files = sorted([f for f in os.listdir(folder)
                        if f.lower().endswith('.exr')])
    if not files:
        print("VFX SEQ2: no EXR in", folder)
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
    """Remove nodes by name if they exist."""
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


def _cleanup_fog_nodes(nt):
    """Remove all VFX_FOG* nodes except VFX_FOG_GROUP (which is managed by build_comp_assembly)."""
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

    # If fog is OFF, remove the old fog group node entirely
    if not getattr(vfx, "use_fog", False):
        fg = nt.nodes.get("VFX_FOG_GROUP")
        if fg is not None:
            nt.nodes.remove(fg)

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

    # ── PER-LAYER GRADES ──
    grade_nodes = ensure_layer_grades(vfx, master, nt)
    if grade_nodes:
        print(f"VFX grades: {len(grade_nodes)} per-layer grade nodes created")

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
                    gnode.location = (500, 500)
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
                    # Always get source node for Alpha and other sockets
                    ln = nt.nodes.get(f"VFX_RL_{lid}")
                    # Use grade output if available, otherwise raw source
                    grade_n = grade_nodes.get(lid)
                    if grade_n is not None:
                        src_sock = grade_n.outputs.get("Image")
                    else:
                        src_sock = ln.outputs.get("Image") if ln else None
                    s = gi(f"OBJ_{lid}")
                    if s is not None and src_sock is not None:
                        relink(s, src_sock)
                    s = gi(f"AL_{lid}")
                    if s is not None and ln is not None and ln.outputs.get("Alpha"):
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
                if getattr(vfx, "use_mask", False) and getattr(vfx, "mask_source", 'NONE') == 'FOG':
                    om = gnode.outputs.get("Mask")
                    if om is not None:
                        view_sock = om
        except Exception as e:
            import traceback
            print("VFX fog apply error:", e)
            traceback.print_exc()

    # без тумана (или если группа не собралась): старая цепочка миксов
    if not fog_done:
        # Replace raw source sockets with grade outputs where available
        graded_sockets = []
        for layer, kind, sock in sockets:
            grade_n = grade_nodes.get(layer.id) if kind == 'OBJ' else None
            if grade_n is not None and grade_n.outputs.get("Image"):
                graded_sockets.append((layer, kind, grade_n.outputs["Image"]))
            else:
                graded_sockets.append((layer, kind, sock))
        if bg_sock is not None:
            current = bg_sock
            mix_list = graded_sockets
        else:
            current = graded_sockets[0][2]
            mix_list = graded_sockets[1:]

        mix_index = 0
        for layer, kind, sock in mix_list:
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

    # ── CRYPTOMATTE ──
    if getattr(vfx, "use_cryptomatte", False):
        try:
            from .cryptomatte import add_cryptomatte_nodes
            add_cryptomatte_nodes(vfx, master, nt)
        except Exception as e:
            print("VFX cryptomatte error:", e)

    # ── POST-EFFECTS: horizontal chain ──
    # Layout: x=800 sources/mix → x=1100 blur → x=1400 DOF → x=1700 glare → x=2000 lensdist → x=2300 output
    # Each effect: find-or-create (safe, no _remove_nodes before creation)

    _PX_SRC = 800    # source / mix column
    _PX_BLUR = 1100  # blur chain
    _PX_DOF = 1400   # depth of field
    _PX_GLARE = 1700 # glare / bloom
    _PX_LD = 2000    # lens distortion
    _PX_OUT = 2300   # composite / viewer
    _PY = 0          # main chain y

    # ── ATMOSPHERIC BLUR (ramp → math → blur, horizontal) ──
    if getattr(vfx, "use_blur", False):
        try:
            _ensure_fogmap(nt, vfx, master)
            mist_b = _get_mist_socket(nt)
            if mist_b is not None:
                # Ramp: remap mist → blur mask
                mr = nt.nodes.get("VFX_BLURRAMP")
                if mr is None:
                    mr = _new_node(nt, "CompositorNodeMapRange",
                                   "ShaderNodeMapRange")
                    if mr is not None:
                        mr.name = "VFX_BLURRAMP"
                        mr.label = "BLUR RAMP"
                if mr is not None:
                    mr.location = (_PX_BLUR - 200, _PY + 200)
                    try:
                        mr.interpolation_type = 'SMOOTHSTEP'
                    except Exception:
                        pass
                    v_in = mr.inputs.get("Value")
                    if v_in is not None:
                        for l in list(v_in.links):
                            nt.links.remove(l)
                        nt.links.new(mist_b, v_in)
                    _safe_set(mr, "From Min", vfx.blur_ramp_black)
                    _safe_set(mr, "From Max", vfx.blur_ramp_white)

                # Math: mask × blur_size
                bm = nt.nodes.get("VFX_BLURMATH")
                if bm is None:
                    bm = _new_node(nt, "CompositorNodeMath",
                                   "ShaderNodeMath")
                    if bm is not None:
                        bm.name = "VFX_BLURMATH"
                        bm.label = "BLUR SIZE"
                if bm is not None:
                    bm.location = (_PX_BLUR, _PY + 200)
                    bm.operation = 'MULTIPLY'
                    bm.inputs[1].default_value = vfx.blur_size
                    if mr is not None and mr.outputs.get("Result"):
                        for l in list(bm.inputs[0].links):
                            nt.links.remove(l)
                        nt.links.new(mr.outputs["Result"], bm.inputs[0])

                # Blur node
                bl = nt.nodes.get("VFX_BLUR")
                if bl is None:
                    try:
                        bl = nt.nodes.new("CompositorNodeBlur")
                        bl.name = "VFX_BLUR"
                        bl.label = "ATMO BLUR"
                    except Exception:
                        bl = None
                if bl is not None:
                    bl.location = (_PX_BLUR, _PY)
                    try:
                        bl.use_variable_size = True
                    except Exception:
                        pass
                    for attr in ("blur_method", "filter_type"):
                        _safe_set(bl, attr, 'GAUSS')
                    img_in = bl.inputs.get("Image")
                    if img_in is None and bl.inputs:
                        img_in = bl.inputs[0]
                    if img_in is not None:
                        for l in list(img_in.links):
                            nt.links.remove(l)
                        nt.links.new(current, img_in)
                    size_in = bl.inputs.get("Size")
                    if size_in is not None and bm is not None:
                        for l in list(size_in.links):
                            nt.links.remove(l)
                        nt.links.new(bm.outputs[0], size_in)
                    if bl.outputs:
                        current = bl.outputs[0]
                    # Mask routing: show blur mask if selected
                    if getattr(vfx, 'use_mask', False) and getattr(vfx, 'mask_source', 'NONE') == 'BLUR' \
                            and mr is not None:
                        view_sock = mr.outputs.get("Result")
        except Exception as e:
            print("VFX blur error:", e)
    else:
        # Clean up blur nodes when disabled
        _remove_nodes(nt, "VFX_BLUR", "VFX_BLURRAMP", "VFX_BLURMATH")

    # ── CAMERA DOF ──
    if getattr(vfx, "use_dof", False):
        try:
            _ensure_fogmap(nt, vfx, master)
            fmn = nt.nodes.get("VFX_RL_FOGMAP")
            if fmn is not None and fmn.outputs.get("Depth"):
                df = nt.nodes.get("VFX_DOF")
                if df is None:
                    try:
                        df = nt.nodes.new("CompositorNodeDefocus")
                        df.name = "VFX_DOF"
                        df.label = "CAMERA DOF"
                    except Exception:
                        df = None
                if df is not None:
                    df.location = (_PX_DOF, _PY)
                    for attr, val in (("fstop", vfx.dof_fstop),
                                      ("f_stop", vfx.dof_fstop)):
                        _safe_set(df, attr, val)
                    for attr, val in (("focal_distance", vfx.dof_focus),
                                      ("focus_distance", vfx.dof_focus),
                                      ("distance", vfx.dof_focus)):
                        _safe_set(df, attr, val)
                    for attr, val in (("blur_max", vfx.dof_maxblur),
                                      ("max_blur", vfx.dof_maxblur)):
                        _safe_set(df, attr, val)
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
                    # Mask routing: show depth mask if selected
                    if getattr(vfx, 'use_mask', False) and getattr(vfx, 'mask_source', 'NONE') == 'DOF' \
                            and fmn is not None:
                        view_sock = fmn.outputs.get("Depth")
        except Exception as e:
            print("VFX dof error:", e)
    else:
        _remove_nodes(nt, "VFX_DOF")

    # ── GLOW / GLARE (always recreate for guaranteed sync) ──
    print(f"VFX glare check: use={getattr(vfx, 'use_glare', False)} type={getattr(vfx, 'glare_type', '?')}")
    if getattr(vfx, "use_glare", False):
        old_gl = nt.nodes.get("VFX_GLARE")
        if old_gl is not None:
            nt.nodes.remove(old_gl)
        gl = _new_node(nt, "CompositorNodeGlare")
        print(f"VFX glare: node={gl}")
        if gl is not None:
            gl.name = "VFX_GLARE"
            gl.label = "GLARE"
            gl.location = (_PX_GLARE, _PY)
            # Type: try every attr × value combo
            type_set = False
            for attr in ("glare_type", "type", "mode"):
                for val in (vfx.glare_type, vfx.glare_type.lower()):
                    try:
                        setattr(gl, attr, val)
                        print(f"VFX glare: set {attr}={val} OK")
                        type_set = True
                        break
                    except Exception as exc:
                        print(f"VFX glare: {attr}={val} FAILED: {exc}")
                if type_set:
                    break
            # Properties via attr
            _safe_set(gl, "threshold", vfx.glare_threshold)
            _safe_set(gl, "size", vfx.glare_size)
            mix_val = 1.0 - (vfx.glare_strength / 2.5)
            mix_val = max(-1.0, min(1.0, mix_val))
            _safe_set(gl, "mix", mix_val)
            # Connect image input
            if gl.inputs:
                nt.links.new(current, gl.inputs[0])
                print(f"VFX glare: connected input from current")
            if gl.outputs:
                current = gl.outputs[0]
        else:
            print("VFX glare: FAILED to create node!")
    else:
        _remove_nodes(nt, "VFX_GLARE")

    # ── LENS DISTORTION ──
    if getattr(vfx, "use_lensdist", False):
        ld = nt.nodes.get("VFX_LENSDIST")
        if ld is None:
            try:
                ld = nt.nodes.new("CompositorNodeLensdist")
                ld.name = "VFX_LENSDIST"
                ld.label = "LENS DIST"
            except Exception:
                ld = None
        if ld is not None:
            ld.location = (_PX_LD, _PY)
            _safe_set(ld, "distort", vfx.lensdist_distort)
            _safe_set(ld, "dispersion", vfx.lensdist_disperse)
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

    # ── MASTER GRADE ──
    mg = ensure_master_grade(vfx, master, nt)
    if mg is not None:
        mg_in = mg.inputs.get("Image")
        if mg_in is not None and current is not None:
            for l in list(mg_in.links):
                nt.links.remove(l)
            nt.links.new(current, mg_in)
            print("VFX master grade: CONNECTED")
        mg_out = mg.outputs.get("Image")
        if mg_out is not None:
            current = mg_out

    # ── COLOR MATCH (creative look, after master grade) ──
    print(f"VFX color match check: use={getattr(vfx, 'use_color_match', False)} preset={getattr(vfx, 'color_match_preset', 'NONE')}")
    if getattr(vfx, "use_color_match", False) and vfx.color_match_preset != 'NONE':
        try:
            from .colormatch import get_or_create_color_match_group, apply_preset
            cm_ng = get_or_create_color_match_group()
            print(f"VFX color match: node_group={cm_ng}")
            if cm_ng is not None:
                apply_preset(cm_ng, vfx.color_match_preset, vfx.color_match_strength)
                cm_node = nt.nodes.get("VFX_COLORMATCH")
                if cm_node is None:
                    for bid in ("CompositorNodeGroup", "ShaderNodeGroup", "NodeGroup"):
                        try:
                            cm_node = nt.nodes.new(bid)
                            print(f"VFX color match: created node via {bid} = {cm_node}")
                            break
                        except Exception as exc:
                            print(f"VFX color match: {bid} failed: {exc}")
                            continue
                    if cm_node is not None:
                        cm_node.name = "VFX_COLORMATCH"
                if cm_node is not None:
                    cm_node.label = "COLOR MATCH"
                    cm_node.node_tree = cm_ng
                    cm_node.location = (2400, 0)
                    cm_in_img = cm_node.inputs.get("Image")
                    cm_in_str = cm_node.inputs.get("Strength")
                    cm_out = cm_node.outputs.get("Image")
                    print(f"VFX color match: img_in={cm_in_img} str_in={cm_in_str} out={cm_out}")
                    if cm_in_img is not None and cm_out is not None:
                        for l in list(cm_in_img.links):
                            nt.links.remove(l)
                        nt.links.new(current, cm_in_img)
                        if cm_in_str is not None:
                            cm_in_str.default_value = vfx.color_match_strength
                        current = cm_out
                        print("VFX color match: CONNECTED OK")
                    else:
                        print("VFX color match: FAILED to find sockets!")
                else:
                    print("VFX color match: FAILED to create node!")
            else:
                print("VFX color match: FAILED to get/create node group!")
        except Exception as e:
            import traceback
            print("VFX color match error:", e)
            traceback.print_exc()

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

