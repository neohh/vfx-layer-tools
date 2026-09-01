"""VFX Layer Tools — Cryptomatte passes."""

import bpy


def enable_cryptomatte_passes(scene):
    """Enable Cryptomatte Object + Material passes on all view layers."""
    count = 0
    for vl in scene.view_layers:
        for attr in ('use_pass_cryptomatte_object', 'use_pass_cryptomatte_material'):
            try:
                if not getattr(vl, attr, False):
                    setattr(vl, attr, True)
                    count += 1
            except Exception:
                pass
    return count


def setup_cryptomatte_for_layers(vfx, master):
    """Enable cryptomatte on master and all layer scenes."""
    enable_cryptomatte_passes(master)
    for layer in vfx.layers:
        if layer.scene:
            enable_cryptomatte_passes(layer.scene)
        if layer.shadow_scene:
            enable_cryptomatte_passes(layer.shadow_scene)
    fm = getattr(vfx, 'fog_map_scene', None)
    if fm:
        enable_cryptomatte_passes(fm)
    bg = getattr(vfx, 'bg_scene', None)
    if bg:
        enable_cryptomatte_passes(bg)


def add_cryptomatte_nodes(vfx, master, nt):
    """Add CompositorNodeCryptomatte nodes for each layer's Render Layers."""
    for layer in vfx.layers:
        if not (layer.enabled and layer.scene):
            continue
        rl_name = f"VFX_RL_{layer.id}"
        rl_node = nt.nodes.get(rl_name)
        if rl_node is None:
            continue

        cm_name = f"VFX_CM_{layer.id}"
        cm = nt.nodes.get(cm_name)
        if cm is None:
            try:
                cm = nt.nodes.new("CompositorNodeCryptomatte")
                cm.name = cm_name
            except Exception:
                continue
        cm.label = f"Crypto: {layer.layer_name}"
        cm["vfx_id"] = layer.id
        cm.location = (rl_node.location.x + 200, rl_node.location.y - 100)

        # Connect Render Layers → Cryptomatte
        if rl_node.outputs.get("Image") and cm.inputs.get("Image"):
            # Remove old links to Image input
            for l in list(cm.inputs["Image"].links):
                nt.links.remove(l)
            nt.links.new(rl_node.outputs["Image"], cm.inputs["Image"])

        # Try to connectCrypto pass
        for out_name in ("Crypto Object", "Crypto Material", "Crypto Asset"):
            rl_out = rl_node.outputs.get(out_name)
            cm_in = cm.inputs.get("Cryptomatte")
            if rl_out and cm_in:
                for l in list(cm_in.links):
                    nt.links.remove(l)
                nt.links.new(rl_out, cm_in)
                break
