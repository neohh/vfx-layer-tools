"""VFX Layer Tools — Color correction / plate matching (Blender 5.2 safe)."""

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


def _new(ng, *ids):
    for i in ids:
        try:
            return ng.nodes.new(i)
        except Exception:
            continue
    return None


def _rgba_in(node):
    for s in node.inputs:
        if s.type == 'RGBA':
            return s
    return None


def _rgba_out(node):
    for s in node.outputs:
        if s.type == 'RGBA':
            return s
    return None


def _mix_node(ng, name, loc, blend='MIX'):
    """Create/reuse a Mix node that works on both legacy and modern Blender."""
    mix = ng.nodes.get(name)
    if mix is None:
        mix = _new(ng, "CompositorNodeMixRGB", "ShaderNodeMix")
        if mix is None:
            return None, None, None, None, None
        mix.name = name
    mix.location = loc
    if mix.bl_idname == 'ShaderNodeMix':
        try:
            mix.data_type = 'RGBA'
        except Exception:
            pass
    try:
        mix.blend_type = blend
    except Exception:
        pass
    if mix.bl_idname == 'ShaderNodeMix':
        fac = a = b = out = None
        for s in mix.inputs:
            if fac is None and s.type == 'VALUE' and s.name == 'Factor':
                fac = s
            if s.type == 'RGBA' and s.name == 'A':
                a = s
            if s.type == 'RGBA' and s.name == 'B':
                b = s
        for s in mix.outputs:
            if s.type == 'RGBA' and out is None:
                out = s
        if a is not None and b is not None:
            return mix, fac, a, b, out
    return (mix, mix.inputs.get("Fac"), mix.inputs.get("Color1"),
            mix.inputs.get("Color2"), _rgba_out(mix))


def get_or_create_color_match_group():
    """Get or rebuild the VFX_ColorMatch node group (5.2-safe)."""
    ng = bpy.data.node_groups.get("VFX_ColorMatch")
    if ng is None:
        ng = bpy.data.node_groups.new("VFX_ColorMatch", 'CompositorNodeTree')
    ng.nodes.clear()
    try:
        ng.interface.items_clear()
    except Exception:
        try:
            ng.interface.clear()
        except Exception:
            pass
    ng.interface.new_socket("Image", in_out='INPUT', socket_type='NodeSocketColor')
    ng.interface.new_socket("Strength", in_out='INPUT', socket_type='NodeSocketFloat')
    ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    for item in ng.interface.items_tree:
        if item.name == "Strength" and item.in_out == 'INPUT':
            try:
                item.default_value = 1.0
            except Exception:
                pass

    gin = ng.nodes.new("NodeGroupInput")
    gin.location = (-600, 0)
    gout = ng.nodes.new("NodeGroupOutput")
    gout.location = (450, 0)
    img_in = gin.outputs.get("Image")
    img_out = gout.inputs.get("Image")
    link_count = 0

    cur = img_in

    # Stage 1: Color Balance (Lift/Gamma/Gain)
    cb = _new(ng, "CompositorNodeColorBalance")
    if cb is not None:
        cb.name = "VFX_CB"
        cb.label = "Color Balance"
        cb.location = (-350, 0)
        try:
            cb.correction_method = 'LIFT_GAMMA_GAIN'
        except Exception:
            pass
        i = _rgba_in(cb)
        if i is not None and cur is not None:
            ng.links.new(cur, i)
            o = _rgba_out(cb)
            if o is not None:
                cur = o

    # Stage 2: Hue / Saturation
    hs = _new(ng, "CompositorNodeHueSat", "ShaderNodeHueSaturation")
    if hs is not None:
        hs.name = "VFX_HS"
        hs.label = "Hue / Sat"
        hs.location = (-100, 0)
        i = _rgba_in(hs)
        if i is not None and cur is not None:
            ng.links.new(cur, i)
            o = _rgba_out(hs)
            if o is not None:
                cur = o

    # Stage 3: Strength mix (original <-> corrected)
    mix, fac, a, b, out = _mix_node(ng, "VFX_STRENGTH_MIX", (200, 0))
    if mix is not None and a is not None and b is not None and out is not None:
        if cur is not None:
            ng.links.new(cur, b)
        if img_in is not None:
            ng.links.new(img_in, a)
        if fac is not None and str_in is not None:
            ng.links.new(str_in, fac)
        cur = out

    if cur is not None and img_out is not None:
        ng.links.new(cur, img_out)
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
    if cb is not None:
        for attr in ("lift", "gamma", "gain"):
            val = preset[attr]
            try:
                setattr(cb, attr, val)
            except Exception:
                try:
                    setattr(cb, attr, tuple(list(val) + [1.0]))
                except Exception:
                    pass

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

    mix = ng.nodes.get("VFX_STRENGTH_MIX")
    if mix is not None:
        for s in mix.inputs:
            if s.type == 'VALUE':
                try:
                    s.default_value = strength
                except Exception:
                    pass
