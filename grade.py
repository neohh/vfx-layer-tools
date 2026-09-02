"""VFX Layer Tools — Two-level color grading system (v2.6.1).

Clean architecture — NO ColorBalance (Blender 5.x defaults are broken).
All adjustments via MixRGB (Add/Multiply) + HueSat + BrightContrast.

Chain:
  Image → MixRGB_Mult(exposure+WB) → MixRGB_Add(Lift) → HueSat(Sat)
  → BrightContrast(Contrast) → alpha-safe output

Neutral defaults produce IDENTICAL passthrough:
  CombinedGain=(1,1,1) → pixel*1 = pixel ✓
  Lift=(0,0,0) → pixel+0 = pixel ✓
  Sat=1.0 → unchanged ✓
  Contrast=1.0 → unchanged ✓
"""

import bpy


def _log(msg):
    print(f"VFX GRADE: {msg}")


def _link(ng, a, b):
    if not a or not b:
        return False
    try:
        ng.links.new(a, b)
        return True
    except Exception as e:
        _log(f"LINK FAILED: {a} -> {b}: {e}")
        return False


def _new_node(ng, *ids):
    for i in ids:
        try:
            return ng.nodes.new(i)
        except Exception:
            continue
    return None


def _compute_gain(exposure, temp, tint):
    """Combined gain color: 2^EV * temperature/tint adjustment."""
    ev = 2.0 ** exposure
    temp_r = 1.0 + temp * 0.5
    temp_b = 1.0 - temp * 0.5
    tint_g = 1.0 + tint * 0.5
    return (ev * temp_r, ev * tint_g, ev * temp_b)


def _make_mix_rgb(ng, location, blend_mode='MULTIPLY', name="Mix"):
    """Create a CompositorNodeMixRGB or ShaderNodeMix for color blending."""
    mix = _new_node(ng, "CompositorNodeMixRGB", "ShaderNodeMix")
    if mix is None:
        return None
    mix.location = location
    mix.name = name

    if mix.bl_idname == 'ShaderNodeMix':
        try:
            mix.data_type = 'RGBA'
        except Exception:
            pass

    # Set blend mode
    for attr in ("blend_type", "blend_mode"):
        try:
            setattr(mix, attr, blend_mode)
            break
        except Exception:
            pass

    # Set Factor = 1.0 (full effect)
    for s in mix.inputs:
        if s.type == 'VALUE' and s.name in ('Factor', 'Fac'):
            s.default_value = 1.0
            break

    return mix


def _get_mix_sockets(mix):
    """Get (A, B, Factor, Output) sockets from a MixRGB node."""
    if mix is None:
        return None, None, None, None

    fac_in = a_in = b_in = out_s = None
    for s in mix.inputs:
        if fac_in is None and s.type == 'VALUE' and s.name in ('Factor', 'Fac'):
            fac_in = s
        if s.type in ('RGBA', 'COLOR') and s.name == 'A':
            a_in = s
        if s.type in ('RGBA', 'COLOR') and s.name == 'B':
            b_in = s
        # Fallback for CompositorNodeMixRGB
        if a_in is None and s.type in ('RGBA', 'COLOR') and s.name == 'Color1':
            a_in = s
        if b_in is None and s.type in ('RGBA', 'COLOR') and s.name == 'Color2':
            b_in = s

    for s in mix.outputs:
        if s.type in ('RGBA', 'COLOR'):
            out_s = s
            break

    return a_in, b_in, fac_in, out_s


def _find_socket(node, name, kind='input', prefer_color=False):
    """Find socket by name."""
    if not node:
        return None
    coll = node.inputs if kind == 'input' else node.outputs
    first_match = None
    for s in coll:
        if s.name == name or s.name.lower() == name.lower():
            if prefer_color and s.type in ('COLOR', 'RGBA'):
                return s
            if first_match is None:
                first_match = s
    return first_match


# ---------------------------------------------------------------------
# VFX_Grade node group
# ---------------------------------------------------------------------

