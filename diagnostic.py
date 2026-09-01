"""VFX Layer Tools — diagnostic + glare property probe."""
import bpy
import os
import io
import sys


def diagnose():
    scene = bpy.context.scene
    if not hasattr(scene, 'vfx'):
        print("ERROR: VFX properties not registered. Re-enable the addon.")
        return
    vfx = scene.vfx
    master = vfx.master_scene or scene
    print("\n" + "=" * 60)
    print("VFX DIAGNOSTIC")
    print("=" * 60)
    print(f"Master scene : {master.name}")
    print(f"Comp mode    : {vfx.comp_mode}")
    print(f"Output dir   : {vfx.output_dir}")
    print(f"Layers count : {len(vfx.layers)}")
    for i, layer in enumerate(vfx.layers):
        print(f"\n--- Layer {i}: {layer.layer_name} (enabled={layer.enabled}) ---")
        sc = layer.scene
        if sc is None:
            print("  scene: NONE")
            continue
        print(f"  scene: {sc.name}  engine={sc.render.engine}")
        for vl in sc.view_layers:
            mist = getattr(vl, 'use_pass_mist', None)
            z = getattr(vl, 'use_pass_z', None)
            normal = getattr(vl, 'use_pass_normal', None)
            print(f"  view_layer '{vl.name}': mist={mist}  z={z}  normal={normal}")
        if layer.shadow_scene:
            print(f"  shadow_scene: {layer.shadow_scene.name}")
        folder = os.path.join(bpy.path.abspath(vfx.output_dir), sc.name)
        if os.path.isdir(folder):
            exr_files = sorted(f for f in os.listdir(folder) if f.lower().endswith('.exr'))
            print(f"  EXR files: {len(exr_files)}")
        else:
            print(f"  EXR folder NOT FOUND: {folder}")
    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)


def _find_comp_tree(scene):
    """Find compositor node tree from scene, trying all known attributes."""
    for attr in ("node_tree", "compositor_node_tree", "compositing_node_tree"):
        try:
            tree = getattr(scene, attr, None)
            if tree is not None:
                return tree
        except Exception:
            pass
    # Try node_groups
    for ng in bpy.data.node_groups:
        if ng.bl_idname == 'CompositorNodeTree':
            return ng
    return None


def test_glare_node():
    """Create a CompositorNodeGlare, dump ALL properties, sockets, and test setters."""
    print("\n" + "=" * 60)
    print("GLARE NODE PROBE")
    print("=" * 60)

    scene = bpy.context.scene

    # Find or create a compositor tree
    nt = _find_comp_tree(scene)

    # If no tree, try enabling compositing
    if nt is None:
        try:
            scene.use_nodes = True
            nt = _find_comp_tree(scene)
        except Exception:
            pass

    # If still no tree, create a temp one
    temp_ng = False
    if nt is None:
        try:
            nt = bpy.data.node_groups.new("_VFX_GLARE_TEST", 'CompositorNodeTree')
            temp_ng = True
            print("(Created temporary node tree for testing)")
        except Exception as e:
            print(f"ERROR: Cannot create node tree: {e}")
            return

    # Create glare node
    gl = None
    try:
        gl = nt.nodes.new("CompositorNodeGlare")
        gl.name = "_VFX_PROBE_GLARE"
        gl.location = (0, 0)
    except Exception as e:
        print(f"ERROR: Cannot create CompositorNodeGlare: {e}")
        if temp_ng:
            try:
                bpy.data.node_groups.remove(nt)
            except Exception:
                pass
        return

    print(f"\nNode: {gl.name}")
    print(f"bl_idname: {gl.bl_idname}")
    try:
        print(f"type: {gl.type}")
    except Exception:
        print("type: <unreadable>")

    # --- ALL rna properties ---
    print("\n--- ALL rna_type.properties ---")
    for prop in gl.rna_type.properties:
        if prop.identifier.startswith("_"):
            continue
        try:
            val = getattr(gl, prop.identifier)
        except Exception:
            val = "<ERROR>"
        print(f"  {prop.identifier:30s} = {val!r:40s}  type={prop.type}")

    # --- Enum properties with items ---
    print("\n--- Enum properties + their items ---")
    for prop in gl.rna_type.properties:
        if prop.type == 'ENUM' and prop.enum_items:
            items = [item.identifier for item in prop.enum_items]
            try:
                current = getattr(gl, prop.identifier)
            except Exception:
                current = "<ERROR>"
            print(f"  {prop.identifier:20s} current={current!r:20s} items={items}")

    # --- ALL input sockets ---
    print("\n--- Input sockets ---")
    for i, sock in enumerate(gl.inputs):
        dv = getattr(sock, 'default_value', '<N/A>')
        print(f"  [{i}] name={sock.name!r:25s} type={sock.type:10s}  default={dv}")

    # --- ALL output sockets ---
    print("\n--- Output sockets ---")
    for i, sock in enumerate(gl.outputs):
        print(f"  [{i}] name={sock.name!r:25s} type={sock.type}")

    # --- Try setting glare_type via every attr ---
    print("\n--- Trying to set glare type ---")
    test_types = ["BLOOM", "bloom", "FOG_GLOW", "fog_glow", "STREAKS", "streaks", "GHOSTS", "ghosts"]
    for attr in ("glare_type", "type", "mode", "quality"):
        found = False
        for val in test_types:
            try:
                old = getattr(gl, attr, "<N/A>")
                setattr(gl, attr, val)
                new = getattr(gl, attr)
                if str(new).lower() == val.lower() or str(new) == val:
                    print(f"  OK:    {attr} = {val!r}  (works!)")
                    found = True
                    break
                else:
                    print(f"  MISS:  {attr} = {val!r} -> got {new!r}")
            except AttributeError:
                pass
            except Exception as e:
                print(f"  FAIL:  {attr} = {val!r} -> {e}")
        if not found:
            # Try to list what the attr actually accepts
            try:
                current = getattr(gl, attr)
                print(f"  INFO:  {attr} exists, current={current!r}")
                # Try to get enum_items from rna
                for prop in gl.rna_type.properties:
                    if prop.identifier == attr and prop.type == 'ENUM':
                        items = [item.identifier for item in prop.enum_items]
                        print(f"         valid items: {items}")
            except Exception:
                pass

    # --- Try setting via socket default_value ---
    print("\n--- Trying to set input socket values ---")
    for sock in gl.inputs:
        for attr in ("threshold", "size", "mix", "quality", "streaks"):
            if sock.name.lower() == attr.lower():
                try:
                    sock.default_value = 1.0
                    print(f"  OK:   input[{sock.name!r}].default_value = 1.0")
                except Exception as e:
                    print(f"  FAIL: input[{sock.name!r}].default_value -> {e}")

    # Cleanup
    try:
        nt.nodes.remove(gl)
    except Exception:
        pass
    if temp_ng:
        try:
            bpy.data.node_groups.remove(nt)
        except Exception:
            pass

    print("\n" + "=" * 60)
    print("GLARE PROBE DONE")
    print("=" * 60)


def run_diagnostic():
    """Run all diagnostics and return as string for clipboard."""
    old = sys.stdout
    sys.stdout = buf = io.StringIO()
    try:
        diagnose()
        test_glare_node()
    finally:
        sys.stdout = old
    return buf.getvalue()


# Auto-run on import
diagnose()
