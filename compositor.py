"""VFX Layer Tools — compositor node tree, fog system."""

import bpy
import os
import re

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
    for attr in ("compositing_node_group", "node_tree", "compositor_node_tree", "compositing_node_tree"):
        tree = getattr(master, attr, None)
        if tree is not None:
            if tree.name.startswith("VFX_SHD_Comp") or (tree.name.startswith("VFX_") and tree.name != "VFX_Compositor"):
                continue
            return tree

    for attr in dir(master):
        low = attr.lower()
        if "node" in low or "comp" in low:
            try:
                val = getattr(master, attr)
            except Exception:
                continue
            if isinstance(val, bpy.types.NodeTree):
                if val.name.startswith("VFX_SHD_Comp") or (val.name.startswith("VFX_") and val.name != "VFX_Compositor"):
                    continue
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

    for attr in ("compositing_node_group", "node_tree", "compositor_node_tree", "compositing_node_tree"):
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

def _get_folder_sequence_files(folder):
    """Find image sequence files in folder, grouped by extension.
    Never mixes extensions. Selects the extension with the largest number of frames.
    Returns (files_sorted, ext, start_frame).
    """
    if not os.path.isdir(folder):
        return [], "", 1
    by_ext = {}
    for f in os.listdir(folder):
        p = os.path.join(folder, f)
        if not os.path.isfile(p):
            continue
        ext = os.path.splitext(f)[1].lower()
        if ext in ('.exr', '.png', '.tif', '.tiff'):
            by_ext.setdefault(ext, []).append(f)
    if not by_ext:
        return [], "", 1
    # Pick the extension with the largest number of frames (complete rendered sequence)
    best_ext = max(by_ext.keys(), key=lambda e: len(by_ext[e]))
    files = sorted(by_ext[best_ext])
    start_frame = 1
    if files:
        base = os.path.splitext(files[0])[0]
        matches = re.findall(r'\d+', base)
        if matches:
            try:
                start_frame = int(matches[-1])
            except Exception:
                pass
    return files, best_ext, start_frame


def _load_sequence_image(scene_name, base_path):
    img_name = f"VFX_SEQ_{scene_name}"
    abs_base = bpy.path.abspath(base_path)
    folder = os.path.join(abs_base, scene_name)

    files, ext, start_frame = _get_folder_sequence_files(folder)

    existing = bpy.data.images.get(img_name)
    if existing is not None:
        if files:
            existing.source = 'SEQUENCE'
            try:
                existing.filepath_raw = os.path.join(folder, files[0])
                existing.filepath = existing.filepath_raw
                existing.reload()
            except Exception:
                pass
            existing["vfx_files_count"] = max(1, len(files))
            existing["vfx_start_frame"] = start_frame
        return existing

    if not files:
        return None

    first_file = os.path.join(folder, files[0])
    try:
        img = bpy.data.images.load(first_file, check_existing=False)
    except Exception as e:
        print(f"VFX: load failed {first_file}: {e}")
        return None

    img.name = img_name
    img.source = 'SEQUENCE'
    try:
        img.filepath_raw = os.path.join(folder, f"####{ext}")
        img.filepath = img.filepath_raw
        img.frame_start = start_frame
        img.frame_offset = 0
        img.frame_duration = max(1, len(files))
        img.reload()
    except Exception:
        pass
    img["vfx_files_count"] = max(1, len(files))
    img["vfx_start_frame"] = start_frame

    return img


def _load_sequence_image2(scene_name, base_path):
    """Primary: load EXR/image sequence via ops.image.open for proper multi-file import."""
    img_name = f"VFX_SEQ_{scene_name}"
    abs_base = bpy.path.abspath(base_path)
    folder = os.path.join(abs_base, scene_name)

    files, ext, start_frame = _get_folder_sequence_files(folder)
    if not files:
        return _load_sequence_image(scene_name, base_path)

    existing = bpy.data.images.get(img_name)
    if existing is not None:
        ex_dir = ""
        try:
            ex_dir = os.path.dirname(bpy.path.abspath(existing.filepath))
        except Exception:
            pass
        same_folder = (
            os.path.normcase(os.path.normpath(ex_dir))
            == os.path.normcase(os.path.normpath(folder))
        )
        if same_folder:
            first_file = os.path.join(folder, files[0])
            try:
                existing.filepath = first_file
                existing.filepath_raw = first_file
            except Exception:
                pass
            existing.source = 'SEQUENCE'
            try:
                existing.reload()
            except Exception:
                pass
            try:
                existing["vfx_files_count"] = max(1, len(files))
                existing["vfx_start_frame"] = start_frame
            except Exception:
                pass
            return existing
        try:
            bpy.data.images.remove(existing)
        except Exception:
            pass

    win = None
    if bpy.context.window_manager and bpy.context.window_manager.windows:
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
    img.source = 'SEQUENCE'
    try:
        img["vfx_files_count"] = max(1, len(files))
        img["vfx_start_frame"] = start_frame
    except Exception:
        pass

    return img


def _configure_image_node_sequence(node, img, is_static=False, frame_start=None, frame_offset=None):
    """Configure CompositorNodeImage to correctly play an image sequence or hold a single frame."""
    if node is None or img is None:
        return
    node.image = img

    dur = None
    st = None
    try:
        fpath = bpy.path.abspath(img.filepath)
        fdir = os.path.dirname(fpath) if not os.path.isdir(fpath) else fpath
        files, _, start_f = _get_folder_sequence_files(fdir)
        if files:
            dur = len(files)
            st = start_f
    except Exception:
        pass

    if dur is None:
        dur = img.get("vfx_files_count") if hasattr(img, "get") else None
    if dur is None:
        dur = getattr(img, "frame_duration", 1)
    if st is None:
        st = img.get("vfx_start_frame") if hasattr(img, "get") else None
    if st is None:
        st = getattr(img, "frame_start", 1)

    # Sanitize st (guard against overflow or corrupted digits like 83310000)
    try:
        st = int(st)
        if st > 100000 or st < -10000:
            st = 1
    except Exception:
        st = 1

    if frame_start is not None:
        try:
            st = int(frame_start)
        except Exception:
            pass

    foff = 0
    if frame_offset is not None:
        try:
            foff = int(frame_offset)
        except Exception:
            foff = 0

    try:
        img.source = 'SEQUENCE'
    except Exception:
        pass

    for target in (node, getattr(node, "image_user", None)):
        if target is None:
            continue
        try:
            target.frame_duration = 1 if is_static else max(1, int(dur))
        except Exception:
            pass
        try:
            target.frame_start = 1 if is_static else int(st)
        except Exception:
            pass
        try:
            target.frame_offset = foff
        except Exception:
            pass
        try:
            target.use_cyclic = is_static
        except Exception:
            pass
        try:
            target.use_auto_refresh = True
        except Exception:
            pass


# ---------------------------------------------------------------------
# EXTERNAL HOLDOUT MATTE SEQUENCE (LAYER CUTOUT)
# ---------------------------------------------------------------------

def get_matte_sequence_info(folder_or_path):
    """Return (files_count, start_frame, first_file_name, status_str)"""
    if not folder_or_path:
        return 0, 1, "", "Папка не указана"
    try:
        abs_path = bpy.path.abspath(folder_or_path)
    except Exception:
        abs_path = folder_or_path

    if os.path.isfile(abs_path):
        folder = os.path.dirname(abs_path)
    elif os.path.isdir(abs_path):
        folder = abs_path
    else:
        return 0, 1, "", "Папка не существует"

    files, ext, start_f = _get_folder_sequence_files(folder)
    if not files:
        return 0, 1, "", "PNG файлы не найдены"
    return len(files), start_f, files[0], f"Найдено: {len(files)} кадров ({ext})"


def load_matte_sequence_image(folder_or_path, name_prefix="VFX_MATTE"):
    """Load an image sequence from an arbitrary folder or file path.
    Returns (bpy.types.Image or None, files_count, start_frame).
    """
    if not folder_or_path:
        return None, 0, 1

    try:
        abs_path = bpy.path.abspath(folder_or_path)
    except Exception:
        abs_path = folder_or_path

    if os.path.isfile(abs_path):
        folder = os.path.dirname(abs_path)
    elif os.path.isdir(abs_path):
        folder = abs_path
    else:
        folder = abs_path
        if not os.path.exists(folder):
            return None, 0, 1

    files, ext, start_frame = _get_folder_sequence_files(folder)
    if not files:
        return None, 0, 1

    try:
        start_frame = int(start_frame)
        if start_frame > 100000 or start_frame < -10000:
            start_frame = 1
    except Exception:
        start_frame = 1

    img_name = f"{name_prefix}"

    # Check existing image datablock
    existing = bpy.data.images.get(img_name)
    if existing is not None:
        try:
            ex_path = bpy.path.abspath(existing.filepath)
            ex_dir = os.path.dirname(ex_path) if os.path.isfile(ex_path) else ex_path
            if os.path.normcase(os.path.normpath(ex_dir)) == os.path.normcase(os.path.normpath(folder)):
                first_file = os.path.join(folder, files[0])
                existing.filepath = first_file
                existing.filepath_raw = first_file
                existing.source = 'SEQUENCE'
                existing.reload()
                existing["vfx_files_count"] = len(files)
                existing["vfx_start_frame"] = start_frame
                return existing, len(files), start_frame
        except Exception:
            pass
        try:
            bpy.data.images.remove(existing)
        except Exception:
            pass

    # Open sequence via ops.image.open or load
    win = None
    if bpy.context.window_manager and bpy.context.window_manager.windows:
        win = bpy.context.window_manager.windows[0]

    img = None
    before = set(bpy.data.images.keys())
    try:
        with bpy.context.temp_override(window=win):
            bpy.ops.image.open(
                directory=folder + os.sep,
                files=[{"name": f} for f in files],
                check_existing=False,
                relative_path=False,
            )
        new_imgs = [bpy.data.images[k] for k in (set(bpy.data.images.keys()) - before)]
        if new_imgs:
            img = new_imgs[0]
    except Exception as e:
        print(f"VFX: matte ops open failed: {e}")

    if img is None:
        first_file = os.path.join(folder, files[0])
        try:
            img = bpy.data.images.load(first_file, check_existing=False)
        except Exception as e:
            print(f"VFX: matte load failed: {e}")
            return None, 0, 1

    img.name = img_name
    img.source = 'SEQUENCE'
    try:
        img.frame_start = start_frame
        img.frame_offset = 0
        img.frame_duration = len(files)
        img["vfx_files_count"] = len(files)
        img["vfx_start_frame"] = start_frame
        img.reload()
    except Exception:
        pass

    return img, len(files), start_frame


def cleanup_layer_matte_nodes(nt, layer_id, is_shadow=None):
    """Remove holdout matte nodes and/or node group for the given layer ID."""
    if nt is not None:
        targets = []
        if is_shadow is None or not is_shadow:
            targets.append(f"VFX_MATTE_{layer_id}")
            targets.extend([
                f"VFX_MATTE_IMG_{layer_id}",
                f"VFX_MATTE_SCALE_{layer_id}",
                f"VFX_MATTE_BW_{layer_id}",
                f"VFX_MATTE_SEP_{layer_id}",
                f"VFX_MATTE_CHOKE_{layer_id}",
                f"VFX_MATTE_FEATHER_{layer_id}",
                f"VFX_MATTE_INV_{layer_id}",
                f"VFX_MATTE_SEP_LAYER_{layer_id}",
                f"VFX_MATTE_MUL_A_{layer_id}",
                f"VFX_MATTE_MUL_RGB_{layer_id}",
                f"VFX_MATTE_SA_{layer_id}",
            ])
        if is_shadow is None or is_shadow:
            targets.append(f"VFX_MATTE_SHD_{layer_id}")
            targets.extend([
                f"VFX_MATTE_INV_SHD_{layer_id}",
                f"VFX_MATTE_SEP_LAYER_SHD_{layer_id}",
                f"VFX_MATTE_MUL_A_SHD_{layer_id}",
                f"VFX_MATTE_MUL_RGB_SHD_{layer_id}",
                f"VFX_MATTE_SA_SHD_{layer_id}",
            ])

        for name in targets:
            n = nt.nodes.get(name)
            if n:
                try:
                    nt.nodes.remove(n)
                except Exception:
                    pass

    # If neither OBJ nor SHD matte nodes remain in nt, clean up the Node Group data block
    group_name = f"VFX_Matte_{layer_id}"
    ng = bpy.data.node_groups.get(group_name)
    if ng is not None:
        has_users = False
        if nt is not None:
            if nt.nodes.get(f"VFX_MATTE_{layer_id}") or nt.nodes.get(f"VFX_MATTE_SHD_{layer_id}"):
                has_users = True
        if not has_users:
            try:
                bpy.data.node_groups.remove(ng)
            except Exception:
                pass



def cleanup_shadow_filter_nodes(nt, layer_id):
    """Remove shadow filter nodes for the given layer ID."""
    if nt is None:
        return
    for name in (f"VFX_SHD_RAMP_{layer_id}", f"VFX_SHD_BLUR_{layer_id}"):
        n = nt.nodes.get(name)
        if n:
            try:
                nt.nodes.remove(n)
            except Exception:
                pass


