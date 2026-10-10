"""VFX Layer Tools — shadow pass system."""

import bpy
import bmesh
import os
from mathutils import Vector

from .core import ensure_root, ensure_camera_collection, ensure_light_collection


def set_shadow_catcher(obj, state=True):
    if hasattr(obj, "is_shadow_catcher"):
        try:
            obj.is_shadow_catcher = state
            return
        except Exception:
            pass
    try:
        if hasattr(obj, "cycles") and hasattr(obj.cycles, "is_shadow_catcher"):
            obj.cycles.is_shadow_catcher = state
        elif hasattr(obj, "cycles") and hasattr(obj.cycles, "shadow_catcher"):
            obj.cycles.shadow_catcher = state
        else:
            obj["vfx_shadow_catcher"] = state
    except Exception:
        obj["vfx_shadow_catcher"] = state


def set_only_shadow_caster(obj):
    if hasattr(obj, "visible_camera"):
        try:
            obj.visible_camera = False
            obj.visible_shadow = True
            obj.visible_diffuse = True
            obj.visible_glossy = True
            obj.visible_transmission = True
            obj.visible_volume_scatter = True
            return
        except Exception:
            pass
    try:
        if hasattr(obj, "cycles_visibility"):
            vis = obj.cycles_visibility
            vis.camera = False
            vis.shadow = True
            for attr in ("diffuse", "glossy", "transmission", "volume_scatter"):
                try:
                    setattr(vis, attr, True)
                except Exception:
                    pass
    except Exception:
        pass


def refresh_shadow_proxies(vfx, master):
    for layer in vfx.layers:
        if not layer.shadow_scene:
            continue
        cast_col = layer.shadow_cast_collection
        catch_col = layer.shadow_catch_collection
        if not cast_col or not catch_col:
            continue
        for col in (cast_col, catch_col):
            for obj in list(col.objects):
                if obj.get("vfx_proxy") == layer.id:
                    try:
                        bpy.data.objects.remove(obj, do_unlink=True)
                    except Exception:
                        pass
        drawable = {'MESH', 'CURVE', 'VOLUME', 'SURFACE', 'META'}

        def make_proxy(obj, suffix, col):
            proxy = obj.copy()
            proxy.name = obj.name + suffix
            if getattr(obj, "data", None):
                proxy.data = obj.data
            proxy["vfx_proxy"] = layer.id
            chain = []
            node = obj.parent
            ok_chain = True
            while node is not None:
                if node.type == 'EMPTY':
                    chain.append(node)
                    node = node.parent
                else:
                    ok_chain = False
                    break
            if ok_chain:
                for n in chain:
                    if n.name not in col.objects:
                        try:
                            col.objects.link(n)
                        except Exception:
                            pass
            else:
                try:
                    mw = obj.matrix_world.copy()
                except Exception:
                    mw = None
                proxy.parent = None
                proxy.matrix_parent_inverse.identity()
                if mw is not None:
                    proxy.matrix_basis = mw
            if col.objects.get(proxy.name) is None:
                col.objects.link(proxy)
            # Ensure any Armature referenced by modifiers is linked to the shadow scene/collection
            for m in proxy.modifiers:
                if m.type == 'ARMATURE' and m.object:
                    if col.objects.get(m.object.name) is None:
                        try:
                            col.objects.link(m.object)
                        except Exception:
                            pass
            return proxy

        if layer.shadow_mode == 'RECEIVE':
            for obj in layer.collection.objects:
                if obj.type not in drawable:
                    continue
                p = make_proxy(obj, "_VFXCatch", catch_col)
                # In RECEIVE mode, the receiver object is rendered with full surface shaders
                # and receiving shadows from casters (not a cutout alpha shadow catcher)
                set_shadow_catcher(p, False)
                p.visible_camera = True
                p.visible_shadow = True
                p.visible_diffuse = True
            for other in vfx.layers:
                if other.id == layer.id or not other.enabled or not other.collection:
                    continue
                for obj in other.collection.objects:
                    if obj.type not in drawable:
                        continue
                    # Skip objects hidden from render
                    if getattr(obj, "hide_render", False):
                        continue
                    # Skip flat sequence planes (image planes with ≤4 verts) —
                    # they cast thin unrealistic line-shadows
                    if (obj.type == 'MESH' and hasattr(obj.data, 'vertices')
                            and len(obj.data.vertices) <= 4):
                        continue
                    p = make_proxy(obj, "_VFXShadowCaster", cast_col)
                    set_only_shadow_caster(p)

            # Also include caster objects from visible scene collections
            # (e.g. feathers, accessories) that aren't already covered by VFX layers.
            # We check the master view layer to skip excluded collections, and
            # filter out rig widgets, hidden objects, and non-renderable types.
            receiver_objs = set(layer.collection.objects) if layer.collection else set()

            # Collect names of VFX layer objects to avoid duplicates
            vfx_obj_names = set()
            for other_l in vfx.layers:
                if other_l.collection:
                    for o in other_l.collection.objects:
                        vfx_obj_names.add(o.name)

            # Build set of excluded collection names from view layer
            def _get_excluded(lc, out=None):
                if out is None:
                    out = set()
                for child in lc.children:
                    if child.exclude:
                        out.add(child.collection.name)
                    _get_excluded(child, out)
                return out

            vl = master.view_layers[0] if master.view_layers else None
            excluded_names = _get_excluded(vl.layer_collection) if vl else set()

            for col in master.collection.children:
                cname = col.name
                # Skip VFX system collections, excluded collections, and known non-caster groups
                if (cname.startswith("VFX")
                        or cname in excluded_names
                        or "муссор" in cname.lower()
                        or "camera" in cname.lower()
                        or "light" in cname.lower()
                        or "перья" in cname.lower()):
                    continue
                for obj in col.all_objects:
                    # Only mesh-like types that cast meaningful shadows (skip curves/IK paths)
                    if obj.type not in {'MESH', 'VOLUME', 'SURFACE', 'META'}:
                        continue
                    # Skip objects whose ACTUAL collection is excluded
                    # (all_objects recurses into child collections that may be excluded)
                    if any(uc.name in excluded_names for uc in obj.users_collection):
                        continue
                    # Skip receiver objects (the moon surface itself)
                    if obj in receiver_objs:
                        continue
                    # Skip objects already present from VFX layers
                    if obj.name in vfx_obj_names:
                        continue
                    # Skip rig widget meshes (Rigify WGT-*)
                    if obj.name.startswith("WGT-"):
                        continue
                    # Skip objects hidden from render
                    if getattr(obj, "hide_render", False):
                        continue
                    if cast_col.objects.get(obj.name + "_VFXShadowCaster") is None:
                        try:
                            p = make_proxy(obj, "_VFXShadowCaster", cast_col)
                            set_only_shadow_caster(p)
                        except Exception:
                            pass
            try:
                setup_shadow_isolation_comp(layer, master)
            except Exception as e:
                print(f"VFX: error configuring shadow isolation for {layer.layer_name}: {e}")
        else:
            catcher = layer.shadow_catcher
            for obj in layer.collection.objects:
                if catcher and obj == catcher:
                    continue
                if obj.type not in drawable:
                    continue
                p = make_proxy(obj, "_VFXShadowCaster", cast_col)
                set_only_shadow_caster(p)
            if catcher and catch_col.objects.get(catcher.name) is None:
                catch_col.objects.link(catcher)


