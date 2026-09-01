"""VFX Layer Tools — Color correction / plate matching."""

import bpy


# ---------------------------------------------------------------------
# PRESETS: (lift, gamma, gain) RGB tuples + hue/sat adjustments
# ---------------------------------------------------------------------

PRESETS = {
    'NONE': {
        'label': 'Off',
        'lift': (0.0, 0.0, 0.0),
        'gamma': (1.0, 1.0, 1.0),     # FIXED: 1.0 is neutral in L/G/G mode, not 0.5
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


def get_or_create_color_match_group():
    """Get or create the VFX_ColorMatch node group."""
    ng = bpy.data.node_groups.get("VFX_ColorMatch")
    if ng is not None:
        # Verify internal links are intact; recreate if broken
        if _validate_group(ng):
            return ng
        # Links broken — remove and recreate
        print("VFX: VFX_ColorMatch links broken, recreating")
        bpy.data.node_groups.remove(ng)

    ng = bpy.data.node_groups.new("VFX_ColorMatch", 'CompositorNodeTree')

    # Interface sockets
    ng.interface.new_socket("Image", in_out='INPUT', socket_type='NodeSocketColor')
    ng.interface.new_socket("Strength", in_out='INPUT', socket_type='NodeSocketFloat')
    ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')

    # Set default for Strength
    for item in ng.interface.items_tree:
        if item.name == "Strength" and item.in_out == 'INPUT':
            item.default_value = 1.0

    gin = ng.nodes.new("NodeGroupInput")
    gin.location = (-600, 0)
    gout = ng.nodes.new("NodeGroupOutput")
    gout.location = (400, 0)

    # Color Balance (Lift/Gamma/Gain)
    cb = ng.nodes.new("CompositorNodeColorBalance")
    cb.name = "VFX_CB"
    cb.label = "Color Balance"
    cb.location = (-200, 0)
    cb.correction_method = 'LIFT_GAMMA_GAIN'

    # Set neutral defaults so passthrough works when OFF
    try:
        cb.lift = (0.0, 0.0, 0.0, 1.0)
    except Exception:
        try:
            cb.lift = (0.0, 0.0, 0.0)
        except Exception:
            pass
    try:
        cb.gamma = (1.0, 1.0, 1.0, 1.0)
    except Exception:
        try:
            cb.gamma = (1.0, 1.0, 1.0)
        except Exception:
            pass
    try:
        cb.gain = (1.0, 1.0, 1.0, 1.0)
    except Exception:
        try:
            cb.gain = (1.0, 1.0, 1.0)
        except Exception:
            pass

    # Hue/Saturation
    hs = ng.nodes.new("CompositorNodeHueSat")
    hs.name = "VFX_HS"
    hs.label = "Hue/Sat"
    hs.location = (100, 0)

    # Mix Strength: blend between original and corrected
    mix = ng.nodes.new("CompositorNodeMixRGB")
    mix.name = "VFX_STRENGTH_MIX"
    mix.label = "Strength"
    mix.location = (250, 0)
    try:
        mix.blend_type = 'MIX'
    except Exception:
        pass

    # --- Build links ---
    img_in = gin.outputs.get("Image")
    str_in = gin.outputs.get("Strength")
    img_out = gout.inputs.get("Image")

    # Image → ColorBalance input
    cb_img = _find_input(cb, 'RGBA')
    if cb_img and img_in:
        ng.links.new(img_in, cb_img)

    # ColorBalance → HueSat
    cb_out = _find_output(cb, 'RGBA')
    hs_img = _find_input(hs, 'RGBA')
    if cb_out and hs_img:
        ng.links.new(cb_out, hs_img)

    # HueSat → Mix.B (corrected image)
    hs_out = _find_output(hs, 'RGBA')
    mix_b = _find_input_by_not_name(mix, 'RGBA', 'Color1')
    if hs_out and mix_b:
        ng.links.new(hs_out, mix_b)

    # Original → Mix.A (passthrough)
    mix_a = _find_input_by_name(mix, 'RGBA', 'Color1')
    if mix_a and img_in:
        ng.links.new(img_in, mix_a)

    # Strength → Mix Fac
    mix_fac = _find_input(mix, 'VALUE')
    if mix_fac and str_in:
        ng.links.new(str_in, mix_fac)

    # Mix → Output
    mix_out = _find_output(mix, 'RGBA')
    if mix_out and img_out:
        ng.links.new(mix_out, img_out)

    return ng


def _validate_group(ng):
    """Check that the node group has the required internal links."""
    required = {"VFX_CB", "VFX_HS", "VFX_STRENGTH_MIX"}
    present = {n.name for n in ng.nodes}
    if not required.issubset(present):
        return False
    # Check that there are links (not just nodes)
    if len(ng.links) < 3:
        return False
    return True


def _find_input(node, sock_type):
    """Find first input socket of given type."""
    for s in node.inputs:
        if s.type == sock_type:
            return s
    return None


def _find_output(node, sock_type):
    """Find first output socket of given type."""
    for s in node.outputs:
        if s.type == sock_type:
            return s
    return None


def _find_input_by_name(node, sock_type, name):
    """Find input socket by type and name."""
    for s in node.inputs:
        if s.type == sock_type and s.name == name:
            return s
    return None


def _find_input_by_not_name(node, sock_type, name):
    """Find first input socket of given type that does NOT have the given name."""
    for s in node.inputs:
        if s.type == sock_type and s.name != name:
            return s
    return None


def apply_preset(ng, preset_name, strength=1.0):
    """Apply a color correction preset to the VFX_ColorMatch group.

    For NONE: resets to neutral passthrough values.
    """
    preset = PRESETS.get(preset_name)
    if preset is None:
        return

    cb = ng.nodes.get("VFX_CB")
    if cb is None:
        return

    # Set Color Balance
    _safe_set_color(cb, "lift", preset['lift'])
    _safe_set_color(cb, "gamma", preset['gamma'])
    _safe_set_color(cb, "gain", preset['gain'])

    # Set Hue/Saturation
    hs = ng.nodes.get("VFX_HS")
    if hs is not None:
        for s in hs.inputs:
            if s.name.lower() == 'hue':
                try:
                    s.default_value = preset.get('hue', 0.5)
                except Exception:
                    pass
            if s.name.lower() == 'saturation':
                try:
                    s.default_value = preset.get('saturation', 1.0)
                except Exception:
                    pass

    # Set Strength mix
    mix = ng.nodes.get("VFX_STRENGTH_MIX")
    if mix is not None:
        for s in mix.inputs:
            if s.type == 'VALUE' and not s.is_linked:
                try:
                    s.default_value = strength
                except Exception:
                    pass


def _safe_set_color(node, attr, rgb):
    """Set a color property, trying different tuple sizes."""
    for vals in (tuple(rgb) + (1.0,), tuple(rgb)):
        try:
            setattr(node, attr, vals)
            return
        except Exception:
            continue