def apply_shadow_filter(nt, layer, sh_node, loc_x=550, loc_y=0):
    """Filter and shape the layer shadow with ColorRamp and optional blur.
    Returns the processed output socket (NodeSocketColor / RGBA).
    """
    if nt is None or sh_node is None:
        return None

    alpha_sock = sh_node.outputs.get("Alpha")
    img_sock = sh_node.outputs.get("Image")
    fac_sock = alpha_sock if alpha_sock is not None else img_sock
    base_sock = img_sock if img_sock is not None else alpha_sock

    use_ramp = getattr(layer, "shadow_use_ramp", True)
    ramp_node_name = f"VFX_SHD_RAMP_{layer.id}"
    blur_node_name = f"VFX_SHD_BLUR_{layer.id}"

    current_sock = base_sock

    if use_ramp and fac_sock is not None:
        ramp = nt.nodes.get(ramp_node_name)
        if ramp is not None and ramp.type != 'VALTORGB':
            try:
                nt.nodes.remove(ramp)
            except Exception:
                pass
            ramp = None
        if ramp is None:
            try:
                ramp = nt.nodes.new("ShaderNodeValToRGB")
                ramp.name = ramp_node_name
            except Exception:
                ramp = None

        if ramp is not None:
            ramp.label = f"{layer.layer_name} SHD Ramp"
            ramp.location = (loc_x, loc_y)

            col = getattr(layer, "shadow_color", (0.0, 0.0, 0.0, 1.0))
            r, g, b = float(col[0]), float(col[1]), float(col[2])
            a = float(col[3]) if len(col) > 3 else 1.0

            pos_black = max(0.0, min(0.99, float(getattr(layer, "shadow_ramp_black", 0.0))))
            pos_white = max(pos_black + 0.005, min(1.0, float(getattr(layer, "shadow_ramp_white", 1.0))))

            try:
                cr = ramp.color_ramp
                if len(cr.elements) >= 2:
                    e0 = cr.elements[0]
                    e1 = cr.elements[-1]
                    e0.position = pos_black
                    e0.color = (r, g, b, 0.0)
                    e1.position = pos_white
                    e1.color = (r, g, b, a)
            except Exception as e:
                print(f"VFX: error configuring shadow ramp: {e}")

            fac_in = ramp.inputs.get("Fac")
            if fac_in is not None:
                if not any(link.from_socket == fac_sock for link in fac_in.links):
                    for l in list(fac_in.links):
                        nt.links.remove(l)
                    nt.links.new(fac_sock, fac_in)

            current_sock = ramp.outputs.get("Color") or ramp.outputs[0]
    else:
        old_ramp = nt.nodes.get(ramp_node_name)
        if old_ramp is not None:
            try:
                nt.nodes.remove(old_ramp)
            except Exception:
                pass

    b_px = int(getattr(layer, "shadow_blur", 0))
    if b_px > 0 and current_sock is not None:
        blur = nt.nodes.get(blur_node_name)
        if blur is not None and blur.type != 'BLUR':
            try:
                nt.nodes.remove(blur)
            except Exception:
                pass
            blur = None
        if blur is None:
            try:
                blur = nt.nodes.new("CompositorNodeBlur")
                blur.name = blur_node_name
            except Exception:
                blur = None
        if blur is not None:
            blur.label = f"{layer.layer_name} SHD Blur"
            blur.location = (loc_x + 220, loc_y)
            _setup_blur_node(blur, b_px, b_px)

            bin_img = blur.inputs.get("Image") or blur.inputs[0]
            if not any(link.from_socket == current_sock for link in bin_img.links):
                for l in list(bin_img.links):
                    nt.links.remove(l)
                nt.links.new(current_sock, bin_img)

            current_sock = blur.outputs.get("Image") or blur.outputs[0]
    else:
        old_blur = nt.nodes.get(blur_node_name)
        if old_blur is not None:
            try:
                nt.nodes.remove(old_blur)
            except Exception:
                pass

    return current_sock


def _setup_dilate_erode_node(node, distance=0, mode='Distance'):
    """Configure CompositorNodeDilateErode across Blender 4.x and 5.x."""
    if node is None:
        return
    # Blender 5.x socket inputs
    if hasattr(node, "inputs"):
        tin = node.inputs.get("Type")
        if tin is not None:
            try:
                tin.default_value = 'Distance' if mode.lower() == 'distance' else 'Steps'
            except Exception:
                pass
        sin = node.inputs.get("Size")
        if sin is not None:
            try:
                sin.default_value = int(distance)
            except Exception:
                pass
    # Older Blender attributes
    if hasattr(node, "mode"):
        try:
            node.mode = 'DISTANCE' if mode.lower() == 'distance' else 'STEP'
        except Exception:
            pass
    if hasattr(node, "distance"):
        try:
            node.distance = int(distance)
        except Exception:
            pass


def _setup_scale_node(scale_node, frame_method='STRETCH'):
    """Safely configure CompositorNodeScale across Blender 4.x and 5.x."""
    if scale_node is None:
        return
    m_val = 'Stretch'
    if str(frame_method).upper() == 'FIT':
        m_val = 'Fit'
    elif str(frame_method).upper() == 'CROP':
        m_val = 'Crop'

    # Blender 5.x socket inputs
    if hasattr(scale_node, "inputs"):
        tin = scale_node.inputs.get("Type")
        if tin is not None:
            try:
                tin.default_value = 'Render Size'
            except Exception:
                pass
        ftin = scale_node.inputs.get("Frame Type")
        if ftin is not None:
            try:
                ftin.default_value = m_val
            except Exception:
                pass
    # Older Blender attributes
    if hasattr(scale_node, "space"):
        try:
            scale_node.space = 'RENDER_SIZE'
        except Exception:
            pass
    if hasattr(scale_node, "frame_method"):
        try:
            scale_node.frame_method = m_val.upper()
        except Exception:
            pass


def _setup_blur_node(blur_node, size_x, size_y=None, filter_type='GAUSS'):
    """Safely configure CompositorNodeBlur across Blender 4.x and 5.x."""
    if blur_node is None:
        return
    sy = size_x if size_y is None else size_y
    # Blender 5.x socket inputs
    if hasattr(blur_node, "inputs"):
        sin = blur_node.inputs.get("Size")
        if sin is not None:
            try:
                sin.default_value = (float(size_x), float(sy))
            except Exception:
                try:
                    sin.default_value = (float(size_x), float(sy), 0.0)
                except Exception:
                    pass
        tin = blur_node.inputs.get("Type")
        if tin is not None:
            try:
                tin.default_value = 'Gaussian'
            except Exception:
                pass
    # Older Blender attributes
    if hasattr(blur_node, "filter_type"):
        try:
            blur_node.filter_type = filter_type
        except Exception:
            pass
    if hasattr(blur_node, "size_x"):
        try:
            blur_node.size_x = float(size_x)
        except Exception:
            pass
    if hasattr(blur_node, "size_y"):
        try:
            blur_node.size_y = float(sy)
        except Exception:
            pass


def _make_mix_node(nt, name, label, blend_mode='MULTIPLY', location=(0, 0)):
    """Create a ShaderNodeMix (Blender 5+) or CompositorNodeMixRGB node safely."""
    mix = _new_node(nt, "ShaderNodeMix", "CompositorNodeMixRGB")
    if mix is None:
        return None, None, None, None, None
    mix.name = name
    mix.label = label
    mix.location = location
    for attr in ("blend_type", "blend_mode"):
        try:
            setattr(mix, attr, blend_mode)
            break
        except Exception:
            pass

    if getattr(mix, "bl_idname", "") == 'ShaderNodeMix':
        try:
            mix.data_type = 'RGBA'
        except Exception:
            pass
        fac_in = next((s for s in mix.inputs if getattr(s, "identifier", "") == "Factor_Float" or (s.type == 'VALUE' and s.name in ("Factor", "Fac"))), mix.inputs[0] if mix.inputs else None)
        a_in = next((s for s in mix.inputs if getattr(s, "identifier", "") == "A_Color" or (s.type in ('RGBA', 'COLOR') and s.name == 'A')), None)
        b_in = next((s for s in mix.inputs if getattr(s, "identifier", "") == "B_Color" or (s.type in ('RGBA', 'COLOR') and s.name == 'B')), None)
        out_s = next((s for s in mix.outputs if getattr(s, "identifier", "") == "Result_Color" or (s.type in ('RGBA', 'COLOR') and s.name == 'Result')), mix.outputs[0] if mix.outputs else None)
    else:  # CompositorNodeMixRGB
        fac_in = mix.inputs.get("Fac") or mix.inputs.get("Factor") or (mix.inputs[0] if mix.inputs else None)
        a_in = mix.inputs.get("Image1") or mix.inputs.get("Color1") or (mix.inputs[1] if len(mix.inputs) > 1 else None)
        b_in = mix.inputs.get("Image2") or mix.inputs.get("Color2") or (mix.inputs[2] if len(mix.inputs) > 2 else None)
        out_s = mix.outputs.get("Image") or (mix.outputs[0] if mix.outputs else None)

    return mix, a_in, b_in, fac_in, out_s


def _enable_lensdist_fit(node):
    """Ensure the 'Fit' option is enabled on a CompositorNodeLensdist node,
    supporting both property and socket interfaces across Blender versions.
    """
    if node is None:
        return
    if hasattr(node, "use_fit"):
        try:
            node.use_fit = True
        except Exception:
            pass
    fit_s = node.inputs.get("Fit") if hasattr(node, "inputs") else None
    if fit_s is None and hasattr(node, "inputs"):
        for si in node.inputs:
            if "fit" in si.name.lower():
                fit_s = si
                break
    if fit_s is not None:
        try:
            fit_s.default_value = True
        except Exception:
            pass


