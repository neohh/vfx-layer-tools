"""VFX Layer Tools — Color correction / plate matching."""

import bpy
import traceback


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


def _log(msg):
    print(f"VFX COLORMATCH: {msg}")


def get_or_create_color_match_group():
    """Get or create the VFX_ColorMatch node group."""
    ng = bpy.data.node_groups.get("VFX_ColorMatch")
    if ng is not None:
        # Verify internal links are intact; recreate if broken
        if _validate_group(ng):
            _log(f"Existing group OK: {len(ng.nodes)} nodes, {len(ng.links)} links")
            return ng
        # Links broken — remove and recreate
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

    # Set default for Strength
    for item in ng.interface.items_tree:
        if item.name == "Strength" and item.in_out == 'INPUT':
            item.default_value = 1.0

    gin = ng.nodes.new("NodeGroupInput")
    gin.location = (-600, 0)
    gout = ng.nodes.new("NodeGroupOutput")
    gout.location = (400, 0)

    # Color Balance (Lift/Gamma/Gain)
    cb = None
    try:
        cb = ng.nodes.new("CompositorNodeColorBalance")
        cb.name = "VFX_CB"
        cb.label = "Color Balance"
        cb.location = (-200, 0)
        # Blender 5.x: correction_method may not exist or may differ
        try:
            cb.correction_method = 'LIFT_GAMMA_GAIN'
        except (AttributeError, TypeError) as e:
            _log(f"correction_method set failed: {e} — trying without")
    except Exception as e:
        _log(f"ERROR creating CompositorNodeColorBalance: {e}")
        traceback.print_exc()

    if cb is not None:
        # Set neutral defaults so passthrough works when OFF
        _safe_set_color(cb, "lift", (0.0, 0.0, 0.0))
        _safe_set_color(cb, "gamma", (1.0, 1.0, 1.0))
        _safe_set_color(cb, "gain", (1.0, 1.0, 1.0))

    # Hue/Saturation
    hs = None
    try:
        hs = ng.nodes.new("CompositorNodeHueSat")
        hs.name = "VFX_HS"
        hs.label = "Hue/Sat"
        hs.location = (100, 0)
    except Exception as e:
        _log(f"ERROR creating CompositorNodeHueSat: {e}")

    # Mix Strength: blend between original and corrected
    mix = None
    for mix_type in ("CompositorNodeMix", "CompositorNodeMixRGB"):
        try:
            mix = ng.nodes.new(mix_type)
            _log(f"Mix node created: {mix_type}")
            break
        except Exception:
            continue
    if mix is None:
        _log("ERROR: cannot create Mix node for color match")
        return ng
    mix.name = "VFX_STRENGTH_MIX"
    mix.label = "Strength"
    mix.location = (250, 0)
    try:
        mix.data_type = 'RGBA'
    except Exception:
        pass
    try:
        mix.blend_type = 'MIX'
    except Exception:
        pass

    # --- Build links ---
    img_in = gin.outputs.get("Image")
    str_in = gin.outputs.get("Strength")
    img_out = gout.inputs.get("Image")
    link_count = 0

    # Image → ColorBalance input
    cb_img = _find_best_input(cb, 'Image', 'RGBA')
    if cb_img and img_in:
        ng.links.new(img_in, cb_img)
        link_count += 1
        _log("Link: GroupIn.Image -> CB.Image")
    else:
        _log(f"WARN: CB Image input not found (cb_img={cb_img})")

    # ColorBalance → HueSat
    cb_out = _find_best_output(cb, 'Image', 'RGBA')
    hs_img = _find_best_input(hs, 'Image', 'RGBA')
    if cb_out and hs_img:
        ng.links.new(cb_out, hs_img)
        link_count += 1
        _log("Link: CB -> HS.Image")
    else:
        _log(f"WARN: CB->HS link failed (cb_out={cb_out}, hs_img={hs_img})")

    # HueSat → Mix.B (corrected image)
    hs_out = _find_best_output(hs, 'Image', 'RGBA')
    mix_b = _find_best_input(mix, 'B', 'RGBA')
    if mix_b is None:
        mix_b = _find_best_input(mix, 'Color2', 'RGBA')
    if mix_b is None:
        mix_b = _find_first_input_not_name(mix, 'A', 'RGBA')
    if hs_out and mix_b:
        ng.links.new(hs_out, mix_b)
        link_count += 1
        _log("Link: HS -> Mix.B")
    else:
        _log(f"WARN: HS->Mix.B failed (hs_out={hs_out}, mix_b={mix_b})")

    # Original → Mix.A (passthrough)
    mix_a = _find_best_input(mix, 'A', 'RGBA')
    if mix_a is None:
        mix_a = _find_best_input(mix, 'Color1', 'RGBA')
    if mix_a and img_in:
        ng.links.new(img_in, mix_a)
        link_count += 1
        _log("Link: GroupIn.Image -> Mix.A")
    else:
        _log(f"WARN: Mix.A link failed (mix_a={mix_a})")

    # Strength → Mix Fac
    mix_fac = _find_best_input(mix, 'Factor', 'VALUE')
    if mix_fac is None:
        mix_fac = _find_best_input(mix, 'Fac', 'VALUE')
    if mix_fac is None:
        mix_fac = _find_first_input(mix, 'VALUE')
    if mix_fac and str_in:
        ng.links.new(str_in, mix_fac)
        link_count += 1
        _log("Link: GroupIn.Strength -> Mix.Fac")
    else:
        _log(f"WARN: Mix.Fac link failed (mix_fac={mix_fac})")

    # Mix → Output
    mix_out = _find_best_output(mix, 'Image', 'RGBA')
    if mix_out is None:
        mix_out = _find_first_output(mix, 'RGBA')
    if mix_out and img_out:
        ng.links.new(mix_out, img_out)
        link_count += 1
        _log("Link: Mix -> GroupOut.Image")
    else:
        _log(f"WARN: Mix->Output failed (mix_out={mix_out})")

    _log(f"Group created: {len(ng.nodes)} nodes, {link_count} links")
    return ng


