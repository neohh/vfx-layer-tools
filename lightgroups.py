"""VFX Layer Tools — Light Groups."""

import bpy
from mathutils import Vector


# Default group names
DEFAULT_GROUPS = ('Key', 'Fill', 'Rim', 'Env')


def _get_light_direction(light_obj):
    """Get the world-space direction a light is pointing."""
    try:
        # Use the light's local -Z axis (forward direction)
        rot = light_obj.matrix_world.to_3x3()
        return (rot @ Vector((0, 0, -1))).normalized()
    except Exception:
        return Vector((0, 0, -1))


def _get_to_subject_vector(light_obj, scene):
    """Get vector from light to scene center (approximate subject)."""
    try:
        bounds_min = Vector((float('inf'),) * 3)
        bounds_max = Vector((float('-inf'),) * 3)
        for obj in scene.objects:
            if obj.type in {'MESH', 'CURVE', 'SURFACE', 'META', 'VOLUME'}:
                for corner in obj.bound_box:
                    world_corner = obj.matrix_world @ Vector(corner)
                    for i in range(3):
                        bounds_min[i] = min(bounds_min[i], world_corner[i])
                        bounds_max[i] = max(bounds_max[i], world_corner[i])
        center = (bounds_min + bounds_max) / 2
        return (center - light_obj.location).normalized()
    except Exception:
        return Vector((0, 0, 1))


def auto_assign_light_groups(vfx, master):
    """Scan master scene lights, create groups, auto-assign based on direction."""
    root = None
    try:
        from .core import ensure_root
        root = ensure_root(master)
    except Exception:
        pass

    # Get or create light group collection
    light_col = None
    try:
        from .core import ensure_light_collection
        light_col = ensure_light_collection(master, root)
    except Exception:
        pass

    # Collect all lights
    lights = []
    for obj in master.objects:
        if obj.type == 'LIGHT':
            lights.append(obj)

    if not lights:
        return 0

    # Determine groups based on light direction
    assigned = 0
    for light_obj in lights:
        direction = _get_light_direction(light_obj)
        to_subject = _get_to_subject_vector(light_obj, master)
        dot = direction.dot(to_subject)

        # Heuristic classification
        if light_obj.type == 'SUN':
            group = 'Env'
        elif light_obj.data.energy < 10:
            group = 'Env'
        elif dot > 0.3:
            # Light points toward subject → Key
            group = 'Key'
        elif dot < -0.3:
            # Light points away from subject (backlight) → Rim
            group = 'Rim'
        else:
            # Light from the side → Fill
            group = 'Fill'

        # Set the light group property
        try:
            if hasattr(light_obj.data, 'lightgroup'):
                light_obj.data.lightgroup = group
            # Also try via custom property as fallback
            light_obj["vfx_light_group"] = group
            assigned += 1
        except Exception:
            light_obj["vfx_light_group"] = group
            assigned += 1

    return assigned


def enable_light_groups_on_view_layer(scene):
    """Enable light group pass on the scene's view layers."""
    count = 0
    for vl in scene.view_layers:
        try:
            if hasattr(vl, 'use_pass_light_group'):
                if not vl.use_pass_light_group:
                    vl.use_pass_light_group = True
                    count += 1
        except Exception:
            pass
    return count


def get_scene_light_groups(master):
    """Get unique light group names from all lights in master scene."""
    groups = set()
    for obj in master.objects:
        if obj.type == 'LIGHT':
            # Try direct property
            try:
                if hasattr(obj.data, 'lightgroup') and obj.data.lightgroup:
                    groups.add(obj.data.lightgroup)
            except Exception:
                pass
            # Try custom property
            grp = obj.get("vfx_light_group", "")
            if grp:
                groups.add(grp)
    return sorted(groups) if groups else list(DEFAULT_GROUPS)


def add_light_group_output_nodes(vfx, master, nt):
    """Add Light Group mix nodes to the comp tree.
    For each layer's Render Layers node, find light group outputs and
    create a simple add-mix to combine them."""
    for layer in vfx.layers:
        if not (layer.enabled and layer.scene):
            continue
        rl_name = f"VFX_RL_{layer.id}"
        rl_node = nt.nodes.get(rl_name)
        if rl_node is None:
            continue

        # Find light group outputs on the Render Layers node
        lg_outputs = []
        for out in rl_node.outputs:
            if out.name.startswith("LG."):
                lg_outputs.append(out)

        if len(lg_outputs) <= 1:
            continue  # No light groups or only one → no mixing needed

        # Create add nodes to combine light groups
        base_y = rl_node.location.y - 200
        current_sock = None
        for i, lg_out in enumerate(lg_outputs):
            add_node = nt.nodes.new("CompositorNodeMath")
            add_node.name = f"VFX_LG_MIX_{layer.id}_{i}"
            add_node.label = f"LG Mix: {lg_out.name}"
            add_node["vfx_lightgroup"] = 1
            add_node.operation = 'ADD'
            add_node.location = (rl_node.location.x + 300, base_y - i * 100)

            if current_sock is None:
                current_sock = lg_out
            else:
                # Connect previous result + this light group
                for s in add_node.inputs:
                    if s.type == 'VALUE':
                        if not s.is_linked:
                            nt.links.new(current_sock, s)
                            break
                # Find the other input for the new light group
                for s in add_node.inputs:
                    if s.type == 'VALUE' and not s.is_linked:
                        nt.links.new(lg_out, s)
                        break
                current_sock = add_node.outputs[0]

        # Store reference for the combined light group output
        if current_sock:
            rl_node["vfx_lg_combined"] = current_sock.name if hasattr(current_sock, 'name') else ""