def build_vfx_grade_group():
    """Create or get the VFX_Grade node group.

    NO ColorBalance — only MixRGB + HueSat + BrightContrast.
    At neutral defaults the chain is a perfect passthrough.
    """
    ng = bpy.data.node_groups.get("VFX_Grade")
    if ng:
        req = {"VFX_MIX_EXP", "VFX_MIX_LIFT", "VFX_HS", "VFX_CT"}
        present = {n.name for n in ng.nodes}
        if req.issubset(present) and len(ng.links) >= 3:
            return ng
        _log("VFX_Grade invalid, recreating")
        try:
            bpy.data.node_groups.remove(ng)
        except Exception:
            pass

    _log("Creating VFX_Grade")
    try:
        ng = bpy.data.node_groups.new("VFX_Grade", 'CompositorNodeTree')
    except Exception as e:
        _log(f"ERROR: {e}")
        return None

    # --- Interface sockets ---
    sockets_def = [
        ("Image",        'INPUT',  'NodeSocketColor'),
        ("Enable",       'INPUT',  'NodeSocketBool'),
        ("CombinedGain", 'INPUT',  'NodeSocketColor'),
        ("Lift",         'INPUT',  'NodeSocketColor'),
        ("Saturation",   'INPUT',  'NodeSocketFloat'),
        ("Contrast",     'INPUT',  'NodeSocketFloat'),
        ("Image",        'OUTPUT', 'NodeSocketColor'),
    ]
    for name, io, st in sockets_def:
        try:
            ng.interface.new_socket(name, in_out=io, socket_type=st)
        except Exception as e:
            _log(f"socket error {name}: {e}")

    # Set neutral defaults on interface
    for item in ng.interface.items_tree:
        if item.in_out == 'INPUT':
            if item.name == "Enable":
                item.default_value = 1
            elif item.name == "CombinedGain":
                item.default_value = (1.0, 1.0, 1.0, 1.0)
            elif item.name == "Lift":
                item.default_value = (0.0, 0.0, 0.0, 1.0)
            elif item.name == "Saturation":
                item.default_value = 1.0
            elif item.name == "Contrast":
                item.default_value = 1.0

    gin = ng.nodes.new("NodeGroupInput")
    gin.location = (-700, 0)
    gout = ng.nodes.new("NodeGroupOutput")
    gout.location = (700, 0)

    # --- Mix 1: Exposure + White Balance (Multiply) ---
    mix_exp = _make_mix_rgb(ng, (-400, 200), 'MULTIPLY', "VFX_MIX_EXP")
    a_exp, b_exp, fac_exp, out_exp = _get_mix_sockets(mix_exp)

    # --- Mix 2: Lift (Add) ---
    mix_lift = _make_mix_rgb(ng, (-100, 200), 'ADD', "VFX_MIX_LIFT")
    a_lift, b_lift, fac_lift, out_lift = _get_mix_sockets(mix_lift)

    # --- HueSat ---
    hs = _new_node(ng, "CompositorNodeHueSat")
    if hs:
        hs.name = "VFX_HS"
        hs.label = "Saturation"
        hs.location = (150, 200)

    # --- BrightContrast ---
    ct = _new_node(ng, "CompositorNodeBrightContrast")
    if ct:
        ct.name = "VFX_CT"
        ct.label = "Contrast"
        ct.location = (350, 200)

    # --- Alpha preservation: SeparateColor (original) ---
    sep_orig = _new_node(ng, "ShaderNodeSeparateColor", "CompositorNodeSeparateColor")
    if sep_orig:
        sep_orig.name = "VFX_SEP_ORIG"
        sep_orig.location = (-500, -200)

    # --- Alpha preservation: SeparateColor (graded) ---
    sep_grad = _new_node(ng, "ShaderNodeSeparateColor", "CompositorNodeSeparateColor")
    if sep_grad:
        sep_grad.name = "VFX_SEP_GRADED"
        sep_grad.location = (350, -200)

    # --- Alpha preservation: CombineColor (graded RGB + original A) ---
    comb_out = _new_node(ng, "ShaderNodeCombineColor", "CompositorNodeCombineColor")
    if comb_out:
        comb_out.name = "VFX_COMB_OUT"
        comb_out.location = (550, -200)

    # ======== LINKS ========

    img_in = _find_socket(gin, "Image", 'output')
    img_out = _find_socket(gout, "Image", 'input')

    # Branch 1: Original alpha preservation
    if img_in and sep_orig:
        _link(ng, img_in, sep_orig.inputs[0])

    # Branch 2: Grade chain
    # Image → MixRGB_Mult(CombinedGain) → MixRGB_Add(Lift) → HueSat → BC
    grade_out = img_in

    # Exposure + WB: Image * CombinedGain
    if a_exp and img_in:
        _link(ng, img_in, a_exp)
    cg = _find_socket(gin, "CombinedGain", 'output')
    if b_exp and cg:
        _link(ng, cg, b_exp)
    if out_exp:
        grade_out = out_exp

    # Lift: prev + Lift_color
    if a_lift and grade_out:
        _link(ng, grade_out, a_lift)
    lv = _find_socket(gin, "Lift", 'output')
    if b_lift and lv:
        _link(ng, lv, b_lift)
    if out_lift:
        grade_out = out_lift

    # Saturation
    if hs:
        hs_img_in = _find_socket(hs, "Image", 'input')
        if hs_img_in and grade_out:
            _link(ng, grade_out, hs_img_in)
        src_sat = _find_socket(gin, "Saturation", 'output')
        hs_sat = _find_socket(hs, "Saturation", 'input')
        if src_sat and hs_sat:
            _link(ng, src_sat, hs_sat)
        hs_img_out = _find_socket(hs, "Image", 'output')
        if hs_img_out:
            grade_out = hs_img_out

    # Contrast
    if ct:
        ct_img_in = _find_socket(ct, "Image", 'input')
        if ct_img_in and grade_out:
            _link(ng, grade_out, ct_img_in)
        src_con = _find_socket(gin, "Contrast", 'output')
        ct_con = _find_socket(ct, "Contrast", 'input')
        if src_con and ct_con:
            _link(ng, src_con, ct_con)
        ct_img_out = _find_socket(ct, "Image", 'output')
        if ct_img_out:
            grade_out = ct_img_out

    # Alpha-safe output: graded RGB + original alpha
    if grade_out and sep_grad:
        _link(ng, grade_out, sep_grad.inputs[0])
    if sep_grad and comb_out:
        _link(ng, sep_grad.outputs[0], comb_out.inputs[0])  # R
        _link(ng, sep_grad.outputs[1], comb_out.inputs[1])  # G
        _link(ng, sep_grad.outputs[2], comb_out.inputs[2])  # B
    if sep_orig and comb_out and len(sep_orig.outputs) > 3:
        _link(ng, sep_orig.outputs[3], comb_out.inputs[3])  # A
    if comb_out and img_out:
        _link(ng, comb_out.outputs[0], img_out)

    # Fallback: direct connect if alpha nodes missing
    if (not sep_grad or not comb_out) and grade_out and img_out:
        _link(ng, grade_out, img_out)

    _log(f"Created: {len(ng.nodes)} nodes, {len(ng.links)} links")
    for l in ng.links:
        _log(f"  {l.from_node.name}.{l.from_socket.name} -> {l.to_node.name}.{l.to_socket.name}")

    return ng


