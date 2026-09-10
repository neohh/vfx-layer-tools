"""VFX Color Match diagnostic — run inside Blender Python console.

Usage:
    from vfx_layer_tools.colormatch_diagnostic import run_colormatch_diag
    run_colormatch_diag()
"""
import bpy


def run_colormatch_diag():
    lines = []
    lines.append("=" * 60)
    lines.append("VFX COLOR MATCH DIAGNOSTIC")
    lines.append("=" * 60)

    # 1. Check node group
    ng = bpy.data.node_groups.get("VFX_ColorMatch")
    if ng is None:
        lines.append("ERROR: VFX_ColorMatch node group does NOT exist")
        return "\n".join(lines)

    lines.append(f"Node group found: {ng.name} (type={ng.bl_idname})")
    lines.append(f"  Nodes: {len(ng.nodes)}")
    lines.append(f"  Links: {len(ng.links)}")

    # 2. List all nodes and their types
    lines.append("\n--- Internal Nodes ---")
    for node in ng.nodes:
        lines.append(f"  {node.name} ({node.type}, bl_idname={node.bl_idname})")
        for s in node.inputs:
            linked = "LINKED" if s.is_linked else "free"
            lines.append(f"    IN:  {s.name} ({s.type}) [{linked}]")
        for s in node.outputs:
            linked = "LINKED" if s.is_linked else "free"
            lines.append(f"    OUT: {s.name} ({s.type}) [{linked}]")

    # 3. List all links
    lines.append("\n--- Internal Links ---")
    if not ng.links:
        lines.append("  *** NO LINKS INSIDE THE GROUP! This is likely the bug. ***")
    for link in ng.links:
        lines.append(f"  {link.from_node.name}.{link.from_socket.name} -> {link.to_node.name}.{link.to_socket.name}")

    # 4. Check interface sockets
    lines.append("\n--- Interface Sockets ---")
    for item in ng.interface.items_tree:
        lines.append(f"  {item.name} ({item.in_out}) type={item.socket_type} default={getattr(item, 'default_value', 'N/A')}")

    # 5. Check ColorBalance values
    cb = ng.nodes.get("VFX_CB")
    if cb is not None:
        lines.append(f"\n--- ColorBalance ({cb.correction_method}) ---")
        lines.append(f"  Lift:  {tuple(cb.lift)}")
        lines.append(f"  Gamma: {tuple(cb.gamma)}")
        lines.append(f"  Gain:  {tuple(cb.gain)}")
    else:
        lines.append("\nERROR: VFX_CB node not found inside group!")

    # 6. Check HueSat values
    hs = ng.nodes.get("VFX_HS")
    if hs is not None:
        lines.append(f"\n--- HueSat ---")
        for s in hs.inputs:
            lines.append(f"  {s.name}: {s.default_value}")
    else:
        lines.append("\nERROR: VFX_HS node not found inside group!")

    # 7. Check Strength Mix values
    mix = ng.nodes.get("VFX_STRENGTH_MIX")
    if mix is not None:
        lines.append(f"\n--- Strength Mix ({mix.bl_idname}) ---")
        lines.append(f"  blend_type: {getattr(mix, 'blend_type', 'N/A')}")
        for s in mix.inputs:
            linked = "LINKED" if s.is_linked else "free"
            lines.append(f"  IN:  {s.name} ({s.type}) = {s.default_value} [{linked}]")
        for s in mix.outputs:
            linked = "LINKED" if s.is_linked else "free"
            lines.append(f"  OUT: {s.name} ({s.type}) [{linked}]")
    else:
        lines.append("\nERROR: VFX_STRENGTH_MIX node not found!")

    # 8. Check VFX properties
    lines.append("\n--- VFX Properties ---")
    scene = bpy.context.scene
    if hasattr(scene, "vfx"):
        vfx = scene.vfx
        lines.append(f"  use_color_match: {getattr(vfx, 'use_color_match', 'N/A')}")
        lines.append(f"  color_match_preset: {getattr(vfx, 'color_match_preset', 'N/A')}")
        lines.append(f"  color_match_strength: {getattr(vfx, 'color_match_strength', 'N/A')}")
    else:
        lines.append("  No scene.vfx found")

    # 9. Check VFX_COLORMATCH in comp tree
    lines.append("\n--- VFX_COLORMATCH in Comp Tree ---")
    master = None
    if hasattr(scene, "vfx"):
        master = scene.vfx.master_scene or scene
    if master:
        nt = None
        for attr in ("node_tree", "compositor_node_tree", "compositing_node_tree"):
            nt = getattr(master, attr, None)
            if nt is not None:
                break
        if nt is None:
            lines.append("  No comp node tree found")
        else:
            cm = nt.nodes.get("VFX_COLORMATCH")
            if cm is None:
                lines.append("  VFX_COLORMATCH node NOT in comp tree")
            else:
                lines.append(f"  VFX_COLORMATCH found: {cm.name} ({cm.type})")
                lines.append(f"    node_tree: {cm.node_tree.name if cm.node_tree else 'NONE'}")
                lines.append(f"    location: {cm.location}")
                for s in cm.inputs:
                    linked = "LINKED" if s.is_linked else "free"
                    src = ""
                    if s.is_linked:
                        src = f" <- {s.links[0].from_node.name}.{s.links[0].from_socket.name}"
                    lines.append(f"    IN:  {s.name} ({s.type}) [{linked}]{src}")
                for s in cm.outputs:
                    linked = "LINKED" if s.is_linked else "free"
                    dst = ""
                    if s.is_linked:
                        dst = f" -> {s.links[0].to_node.name}.{s.links[0].to_socket.name}"
                    lines.append(f"    OUT: {s.name} ({s.type}) [{linked}]{dst}")
    else:
        lines.append("  No master scene")

    lines.append("\n" + "=" * 60)
    result = "\n".join(lines)
    print(result)
    return result
