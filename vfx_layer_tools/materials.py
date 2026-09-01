"""VFX Layer Tools — material adjustment (viewport lighting)."""

import bpy


ADJ_PREFIX = "VFX_ADJ_"

_last_adjust_stats = {"objects": 0, "materials": 0, "applied": 0, "notes": []}


# ---------------------------------------------------------------------
# MATERIAL ADJUST
# ---------------------------------------------------------------------

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
        from .compositor import build_comp_assembly
        scene = context.scene
        vfx = scene.vfx
        master = vfx.master_scene or scene
        build_comp_assembly(vfx, master)
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    except Exception as e:
        print("VFX _trigger_comp error:", e)


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