def get_or_create_layer_matte_group(layer, vfx=None):
    """Create or update a self-contained Node Group for holdout matte cutout.
    Interface:
      Input:
        Image (NodeSocketColor) - The layer or shadow image stream to be masked
      Output:
        Image (NodeSocketColor) - Masked cutout image stream
        Matte (NodeSocketFloat) - Black-and-white holdout mask (for inspection / compositing)
    """
    path = getattr(layer, "holdout_matte_path", "")
    if not path:
        return None

    img, num_files, start_frame = load_matte_sequence_image(path, name_prefix=f"VFX_MATTE_SEQ_{layer.id}")
    if img is None:
        return None

    group_name = f"VFX_Matte_{layer.id}"
    ng = bpy.data.node_groups.get(group_name)
    if ng is None:
        ng = bpy.data.node_groups.new(group_name, 'CompositorNodeTree')

    # Interface setup - preserve existing interface to avoid breaking external node group links!
    need_interface = True
    if hasattr(ng, "interface") and hasattr(ng.interface, "items_tree"):
        in_socks = [s.name for s in ng.interface.items_tree if getattr(s, "in_out", None) == 'INPUT']
        out_socks = [s.name for s in ng.interface.items_tree if getattr(s, "in_out", None) == 'OUTPUT']
        if "Image" in in_socks and "Image" in out_socks and "Matte" in out_socks:
            need_interface = False

    if need_interface and hasattr(ng, "interface"):
        try:
            for item in list(ng.interface.items_tree):
                ng.interface.remove(item)
        except Exception:
            pass
        try:
            ng.interface.new_socket("Image", in_out='INPUT', socket_type='NodeSocketColor')
            ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
            ng.interface.new_socket("Matte", in_out='OUTPUT', socket_type='NodeSocketFloat')
        except Exception as e:
            print(f"VFX: error creating matte group interface sockets: {e}")

    if vfx is None:
        vfx = getattr(getattr(layer, "id_data", None), "vfx", None)

    use_ld = bool(getattr(vfx, "use_lensdist", False)) if vfx else False
    undistort_matte = bool(getattr(layer, "holdout_matte_undistort", True)) and use_ld
    inv_distort = -float(getattr(vfx, "lensdist_distort", 0.0)) if undistort_matte else 0.0

    fstart = getattr(layer, "holdout_matte_frame_start", 1)
    foffset = getattr(layer, "holdout_matte_frame_offset", 0)
    choke = int(getattr(layer, "holdout_matte_choke", 0))
    feather = float(getattr(layer, "holdout_matte_feather", 0.0))
    invert = getattr(layer, "holdout_matte_invert", False)
    mode = getattr(layer, "holdout_matte_mode", 'LUMA')

    sig = f"{path}_{fstart}_{foffset}_{choke}_{feather}_{invert}_{mode}_{num_files}_{undistort_matte}_{inv_distort:.5f}_gate_v3"
    if ng.get("vfx_matte_sig") == sig and len(ng.nodes) >= 12:
        return ng

    ng.nodes.clear()

    gin = ng.nodes.new("NodeGroupInput")
    gin.location = (-750, 0)
    gout = ng.nodes.new("NodeGroupOutput")
    gout.location = (1750, 0)

    # 1. Matte image sequence
    img_node = ng.nodes.new("CompositorNodeImage")
    img_node.name = "Matte_Image"
    img_node.label = f"Matte Seq ({num_files} frames)"
    img_node.location = (-550, 200)
    _configure_image_node_sequence(img_node, img, is_static=False, frame_start=fstart, frame_offset=foffset)

    # 2. Scale node (match canvas resolution)
    scale_node = _new_node(ng, "CompositorNodeScale")
    if scale_node is not None:
        scale_node.name = "Matte_Scale"
        scale_node.label = "Scale to Canvas"
        scale_node.location = (-350, 200)
        _setup_scale_node(scale_node)
        ng.links.new(img_node.outputs["Image"], scale_node.inputs[0])
        matte_color_out = scale_node.outputs.get("Image") or img_node.outputs.get("Image")
    else:
        matte_color_out = img_node.outputs.get("Image")

    # 2.5. Undistort node (Match undistorted background plate)
    if undistort_matte and abs(inv_distort) > 1e-6:
        dist_node = _new_node(ng, "CompositorNodeLensdist")
        if dist_node is not None:
            dist_node.name = "Matte_Undistort"
            dist_node.label = f"Undistort Matte ({inv_distort:+.3f})"
            dist_node.location = (-150, 200)

            for target_key, val in (("distort", inv_distort), ("dispersion", 0.0)):
                s = None
                for si in dist_node.inputs:
                    if target_key in si.name.lower():
                        s = si
                        break
                if s is not None:
                    try:
                        s.default_value = val
                    except Exception:
                        pass

            _enable_lensdist_fit(dist_node)

            d_in = dist_node.inputs.get("Image") or dist_node.inputs[0]
            ng.links.new(matte_color_out, d_in)
            matte_color_out = dist_node.outputs.get("Image") or dist_node.outputs[0]

    # 3. Channel selection
    mode = getattr(layer, "holdout_matte_mode", 'LUMA')
    if mode == 'ALPHA':
        sep_m = _new_node(ng, "CompositorNodeSeparateColor")
        if sep_m is not None:
            sep_m.name = "Matte_SepAlpha"
            sep_m.location = (0, 200)
            ng.links.new(matte_color_out, sep_m.inputs[0])
            cur_mask = sep_m.outputs.get("Alpha") or sep_m.outputs[-1]
        else:
            cur_mask = matte_color_out
    else:
        bw = _new_node(ng, "CompositorNodeRGBToBW")
        if bw is not None:
            bw.name = "Matte_RGBToBW"
            bw.location = (0, 200)
            ng.links.new(matte_color_out, bw.inputs[0])
            cur_mask = bw.outputs.get("Val") or bw.outputs[0]
        else:
            cur_mask = matte_color_out

    # 4. Choke (Dilate/Erode)
    choke = int(getattr(layer, "holdout_matte_choke", 0))
    if choke != 0:
        choke_node = _new_node(ng, "CompositorNodeDilateErode")
        if choke_node is not None:
            choke_node.name = "Matte_Choke"
            choke_node.label = f"Choke ({choke:+d}px)"
            choke_node.location = (200, 200)
            _setup_dilate_erode_node(choke_node, distance=choke, mode='Distance')
            min_sock = choke_node.inputs.get("Mask") or choke_node.inputs[0]
            ng.links.new(cur_mask, min_sock)
            cur_mask = choke_node.outputs.get("Mask") or choke_node.outputs[0]

    # 5. Feather (Blur)
    feather = float(getattr(layer, "holdout_matte_feather", 0.0))
    if feather > 0.001:
        blur_node = _new_node(ng, "CompositorNodeBlur")
        if blur_node is not None:
            blur_node.name = "Matte_Feather"
            blur_node.label = f"Feather ({feather:.0f}px)"
            blur_node.location = (400, 200)
            _setup_blur_node(blur_node, feather, feather)
            ng.links.new(cur_mask, blur_node.inputs[0])
            cur_mask = blur_node.outputs[0]

    # 5.5. Frame Range Gate
    # When animation timeline goes past the last sequence frame (or before start),
    # Blender's Image node clamps/holds the last frame. Zero out the mask (black = 0.0)
    # so that once the video ends, the mask does not freeze-cutout the 3D objects!
    in_range_sock = None
    if num_files > 0:
        st_node = _new_node(ng, "CompositorNodeSceneTime")
        if st_node is not None:
            f_sock = st_node.outputs.get("Frame")
            if f_sock is not None:
                st_node.name = "Matte_SceneTime"
                st_node.location = (400, 450)

                range_start = fstart - foffset
                range_end = fstart - foffset + num_files - 1

                m_ge = _new_node(ng, "ShaderNodeMath", "CompositorNodeMath")
                if m_ge is not None:
                    m_ge.name = "Matte_RangeMin"
                    m_ge.label = f">= {range_start}"
                    m_ge.operation = 'GREATER_THAN'
                    m_ge.location = (580, 480)
                    m_ge.inputs[1].default_value = float(range_start) - 0.5
                    ng.links.new(f_sock, m_ge.inputs[0])

                m_le = _new_node(ng, "ShaderNodeMath", "CompositorNodeMath")
                if m_le is not None:
                    m_le.name = "Matte_RangeMax"
                    m_le.label = f"<= {range_end}"
                    m_le.operation = 'LESS_THAN'
                    m_le.location = (580, 360)
                    m_le.inputs[1].default_value = float(range_end) + 0.5
                    ng.links.new(f_sock, m_le.inputs[0])

                if m_ge is not None and m_le is not None:
                    m_and = _new_node(ng, "ShaderNodeMath", "CompositorNodeMath")
                    if m_and is not None:
                        m_and.name = "Matte_InRange"
                        m_and.label = "In Frame Range"
                        m_and.operation = 'MULTIPLY'
                        m_and.location = (740, 420)
                        ng.links.new(m_ge.outputs[0], m_and.inputs[0])
                        ng.links.new(m_le.outputs[0], m_and.inputs[1])
                        in_range_sock = m_and.outputs[0]

                        m_gate = _new_node(ng, "ShaderNodeMath", "CompositorNodeMath")
                        if m_gate is not None:
                            m_gate.name = "Matte_ApplyGate"
                            m_gate.label = "Gate Mask"
                            m_gate.operation = 'MULTIPLY'
                            m_gate.location = (740, 200)
                            ng.links.new(cur_mask, m_gate.inputs[0])
                            ng.links.new(in_range_sock, m_gate.inputs[1])
                            cur_mask = m_gate.outputs[0]

    # 6. Invert (Cutout vs Keep factor)
    # Default (invert=False): white mask = CUT OUT layer (F = 1 - mask).
    # Invert (invert=True): white mask = KEEP layer (F = mask).
    invert = getattr(layer, "holdout_matte_invert", False)
    if not invert:
        sub_node = _new_node(ng, "ShaderNodeMath", "CompositorNodeMath")
        if sub_node is not None:
            sub_node.name = "Matte_Invert"
            sub_node.label = "1.0 - Matte (Cutout)"
            sub_node.operation = 'SUBTRACT'
            sub_node.location = (920, 200)
            sub_node.inputs[0].default_value = 1.0
            ng.links.new(cur_mask, sub_node.inputs[1])
            fac_sock = sub_node.outputs[0]
        else:
            fac_sock = cur_mask
    else:
        # Inverted mode: cur_mask=1 means keep, 0 means cut out.
        # When outside frame range (in_range=0), we want to KEEP everything (fac=1.0).
        if in_range_sock is not None:
            inv_range = _new_node(ng, "ShaderNodeMath", "CompositorNodeMath")
            inv_range.name = "Matte_InvRange"
            inv_range.operation = 'SUBTRACT'
            inv_range.location = (920, 320)
            inv_range.inputs[0].default_value = 1.0
            ng.links.new(in_range_sock, inv_range.inputs[1])

            add_range = _new_node(ng, "ShaderNodeMath", "CompositorNodeMath")
            add_range.name = "Matte_AddKeep"
            add_range.operation = 'ADD'
            add_range.location = (1060, 200)
            ng.links.new(cur_mask, add_range.inputs[0])
            ng.links.new(inv_range.outputs[0], add_range.inputs[1])
            fac_sock = add_range.outputs[0]
        else:
            fac_sock = cur_mask

    # Connect raw mask to Group Output 'Matte' socket
    m_out = gout.inputs.get("Matte") or (gout.inputs[1] if len(gout.inputs) > 1 else None)
    if m_out is not None:
        ng.links.new(cur_mask, m_out)

    # 7. Cutout on Group Input Image
    in_img = gin.outputs.get("Image") or gin.outputs[0]

    # Separate Alpha
    sep = _new_node(ng, "CompositorNodeSeparateColor")
    if sep is not None:
        sep.name = "Cutout_SepColor"
        sep.location = (1100, 50)
        ng.links.new(in_img, sep.inputs[0])
        orig_a = sep.outputs.get("Alpha") or sep.outputs[-1]
    else:
        orig_a = None

    # Alpha multiply: orig_alpha * fac
    mul_a = _new_node(ng, "ShaderNodeMath", "CompositorNodeMath")
    if mul_a is not None:
        mul_a.name = "Cutout_MulAlpha"
        mul_a.operation = 'MULTIPLY'
        mul_a.location = (1280, 80)
        if orig_a is not None:
            ng.links.new(orig_a, mul_a.inputs[0])
        else:
            mul_a.inputs[0].default_value = 1.0
        ng.links.new(fac_sock, mul_a.inputs[1])
        final_alpha_sock = mul_a.outputs[0]
    else:
        final_alpha_sock = fac_sock

    # RGB multiply: RGB * fac
    mul_rgb, a_in, b_in, fac_in, out_s = _make_mix_node(
        ng, "Cutout_MulRGB", "Multiply RGB",
        blend_mode='MULTIPLY', location=(1280, -80)
    )
    if mul_rgb is not None:
        if fac_in is not None:
            fac_in.default_value = 1.0
        if a_in is not None:
            ng.links.new(in_img, a_in)
        if b_in is not None:
            ng.links.new(fac_sock, b_in)
        rgb_out_sock = out_s if out_s is not None else in_img
    else:
        rgb_out_sock = in_img

    # Set Alpha
    sa = _new_node(ng, "CompositorNodeSetAlpha")
    if sa is not None:
        sa.name = "Cutout_SetAlpha"
        sa.location = (1460, 0)
        if hasattr(sa, "mode"):
            try:
                sa.mode = 'REPLACE'
            except Exception:
                pass
        if hasattr(sa, "inputs") and "Type" in sa.inputs:
            try:
                sa.inputs["Type"].default_value = 'Replace Alpha'
            except Exception:
                pass
        img_in = sa.inputs.get("Image") or sa.inputs[0]
        alpha_in = sa.inputs.get("Alpha") or sa.inputs[1]
        ng.links.new(rgb_out_sock, img_in)
        ng.links.new(final_alpha_sock, alpha_in)
        final_cutout_sock = sa.outputs.get("Image") or sa.outputs[0]
    else:
        final_cutout_sock = rgb_out_sock

    out_img = gout.inputs.get("Image") or gout.inputs[0]
    ng.links.new(final_cutout_sock, out_img)

    ng["vfx_matte_sig"] = sig
    return ng


def apply_layer_matte(nt, layer, sock, is_shadow=False, loc_x=600, loc_y=0, vfx=None):
    """Apply an external PNG image sequence matte (holdout) to a layer's image socket
    via an encapsulated Node Group.
    """
    if sock is None:
        return None

    if not getattr(layer, "use_holdout_matte", False):
        cleanup_layer_matte_nodes(nt, layer.id, is_shadow=is_shadow)
        return sock

    if is_shadow and not getattr(layer, "holdout_matte_mask_shadow", True):
        cleanup_layer_matte_nodes(nt, layer.id, is_shadow=True)
        return sock

    if vfx is None:
        vfx = getattr(getattr(layer, "id_data", None), "vfx", None)

    ng = get_or_create_layer_matte_group(layer, vfx=vfx)
    if ng is None:
        cleanup_layer_matte_nodes(nt, layer.id, is_shadow=is_shadow)
        return sock

    node_name = f"VFX_MATTE_SHD_{layer.id}" if is_shadow else f"VFX_MATTE_{layer.id}"
    label = f"Matte Cutout SHD: {layer.layer_name}" if is_shadow else f"Matte Cutout: {layer.layer_name}"

    grp_node = nt.nodes.get(node_name)
    if grp_node is not None and grp_node.type != 'GROUP':
        nt.nodes.remove(grp_node)
        grp_node = None

    if grp_node is None:
        grp_node = _new_node(nt, "CompositorNodeGroup", "ShaderNodeGroup", "NodeGroup")
        if grp_node is not None:
            grp_node.name = node_name

    if grp_node is None:
        return sock

    grp_node.node_tree = ng
    grp_node.label = label
    grp_node.location = (loc_x, loc_y)

    in_img = grp_node.inputs.get("Image") or grp_node.inputs[0]
    for l in list(in_img.links):
        nt.links.remove(l)
    nt.links.new(sock, in_img)

    return grp_node.outputs.get("Image") or grp_node.outputs[0]


