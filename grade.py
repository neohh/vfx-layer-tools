"""VFX Layer Tools — Two-level color grading system.

Per-layer grade: before FOG group, for matching layers.
Master grade: after all post-effects, for final look.

VFX_Grade node group chain (Blender 5.x compatible):
  GroupInput → CB1(Exp+WB) → CB2(LGG) → HueSat → BrightContrast → GroupOutput

No CompositorNodeMix/CompositorNodeMixRGB (don't exist in Blender 5.x compositor).
"""

import bpy
import traceback


def _log(msg):
    print(f"VFX GRADE: {msg}")


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _safe_set_color(node, attr, rgb):
    """Set a color property, trying different tuple sizes."""
    for vals in (tuple(rgb) + (1.0,), tuple(rgb)):
        try:
            setattr(node, attr, vals)
            return
        except Exception:
            continue


def _compute_wb_gain(exposure, temp, tint):
    """Compute gain color for exposure + white balance.

    exposure: EV stops (2^EV is the multiplier)
    temp: -1..1, positive=warm(red+), negative=cool(blue+)
    tint: -1..1, positive=green+, negative=magenta+
    """
    ev = 2.0 ** exposure
    temp_r = 1.0 + temp * 0.5
    temp_b = 1.0 - temp * 0.5
    tint_g = 1.0 + tint * 0.5
    return (ev * temp_r, ev * tint_g, ev * temp_b)


def _find_sock(node, name, kind='input'):
    """Find socket by name (case-insensitive)."""
    if not node:
        return None
    coll = node.inputs if kind == 'input' else node.outputs
    for s in coll:
        if s.name == name:
            return s
    for s in coll:
        if s.name.lower() == name.lower():
            return s
    return None


def _link(ng, a, b):
    """Create a link, logging errors."""
    if not a or not b:
        return
    try:
        ng.links.new(a, b)
    except Exception as e:
        _log(f"Link FAIL: {e}")


# ---------------------------------------------------------------------
# VFX_Grade node group
# ---------------------------------------------------------------------

def build_vfx_grade_group():
    """Create or get the VFX_Grade reusable node group.

    Chain: GroupInput → CB1(Exp+WB) → CB2(LGG) → HueSat → BrightContrast → GroupOutput
    """
    ng = bpy.data.node_groups.get("VFX_Grade")
    if ng:
        req = {"VFX_CB_EXP_WB", "VFX_CB_LGG", "VFX_HS"}
        present = {n.name for n in ng.nodes}
        if req.issubset(present) and len(ng.links) >= 2:
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
        _log(f"ERROR creating node group: {e}")
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
        except Exception as e:
            _log(f"Socket fail: {name}: {e}")

    defaults = {
        "Enable": 1, "Exposure": 0, "Temperature": 0, "Tint": 0,
        "Lift": (0, 0, 0, 1), "Gamma": (0.5, 0.5, 0.5, 1),
        "Gain": (1, 1, 1, 1), "Saturation": 1, "Contrast": 1,
    }
    for item in ng.interface.items_tree:
        if item.name in defaults and item.in_out == 'INPUT':
            item.default_value = defaults[item.name]

    # --- Nodes ---
    gin = ng.nodes.new("NodeGroupInput")
    gin.location = (-600, 0)
    gout = ng.nodes.new("NodeGroupOutput")
    gout.location = (600, 0)

    # CB1: Exposure + White Balance (via gain)
    cb1 = None
    try:
        cb1 = ng.nodes.new("CompositorNodeColorBalance")
        cb1.name = "VFX_CB_EXP_WB"
        cb1.label = "Exp+WB"
        cb1.location = (-300, 200)
    except Exception as e:
        _log(f"CB1 fail: {e}")

    # CB2: Lift/Gamma/Gain
    cb2 = None
    try:
        cb2 = ng.nodes.new("CompositorNodeColorBalance")
        cb2.name = "VFX_CB_LGG"
        cb2.label = "Lift/Gamma/Gain"
        cb2.location = (0, 200)
    except Exception as e:
        _log(f"CB2 fail: {e}")

    # HueSat: Saturation
    hs = None
    try:
        hs = ng.nodes.new("CompositorNodeHueSat")
        hs.name = "VFX_HS"
        hs.label = "Saturation"
        hs.location = (300, 200)
    except Exception as e:
        _log(f"HueSat fail: {e}")

    # BrightContrast: Contrast
    ct = None
    try:
        ct = ng.nodes.new("CompositorNodeBrightContrast")
        ct.name = "VFX_CONTRAST"
        ct.label = "Contrast"
        ct.location = (500, 200)
    except Exception as e:
        _log(f"BrightContrast fail: {e}")

    # --- Link chain ---
    cur = _find_sock(gin, "Image", 'output')
    for node in (cb1, cb2, hs, ct):
        if node:
            inp = _find_sock(node, "Image", 'input')
            if inp:
                _link(ng, cur, inp)
            cur = _find_sock(node, "Image", 'output') or cur
    out_sock = _find_sock(gout, "Image", 'input')
    if cur and out_sock:
        _link(ng, cur, out_sock)

    _log(f"Created: {len(ng.nodes)} nodes, {len(ng.links)} links")
    return ng