def repair_shadow_proxies():
    for col in bpy.data.collections:
        vp = col.get("vfx_pass", "")
        if vp == "SHADOW_CAST":
            for obj in col.objects:
                if obj.get("vfx_proxy"):
                    set_only_shadow_caster(obj)
        elif vp == "SHADOW_CATCH":
            for obj in col.objects:
                if obj.get("vfx_proxy"):
                    set_shadow_catcher(obj, True)


def create_default_catcher(layer, master):
    name = f"VFX_{layer.layer_name}_ShadowCatcher"
    existing = bpy.data.objects.get(name)
    if existing:
        return existing
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=50.0)
    bm.to_mesh(mesh)
    bm.free()
    catcher = bpy.data.objects.new(name, mesh)
    master.collection.objects.link(catcher)
    min_z = None
    if layer.collection:
        for obj in layer.collection.objects:
            if obj.type in {'MESH', 'CURVE', 'VOLUME', 'SURFACE', 'META'} and obj.bound_box:
                for corner in obj.bound_box:
                    world_corner = obj.matrix_world @ Vector(corner)
                    z = world_corner.z
                    min_z = z if min_z is None else min(min_z, z)
    if min_z is not None:
        catcher.location.z = min_z - 0.05
    set_shadow_catcher(catcher, True)
    return catcher