def _validate_group(ng):
    """Check that the node group has the required internal links."""
    required = {"VFX_CB", "VFX_HS", "VFX_STRENGTH_MIX"}
    present = {n.name for n in ng.nodes}
    if not required.issubset(present):
        _log(f"Validation FAIL: missing nodes {required - present}")
        return False
    # Check that there are links (not just nodes)
    if len(ng.links) < 3:
        _log(f"Validation FAIL: only {len(ng.links)} links (need >=3)")
        return False
    return True


# ---------------------------------------------------------------------
# Socket finders — robust for Blender 4.x / 5.x
# ---------------------------------------------------------------------

def _socket_matches(sock, sock_type):
    """Check if a socket matches the desired type (handles Blender 5.x changes)."""
    if sock.type == sock_type:
        return True
    # Blender 5.x may use 'COLOR' instead of 'RGBA'
    if sock_type == 'RGBA' and sock.type == 'COLOR':
        return True
    if sock_type == 'COLOR' and sock.type == 'RGBA':
        return True
    return False


def _find_best_input(node, name, sock_type):
    """Find input socket by name first, then by type."""
    if node is None:
        return None
    # 1. Exact name match
    for s in node.inputs:
        if s.name == name and _socket_matches(s, sock_type):
            return s
    # 2. Case-insensitive name match
    name_lower = name.lower()
    for s in node.inputs:
        if s.name.lower() == name_lower and _socket_matches(s, sock_type):
            return s
    # 3. Any socket of the right type
    for s in node.inputs:
        if _socket_matches(s, sock_type):
            return s
    return None


def _find_best_output(node, name, sock_type):
    """Find output socket by name first, then by type."""
    if node is None:
        return None
    for s in node.outputs:
        if s.name == name and _socket_matches(s, sock_type):
            return s
    name_lower = name.lower()
    for s in node.outputs:
        if s.name.lower() == name_lower and _socket_matches(s, sock_type):
            return s
    for s in node.outputs:
        if _socket_matches(s, sock_type):
            return s
    return None


def _find_first_input(node, sock_type):
    """Find first input socket of given type."""
    if node is None:
        return None
    for s in node.inputs:
        if _socket_matches(s, sock_type):
            return s
    return None


def _find_first_output(node, sock_type):
    """Find first output socket of given type."""
    if node is None:
        return None
    for s in node.outputs:
        if _socket_matches(s, sock_type):
            return s
    return None


def _find_first_input_not_name(node, exclude_name, sock_type):
    """Find first input socket of given type that does NOT have the given name."""
    if node is None:
        return None
    for s in node.inputs:
        if _socket_matches(s, sock_type) and s.name != exclude_name:
            return s
    return None


def apply_preset(ng, preset_name, strength=1.0):
    """Apply a color correction preset to the VFX_ColorMatch group.

    For NONE: resets to neutral passthrough values.
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
    else:
        _log("WARN: VFX_HS not found in group")

    # Set Strength mix
    mix = ng.nodes.get("VFX_STRENGTH_MIX")
    if mix is not None:
        for s in mix.inputs:
            if _socket_matches(s, 'VALUE') and not s.is_linked:
                try:
                    s.default_value = strength
                except Exception:
                    pass
    else:
        _log("WARN: VFX_STRENGTH_MIX not found in group")


def _safe_set_color(node, attr, rgb):
    """Set a color property, trying different tuple sizes."""
    for vals in (tuple(rgb) + (1.0,), tuple(rgb)):
        try:
            setattr(node, attr, vals)
            return
        except Exception:
            continue