# ---------------------------------------------------------------------
# Apply values
# ---------------------------------------------------------------------

def apply_grade_values(grade_node, source):
    """Set grade socket values from a source (VFXLayer or VFXProject)."""
    if not grade_node or not grade_node.node_tree:
        return
    ng = grade_node.node_tree

    # CB1: Exposure + White Balance
    cb1 = ng.nodes.get("VFX_CB_EXP_WB")
    if cb1:
        exp = getattr(source, 'g_exposure', 0)
        temp = getattr(source, 'g_temp', 0)
        tint = getattr(source, 'g_tint', 0)
        _safe_set_color(cb1, "gain", _compute_wb_gain(exp, temp, tint))
        _safe_set_color(cb1, "lift", (0, 0, 0))
        _safe_set_color(cb1, "gamma", (1, 1, 1))

    # CB2: Lift/Gamma/Gain
    cb2 = ng.nodes.get("VFX_CB_LGG")
    if cb2:
        _safe_set_color(cb2, "lift", getattr(source, 'g_lift', (0, 0, 0)))
        _safe_set_color(cb2, "gamma", getattr(source, 'g_gamma', (0.5, 0.5, 0.5)))
        _safe_set_color(cb2, "gain", getattr(source, 'g_gain', (1, 1, 1)))

    # HueSat: Saturation
    hs = ng.nodes.get("VFX_HS")
    if hs:
        for s in hs.inputs:
            if s.name.lower() == 'saturation':
                try:
                    s.default_value = getattr(source, 'g_saturation', 1)
                except Exception:
                    pass

    # BrightContrast: Contrast
    # BrightContrast formula: result = (x-0.5)*(contrast/100+1)+0.5
    # Our formula: (x-0.5)*C+0.5  →  contrast = (C-1)*100
    ct = ng.nodes.get("VFX_CONTRAST")
    if ct:
        c = getattr(source, 'g_contrast', 1)
        val = (c - 1.0) * 100.0
        try:
            ct.contrast = val
        except Exception:
            for s in ct.inputs:
                if s.name.lower() == 'contrast':
                    try:
                        s.default_value = val
                    except Exception:
                        pass


# ---------------------------------------------------------------------
# Per-layer grades
# ---------------------------------------------------------------------

def _create_grade_node(nt, name, label, location):
    """Create or reuse a grade node in the comp tree."""
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
    """Create per-layer grade nodes. Returns dict {layer_id: grade_node}.

    Also connects source node → grade node for each layer.
    """
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
            apply_grade_values(gn, layer)
            # Connect source → grade
            source_node = nt.nodes.get(f"VFX_RL_{layer.id}")
            if source_node:
                src_out = _find_sock(source_node, "Image", 'output')
                gr_in = _find_sock(gn, "Image", 'input')
                if src_out and gr_in:
                    _link(nt, src_out, gr_in)
            grades[layer.id] = gn
        x += 250
    return grades


# ---------------------------------------------------------------------
# Master grade
# ---------------------------------------------------------------------

def ensure_master_grade(vfx, master, nt):
    """Create master grade node. Returns node or None."""
    if not getattr(vfx, 'm_grade_enable', False):
        existing = nt.nodes.get("VFX_GRADE_MASTER")
        if existing:
            nt.nodes.remove(existing)
        return None
    gn = _create_grade_node(nt, "VFX_GRADE_MASTER", "MASTER GRADE", (2200, 0))
    if gn:
        apply_grade_values(gn, vfx)
    return gn