# ---------------------------------------------------------------------
# Apply values to grade node INPUT SOCKETS
# ---------------------------------------------------------------------

def apply_grade_values(grade_node, source, prefix='l_'):
    """Write property values to the grade node instance's input sockets.

    Each instance (per-layer or master) has independent values.
    Neutral defaults = passthrough (no visible change).
    """
    if not grade_node:
        return

    exposure = getattr(source, f'{prefix}exposure', 0)
    temp = getattr(source, f'{prefix}temp', 0)
    tint = getattr(source, f'{prefix}tint', 0)
    gain = _compute_gain(exposure, temp, tint)

    for s in grade_node.inputs:
        try:
            if s.name == 'CombinedGain':
                s.default_value = gain + (1.0,)
            elif s.name == 'Lift':
                v = getattr(source, f'{prefix}lift', (0, 0, 0))
                s.default_value = tuple(v) + (1.0,) if len(v) == 3 else tuple(v)
            elif s.name == 'Saturation':
                s.default_value = getattr(source, f'{prefix}sat', 1)
            elif s.name == 'Contrast':
                s.default_value = getattr(source, f'{prefix}contrast', 1)
        except Exception as e:
            _log(f"Error setting {s.name}: {e}")


# ---------------------------------------------------------------------
# Create / reuse grade nodes in comp tree
# ---------------------------------------------------------------------

def _create_grade_node(nt, name, label, location):
    ng = build_vfx_grade_group()
    if not ng:
        return None
    node = nt.nodes.get(name)
    if node is None:
        for bid in ("CompositorNodeGroup", "ShaderNodeGroup", "NodeGroup"):
            try:
                node = nt.nodes.new(bid)
                break
            except Exception:
                continue
        if node:
            node.name = name
    if node:
        try:
            node.node_tree = ng
        except Exception:
            pass
        node.label = label
        node.location = location
    return node


def ensure_layer_grades(vfx, master, nt):
    """Create per-layer grade nodes. Returns dict {layer_id: grade_node}."""
    grades = {}
    x = 200
    for layer in vfx.layers:
        if not (layer.enabled and layer.scene):
            continue
        if not getattr(layer, 'grade_enable', False):
            existing = nt.nodes.get(f"VFX_GRADE_{layer.id}")
            if existing:
                nt.nodes.remove(existing)
            continue
        gn = _create_grade_node(
            nt, f"VFX_GRADE_{layer.id}",
            f"GRADE: {layer.layer_name}", (x, 0)
        )
        if gn:
            apply_grade_values(gn, layer, 'l_')
            grades[layer.id] = gn
        x += 250
    return grades


def ensure_master_grade(vfx, master, nt):
    """Create master grade node. Returns node or None."""
    if not getattr(vfx, 'm_grade_enable', False):
        existing = nt.nodes.get("VFX_GRADE_MASTER")
        if existing:
            nt.nodes.remove(existing)
        return None
    gn = _create_grade_node(nt, "VFX_GRADE_MASTER", "MASTER GRADE", (2200, 0))
    if gn:
        apply_grade_values(gn, vfx, 'm_')
    return gn
