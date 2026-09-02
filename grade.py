"""VFX Layer Tools — Two-level color grading system.

Architecture:
  GroupInput.Image → MixRGB(Multiply, CombinedGain) → MixRGB(Multiply, WBTint)
  → ColorBalance(LGG) → HueSat → BrightContrast → alpha-safe output

Exposure + White Balance computed in Python → passed as COLOR inputs.
Lift/Gamma/Gain via ColorBalance (float inputs force-neutralized).
Saturation via HueSat. Contrast via BrightContrast.
Alpha-safe: original alpha preserved via SeparateColor + CombineColor.
"""

import bpy


def _log(msg):
    print(f"VFX GRADE: {msg}")


def _find_sock(node, name, kind='input', prefer_color=False, index_hint=None):
    """Find socket by name with optional color preference."""
    if not node:
        return None
    coll = node.inputs if kind == 'input' else node.outputs
    first_match = None
    for i, s in enumerate(coll):
        if s.name == name or s.name.lower() == name.lower():
            if prefer_color and s.type in ('COLOR', 'RGBA'):
                return s
            if first_match is None:
                first_match = s
    return first_match


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


def _compute_wb_gain(exposure, temp, tint):
    """Compute combined gain color: 2^EV * temperature/tint adjustment."""
    ev = 2.0 ** exposure
    temp_r = 1.0 + temp * 0.5
    temp_b = 1.0 - temp * 0.5
    tint_g = 1.0 + tint * 0.5
    return (ev * temp_r, ev * tint_g, ev * temp_b)


def _force_neutralize_cb(cb):
    """Force ALL ColorBalance float inputs to neutral.

    Blender 5.x ColorBalance defaults:
      Lift float  = 0.750 (NOT neutral!)
      Gamma float = 1.317 (NOT neutral!)
      Gain float  = 1.0

    Neutral values: Lift=0, Gamma=1, Gain=1, Factor=1
    """
    if cb is None:
        return
    for s in cb.inputs:
        nl = s.name.lower() if s.name else ""
        if s.type == 'VALUE':
            if 'factor' in nl:
                s.default_value = 1.0
            elif 'lift' in nl:
                s.default_value = 0.0
            elif 'gamma' in nl:
                s.default_value = 1.0
            elif 'gain' in nl:
                s.default_value = 1.0


def _make_mix_node(ng, location):
    """Create a MixRGB or ShaderNodeMix for blending."""
    mix = _new_node(ng, "CompositorNodeMixRGB", "ShaderNodeMix")
    if mix is None:
        return None, None, None, None, None
    mix.location = location

    if mix.bl_idname == 'ShaderNodeMix':
        try:
            mix.data_type = 'RGBA'
        except Exception:
            pass

    # Find sockets
    fac_in = None
    a_in = None
    b_in = None
    out_s = None

    for s in mix.inputs:
        if fac_in is None and s.type == 'VALUE' and s.name == 'Factor':
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

    return mix, fac_in, a_in, b_in, out_s


# ---------------------------------------------------------------------
# VFX_Grade node group
# ---------------------------------------------------------------------

