"""VFX Layer Tools — Two-level color grading system (alpha-safe).

Each grade node instance stores values in its own INPUT SOCKETS.
The internal chain reads from GroupInput. Per-layer and master are independent.
Alpha-safe: preserves original alpha, only grades RGB.
"""

import bpy


def _log(msg):
    print(f"VFX GRADE: {msg}")


def _find_sock(node, name, kind='input', prefer_color=False):
    """Find socket by name. If prefer_color=True, prefer COLOR/RGBA sockets
    over VALUE sockets (needed for ColorBalance which has float+color inputs
    with the same name like Lift/Gamma/Gain)."""
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


def _link(ng, a, b):
    if not a or not b:
        return
    try:
        ng.links.new(a, b)
    except Exception:
        pass


def _new_node(ng, *ids):
    for i in ids:
        try:
            return ng.nodes.new(i)
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------
# VFX_Grade node group (alpha-safe)
# ---------------------------------------------------------------------

def build_vfx_grade_group():
    """Create or get the VFX_Grade node group.

    Internal chain (all values read from exposed GroupInput sockets):
      Image ─┬─ SeparateColor(orig) → Alpha ──────────────────────┐
             │                                                     │
             └─ CB1(Exp) → CB2(WB) → CB3(LGG) → HS → CT          │
                          graded_image → SeparateColor(graded)     │
                          R,G,B ──────────────────────────────→ CombineColor → Output
    """
    ng = bpy.data.node_groups.get("VFX_Grade")
    if ng:
        req = {"VFX_CB_EXP", "VFX_CB_WB", "VFX_CB_LGG", "VFX_HS", "VFX_CT"}
        present = {n.name for n in ng.nodes}
        if req.issubset(present) and len(ng.links) >= 5:
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
    for name, io, st in [
        ("Image", 'INPUT', 'NodeSocketColor'),
        ("Enable", 'INPUT', 'NodeSocketBool'),
        ("Exposure", 'INPUT', 'NodeSocketFloat'),
        ("Temperature", 'INPUT', 'NodeSocketFloat'),
        ("Tint", 'INPUT', 'NodeSocketFloat'),
        ("Lift", 'INPUT', 'NodeSocketColor'),
        ("Gamma", 'INPUT', 'NodeSocketColor'),
        ("Gain", 'INPUT', 'NodeSocketColor'),
        ("Saturation", 'INPUT', 'NodeSocketFloat'),
        ("Contrast", 'INPUT', 'NodeSocketFloat'),
        ("Image", 'OUTPUT', 'NodeSocketColor'),
    ]:
        try:
            ng.interface.new_socket(name, in_out=io, socket_type=st)
        except Exception:
            pass
    for item in ng.interface.items_tree:
        if item.name == "Enable" and item.in_out == 'INPUT':
            item.default_value = 1

    gin = ng.nodes.new("NodeGroupInput")
    gin.location = (-1200, 0)
    gout = ng.nodes.new("NodeGroupOutput")
    gout.location = (1000, 0)

    # --- SeparateColor: original alpha (branch 1) ---
    sep_orig = _new_node(ng, "ShaderNodeSeparateColor", "CompositorNodeSeparateColor")
    if sep_orig:
        sep_orig.name = "VFX_SEP_ORIG"
        sep_orig.location = (-900, -300)

    # --- ColorBalance nodes ---
    cb1 = _new_node(ng, "CompositorNodeColorBalance")
    if cb1:
        cb1.name = "VFX_CB_EXP"; cb1.label = "Exposure"; cb1.location = (-200, 200)

    cb2 = _new_node(ng, "CompositorNodeColorBalance")
    if cb2:
        cb2.name = "VFX_CB_WB"; cb2.label = "WB"; cb2.location = (100, 200)

    cb3 = _new_node(ng, "CompositorNodeColorBalance")
    if cb3:
        cb3.name = "VFX_CB_LGG"; cb3.label = "LGG"; cb3.location = (400, 200)

    # --- HueSat + BrightContrast ---
    hs = _new_node(ng, "CompositorNodeHueSat")
    if hs:
        hs.name = "VFX_HS"; hs.label = "Sat"; hs.location = (600, 200)

    ct = _new_node(ng, "CompositorNodeBrightContrast")
    if ct:
        ct.name = "VFX_CT"; ct.label = "Contrast"; ct.location = (800, 200)

    # --- Math for Exposure: 2^EV ---
    mp = _new_node(ng, "CompositorNodeMath")
    if mp:
        mp.name = "VFX_POW"; mp.operation = 'POWER'; mp.location = (-600, 400)
        mp.inputs[0].default_value = 2.0

    # CombineColor: (EV, EV, EV) → color for CB1 gain
    ce = _new_node(ng, "CompositorNodeCombineColor", "ShaderNodeCombineColor")
    if ce:
        ce.name = "VFX_COMB_EXP"; ce.location = (-400, 400)

    # --- Math for White Balance ---
    ma_r = _new_node(ng, "CompositorNodeMath")
    if ma_r:
        ma_r.name = "VFX_ADD_R"; ma_r.operation = 'ADD'; ma_r.location = (-600, 100)
        ma_r.inputs[1].default_value = 1.0

    ma_g = _new_node(ng, "CompositorNodeMath")
    if ma_g:
        ma_g.name = "VFX_ADD_G"; ma_g.operation = 'ADD'; ma_g.location = (-600, 0)
        ma_g.inputs[1].default_value = 1.0

    ms_b = _new_node(ng, "CompositorNodeMath")
    if ms_b:
        ms_b.name = "VFX_SUB_B"; ms_b.operation = 'SUBTRACT'; ms_b.location = (-600, -100)
        ms_b.inputs[0].default_value = 1.0

    # CombineColor: (temp_r, tint_g, temp_b) → color for CB2 gain
    cw = _new_node(ng, "CompositorNodeCombineColor", "ShaderNodeCombineColor")
    if cw:
        cw.name = "VFX_COMB_WB"; cw.location = (-400, 0)

    # --- SeparateColor: graded RGB (branch 2) ---
    sep_grad = _new_node(ng, "ShaderNodeSeparateColor", "CompositorNodeSeparateColor")
    if sep_grad:
        sep_grad.name = "VFX_SEP_GRADED"
        sep_grad.location = (600, -300)

    # --- CombineColor: graded RGB + original alpha → output ---
    comb_out = _new_node(ng, "ShaderNodeCombineColor", "CompositorNodeCombineColor")
    if comb_out:
        comb_out.name = "VFX_COMB_OUT"
        comb_out.location = (800, -300)

    # ======== LINKS ========

    img_in = _find_sock(gin, "Image", 'output')
    img_out = _find_sock(gout, "Image", 'input')

    # Branch 1: Original image → SeparateColor_orig → orig_A
    if img_in and sep_orig:
        _link(ng, img_in, sep_orig.inputs[0])

    # Branch 2: Image → Grade chain → graded_image
    cur = img_in
    for node in (cb1, cb2, cb3, hs, ct):
        if node:
            inp = _find_sock(node, "Image", 'input')
            if inp:
                _link(ng, cur, inp)
            cur = _find_sock(node, "Image", 'output') or cur

    # graded_image → SeparateColor_graded → R, G, B
    if cur and sep_grad:
        _link(ng, cur, sep_grad.inputs[0])

    # CombineColor(graded_R, graded_G, graded_B, orig_A) → GroupOutput
    if sep_grad and comb_out:
        _link(ng, sep_grad.outputs[0], comb_out.inputs[0])  # R
        _link(ng, sep_grad.outputs[1], comb_out.inputs[1])  # G
        _link(ng, sep_grad.outputs[2], comb_out.inputs[2])  # B
    if sep_orig and comb_out and len(sep_orig.outputs) > 3:
        _link(ng, sep_orig.outputs[3], comb_out.inputs[3])  # A

    if comb_out and img_out:
        _link(ng, comb_out.outputs[0], img_out)

    # Fallback: if alpha-safe nodes missing, connect directly
    if (not sep_grad or not comb_out) and cur and img_out:
        _link(ng, cur, img_out)

    # Exposure: gin.Exposure → POW(2,exp) → CombineColor → CB1.Gain
    _link(ng, _find_sock(gin, "Exposure", 'output'),
          mp.inputs[1] if mp else None)
    if mp and ce:
        _link(ng, mp.outputs[0], ce.inputs[0])
        _link(ng, mp.outputs[0], ce.inputs[1])
        if len(ce.inputs) > 2:
            _link(ng, mp.outputs[0], ce.inputs[2])
    if ce and cb1:
        g = _find_sock(cb1, "Gain", 'input')
        if g:
            _link(ng, ce.outputs[0], g)

    # White Balance: gin.Temp → ADD_R, gin.Tint → ADD_G, 1-Temp → SUB_B → COMB_WB → CB2.Gain
    _link(ng, _find_sock(gin, "Temperature", 'output'),
          ma_r.inputs[0] if ma_r else None)
    _link(ng, _find_sock(gin, "Temperature", 'output'),
          ms_b.inputs[1] if ms_b else None)
    _link(ng, _find_sock(gin, "Tint", 'output'),
          ma_g.inputs[0] if ma_g else None)
    if ma_r and cw:
        _link(ng, ma_r.outputs[0], cw.inputs[0])
    if ma_g and cw:
        _link(ng, ma_g.outputs[0], cw.inputs[1])
    if ms_b and cw and len(cw.inputs) > 2:
        _link(ng, ms_b.outputs[0], cw.inputs[2])
    if cw and cb2:
        g = _find_sock(cb2, "Gain", 'input')
        if g:
            _link(ng, cw.outputs[0], g)

    # LGG: gin.Lift/Gamma/Gain → CB3.Lift/Gamma/Gain (COLOR inputs, not float)
    for name in ("Lift", "Gamma", "Gain"):
        src = _find_sock(gin, name, 'output')
        dst = _find_sock(cb3, name, 'input', prefer_color=True)
        if src and dst:
            _link(ng, src, dst)

    # Saturation
    _link(ng, _find_sock(gin, "Saturation", 'output'),
          _find_sock(hs, "Saturation", 'input') if hs else None)

    # Contrast
    _link(ng, _find_sock(gin, "Contrast", 'output'),
          _find_sock(ct, "Contrast", 'input') if ct else None)

    _log(f"Created: {len(ng.nodes)} nodes, {len(ng.links)} links")
    return ng


# ---------------------------------------------------------------------
# Apply values to INPUT SOCKETS (independent per instance)
# ---------------------------------------------------------------------

def apply_grade_values(grade_node, source, prefix='l_'):
    """Write values to the grade node's INPUT SOCKETS.

    Each instance (per-layer or master) has its own socket default_values.
    Changing one instance never affects another.
    """
    if not grade_node:
        return

    for s in grade_node.inputs:
        try:
            if s.name == 'Exposure':
                s.default_value = getattr(source, f'{prefix}exposure', 0)
            elif s.name == 'Temperature':
                s.default_value = getattr(source, f'{prefix}temp', 0)
            elif s.name == 'Tint':
                s.default_value = getattr(source, f'{prefix}tint', 0)
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
        except Exception:
            pass


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
            # Connect source image → grade input
            src = nt.nodes.get(f"VFX_RL_{layer.id}")
            if src:
                so = src.outputs.get("Image")
                gi = gn.inputs.get("Image")
                if so and gi:
                    try:
                        nt.links.new(so, gi)
                    except Exception:
                        pass
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