def setup_shadow_isolation_comp(layer, master, output_dir=None):
    """Set up the compositor in layer.shadow_scene to isolate shadows on a transparent background."""
    sc_shd = layer.shadow_scene
    if not sc_shd:
        return None

    if not output_dir:
        output_dir = getattr(master.vfx, "output_dir", "")
    abs_base = bpy.path.abspath(output_dir) if output_dir else ""
    clean_folder = os.path.join(abs_base, layer.scene.name) if layer.scene and abs_base else ""

    clean_file = None
    if clean_folder and os.path.exists(clean_folder):
        files = sorted(f for f in os.listdir(clean_folder) if f.lower().endswith(('.exr', '.png', '.tif', '.tiff')))
        if files:
            clean_file = os.path.join(clean_folder, files[0])

    group_name = f"VFX_SHD_Comp_{layer.id}"
    tree = bpy.data.node_groups.get(group_name)
    if not tree:
        tree = bpy.data.node_groups.new(name=group_name, type='CompositorNodeTree')
    else:
        tree.nodes.clear()

    # 1. Render Layers node for SHD scene
    rl = tree.nodes.new("CompositorNodeRLayers")
    rl.location = (0, 0)
    rl.scene = sc_shd

    # 2. Clean layer image node (if rendered)
    im_node = None
    if clean_file:
        img_name = f"VFX_CLEAN_{layer.scene.name}"
        img = bpy.data.images.get(img_name)
        if not img:
            try:
                img = bpy.data.images.load(clean_file)
                img.name = img_name
            except Exception as e:
                print(f"VFX: error loading clean layer image {clean_file}: {e}")
        if img:
            im_node = tree.nodes.new("CompositorNodeImage")
            im_node.location = (0, 300)
            im_node.image = img
            img.source = 'FILE'
            try:
                img.reload()
            except Exception:
                pass

    # Interface output socket
    if not tree.interface.items_tree:
        tree.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    gout = tree.nodes.new("NodeGroupOutput")
    gout.location = (1500, 100)

    if im_node and im_node.image:
        # Ratio = Shaded / Clean
        div = tree.nodes.new("ShaderNodeMix")
        div.data_type = 'RGBA'
        div.blend_type = 'DIVIDE'
        div.inputs['Factor'].default_value = 1.0
        div.location = (250, -100)
        tree.links.new(rl.outputs['Image'], div.inputs[6])
        tree.links.new(im_node.outputs['Image'], div.inputs[7])

        bw = tree.nodes.new("CompositorNodeRGBToBW")
        bw.location = (450, -100)
        tree.links.new(div.outputs[2], bw.inputs[0])

        inv = tree.nodes.new("CompositorNodeInvert")
        inv.location = (650, -100)
        tree.links.new(bw.outputs['Val'], inv.inputs['Color'])

        # Sky mask: empty transparent sky should never receive shadow
        mask_sky = tree.nodes.new("ShaderNodeMix")
        mask_sky.data_type = 'RGBA'
        mask_sky.blend_type = 'MULTIPLY'
        mask_sky.inputs['Factor'].default_value = 1.0
        mask_sky.location = (850, -100)
        tree.links.new(inv.outputs['Color'], mask_sky.inputs[6])
        tree.links.new(rl.outputs['Alpha'], mask_sky.inputs[7])

        # Clean up near-zero noise threshold while preserving full penumbra falloff
        ramp = tree.nodes.new("ShaderNodeValToRGB")
        ramp.location = (1050, -100)
        ramp.color_ramp.elements[0].position = 0.002
        ramp.color_ramp.elements[1].position = 1.0
        tree.links.new(mask_sky.outputs[2], ramp.inputs['Fac'])

        # Multiply Shaded Moon by Matte to zero out RGB in non-shadowed areas
        mult = tree.nodes.new("ShaderNodeMix")
        mult.data_type = 'RGBA'
        mult.blend_type = 'MULTIPLY'
        mult.inputs['Factor'].default_value = 1.0
        mult.location = (1050, 150)
        tree.links.new(rl.outputs['Image'], mult.inputs[6])
        tree.links.new(ramp.outputs['Color'], mult.inputs[7])

        # Set Alpha
        sa = tree.nodes.new("CompositorNodeSetAlpha")
        sa.location = (1280, 100)
        tree.links.new(mult.outputs[2], sa.inputs['Image'])
        tree.links.new(ramp.outputs['Color'], sa.inputs['Alpha'])

        tree.links.new(sa.outputs['Image'], gout.inputs[0])
    else:
        # Fallback: pass through render layers
        tree.links.new(rl.outputs['Image'], gout.inputs[0])

    if hasattr(sc_shd, "use_nodes"):
        try:
            sc_shd.use_nodes = True
        except Exception:
            pass
    for attr in ("compositing_node_group", "node_tree", "compositor_node_tree", "compositing_node_tree"):
        if hasattr(sc_shd, attr):
            try:
                setattr(sc_shd, attr, tree)
                break
            except Exception:
                pass
    return tree