def build_vfx_grade_group():
    """Create or get the VFX_Grade node group.

    Chain (linear, alpha-safe):
      Image → MixRGB_Mult(Gain) → MixRGB_Mult(WBTint)
      → ColorBalance(LGG) → HueSat → BrightContrast
      → alpha-safe output (original alpha preserved)
    """
    ng = bpy.data.node_groups.get("VFX_Grade")
    if ng:
        req = {"VFX_CB_LGG", "VFX_HS", "VFX_CT"}
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
        ("Image",       'INPUT',  'NodeSocketColor'),
        ("Enable",      'INPUT',  'NodeSocketBool'),
        ("CombinedGain", 'INPUT', 'NodeSocketColor'),
        ("WBTint",      'INPUT',  'NodeSocketColor'),
        ("Lift",        'INPUT',  'NodeSocketColor'),
        ("Gamma",       'INPUT',  'NodeSocketColor'),
        ("Gain",        'INPUT',  'NodeSocketColor'),
        ("Saturation",  'INPUT',  'NodeSocketFloat'),
        ("Contrast",    'INPUT',  'NodeSocketFloat'),
        ("Image",       'OUTPUT', 'NodeSocketColor'),
    ]
    for name, io, st in sockets_def:
        try:
            ng.interface.new_socket(name, in_out=io, socket_type=st)
        except Exception as e:
            _log(f"socket create error {name}: {e}")

    # Set defaults
    for item in ng.interface.items_tree:
        if item.name == "Enable" and item.in_out == 'INPUT':
            item.default_value = 1
        if item.name == "Saturation" and item.in_out == 'INPUT':
            item.default_value = 1.0
        if item.name == "Contrast" and item.in_out == 'INPUT':
            item.default_value = 1.0
        if item.name == "CombinedGain" and item.in_out == 'INPUT':
            item.default_value = (1, 1, 1, 1)
        if item.name == "WBTint" and item.in_out == 'INPUT':
            item.default_value = (1, 1, 1, 1)
        if item.name == "Lift" and item.in_out == 'INPUT':
            item.default_value = (0, 0, 0, 1)
        if item.name == "Gamma" and item.in_out == 'INPUT':
            item.default_value = (0.5, 0.5, 0.5, 1)
        if item.name == "Gain" and item.in_out == 'INPUT':
            item.default_value = (1, 1, 1, 1)

    gin = ng.nodes.new("NodeGroupInput")
    gin.location = (-700, 0)
    gout = ng.nodes.new("NodeGroupOutput")
    gout.location = (700, 0)

    # --- Exposure: MixRGB(Multiply) with CombinedGain ---
    mix_exp, exp_fac, exp_a, exp_b, exp_out = _make_mix_node(ng, (-400, 200))
    if mix_exp:
        mix_exp.name = "VFX_MIX_EXP"
        mix_exp.label = "Exposure"
        # Set multiply blend mode
        for attr in ("blend_type", "blend_mode"):
            try:
                setattr(mix_exp, attr, 'MULTIPLY')
                break
            except Exception:
                pass
        # Factor = 1.0 (full effect)
        if exp_fac:
            exp_fac.default_value = 1.0

    # --- White Balance tint: MixRGB(Multiply) with WBTint ---
    mix_wb, wb_fac, wb_a, wb_b, wb_out = _make_mix_node(ng, (-100, 200))
    if mix_wb:
        mix_wb.name = "VFX_MIX_WB"
        mix_wb.label = "White Balance"
        for attr in ("blend_type", "blend_mode"):
            try:
                setattr(mix_wb, attr, 'MULTIPLY')
                break
            except Exception:
                pass
        if wb_fac:
            wb_fac.default_value = 1.0

    # --- ColorBalance: Lift/Gamma/Gain ---
    cb_lgg = _new_node(ng, "CompositorNodeColorBalance")
    if cb_lgg:
        cb_lgg.name = "VFX_CB_LGG"
        cb_lgg.label = "Lift/Gamma/Gain"
        cb_lgg.location = (150, 200)
        _force_neutralize_cb(cb_lgg)

    # --- HueSat ---
    hs = _new_node(ng, "CompositorNodeHueSat")
    if hs:
        hs.name = "VFX_HS"
        hs.label = "Saturation"
        hs.location = (350, 200)

    # --- BrightContrast ---
    ct = _new_node(ng, "CompositorNodeBrightContrast")
    if ct:
        ct.name = "VFX_CT"
        ct.label = "Contrast"
        ct.location = (500, 200)

    # --- Alpha preservation nodes ---
    sep_orig = _new_node(ng, "ShaderNodeSeparateColor", "CompositorNodeSeparateColor")
    if sep_orig:
        sep_orig.name = "VFX_SEP_ORIG"
        sep_orig.location = (-400, -200)

    sep_grad = _new_node(ng, "ShaderNodeSeparateColor", "CompositorNodeSeparateColor")
    if sep_grad:
        sep_grad.name = "VFX_SEP_GRADED"
        sep_grad.location = (500, -200)

    comb_out = _new_node(ng, "ShaderNodeCombineColor", "CompositorNodeCombineColor")
    if comb_out:
        comb_out.name = "VFX_COMB_OUT"
        comb_out.location = (700, -200)

    # ======== LINKS ========

    img_in = _find_sock(gin, "Image", 'output')
    img_out = _find_sock(gout, "Image", 'input')

    # Branch 1: Original alpha preservation
    if img_in and sep_orig:
        _link(ng, img_in, sep_orig.inputs[0])

    # Branch 2: Grade chain
    # Image → Exposure Mix (A=input, B=CombinedGain)
    grade_out = img_in
    if exp_a and img_in:
        _link(ng, img_in, exp_a)
    cg = _find_sock(gin, "CombinedGain", 'output')
    if exp_b and cg:
        _link(ng, cg, exp_b)
    if exp_out:
        grade_out = exp_out

    # → WB Mix (A=prev, B=WBTint)
    if wb_a and grade_out:
        _link(ng, grade_out, wb_a)
    wt = _find_sock(gin, "WBTint", 'output')
    if wb_b and wt:
        _link(ng, wt, wb_b)
    if wb_out:
        grade_out = wb_out

    # → ColorBalance (LGG)
    if cb_lgg:
        cb_img_in = _find_sock(cb_lgg, "Image", 'input')
        if cb_img_in and grade_out:
            _link(ng, grade_out, cb_img_in)
        # Connect COLOR inputs for Lift/Gamma/Gain
        for name in ("Lift", "Gamma", "Gain"):
            src = _find_sock(gin, name, 'output')
            dst = _find_sock(cb_lgg, name, 'input', prefer_color=True)
            if src and dst:
                _link(ng, src, dst)
            elif src and dst is None:
                # Fallback: find by index (ColorBalance has float + color for each)
                _log(f"WARN: no color socket for {name} on CB_LGG, trying all")
                for s in cb_lgg.inputs:
                    if s.name == name:
                        _link(ng, src, s)
                        break
        cb_img_out = _find_sock(cb_lgg, "Image", 'output')
        if cb_img_out:
            grade_out = cb_img_out
        # Re-neutralize float inputs (ColorBalance might reset them)
        _force_neutralize_cb(cb_lgg)

    # → HueSat
    if hs:
        hs_img_in = _find_sock(hs, "Image", 'input')
        if hs_img_in and grade_out:
            _link(ng, grade_out, hs_img_in)
        src_sat = _find_sock(gin, "Saturation", 'output')
        hs_sat = _find_sock(hs, "Saturation", 'input')
        if src_sat and hs_sat:
            _link(ng, src_sat, hs_sat)
        hs_img_out = _find_sock(hs, "Image", 'output')
        if hs_img_out:
            grade_out = hs_img_out

    # → BrightContrast
    if ct:
        ct_img_in = _find_sock(ct, "Image", 'input')
        if ct_img_in and grade_out:
            _link(ng, grade_out, ct_img_in)
        src_con = _find_sock(gin, "Contrast", 'output')
        ct_con = _find_sock(ct, "Contrast", 'input')
        if src_con and ct_con:
            _link(ng, src_con, ct_con)
        ct_img_out = _find_sock(ct, "Image", 'output')
        if ct_img_out:
            grade_out = ct_img_out

    # Alpha-safe output: graded RGB + original alpha → CombineColor → Output
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

    # Fallback: if alpha-safe nodes missing, connect directly
    if (not sep_grad or not comb_out) and grade_out and img_out:
        _link(ng, grade_out, img_out)

    _log(f"Created: {len(ng.nodes)} nodes, {len(ng.links)} links")
    _log("Nodes: " + ", ".join(n.name for n in ng.nodes))
    _log("Links:")
    for l in ng.links:
        _log(f"  {l.from_node.name}.{l.from_socket.name} -> {l.to_node.name}.{l.to_socket.name}")

    return ng