def get_or_create_layer_blend_group(layer, b_mode):
    """Create or update a self-contained Node Group for layer blend mode compositing.
    Encapsulates:
      1. Exposure trim (EV)
      2. Separate Color (extract Alpha)
      3. Blend Math (Overlay, Soft Light, Multiply, etc.)
      4. Influence interpolation (Lerp between raw decal and blend math)
      5. Opacity modulation (Alpha * Opacity)
      6. Final composite (Mix onto Background stream)

    Interface:
      Inputs:
        Background (NodeSocketColor) - The composite stack underneath
        Target Plate (NodeSocketColor) - The target stream to blend math against (Background or Stack)
        Layer Image (NodeSocketColor) - The layer/decal image stream
        Influence (NodeSocketFloat) - Factor between original decal and blend math (0.0 to 1.0)
        Exposure (NodeSocketFloat) - EV trim on decal before blend math
        Opacity (NodeSocketFloat) - Layer opacity (0.0 to 1.0)
      Outputs:
        Image (NodeSocketColor) - Resulting composite stream
    """
    group_name = f"VFX_Blend_{layer.id}"
    ng = bpy.data.node_groups.get(group_name)
    if ng is None:
        ng = bpy.data.node_groups.new(group_name, 'CompositorNodeTree')

    # Interface setup
    need_interface = True
    if hasattr(ng, "interface") and hasattr(ng.interface, "items_tree"):
        in_socks = [s.name for s in ng.interface.items_tree if getattr(s, "in_out", None) == 'INPUT']
        out_socks = [s.name for s in ng.interface.items_tree if getattr(s, "in_out", None) == 'OUTPUT']
        if all(k in in_socks for k in ("Background", "Target Plate", "Layer Image", "Influence", "Exposure", "Opacity")) and "Image" in out_socks:
            need_interface = False

    if need_interface and hasattr(ng, "interface"):
        try:
            for item in list(ng.interface.items_tree):
                ng.interface.remove(item)
        except Exception:
            pass
        try:
            ng.interface.new_socket("Background", in_out='INPUT', socket_type='NodeSocketColor')
            ng.interface.new_socket("Target Plate", in_out='INPUT', socket_type='NodeSocketColor')
            ng.interface.new_socket("Layer Image", in_out='INPUT', socket_type='NodeSocketColor')
            ng.interface.new_socket("Influence", in_out='INPUT', socket_type='NodeSocketFloat')
            ng.interface.new_socket("Exposure", in_out='INPUT', socket_type='NodeSocketFloat')
            ng.interface.new_socket("Opacity", in_out='INPUT', socket_type='NodeSocketFloat')
            ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
        except Exception as e:
            print(f"VFX: error creating blend group interface sockets: {e}")
    elif need_interface and hasattr(ng, "inputs"):
        try:
            for s in ("Background", "Target Plate", "Layer Image"):
                if s not in ng.inputs:
                    ng.inputs.new('NodeSocketColor', s)
            for s in ("Influence", "Exposure", "Opacity"):
                if s not in ng.inputs:
                    ng.inputs.new('NodeSocketFloat', s)
            if "Image" not in ng.outputs:
                ng.outputs.new('NodeSocketColor', "Image")
        except Exception as e:
            print(f"VFX: error creating legacy blend group sockets: {e}")

    sig = f"{b_mode}"
    if ng.get("vfx_blend_sig") == sig and len(ng.nodes) >= 6:
        return ng

    ng.nodes.clear()

    gin = ng.nodes.new("NodeGroupInput")
    gin.location = (-600, 0)
    gout = ng.nodes.new("NodeGroupOutput")
    gout.location = (800, 0)

    # 1. Exposure node for decal
    exp_node = _new_node(ng, "CompositorNodeExposure")
    if exp_node is not None:
        exp_node.location = (-350, 100)
        exp_node.name = "Blend_Exposure"
        ng.links.new(gin.outputs.get("Layer Image") or gin.outputs[2], exp_node.inputs[0])
        ng.links.new(gin.outputs.get("Exposure") or gin.outputs[4], exp_node.inputs[1])
        decal_stream = exp_node.outputs[0]
    else:
        decal_stream = gin.outputs.get("Layer Image") or gin.outputs[2]

    # 2. Extract Alpha from layer stream
    sep = _new_node(ng, "CompositorNodeSeparateColor")
    if sep is not None:
        sep.location = (-350, -150)
        sep.name = "Blend_SeparateAlpha"
        ng.links.new(gin.outputs.get("Layer Image") or gin.outputs[2], sep.inputs[0])
        alpha_out = sep.outputs.get("Alpha") or sep.outputs[-1]
    else:
        alpha_out = None

    # 3. Pure Blend Math: Blend(Target Plate, decal_stream)
    b_mix, b_a, b_b, b_fac, b_out = _make_mix_node(
        ng, "Blend_Math", f"Math ({b_mode})", blend_mode=b_mode, location=(-100, 100)
    )
    if b_mix is not None:
        if hasattr(b_mix, "clamp_factor"):
            try:
                b_mix.clamp_factor = True
            except Exception:
                pass
        target_plate_sock = gin.outputs.get("Target Plate") or gin.outputs[1]
        if b_a is not None and target_plate_sock is not None:
            ng.links.new(target_plate_sock, b_a)
        if b_b is not None and decal_stream is not None:
            ng.links.new(decal_stream, b_b)
        if b_fac is not None:
            b_fac.default_value = 1.0

    # 4. Influence interpolation: Lerp(decal_stream, b_out, Influence)
    infl_mix, i_a, i_b, i_fac, i_out = _make_mix_node(
        ng, "Blend_Influence", "Influence Lerp", blend_mode='MIX', location=(150, 100)
    )
    if infl_mix is not None:
        if i_a is not None and decal_stream is not None:
            ng.links.new(decal_stream, i_a)
        if i_b is not None and b_out is not None:
            ng.links.new(b_out, i_b)
        infl_sock = gin.outputs.get("Influence") or gin.outputs[3]
        if i_fac is not None and infl_sock is not None:
            ng.links.new(infl_sock, i_fac)

    # 5. Opacity modulation: Alpha * Opacity
    mul = _new_node(ng, "ShaderNodeMath", "CompositorNodeMath")
    opac_sock = gin.outputs.get("Opacity") or gin.outputs[5]
    if mul is not None:
        mul.location = (150, -150)
        mul.name = "Blend_Opacity"
        mul.operation = 'MULTIPLY'
        if alpha_out is not None:
            ng.links.new(alpha_out, mul.inputs[0])
        else:
            mul.inputs[0].default_value = 1.0
        if opac_sock is not None:
            ng.links.new(opac_sock, mul.inputs[1])
        fac_sock = mul.outputs[0]
    else:
        fac_sock = alpha_out

    # 6. Final composite: Mix(Background, i_out, Factor=fac_sock)
    comp_mix, c_a, c_b, c_fac, c_out = _make_mix_node(
        ng, "Blend_Composite", "Composite over Background", blend_mode='MIX', location=(450, 0)
    )
    bg_sock_in = gin.outputs.get("Background") or gin.outputs[0]
    if comp_mix is not None:
        if c_a is not None and bg_sock_in is not None:
            ng.links.new(bg_sock_in, c_a)
        if c_b is not None and i_out is not None:
            ng.links.new(i_out, c_b)
        if c_fac is not None and fac_sock is not None:
            ng.links.new(fac_sock, c_fac)
        final_stream = c_out
    else:
        final_stream = bg_sock_in

    out_sock = gout.inputs.get("Image") or gout.inputs[0]
    if final_stream is not None and out_sock is not None:
        ng.links.new(final_stream, out_sock)

    ng["vfx_blend_sig"] = sig
    return ng


def _cleanup_bg_nodes(nt):
    """Remove background compositor nodes if background is deleted or disabled."""
    if nt is None:
        return
    for name in ("VFX_RL_BG", "VFX_BG_IMG", "VFX_BG_CLIP", "VFX_BG_SCALE", "VFX_BG_UNDISTORT"):
        n = nt.nodes.get(name)
        if n:
            nt.nodes.remove(n)


def ensure_background_node(vfx, master, nt):
    """Create and configure the background source node and scale node.
    Returns the background output socket (Image), or None if no background.
    """
    if nt is None:
        return None

    has_bg = getattr(vfx, "use_bg", False)
    if not has_bg and getattr(vfx, "bg_scene", None) is not None:
        # Backward compatibility: if bg_scene is set, treat background as active
        has_bg = True

    if not has_bg:
        _cleanup_bg_nodes(nt)
        return None

    src_type = getattr(vfx, "bg_source_type", 'VIDEO')
    fit_mode = getattr(vfx, "bg_fit_mode", 'STRETCH')

    source_sock = None
    loc_x, loc_y = 0, 450

    if src_type == 'MOVIE_CLIP':
        clip = getattr(vfx, "bg_movie_clip", None)
        if clip is None and bpy.data.movieclips:
            clip = bpy.data.movieclips[0]
            try:
                vfx.bg_movie_clip = clip
            except Exception:
                pass
        if clip is not None:
            cnode = nt.nodes.get("VFX_BG_CLIP")
            if cnode is not None and cnode.type != 'MOVIECLIP':
                nt.nodes.remove(cnode)
                cnode = None
            if cnode is None:
                cnode = nt.nodes.new("CompositorNodeMovieClip")
                cnode.name = "VFX_BG_CLIP"
            cnode.label = f"BG Clip: {clip.name}"
            cnode.clip = clip
            cnode.location = (loc_x, loc_y)
            source_sock = cnode.outputs.get("Image")
        else:
            old = nt.nodes.get("VFX_BG_CLIP")
            if old:
                nt.nodes.remove(old)

    elif src_type in ('VIDEO', 'IMAGE'):
        path = getattr(vfx, "bg_video_path" if src_type == 'VIDEO' else "bg_image_path", "")
        # Fallback to movieclip if video path is empty but clip is chosen
        if src_type == 'VIDEO' and not path and getattr(vfx, "bg_movie_clip", None):
            clip = vfx.bg_movie_clip
            cnode = nt.nodes.get("VFX_BG_CLIP")
            if cnode is not None and cnode.type != 'MOVIECLIP':
                nt.nodes.remove(cnode)
                cnode = None
            if cnode is None:
                cnode = nt.nodes.new("CompositorNodeMovieClip")
                cnode.name = "VFX_BG_CLIP"
            cnode.label = f"BG Clip: {clip.name}"
            cnode.clip = clip
            cnode.location = (loc_x, loc_y)
            source_sock = cnode.outputs.get("Image")
        elif path:
            abs_path = bpy.path.abspath(path)
            if os.path.exists(abs_path):
                ext = os.path.splitext(abs_path)[1].lower() if os.path.isfile(abs_path) else ""
                # Video file: load into MovieClip for fast timeline playback
                if src_type == 'VIDEO' and ext in ('.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'):
                    try:
                        clip = bpy.data.movieclips.load(abs_path, check_existing=True)
                        cnode = nt.nodes.get("VFX_BG_CLIP")
                        if cnode is not None and cnode.type != 'MOVIECLIP':
                            nt.nodes.remove(cnode)
                            cnode = None
                        if cnode is None:
                            cnode = nt.nodes.new("CompositorNodeMovieClip")
                            cnode.name = "VFX_BG_CLIP"
                        cnode.label = f"BG Video: {os.path.basename(abs_path)}"
                        cnode.clip = clip
                        cnode.location = (loc_x, loc_y)
                        source_sock = cnode.outputs.get("Image")
                    except Exception as e:
                        print(f"VFX: error loading bg movie clip: {e}")
                else:
                    # Image or image sequence (folder or single file)
                    img_node = nt.nodes.get("VFX_BG_IMG")
                    if img_node is not None and img_node.type != 'IMAGE':
                        nt.nodes.remove(img_node)
                        img_node = None
                    if img_node is None:
                        img_node = nt.nodes.new("CompositorNodeImage")
                        img_node.name = "VFX_BG_IMG"
                    img_node.label = f"BG {'Sequence' if src_type == 'VIDEO' else 'Image'}: {os.path.basename(abs_path)}"
                    img_node.location = (loc_x, loc_y)

                    img_name = f"VFX_BG_{src_type}"
                    img = None
                    existing = bpy.data.images.get(img_name)
                    if existing is not None:
                        ex_p = bpy.path.abspath(existing.filepath)
                        if os.path.normcase(os.path.normpath(ex_p)) == os.path.normcase(os.path.normpath(abs_path)):
                            img = existing
                        else:
                            try:
                                bpy.data.images.remove(existing)
                            except Exception:
                                pass

                    if img is None:
                        if src_type == 'VIDEO' and os.path.isdir(abs_path):
                            img, _, _ = load_matte_sequence_image(abs_path, name_prefix=img_name)
                        else:
                            try:
                                img = bpy.data.images.load(abs_path, check_existing=False)
                                img.name = img_name
                            except Exception as e:
                                print(f"VFX: error loading bg image: {e}")

                    if img is not None:
                        if src_type == 'VIDEO':
                            _configure_image_node_sequence(img_node, img, is_static=False)
                        else:
                            _configure_image_node_sequence(img_node, img, is_static=True)
                        source_sock = img_node.outputs.get("Image")

    elif src_type == 'SCENE':
        bg_scene = getattr(vfx, "bg_scene", None)
        if bg_scene is not None:
            if getattr(vfx, "comp_mode", 'LIVE') == 'FILES':
                r_node = nt.nodes.get("VFX_RL_BG")
                if r_node is not None and r_node.type != 'IMAGE':
                    nt.nodes.remove(r_node)
                    r_node = None
                if r_node is None:
                    r_node = nt.nodes.new("CompositorNodeImage")
                    r_node.name = "VFX_RL_BG"
                img = _load_sequence_image2(bg_scene.name, vfx.output_dir)
                r_node.label = f"BACKGROUND ({bg_scene.name})"
                r_node.location = (loc_x, loc_y)
                _configure_image_node_sequence(r_node, img, is_static=getattr(vfx, "bg_is_static", True))
                source_sock = r_node.outputs.get("Image")
            else:
                r_node = nt.nodes.get("VFX_RL_BG")
                if r_node is not None and r_node.type != 'R_LAYERS':
                    nt.nodes.remove(r_node)
                    r_node = None
                if r_node is None:
                    r_node = nt.nodes.new("CompositorNodeRLayers")
                    r_node.name = "VFX_RL_BG"
                r_node.scene = bg_scene
                r_node.label = "BACKGROUND"
                r_node.location = (loc_x, loc_y)
                source_sock = r_node.outputs.get("Image")

    # Clean up nodes from other unused source types
    if src_type != 'MOVIE_CLIP' and not (src_type == 'VIDEO' and source_sock and nt.nodes.get("VFX_BG_CLIP")):
        old = nt.nodes.get("VFX_BG_CLIP")
        if old and old.outputs.get("Image") != source_sock:
            nt.nodes.remove(old)
    if src_type not in ('VIDEO', 'IMAGE') or (src_type == 'VIDEO' and nt.nodes.get("VFX_BG_CLIP")):
        old = nt.nodes.get("VFX_BG_IMG")
        if old and old.outputs.get("Image") != source_sock:
            nt.nodes.remove(old)
    if src_type != 'SCENE':
        old = nt.nodes.get("VFX_RL_BG")
        if old and old.outputs.get("Image") != source_sock:
            nt.nodes.remove(old)

    if source_sock is None:
        old_scale = nt.nodes.get("VFX_BG_SCALE")
        if old_scale:
            nt.nodes.remove(old_scale)
        return None

    # For 3D Scene renders, resolution already matches scene canvas
    if src_type == 'SCENE':
        old_scale = nt.nodes.get("VFX_BG_SCALE")
        if old_scale:
            nt.nodes.remove(old_scale)
        return source_sock

    # For 2D footage/images, scale to canvas render resolution
    scale_node = nt.nodes.get("VFX_BG_SCALE")
    if scale_node is None:
        scale_node = _new_node(nt, "CompositorNodeScale")
        if scale_node is not None:
            scale_node.name = "VFX_BG_SCALE"
    if scale_node is not None:
        scale_node.label = f"Scale BG ({fit_mode})"
        scale_node.location = (loc_x + 220, loc_y)
        _setup_scale_node(scale_node, frame_method=fit_mode)
        if scale_node.inputs:
            for l in list(scale_node.inputs[0].links):
                nt.links.remove(l)
            nt.links.new(source_sock, scale_node.inputs[0])
        return scale_node.outputs.get("Image") or source_sock

    return source_sock


