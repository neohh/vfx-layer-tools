"""VFX Layer Tools — Color correction / plate matching."""

import bpy


# ---------------------------------------------------------------------
# PRESETS: (lift, gamma, gain) RGB tuples + hue/sat adjustments
# ---------------------------------------------------------------------

PRESETS = {
    'NONE': {
        'label': 'Off',
        'lift': (0.0, 0.0, 0.0),
        'gamma': (0.5, 0.5, 0.5),
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
        return ng

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

    # Links: Image → ColorBalance → HueSat → Mix(B) → Output
    img_in = gin.outputs.get("Image")
    str_in = gin.outputs.get("Strength")
    img_out = gout.inputs.get("Image")

    # Image → CB
    cb_img = None
    for s in cb.inputs:
        if s.type == 'RGBA':
            cb_img = s
            break
    if cb_img and img_in:
        ng.links.new(img_in, cb_img)

    # CB → HS
    cb_out = None
    for s in cb.outputs:
        if s.type == 'RGBA':
            cb_out = s
            break
    hs_img = None
    for s in hs.inputs:
        if s.type == 'RGBA':
            hs_img = s
            break
    if cb_out and hs_img:
        ng.links.new(cb_out, hs_img)

    # HS → Mix(B)
    hs_out = None
    for s in hs.outputs:
        if s.type == 'RGBA':
            hs_out = s
            break
    mix_b = None
    mix_fac = None
    for s in mix.inputs:
        if s.type == 'RGBA' and s.name != 'Color1':
            if mix_b is None:
                mix_b = s
        if s.type == 'VALUE' and mix_fac is None:
            mix_fac = s
    if hs_out and mix_b:
        ng.links.new(hs_out, mix_b)

    # Original → Mix(A)
    mix_a = None
    for s in mix.inputs:
        if s.type == 'RGBA' and s.name == 'Color1':
            mix_a = s
            break
    if mix_a and img_in:
        ng.links.new(img_in, mix_a)

    # Strength → Mix Fac
    if mix_fac and str_in:
        ng.links.new(str_in, mix_fac)

    # Mix → Output
    mix_out = None
    for s in mix.outputs:
        if s.type == 'RGBA':
            mix_out = s
            break
    if mix_out and img_out:
        ng.links.new(mix_out, img_out)

    return ng


def apply_preset(ng, preset_name, strength=1.0):
    """Apply a color correction preset to the VFX_ColorMatch group."""
    preset = PRESETS.get(preset_name)
    if preset is None or preset_name == 'NONE':
        return

    cb = ng.nodes.get("VFX_CB")
    if cb is None:
        return

    # Set Color Balance
    try:
        cb.lift = preset['lift']
    except Exception:
        pass
    try:
        cb.gamma = preset['gamma']
    except Exception:
        cb.gamma = tuple(list(preset['gamma']) + [1.0])
    except Exception:
        pass
    try:
        cb.gain = preset['gain']
    except Exception:
        pass

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
            if s.type == 'VALUE':
                try:
                    s.default_value = strength
                except Exception:
                    pass
