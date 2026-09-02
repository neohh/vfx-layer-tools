"""VFX Layer Tools — Color correction / plate matching.

Blender 5.x compatibility:
- CompositorNodeColorBalance has no correction_method (always Lift/Gamma/Gain)
- CompositorNodeMix / CompositorNodeMixRGB don't exist in compositor
- Strength is applied by blending preset values toward neutral in Python
"""

import bpy
import traceback


# ---------------------------------------------------------------------
# NEUTRAL values (passthrough)
# ---------------------------------------------------------------------
NEUTRAL = {
    'lift': (0.0, 0.0, 0.0),
    'gamma': (1.0, 1.0, 1.0),
    'gain': (1.0, 1.0, 1.0),
    'hue': 0.5,
    'saturation': 1.0,
}

# ---------------------------------------------------------------------
# PRESETS: (lift, gamma, gain) RGB tuples + hue/sat adjustments
# ---------------------------------------------------------------------

PRESETS = {
    'NONE': {
        'label': 'Off',
        'lift': (0.0, 0.0, 0.0),
        'gamma': (1.0, 1.0, 1.0),
        'gain': (1.0, 1.0, 1.0),
        'hue': 0.5,
        'saturation': 1.0,
    },
    'WARM': {
        'label': 'Warm',
        'lift': (0.02, 0.01, 0.0),
        'gamma': (0.52, 0.48, 0.45),
        'gain': (1.05, 1.0, 0.92),
        'hue': 0.5,
        'saturation': 1.1,
    },
    'TEAL_ORANGE': {
        'label': 'Teal & Orange',
        'lift': (-0.02, 0.01, 0.04),
        'gamma': (0.48, 0.50, 0.53),
        'gain': (1.08, 1.0, 0.88),
        'hue': 0.5,
        'saturation': 1.15,
    },
    'COOL': {
        'label': 'Cool',
        'lift': (0.0, 0.01, 0.03),
        'gamma': (0.47, 0.49, 0.53),
        'gain': (0.92, 0.98, 1.08),
        'hue': 0.5,
        'saturation': 0.95,
    },
    'FILM': {
        'label': 'Film',
        'lift': (0.03, 0.02, 0.01),
        'gamma': (0.50, 0.48, 0.46),
        'gain': (1.02, 1.0, 0.95),
        'hue': 0.48,
        'saturation': 0.9,
    },
}


def _log(msg):
    print(f"VFX COLORMATCH: {msg}")


def _lerp3(a, b, t):
    """Linearly interpolate two 3-tuples."""
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def _lerp(a, b, t):
    """Linearly interpolate two floats."""
    return a + (b - a) * t


# ---------------------------------------------------------------------
# NODE GROUP: GroupInput → ColorBalance → HueSat → GroupOutput
# No Mix node — strength is baked into preset values by apply_preset()
# ---------------------------------------------------------------------

def get_or_create_color_match_group():
    """Get or create the VFX_ColorMatch node group."""
    ng = bpy.data.node_groups.get("VFX_ColorMatch")
    if ng is not None:
        if _validate_group(ng):
            _log(f"Existing group OK: {len(ng.nodes)} nodes, {len(ng.links)} links")
            return ng
        _log("Links broken, recreating group")
        try:
            bpy.data.node_groups.remove(ng)
        except Exception:
            pass

    _log("Creating new VFX_ColorMatch node group")

    try:
        ng = bpy.data.node_groups.new("VFX_ColorMatch", 'CompositorNodeTree')
    except Exception as e:
        _log(f"ERROR creating node group: {e}")
        return None

    # Interface sockets
    try:
        ng.interface.new_socket("Image", in_out='INPUT', socket_type='NodeSocketColor')
        ng.interface.new_socket("Strength", in_out='INPUT', socket_type='NodeSocketFloat')
        ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    except Exception as e:
        _log(f"ERROR creating interface sockets: {e}")
        return None

    for item in ng.interface.items_tree:
        if item.name == "Strength" and item.in_out == 'INPUT':
            item.default_value = 1.0

    gin = ng.nodes.new("NodeGroupInput")
    gin.location = (-600, 0)
    gout = ng.nodes.new("NodeGroupOutput")
    gout.location = (400, 0)

    # Color Balance (Lift/Gamma/Gain) — no correction_method in Blender 5.x
    cb = None
    try:
        cb = ng.nodes.new("CompositorNodeColorBalance")
        cb.name = "VFX_CB"
        cb.label = "Color Balance"
        cb.location = (-200, 0)
    except Exception as e:
        _log(f"ERROR creating CompositorNodeColorBalance: {e}")
        traceback.print_exc()

    if cb is not None:
        _safe_set_color(cb, "lift", NEUTRAL['lift'])
        _safe_set_color(cb, "gamma", NEUTRAL['gamma'])
        _safe_set_color(cb, "gain", NEUTRAL['gain'])

    # Hue/Saturation
    hs = None
    try:
        hs = ng.nodes.new("CompositorNodeHueSat")
        hs.name = "VFX_HS"
        hs.label = "Hue/Sat"
        hs.location = (100, 0)
    except Exception as e:
        _log(f"ERROR creating CompositorNodeHueSat: {e}")

    # --- Debug: dump all sockets ---
    _dump_sockets(gin, "GroupInput")
    _dump_sockets(cb, "ColorBalance")
    _dump_sockets(hs, "HueSat")
    _dump_sockets(gout, "GroupOutput")

    # --- Build links: GroupInput → ColorBalance → HueSat → GroupOutput ---
    img_in = gin.outputs.get("Image")
    img_out = gout.inputs.get("Image")
    link_count = 0

    def _safe_link(from_sock, to_sock, desc):
        nonlocal link_count
        if from_sock is None or to_sock is None:
            _log(f"SKIP link {desc}: from={from_sock} to={to_sock}")
            return
        try:
            ng.links.new(from_sock, to_sock)
            link_count += 1
            _log(f"Link OK: {desc}")
        except Exception as e:
            _log(f"Link FAILED {desc}: {e}")

    # GroupInput.Image → ColorBalance.Image
    cb_img = _find_input_by_name_only(cb, 'Image')
    _safe_link(img_in, cb_img, "GroupIn.Image -> CB.Image")

    # ColorBalance → HueSat
    cb_out = _find_output_by_name_only(cb, 'Image')
    hs_img = _find_input_by_name_only(hs, 'Image')
    _safe_link(cb_out, hs_img, "CB.Image -> HS.Image")

    # HueSat → GroupOutput.Image
    hs_out = _find_output_by_name_only(hs, 'Image')
    _safe_link(hs_out, img_out, "HS.Image -> GroupOut.Image")

    _log(f"Group created: {len(ng.nodes)} nodes, {link_count} links")
    return ng