def ensure_background_undistort(vfx, master, nt, bg_sock):
    """If Lens Distortion is enabled on the project, apply the inverse distortion
    (-lensdist_distort) to the raw background plate. This undistorts the camera
    footage so straight-line 3D CGI objects composite into true rectilinear space.
    The matching positive distortion (+lensdist_distort) is reapplied at the end
    of the composite graph (VFX_LENSDIST).
    """
    if nt is None or bg_sock is None:
        return bg_sock

    if not getattr(vfx, "use_lensdist", False):
        _remove_nodes(nt, "VFX_BG_UNDISTORT")
        return bg_sock

    node = nt.nodes.get("VFX_BG_UNDISTORT")
    if node is not None and node.type != 'LENSDIST':
        nt.nodes.remove(node)
        node = None
    if node is None:
        try:
            node = nt.nodes.new("CompositorNodeLensdist")
            node.name = "VFX_BG_UNDISTORT"
            node.label = "UNDISTORT BG"
        except Exception as e:
            print("VFX: failed to create VFX_BG_UNDISTORT node:", e)
            return bg_sock

    node.label = "UNDISTORT BG"
    scale_node = nt.nodes.get("VFX_BG_SCALE")
    if scale_node is not None:
        node.location = (scale_node.location.x + 220, scale_node.location.y)
    else:
        node.location = (440, 450)

    # Invert the distortion coefficient: -distort
    inv_distort = -float(getattr(vfx, "lensdist_distort", 0.0))
    for target_key, val in (("distort", inv_distort), ("dispersion", 0.0)):
        s = None
        for si in node.inputs:
            if target_key in si.name.lower():
                s = si
                break
        if s is not None:
            try:
                s.default_value = val
            except Exception:
                pass

    _enable_lensdist_fit(node)

    img_in = node.inputs.get("Image")
    if img_in is None and node.inputs:
        img_in = node.inputs[0]
    if img_in is not None:
        for l in list(img_in.links):
            nt.links.remove(l)
        nt.links.new(bg_sock, img_in)

    return node.outputs.get("Image") or (node.outputs[0] if node.outputs else bg_sock)


def rebuild_comp_from_files(vfx, master):
    try:
        from .occlusion import rebuild_all_occlusion
        rebuild_all_occlusion(vfx, master)
    except Exception:
        pass
    nt = get_comp_tree(master)
    if not nt:
        return

    for node in list(nt.nodes):
        if node.type == 'R_LAYERS' and node.get("vfx_id") \
                and node.name != "VFX_RL_FOGMAP":
            nt.nodes.remove(node)

    valid_names = set()
    y = 0
    for layer in vfx.layers:
        if not layer.enabled:
            continue

        if layer.scene:
            node_name = f"VFX_RL_{layer.id}"
            valid_names.add(node_name)
            img = _load_sequence_image2(layer.scene.name, vfx.output_dir)

            node = nt.nodes.get(node_name)
            if node is not None and node.type != 'IMAGE':
                nt.nodes.remove(node)
                node = None
            if node is None:
                node = nt.nodes.new("CompositorNodeImage")
                node.name = node_name
            # Unset image first if already set, then reassign to force compositor buffer update
            node.image = None
            _configure_image_node_sequence(node, img, is_static=getattr(layer, "is_static", False))
            node.label = layer.layer_name
            node["vfx_id"] = layer.id
            node["vfx_pass"] = "OBJECT"
            node.location = (0, y)

        if layer.shadow_scene or getattr(layer, "shadow_tech", '3D') == 'AI':
            node_name = f"VFX_RL_{layer.id}_SHD"
            valid_names.add(node_name)
            is_ai = getattr(layer, "shadow_tech", '3D') == 'AI'
            src_folder = f"VFX_{layer.layer_name}_AI_SHD" if is_ai else layer.shadow_scene.name
            img = _load_sequence_image2(src_folder, vfx.output_dir)

            node = nt.nodes.get(node_name)
            if node is not None and node.type != 'IMAGE':
                nt.nodes.remove(node)
                node = None
            if node is None:
                node = nt.nodes.new("CompositorNodeImage")
                node.name = node_name
            node.image = None
            _configure_image_node_sequence(node, img, is_static=False)
            node.label = f"{layer.layer_name} AI SHD" if is_ai else f"{layer.layer_name} SHD"
            node["vfx_id"] = layer.id
            node["vfx_pass"] = "SHADOW"
            node.location = (350, y)

        y -= 220

    has_bg = getattr(vfx, "use_bg", False)
    bg_src = getattr(vfx, "bg_source_type", 'VIDEO')
    bg_scene = getattr(vfx, "bg_scene", None)
    if bg_scene and (bg_src == 'SCENE' or not has_bg):
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
        node.image = None
        _configure_image_node_sequence(node, img, is_static=getattr(vfx, "bg_is_static", True))
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
            img = _load_sequence_image2(fm.name, vfx.output_dir)
            node = nt.nodes.get(mn)
            if img is not None:
                if node is not None and node.type != 'IMAGE':
                    nt.nodes.remove(node)
                    node = None
                if node is None:
                    node = nt.nodes.new("CompositorNodeImage")
                    node.name = mn
                node.image = None
                _configure_image_node_sequence(node, img, is_static=False)
                node.label = "FOG MAP (Sequence)"
                node["vfx_id"] = "FOGMAP"
                node["vfx_pass"] = "MIST"
                node.location = (-350, 600)
            else:
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
    try:
        from .occlusion import rebuild_all_occlusion
        rebuild_all_occlusion(vfx, master)
    except Exception:
        pass
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

    has_bg = getattr(vfx, "use_bg", False)
    bg_src = getattr(vfx, "bg_source_type", 'VIDEO')
    bg_scene = getattr(vfx, "bg_scene", None)
    if bg_scene and (bg_src == 'SCENE' or not has_bg):
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
               ext_node="", image_sock=None, depth_sock=None,
               crypto_on=False, crypto_amount=1.0, crypto_y=0.0,
               crypto_pick="", crypto_node=""):
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

    # Crypto influence modulation: lift/dip the mask over picked objects.
    # amount > 0  -> SCREEN:  mask*(1-amt) + amt   (brighten = MORE effect)
    # amount < 0  -> MULTIPLY: mask*(1+amt)        (darken   = LESS effect)
    # One MixRGB node, created only when the feature is actually on.
    try:
        crypto_amount = float(crypto_amount or 0.0)
    except Exception:
        crypto_amount = 0.0
    if crypto_on and sock is not None and abs(crypto_amount) > 0.001:
        master = getattr(props, 'master_scene', None) or bpy.context.scene
        names = [n.strip() for n in (crypto_pick or '').split('|') if n.strip()]
        cnode = _ensure_crypto_influence(
            vfx=props, master=master, nt=nt, obj_names=names, node_name=crypto_node)
        if cnode is not None:
            csock = cnode.outputs.get("Matte")
            if csock is None:
                for s in cnode.outputs:
                    if s.type in ('VALUE', 'RGBA'):
                        csock = s
                        break
            if csock is not None and csock.type == 'RGBA':
                csock = _to_float(nt, csock, name_prefix + "CI", (-2600, -300))
            if csock is not None:
                try:
                    amt = max(-0.99, min(1.0, crypto_amount))
                    mul = _new_node(nt, "CompositorNodeMixRGB", "ShaderNodeMix")
                    if mul is not None:
                        mul.name = f"MASK_{name_prefix}_CI"
                        mul.label = f"MASK {name_prefix} crypto"
                        mul.location = (-2450, crypto_y - 150)
                        try:
                            mul.data_type = 'RGBA'
                        except Exception:
                            pass
                        try:
                            mul.blend_type = 'SCREEN' if amt >= 0.0 else 'MULTIPLY'
                        except Exception:
                            pass
                        a_in = b_in = f_in = None
                        for s in mul.inputs:
                            if f_in is None and s.type == 'VALUE' and s.name in ('Factor', 'Fac'):
                                f_in = s
                            if s.type == 'RGBA' and s.name == 'A':
                                a_in = s
                            if s.type == 'RGBA' and s.name == 'B':
                                b_in = s
                        out_s = None
                        for s in mul.outputs:
                            if s.type == 'RGBA':
                                out_s = s
                                break
                        if a_in is not None:
                            for l in list(a_in.links):
                                nt.links.remove(l)
                            nt.links.new(sock, a_in)
                        if b_in is not None:
                            v = amt if amt >= 0.0 else (1.0 - amt)
                            b_in.default_value = (v, v, v, 1.0)
                            for l in list(b_in.links):
                                nt.links.remove(l)
                            nt.links.new(csock, b_in)
                        if f_in is not None:
                            f_in.default_value = 1.0
                        if out_s is not None:
                            sock = out_s
                except Exception as e:
                    print("VFX crypto modulate error:", name_prefix, e)

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
            _setup_blur_node(bl, soft, soft)
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
    """Group: Mist (0..1) -> MapRange (Far Start..Far End) -> ColorRamp (EDITABLE) -> Multiply Max Blur.
    Outputs 'Blur' (px) for the Blur node Size socket.
    The ColorRamp node inside is user-editable in the node editor.
    """
    ng = bpy.data.node_groups.get("VFX_DofRamp")
    if ng is None:
        ng = bpy.data.node_groups.new("VFX_DofRamp", 'CompositorNodeTree')
    have = {(s.name, getattr(s, "in_out", None), getattr(s, "socket_type", None)) for s in ng.interface.items_tree}
    need = {("Mist", 'INPUT', 'NodeSocketFloat'),
            ("Far Start", 'INPUT', 'NodeSocketFloat'), ("Far End", 'INPUT', 'NodeSocketFloat'),
            ("Max Blur", 'INPUT', 'NodeSocketFloat'), ("Blur", 'OUTPUT', 'NodeSocketFloat')}
    need_rebuild = (len(ng.nodes) < 4) or not need.issubset(have)
    if need_rebuild:
        ng.nodes.clear()
        try:
            for item in list(ng.interface.items_tree):
                ng.interface.remove(item)
        except Exception:
            pass
        for name, io, typ in sorted(need, key=lambda t: t[1] != 'INPUT'):
            try:
                ng.interface.new_socket(name, in_out=io, socket_type=typ)
            except Exception:
                pass
        gin = ng.nodes.new("NodeGroupInput")
        gin.location = (-600, 0)
        gout = ng.nodes.new("NodeGroupOutput")
        gout.location = (500, 0)
        # Mist (0..1) -> Map Range over Far Start..Far End
        mr = _new_node(ng, "ShaderNodeMapRange", "CompositorNodeMapRange")
        mr.name = "DR_MR"
        mr.location = (-350, 100)
        try:
            mr.interpolation_type = 'LINEAR'
            mr.clamp = True
        except Exception:
            pass
        ng.links.new(gin.outputs.get("Mist"), mr.inputs.get("Value"))
        ng.links.new(gin.outputs.get("Far Start"), mr.inputs.get("From Min"))
        ng.links.new(gin.outputs.get("Far End"), mr.inputs.get("From Max"))
        # EDITABLE ramp: black = sharp (0 blur), white = max blur
        ramp = _new_node(ng, "ShaderNodeValToRGB", "CompositorNodeValToRGB")
        ramp.name = "DR_RAMP"
        ramp.label = "EDIT ME: sharp -> blurry"
        ramp.location = (-50, 100)
        try:
            ramp.color_ramp.elements[0].position = 0.0
            ramp.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
            ramp.color_ramp.elements[1].position = 1.0
            ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
        except Exception:
            pass
        ng.links.new(mr.outputs.get("Result"), ramp.inputs[0])
        # ramp -> max blur px
        mul = _new_node(ng, "ShaderNodeMath", "CompositorNodeMath")
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
    if rl is not None:
        img_in = node.inputs.get("Image") or (node.inputs[0] if node.inputs else None)
        if img_in is not None:
            try:
                nt.links.new(rl.outputs.get("Image") or rl.outputs[0], img_in)
            except Exception:
                pass
        for i in range(3):
            cin = node.inputs.get(f"Crypto {i:02d}")
            cout = rl.outputs.get(f"CryptoObject{i:02d}")
            if cin is not None and cout is not None:
                try:
                    nt.links.new(cout, cin)
                except Exception:
                    pass
    if obj_name:
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