# ---------------------------------------------------------------------
# Apply values to INPUT SOCKETS (independent per instance)
# ---------------------------------------------------------------------

def apply_grade_values(grade_node, source, prefix='l_'):
    """Write values to the grade node's INPUT SOCKETS.

    Each instance (per-layer and master) has its own socket values.
    CombinedGain and WBTint computed in Python from exposure/temp/tint.
    """
    if not grade_node:
        return

    exposure = getattr(source, f'{prefix}exposure', 0)
    temp = getattr(source, f'{prefix}temp', 0)
    tint = getattr(source, f'{prefix}tint', 0)
    gain = _compute_wb_gain(exposure, temp, tint)

    # WB tint: just the tint adjustment (green/magenta shift)
    tint_r = 1.0
    tint_g = 1.0 + tint * 0.3
    tint_b = 1.0

    for s in grade_node.inputs:
        try:
            if s.name == 'CombinedGain':
                s.default_value = gain + (1.0,)
            elif s.name == 'WBTint':
                s.default_value = (tint_r, tint_g, tint_b, 1.0)
            elif s.name == 'Lift':
                v = getattr(source, f'{prefix}lift', (0, 0, 0))
                s.default_value = tuple(v) + (1.0,) if len(v) == 3 else tuple(v)
            elif s.name == 'Gamma':
                v = getattr(source, f'{prefix}gamma', (0.5, 0.5, 0.5))
                s.default_value = tuple(v) + (1.0,) if len(v) == 3 else tuple(v)
            elif s.name == 'Gain':
                v = getattr(source, f'{prefix}gain', (1, 1, 1))
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