def _validate_group(ng):
    """Check that the node group has the required internal nodes and links."""
    required = {"VFX_CB", "VFX_HS"}
    present = {n.name for n in ng.nodes}
    if not required.issubset(present):
        _log(f"Validation FAIL: missing nodes {required - present}")
        return False
    if len(ng.links) < 2:
        _log(f"Validation FAIL: only {len(ng.links)} links (need >=2)")
        return False
    return True


# ---------------------------------------------------------------------
# Socket finders — name-first, type-agnostic
# ---------------------------------------------------------------------

def _find_input_by_name_only(node, name):
    """Find input socket by name only, ignoring type."""
    if node is None:
        return None
    for s in node.inputs:
        if s.name == name:
            return s
    name_lower = name.lower()
    for s in node.inputs:
        if s.name.lower() == name_lower:
            return s
    return None


def _find_output_by_name_only(node, name):
    """Find output socket by name only, ignoring type."""
    if node is None:
        return None
    for s in node.outputs:
        if s.name == name:
            return s
    name_lower = name.lower()
    for s in node.outputs:
        if s.name.lower() == name_lower:
            return s
    return None


def _dump_sockets(node, label=""):
    """Print all sockets on a node for debugging."""
    if node is None:
        _log(f"{label}: node is None")
        return
    parts = [f"{label} ({node.bl_idname})"]
    for s in node.inputs:
        parts.append(f"  IN: {s.name!r} type={s.type!r}")
    for s in node.outputs:
        parts.append(f"  OUT: {s.name!r} type={s.type!r}")
    _log("\n".join(parts))


# ---------------------------------------------------------------------
# PRESET APPLICATION — strength baked into values (no Mix node)
# ---------------------------------------------------------------------

def apply_preset(ng, preset_name, strength=1.0):
    """Apply a color correction preset to the VFX_ColorMatch group.

    Strength blends preset values toward neutral:
      strength=0 → neutral (passthrough)
      strength=1 → full preset
    """
    preset = PRESETS.get(preset_name)
    if preset is None:
        _log(f"apply_preset: unknown preset '{preset_name}'")
        return

    _log(f"apply_preset: {preset_name} strength={strength}")

    cb = ng.nodes.get("VFX_CB")
    if cb is None:
        _log("WARN: VFX_CB not found in group")
        return

    # Blend lift/gamma/gain toward neutral based on strength
    lift = _lerp3(NEUTRAL['lift'], preset['lift'], strength)
    gamma = _lerp3(NEUTRAL['gamma'], preset['gamma'], strength)
    gain = _lerp3(NEUTRAL['gain'], preset['gain'], strength)
    _safe_set_color(cb, "lift", lift)
    _safe_set_color(cb, "gamma", gamma)
    _safe_set_color(cb, "gain", gain)
    _log(f"  CB lift={lift} gamma={gamma} gain={gain}")

    # Blend hue/saturation toward neutral based on strength
    hs = ng.nodes.get("VFX_HS")
    if hs is not None:
        hue = _lerp(NEUTRAL['hue'], preset.get('hue', 0.5), strength)
        sat = _lerp(NEUTRAL['saturation'], preset.get('saturation', 1.0), strength)
        for s in hs.inputs:
            if s.name.lower() == 'hue':
                try:
                    s.default_value = hue
                except Exception:
                    pass
            if s.name.lower() == 'saturation':
                try:
                    s.default_value = sat
                except Exception:
                    pass
        _log(f"  HS hue={hue} sat={sat}")
    else:
        _log("WARN: VFX_HS not found in group")


def _safe_set_color(node, attr, rgb):
    """Set a color property, trying different tuple sizes."""
    for vals in (tuple(rgb) + (1.0,), tuple(rgb)):
        try:
            setattr(node, attr, vals)
            return
        except Exception:
            continue