# ---------------------------------------------------------------------
# CRYPTO INFLUENCE — per-effect cryptomatte masks of picked objects,
# used to modulate each effect independently (fog / DOF / glare / grade /
# color match each have their OWN picked set and amount).
# ---------------------------------------------------------------------

# effect key -> cryptomatte node name in the comp tree
CRYPTO_INFL_NODES = {
    'FOG': "VFX_CRYPTO_FOG",
    'DOF': "VFX_CRYPTO_DOF",
    'GLARE': "VFX_CRYPTO_GLARE",
    'GRADE': "VFX_CRYPTO_GRADE",
    'CM': "VFX_CRYPTO_CM",
}


def _ensure_fogmap_crypto_pass(vfx):
    """Object cryptomatte pass on the live FOGMAP scene (whole-scene matte)."""
    fm = getattr(vfx, "fog_map_scene", None)
    if fm is not None:
        for vl in fm.view_layers:
            try:
                vl.use_pass_cryptomatte_object = True
            except Exception:
                pass


def _ensure_crypto_influence(vfx, master, nt, obj_names, node_name):
    """Create/update a per-effect Cryptomatte node (e.g. VFX_CRYPTO_FOG)
    fed by the live FOGMAP render layer, with the effect's picked object
    names as matte IDs. Output is 1 inside those objects, 0 elsewhere.
    Returns the node or None."""
    if not obj_names or not node_name:
        return None
    _ensure_fogmap_crypto_pass(vfx)
    node = nt.nodes.get(node_name)
    if node is None:
        try:
            node = nt.nodes.new("CompositorNodeCryptomatte")
        except Exception:
            return None
        node.name = node_name
        node.label = "CRYPTO " + node_name.split("VFX_CRYPTO_")[-1]
        node.location = (-350, -700)
    fm_node = nt.nodes.get("VFX_RL_FOGMAP")
    if fm_node is not None:
        img_in = node.inputs.get("Image") or (node.inputs[0] if node.inputs else None)
        if img_in is not None:
            for l in list(img_in.links):
                nt.links.remove(l)
            try:
                nt.links.new(fm_node.outputs.get("Image") or fm_node.outputs[0], img_in)
            except Exception:
                pass
        for i in range(3):
            cin = node.inputs.get(f"Crypto {i:02d}")
            cout = fm_node.outputs.get(f"CryptoObject{i:02d}")
            if cin is not None and cout is not None:
                for l in list(cin.links):
                    nt.links.remove(l)
                try:
                    nt.links.new(cout, cin)
                except Exception:
                    pass
    try:
        node.layer_name = "CryptoObject00"
    except Exception:
        pass
    # In Blender 5.2, CompositorNodeCryptomatte uses matte_id as comma/space-separated string
    matte_str = ", ".join(obj_names)
    try:
        node.matte_id = matte_str
    except Exception:
        pass
    # Support legacy versions if entries attribute exists
    try:
        existing = [it.name for it in getattr(node, "entries", [])]
        for name in list(existing):
            if name not in obj_names:
                try:
                    node.remove_name(name)
                except Exception:
                    pass
        for name in obj_names:
            try:
                node.add_name(name)
            except Exception:
                pass
    except Exception:
        pass
    return node


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
    fn = nt.nodes.get("VFX_RL_FOGMAP")
    if getattr(vfx, "comp_mode", 'LIVE') == 'FILES':
        fm = getattr(vfx, "fog_map_scene", None)
        scene_name = fm.name if fm else "VFX_FOGMAP"
        img = _load_sequence_image2(scene_name, vfx.output_dir)
        if img is not None:
            if fn is not None and fn.type != 'IMAGE':
                nt.nodes.remove(fn)
                fn = None
            if fn is None:
                fn = nt.nodes.new("CompositorNodeImage")
                fn.name = "VFX_RL_FOGMAP"
            fn.image = None
            _configure_image_node_sequence(fn, img, is_static=False)
            fn.label = "FOG MAP (Sequence)"
            fn["vfx_id"] = "FOGMAP"
            fn["vfx_pass"] = "MIST"
            fn.location = (-350, 600)
            return fn

    if fn is not None and fn.type == 'IMAGE':
        return fn

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


def _fog_group_links_ok(ng):
    """True if the critical internal links of VFX_FogGroup are intact.
    Heals groups that were mangled manually (dragged wires) or built by
    a version whose silent try/except left sockets unconnected: without
    this check a broken group would pass the scheme-version test and
    keep producing a black/constant fog map forever."""
    try:
        ramp = ng.nodes.get("VFX_FOG_RAMP")
        cin = ng.nodes.get("VFX_FOG_CRYPTO")
        inv = ng.nodes.get("VFX_FOG_INV")
        gin = next((n for n in ng.nodes if n.type == 'GROUP_INPUT'), None)
        if not (ramp and cin and inv and gin):
            return False
        # MapRange feeding the ramp must exist and take Density into To Max
        mr = ramp.inputs[0].links[0].from_node if ramp.inputs[0].links else None
        if mr is None or mr.type != 'MAP_RANGE':
            return False
        if not mr.inputs.get("To Max").links:
            return False
        # ramp -> crypto.A, crypto MapRange -> crypto.B
        a_in = next((s for s in cin.inputs if s.type == 'RGBA' and s.name == 'A'), None)
        b_in = next((s for s in cin.inputs if s.type == 'RGBA' and s.name == 'B'), None)
        if a_in is None or b_in is None or not a_in.links or not b_in.links:
            return False
        if getattr(a_in.links[0].from_node, "name", None) != ramp.name:
            return False
        # crypto -> invert.Color
        inv_in = inv.inputs.get("Color")
        if inv_in is None or not inv_in.links:
            return False
        if getattr(inv_in.links[0].from_node, "name", None) != cin.name:
            return False
        # invert -> mix.Factor
        mix = ng.nodes.get("VFX_FOG_MIX")
        if mix is None:
            return False
        fac_in = next((s for s in mix.inputs if s.type == 'VALUE' and s.name in ('Factor', 'Fac')), None)
        if fac_in is None or not fac_in.links:
            return False
        if getattr(fac_in.links[0].from_node, "name", None) != mix.name and getattr(fac_in.links[0].from_node, "name", None) != inv.name:
            return False
        return True
    except Exception:
        return False


def _build_fog_group2(vfx):
    """VFX_FogGroup — EXACT reference topology, nothing extra:

        Depth (m) -> MapRange(Fog Start..Fog Full, clamped)
                  -> FOG MAP RAMP (editable ColorRamp)
                  -> Crypto Influence (modulate ramp by picked objects)
                  -> Invert (Color in, Fac = 1)
                  -> Mix.Factor
        Mix.A = Fog Color,  Mix.B = Image (layers assembled OUTSIDE)

    Outputs: Image = Mix.Result, Mask = ramp output (the fog map).
    """
    VFX_FOG_SCHEME_VERSION = 8  # 8 = strict validation of invert -> mix link + self healing
    ng = bpy.data.node_groups.get("VFX_FogGroup")
    needed_inputs = {"Image", "Depth", "Fog Color", "Ramp Black", "Ramp White", "Density",
                     "Crypto", "Crypto Mask"}
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
        if needed_inputs.issubset(have) and scheme >= VFX_FOG_SCHEME_VERSION \
                and _fog_group_links_ok(ng):
            return ng  # modern group already built — keep user's ramp edits
        # purge outdated / broken group
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

    def _add_sock(name, io, typ, default=None):
        try:
            s = ng.interface.new_socket(name, in_out=io, socket_type=typ)
            if default is not None and hasattr(s, "default_value"):
                try:
                    s.default_value = default
                except Exception:
                    pass
            return s
        except Exception:
            return None

    _add_sock("Image", 'INPUT', 'NodeSocketColor')
    _add_sock("Depth", 'INPUT', 'NodeSocketFloat', default=0.0)
    _add_sock("Fog Color", 'INPUT', 'NodeSocketColor', default=(0.8, 0.85, 0.9, 1.0))
    _add_sock("Ramp Black", 'INPUT', 'NodeSocketFloat', default=0.0)
    _add_sock("Ramp White", 'INPUT', 'NodeSocketFloat', default=100.0)
    _add_sock("Density", 'INPUT', 'NodeSocketFloat', default=1.0)
    _add_sock("Crypto", 'INPUT', 'NodeSocketFloat', default=1.0)
    _add_sock("Crypto Mask", 'INPUT', 'NodeSocketFloat', default=0.0)
    _add_sock("Image", 'OUTPUT', 'NodeSocketColor')
    _add_sock("Mask", 'OUTPUT', 'NodeSocketFloat')

    gin = ng.nodes.new("NodeGroupInput")
    gin.location = (-900, 0)
    gout = ng.nodes.new("NodeGroupOutput")
    gout.location = (750, 0)

    def g_in(name):
        return gin.outputs.get(name)

    # meters -> 0..1 over the Fog Start..Fog Full window.
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
    try:
        mr.inputs.get("To Min").default_value = 0.0
    except Exception:
        pass
    try:
        ng.links.new(g_in("Density"), mr.inputs.get("To Max"))
    except Exception:
        pass

    # EDITABLE ramp: the fog density curve
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

    # ── Crypto influence: modulate fog map over picked objects
    cin = _new_node(ng, "ShaderNodeMix", "CompositorNodeMixRGB")
    if cin is None:
        return ng
    cin.location = (-500, -150)
    cin.name = "VFX_FOG_CRYPTO"
    cin.label = "crypto influence"
    try:
        cin.data_type = 'RGBA'
    except Exception:
        pass
    try:
        cin.blend_type = 'MULTIPLY'
    except Exception:
        pass

    ca_in = cb_in = cf_in = None
    for s in cin.inputs:
        if cf_in is None and s.type == 'VALUE' and s.name in ('Factor', 'Fac'):
            cf_in = s
        if s.type == 'RGBA' and s.name == 'A':
            ca_in = s
        if s.type == 'RGBA' and s.name == 'B':
            cb_in = s
    if ca_in is None:
        ca_in = cin.inputs.get("Color1") or (cin.inputs[1] if len(cin.inputs) > 1 else None)
    if cb_in is None:
        cb_in = cin.inputs.get("Color2") or (cin.inputs[2] if len(cin.inputs) > 2 else None)
    if cf_in is None:
        cf_in = cin.inputs.get("Fac") or (cin.inputs[0] if len(cin.inputs) > 0 else None)

    if ca_in is not None:
        ng.links.new(ramp.outputs[0], ca_in)

    cin_mr = _new_node(ng, "ShaderNodeMapRange", "CompositorNodeMapRange")
    if cin_mr is not None:
        cin_mr.location = (-700, -250)
        cin_mr.name = "VFX_FOG_CRYPTO_MR"
        try:
            cin_mr.clamp = True
        except Exception:
            pass
        try:
            cin_mr.inputs.get("To Min").default_value = 1.0
        except Exception:
            pass
        try:
            ng.links.new(g_in("Crypto"), cin_mr.inputs.get("To Max"))
        except Exception:
            pass
        try:
            ng.links.new(g_in("Crypto Mask"), cin_mr.inputs.get("Value"))
        except Exception:
            pass
        if cb_in is not None:
            try:
                ng.links.new(cin_mr.outputs.get("Result"), cb_in)
            except Exception:
                pass
    if cb_in is not None and not cb_in.links:
        try:
            cb_in.default_value = (1.0, 1.0, 1.0, 1.0)
        except Exception:
            pass
    if cf_in is not None:
        try:
            cf_in.default_value = 1.0
        except Exception:
            pass

    cin_out_rgba = None
    for s in cin.outputs:
        if s.type == 'RGBA':
            cin_out_rgba = s
            break
    map_sock = cin_out_rgba if cin_out_rgba is not None else ramp.outputs[0]

    # Invert: map -> mix factor (1 = clear layers, 0 = fog color)
    inv = _new_node(ng, "CompositorNodeInvert", "ShaderNodeInvert")
    if inv is None:
        return ng
    inv.location = (-300, 250)
    inv.name = "VFX_FOG_INV"
    inv.label = "density -> mix factor"
    try:
        inv.inputs["Fac"].default_value = 1.0  # full inversion
    except Exception:
        pass
    inv_color_in = inv.inputs.get("Color")
    inv_color_out = inv.outputs.get("Color")
    if inv_color_in is None or inv_color_out is None:
        inv_color_in = inv.inputs[0] if inv.inputs else None
        inv_color_out = inv.outputs[0] if inv.outputs else None
    if inv_color_in is not None:
        ng.links.new(map_sock, inv_color_in)
    factor_sock = inv_color_out

    # ── MIX: Fac = inverted map, A = Fog Color, B = assembled layers ──
    mix = _new_node(ng, "ShaderNodeMix", "CompositorNodeMixRGB")
    if mix is None:
        return ng
    mix.location = (450, 0)
    mix.name = "VFX_FOG_MIX"
    mix.label = "fog mix"
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
    if fac_sock is None:
        fac_sock = mix.inputs.get("Fac")

    fog_color_sock = g_in("Fog Color")
    if a_sock is not None and fog_color_sock is not None:
        ng.links.new(fog_color_sock, a_sock)
    img_in = g_in("Image")
    if b_sock is not None and img_in is not None:
        ng.links.new(img_in, b_sock)
    if fac_sock is not None and factor_sock is not None:
        ng.links.new(factor_sock, fac_sock)

    img_out = None
    for s in mix.outputs:
        if s.type == 'RGBA':
            img_out = s
            break
    if img_out is None and mix.outputs:
        img_out = mix.outputs[0]
    oi = gout.inputs.get("Image")
    if img_out is not None and oi is not None:
        ng.links.new(img_out, oi)
    mk_out = gout.inputs.get("Mask")
    if mk_out is not None:
        ng.links.new(map_sock, mk_out)

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
    # Fog ramp: user values are METERS from the camera. The group maps the
    # mist-derived distance: near -> clear, far -> full fog color (clamped).
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
        if node.type == 'CRYPTOMATTE' and node.name not in ("VFX_CRYPTO_PICK",) and node.name not in CRYPTO_INFL_NODES.values():
            nt.nodes.remove(node)

    # Clean up inactive / deleted matte sequence nodes
    active_matte_ids = {layer.id for layer in vfx.layers if getattr(layer, "use_holdout_matte", False)}
    for node in list(nt.nodes):
        if node.name.startswith("VFX_MATTE_"):
            if node.type != 'GROUP':
                try:
                    nt.nodes.remove(node)
                except Exception:
                    pass
            else:
                parts = node.name.split("_")
                lid = parts[-1]
                if lid not in active_matte_ids:
                    try:
                        nt.nodes.remove(node)
                    except Exception:
                        pass

    for ng in list(bpy.data.node_groups):
        if ng.name.startswith("VFX_Matte_"):
            lid = ng.name[len("VFX_Matte_"):]
            if lid not in active_matte_ids:
                try:
                    bpy.data.node_groups.remove(ng)
                except Exception:
                    pass

    # Clean up inactive / deleted blend node groups
    active_blend_ids = {layer.id for layer in vfx.layers if getattr(layer, "blend_mode", 'ALPHA_OVER') != 'ALPHA_OVER'}
    for ng in list(bpy.data.node_groups):
        if ng.name.startswith("VFX_Blend_"):
            lid = ng.name[len("VFX_Blend_"):]
            if lid not in active_blend_ids:
                try:
                    bpy.data.node_groups.remove(ng)
                except Exception:
                    pass

    # Clean up any legacy loose blend nodes
    for node in list(nt.nodes):
        if node.name.startswith(("VFX_EXP_", "VFX_SEP_A_", "VFX_MATH_", "VFX_INFL_", "VFX_OPAC_")):
            try:
                nt.nodes.remove(node)
            except Exception:
                pass

    # Clean up inactive / deleted shadow filter nodes
    active_shd_ids = {layer.id for layer in vfx.layers if layer.shadow_scene or getattr(layer, "shadow_tech", '3D') == 'AI'}
    for node in list(nt.nodes):
        if node.name.startswith("VFX_SHD_RAMP_") or node.name.startswith("VFX_SHD_BLUR_"):
            parts = node.name.split("_")
            lid = parts[-1]
            if lid not in active_shd_ids:
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

        if layer.shadow_scene or getattr(layer, "shadow_tech", '3D') == 'AI':
            sh = nt.nodes.get(f"VFX_RL_{layer.id}_SHD")
            if sh:
                sh_sock = apply_shadow_filter(nt, layer, sh, loc_x=260, loc_y=-len(sockets) * 200)
                if sh_sock is None and sh.outputs.get("Image"):
                    sh_sock = sh.outputs["Image"]

        if layer.scene:
            ob = nt.nodes.get(f"VFX_RL_{layer.id}")
            if ob and ob.outputs.get("Image"):
                ob_sock = ob.outputs["Image"]
        # For RECEIVE mode (layer receives shadows onto itself):
        # The SHD scene renders the isolated shadow onto the receiver.
        # OBJ (clean receiver) is placed first, and SHD (isolated shadow) is composited on top of it.
        if getattr(layer, "shadow_mode", 'CAST') == 'RECEIVE':
            if ob_sock:
                sockets.append((layer, "OBJ", ob_sock))
            if sh_sock:
                sockets.append((layer, "SHD", sh_sock))
        else:
            if sh_sock:
                sockets.append((layer, "SHD", sh_sock))
            if ob_sock:
                sockets.append((layer, "OBJ", ob_sock))

    bg_sock = ensure_background_node(vfx, master, nt)
    if bg_sock is not None and getattr(vfx, "use_lensdist", False):
        bg_sock = ensure_background_undistort(vfx, master, nt, bg_sock)
    else:
        _remove_nodes(nt, "VFX_BG_UNDISTORT")

    if not sockets and bg_sock is None:
        return

    view_sock = None
    fog_done = False

    # ── PER-LAYER GRADES ──
    grade_nodes = ensure_layer_grades(vfx, master, nt)

    # ── GLOBAL & PER-LAYER BLUR / DOF ──
    # When use_dof is True: all layers are blurred per-layer by the unified Mist DOF map,
    # preventing edge-bleeding onto sharp foreground layers.
    # Individual layers can modulate their blur via blur_multiplier (0 = sharp, 1 = normal, >1 = extra)
    # or override with blur_override (fixed px).
    global_dof = getattr(vfx, "use_dof", False)
    dof_pre_out = None

    if global_dof:
        try:
            _ensure_fogmap(nt, vfx, master)
            depth_b = _get_mist_socket(nt) or _get_depth_socket(nt)
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
                    pre.label = "DOF FOCUS RAMP (Mist -> px)"
                    pre.location = (600, 300)
                    din = pre.inputs.get("Mist") or pre.inputs.get("Depth")
                    if din is not None:
                        for l in list(din.links):
                            nt.links.remove(l)
                        nt.links.new(depth_b, din)
                    m_start = float(getattr(vfx, "mist_start", 0.0) or 0.0)
                    m_depth = max(0.1, float(getattr(vfx, "mist_depth", 50.0) or 50.0))
                    fs_val = float(getattr(vfx, "dof_far_start", 0.0) or 0.0)
                    fe_val = float(getattr(vfx, "dof_far_end", 1.0) or 1.0)
                    if fs_val > 1.0:
                        far0 = max(0.0, min(1.0, (fs_val - m_start) / m_depth))
                    else:
                        far0 = max(0.0, min(1.0, fs_val))
                    if fe_val > 1.0:
                        far1 = max(far0 + 0.01, min(1.0, (fe_val - m_start) / m_depth))
                    else:
                        far1 = max(far0 + 0.01, min(1.0, fe_val))
                    max_blur_px = float(vfx.dof_maxblur) if float(vfx.dof_maxblur) > 0.1 else 25.0
                    for name, val in (("Far Start", far0), ("Far End", far1), ("Max Blur", max_blur_px)):
                        s = pre.inputs.get(name)
                        if s is not None:
                            try:
                                s.default_value = val
                            except Exception:
                                pass
                    dof_pre_out = pre.outputs.get("Blur")
        except Exception as e:
            print("VFX layer dof_pre setup error:", e)

    blur_nodes = {}
    bx = 350
    # Process blur nodes: create/connect if global_dof is active and layer multiplier > 0
    for layer in vfx.layers:
        b_name = f"VFX_BLUR_{layer.id}"
        bn = nt.nodes.get(b_name)
        mult = float(getattr(layer, "blur_multiplier", 1.0))
        override_px = float(getattr(layer, "blur_override", 0.0))

        should_blur = global_dof and (override_px > 0.001 or mult > 0.001)

        if not should_blur or not (layer.enabled and layer.scene):
            if bn is not None:
                nt.nodes.remove(bn)
            continue

        if bn is not None and bn.type != 'BLUR':
            nt.nodes.remove(bn)
            bn = None
        if bn is None:
            try:
                bn = nt.nodes.new("CompositorNodeBlur")
                bn.name = b_name
            except Exception:
                bn = None
        if bn is not None:
            bn.label = f"BLUR: {layer.layer_name}"
            bn.location = (bx, -400)
            try:
                bn.use_variable_size = True
            except Exception:
                pass
            for attr in ("blur_method", "filter_type"):
                _safe_set(bn, attr, 'GAUSS')

            # Layer source socket (orig or graded)
            src_sock = grade_nodes.get(layer.id)
            if src_sock is None:
                ln = nt.nodes.get(f"VFX_RL_{layer.id}")
                if ln and ln.outputs.get("Image"):
                    src_sock = ln.outputs["Image"]

            if src_sock is not None:
                img_in = bn.inputs.get("Image") or (bn.inputs[0] if bn.inputs else None)
                if img_in is not None:
                    for l in list(img_in.links):
                        nt.links.remove(l)
                    nt.links.new(src_sock, img_in)

            # Drive blur size
            sz_in = bn.inputs.get("Size")
            if sz_in is not None:
                for l in list(sz_in.links):
                    nt.links.remove(l)

                if override_px > 0.001:
                    sz_in.default_value = (override_px, override_px)
                elif dof_pre_out is not None:
                    if abs(mult - 1.0) < 0.001:
                        nt.links.new(dof_pre_out, sz_in)
                    else:
                        # Scale blur size by layer's multiplier
                        mul_node_name = f"VFX_BLURMUL_{layer.id}"
                        mul_node = nt.nodes.get(mul_node_name)
                        if mul_node is None:
                            mul_node = _new_node(nt, "ShaderNodeMath", "CompositorNodeMath")
                            if mul_node is not None:
                                mul_node.name = mul_node_name
                        if mul_node is not None:
                            mul_node.operation = 'MULTIPLY'
                            mul_node.location = (bx - 120, -550)
                            for l in list(mul_node.inputs[0].links):
                                nt.links.remove(l)
                            nt.links.new(dof_pre_out, mul_node.inputs[0])
                            mul_node.inputs[1].default_value = mult
                            nt.links.new(mul_node.outputs[0], sz_in)

            blur_nodes[layer.id] = bn.outputs[0] if bn.outputs else src_sock
            bx += 220

    # Cleanup unused blur multiplier nodes
    for n in list(nt.nodes):
        if n.name.startswith("VFX_BLURMUL_"):
            lid = n.name.replace("VFX_BLURMUL_", "")
            if lid not in blur_nodes:
                nt.nodes.remove(n)

    # ── ASSEMBLE THE FULL STACK (BG -> SHD -> OBJ, grades & blurs applied) ──
    # Layers are composited into ONE image outside the fog group;
    # the group then mixes this assembled stack with the fog color
    # by the shared depth-ramp map.
    if bg_sock is not None:
        stack_sock = bg_sock
        mix_list = sockets
    elif sockets:
        first_layer, first_kind, first_sock = sockets[0]
        stack_sock = blur_nodes.get(first_layer.id, grade_nodes.get(first_layer.id, first_sock)) if first_kind == 'OBJ' else first_sock
        if first_kind == 'OBJ':
            if getattr(first_layer, "use_holdout_matte", False):
                stack_sock = apply_layer_matte(nt, first_layer, stack_sock, is_shadow=False, loc_x=650, loc_y=100, vfx=vfx)
            else:
                cleanup_layer_matte_nodes(nt, first_layer.id, is_shadow=False)
        elif first_kind == 'SHD':
            if getattr(first_layer, "use_holdout_matte", False) and getattr(first_layer, "holdout_matte_mask_shadow", True):
                stack_sock = apply_layer_matte(nt, first_layer, stack_sock, is_shadow=True, loc_x=720, loc_y=-100, vfx=vfx)
            else:
                cleanup_layer_matte_nodes(nt, first_layer.id, is_shadow=True)
        mix_list = sockets[1:]
    else:
        stack_sock = None
        mix_list = []

    current = stack_sock
    mix_index = 0
    for layer, kind, sock in mix_list:
        if kind == 'OBJ':
            bsock = blur_nodes.get(layer.id)
            if bsock is not None:
                sock = bsock
            else:
                gsock = grade_nodes.get(layer.id)
                if gsock is not None:
                    sock = gsock

            if getattr(layer, "use_holdout_matte", False):
                sock = apply_layer_matte(nt, layer, sock, is_shadow=False, loc_x=650, loc_y=-mix_index * 200 + 80, vfx=vfx)
            else:
                cleanup_layer_matte_nodes(nt, layer.id, is_shadow=False)
        elif kind == 'SHD':
            if getattr(layer, "use_holdout_matte", False) and getattr(layer, "holdout_matte_mask_shadow", True):
                sock = apply_layer_matte(nt, layer, sock, is_shadow=True, loc_x=720, loc_y=-mix_index * 200 - 80, vfx=vfx)
            else:
                cleanup_layer_matte_nodes(nt, layer.id, is_shadow=True)

        is_ai_shd = (kind == 'SHD' and getattr(layer, "shadow_tech", '3D') == 'AI')

        if is_ai_shd:
            # AI Shadow uses Multiply / Darken Mix node directly onto background stack
            # to preserve ground texture and avoid opaque alpha over-darkening.
            blend_type = getattr(layer, "ai_shadow_blend_mode", 'MULTIPLY')
            mix, a_in, b_in, fac_in, out_s = _make_mix_node(
                nt,
                f"VFX_AI_SHD_MIX_{mix_index:02d}",
                f"{layer.layer_name} AI Shadow ({blend_type})",
                blend_mode=blend_type,
                location=(950, -mix_index * 200)
            )
            if mix is not None:
                mix["vfx_mix"] = 1
                mix_fac = getattr(layer, "shadow_strength", 1.0)
                if fac_in is not None:
                    try:
                        fac_in.default_value = mix_fac
                    except Exception:
                        pass
                if a_in is not None and current is not None:
                    nt.links.new(current, a_in)
                if b_in is not None and sock is not None:
                    nt.links.new(sock, b_in)
                current = out_s if out_s is not None else current
                mix_index += 1
        elif kind == 'OBJ' and getattr(layer, "blend_mode", 'ALPHA_OVER') != 'ALPHA_OVER':
            b_mode = getattr(layer, "blend_mode", 'OVERLAY')
            b_target = getattr(layer, "blend_target", 'STACK')
            b_opac = float(getattr(layer, "blend_opacity", 1.0))
            b_infl = float(getattr(layer, "blend_influence", 0.5))
            b_exp = float(getattr(layer, "blend_exposure", 0.0))

            target_in = bg_sock if (b_target == 'BACKGROUND' and bg_sock is not None) else current

            ng = get_or_create_layer_blend_group(layer, b_mode)
            group_node_name = f"VFX_BLEND_{mix_index:02d}"
            bg_node = nt.nodes.get(group_node_name)
            if bg_node is not None and bg_node.type != 'GROUP':
                nt.nodes.remove(bg_node)
                bg_node = None
            if bg_node is None:
                bg_node = _new_node(nt, "CompositorNodeGroup", "ShaderNodeGroup", "NodeGroup")
                if bg_node is not None:
                    bg_node.name = group_node_name

            if bg_node is not None and ng is not None:
                bg_node["vfx_mix"] = 1
                bg_node.node_tree = ng
                bg_node.label = f"{layer.layer_name} ({b_mode})"
                bg_node.location = (950, -mix_index * 200)

                def _connect(sock_out, in_name):
                    target_s = bg_node.inputs.get(in_name)
                    if target_s is not None and sock_out is not None:
                        for l in list(target_s.links):
                            nt.links.remove(l)
                        nt.links.new(sock_out, target_s)

                def _set_val(in_name, val):
                    s = bg_node.inputs.get(in_name)
                    if s is not None:
                        try:
                            s.default_value = val
                        except Exception:
                            pass

                _connect(current, "Background")
                _connect(target_in, "Target Plate")
                _connect(sock, "Layer Image")
                _set_val("Influence", b_infl)
                _set_val("Exposure", b_exp)
                _set_val("Opacity", b_opac)

                out_s = bg_node.outputs.get("Image") or (bg_node.outputs[0] if bg_node.outputs else None)
                current = out_s if out_s is not None else current
                mix_index += 1
        else:
            mix = nt.nodes.new("CompositorNodeAlphaOver")
            mix.name = f"VFX_MIX_{mix_index:02d}"
            mix.label = f"{layer.layer_name} {kind}"
            mix["vfx_mix"] = 1
            mix.location = (950, -mix_index * 200)

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
            # Fog is driven by the MIST pass (0 near .. 1 far) — like the
            # original scheme — NOT the raw Z pass. Mist fraction -> meters:
            # Z = Mist Start + mist * Mist Depth (world mist hits 1.0
            # exactly at Start + Depth).
            fm_node = nt.nodes.get("VFX_RL_FOGMAP")
            mist_out = _get_mist_socket(nt)
            depth_sock = None
            depth_is_z = False
            if mist_out is None:
                # last resort only: true meters from the Z pass
                depth_sock = _get_depth_socket(nt)
                depth_is_z = depth_sock is not None
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

            for name, val in (("Ramp Black", ramp_black),
                              ("Ramp White", ramp_white)):
                s = gi(name)
                if s is not None:
                    s.default_value = val

            dens = gi("Density")
            if dens is not None:
                dens.default_value = float(getattr(vfx, "fog_density", 1.0))

            # Crypto influence: brighten/darken fog over the FOG effect's
            # own picked objects (independent of every other effect)
            cmask_in = gi("Crypto Mask")
            if cmask_in is not None:
                fog_amt = float(getattr(vfx, "fog_crypto_amount", 0.0) or 0.0)
                if bool(getattr(vfx, "crypto_on_fog", False)) and abs(fog_amt) > 0.001:
                    for l in list(cmask_in.links):
                        nt.links.remove(l)
                    names = [n.strip() for n in (getattr(vfx, "fog_crypto_objects", "") or "").split("|")
                             if n.strip()]
                    cnode = _ensure_crypto_influence(
                        vfx, master, nt, names, CRYPTO_INFL_NODES['FOG'])
                    if cnode is not None:
                        matte_out = cnode.outputs.get("Matte") or (cnode.outputs[1] if len(cnode.outputs) > 1 else cnode.outputs[0])
                        try:
                            nt.links.new(matte_out, cmask_in)
                        except Exception as e:
                            print("VFX fog crypto link error:", e)
                else:
                    for l in list(cmask_in.links):
                        nt.links.remove(l)
                cin_sock = gi("Crypto")
                if cin_sock is not None:
                    # multiplier: 1 + Amount → 1 = untouched fog, 0 = clear
                    # on picked, 2 = max dense. Feature off → Amount 0 → 1.
                    cin_sock.default_value = 1.0 + fog_amt

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
                      f"{'Z (no mist pass!)' if depth_is_z else 'mist'}")
            om = gnode.outputs.get("Mask")
            if om is not None and getattr(vfx, "use_mask", False) and getattr(vfx, "mask_source", 'NONE') == 'FOG':
                view_sock = om
        except Exception as e:
            import traceback
            print("VFX fog apply error:", e)
            traceback.print_exc()


    _PX_GLARE = 1700
    _PX_LD = 2000
    _PX_GRADE = 2300
    _PX_OUT = 2600
    _PY = 0

    # Clean up full-image 2D blur node (superseded by clean per-layer blur)
    _remove_nodes(nt, "VFX_DOF")
    if getattr(vfx, "use_mask", False) and getattr(vfx, "mask_source", 'NONE') == 'DOF':
        pre = nt.nodes.get("VFX_DOF_PRE")
        if pre is not None and pre.outputs.get("Blur"):
            view_sock = pre.outputs.get("Blur")

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
                    ext_node=vfx.glare_mask_ext_node, image_sock=orig_in,
                    crypto_on=getattr(vfx, "crypto_on_glare", False),
                    crypto_amount=getattr(vfx, "glare_crypto_amount", 0.0),
                    crypto_pick=getattr(vfx, "glare_crypto_objects", ""),
                    crypto_node=CRYPTO_INFL_NODES['GLARE'],
                    crypto_y=_PY - 100)
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
                ld.label = "REDISTORT COMP"
            except Exception:
                ld = None
        if ld is not None:
            ld.location = (_PX_LD, _PY)
            # Set socket matching both Blender 4.x ("Distort") and Blender 5.x ("Distortion")
            for target_key, val in (("distort", vfx.lensdist_distort), ("dispersion", vfx.lensdist_disperse)):
                s = None
                for si in ld.inputs:
                    if target_key in si.name.lower():
                        s = si
                        break
                if s is not None:
                    try:
                        s.default_value = val
                    except Exception:
                        pass
            _enable_lensdist_fit(ld)
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
                    ext_node=vfx.grade_mask_ext_node, image_sock=orig_in,
                    crypto_on=getattr(vfx, "crypto_on_grade", False),
                    crypto_amount=getattr(vfx, "grade_crypto_amount", 0.0),
                    crypto_pick=getattr(vfx, "grade_crypto_objects", ""),
                    crypto_node=CRYPTO_INFL_NODES['GRADE'],
                    crypto_y=_PY - 400)
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
                cm_out = None
                for s in cm_node.outputs:
                    if s.type == 'RGBA':
                        cm_out = s
                        break
                # Crypto influence on the color match (its own picked set)
                cm_act = (bool(getattr(vfx, "crypto_on_colormatch", False))
                          and abs(float(getattr(vfx, "cm_crypto_amount", 0.0) or 0.0)) > 0.001)
                if cm_out is not None and cm_act:
                    names = [n.strip() for n in (getattr(vfx, "cm_crypto_objects", "") or "").split("|")
                             if n.strip()]
                    cnode = _ensure_crypto_influence(
                        vfx, master, nt, names, CRYPTO_INFL_NODES['CM'])
                    if cnode is not None and cnode.outputs:
                        msock = cnode.outputs[0]
                        masked = _apply_mask(nt, "COLORMATCH", current, cm_out, msock,
                                             _PY - 300)
                        if masked is not None:
                            current = masked
                elif cm_out is not None:
                    current = cm_out
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
    try:
        if master is not None:
            master.update_tag()
    except Exception:
        pass
    if hasattr(bpy.context, "window_manager") and bpy.context.window_manager:
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

    # Ensure all transparent materials write into Depth and Mist pass
    # (In EEVEE/EEVEE-Next, BLENDED materials are completely omitted from Z/Mist)
    for obj in sc.objects:
        if obj.type == 'MESH':
            for slot in obj.material_slots:
                mat = slot.material
                if mat:
                    if hasattr(mat, "surface_render_method") and mat.surface_render_method == 'BLENDED':
                        mat.surface_render_method = 'DITHERED'
                    elif hasattr(mat, "blend_method") and mat.blend_method == 'BLEND':
                        mat.blend_method = 'HASHED'

    _ensure_fogmap_scene_comp(sc)


def _ensure_fogmap_scene_comp(sc):
    """Setup an isolated node tree for the VFX_FOGMAP scene so when rendered to file,
    it outputs the Mist pass directly into its Image output.
    """
    if sc is None:
        return
    tree_name = "VFX_FOGMAP_Comp"
    ng = bpy.data.node_groups.get(tree_name)
    if ng is None:
        ng = bpy.data.node_groups.new(tree_name, 'CompositorNodeTree')
    if (len(ng.nodes) < 2
            or not any(n.type == 'GROUP_OUTPUT' for n in ng.nodes)
            or not any(n.type == 'R_LAYERS' for n in ng.nodes)):
        ng.nodes.clear()
        try:
            for it in list(ng.interface.items_tree):
                ng.interface.remove(it)
        except Exception:
            pass
        try:
            ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
        except Exception:
            pass
        gout = ng.nodes.new("NodeGroupOutput")
        gout.location = (300, 0)
        rl = ng.nodes.new("CompositorNodeRLayers")
        rl.location = (0, 0)
        rl.scene = sc
        m_sock = rl.outputs.get("Mist") or (rl.outputs.get("Image") if rl.outputs else None)
        img_in = gout.inputs.get("Image") or (gout.inputs[0] if gout.inputs else None)
        if m_sock and img_in:
            ng.links.new(m_sock, img_in)

    if hasattr(sc, "compositing_node_group"):
        try:
            sc.compositing_node_group = ng
        except Exception:
            pass
    elif hasattr(sc, "node_tree"):
        try:
            sc.node_tree = ng
        except Exception:
            pass

