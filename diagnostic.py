"""VFX Layer Tools — diagnostic script.
Run in Blender Python console: exec(open(r'PATH_TO_THIS_FILE').read())
"""
import bpy
import os


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

    # --- Check each layer ---
    for i, layer in enumerate(vfx.layers):
        print(f"\n--- Layer {i}: {layer.layer_name} (enabled={layer.enabled}) ---")

        # Scene
        sc = layer.scene
        if sc is None:
            print("  scene: NONE  <-- PROBLEM: no render scene")
            continue
        print(f"  scene: {sc.name}  engine={sc.render.engine}")

        # View-layer passes
        for vl in sc.view_layers:
            mist = getattr(vl, 'use_pass_mist', None)
            z = getattr(vl, 'use_pass_z', None)
            normal = getattr(vl, 'use_pass_normal', None)
            print(f"  view_layer '{vl.name}': mist={mist}  z={z}  normal={normal}")
            if not mist:
                print("    ^^^ Mist pass is OFF — EXR will NOT contain Mist data!")

        # Shadow scene
        if layer.shadow_scene:
            shd = layer.shadow_scene
            print(f"  shadow_scene: {shd.name}")
            for vl in shd.view_layers:
                mist = getattr(vl, 'use_pass_mist', None)
                print(f"    view_layer '{vl.name}': mist={mist}")

        # EXR folder
        folder = os.path.join(bpy.path.abspath(vfx.output_dir), sc.name)
        if os.path.isdir(folder):
            exr_files = sorted(f for f in os.listdir(folder) if f.lower().endswith('.exr'))
            print(f"  EXR folder: {folder}")
            print(f"  EXR files : {len(exr_files)}")
            if exr_files:
                print(f"    first: {exr_files[0]}")
                print(f"    last : {exr_files[-1]}")

            # Probe first EXR for channels
            if exr_files:
                _probe_exr(os.path.join(folder, exr_files[0]))
        else:
            print(f"  EXR folder NOT FOUND: {folder}")
            print("  <-- No EXR rendered yet. Run 'Render All Layers' first.")

    # --- Check compositor ---
    print(f"\n--- Compositor (master={master.name}) ---")
    if not master.use_nodes:
        print("  use_nodes = False  <-- Enable compositing first!")
        return

    nt = master.node_tree
    if nt is None:
        print("  node_tree = None")
        return

    print(f"  node_tree: {nt.name}")
    print(f"  nodes ({len(nt.nodes)}):")
    for node in nt.nodes:
        ntype = node.type
        outputs = [s.name for s in node.outputs]
        label = getattr(node, 'label', '')
        print(f"    [{ntype}] '{node.name}' label='{label}' outputs={outputs}")

        if ntype == 'IMAGE' and node.image:
            img = node.image
            print(f"      image: {img.name}  source={img.source}")
            print(f"      filepath: {img.filepath}")
            # Check multi-channel
            if hasattr(img, 'packed_files'):
                print(f"      channels: {img.channels}")

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)


def _probe_exr(filepath):
    """Try to open EXR with OpenEXR or image node to check channels."""
    print(f"  Probing: {os.path.basename(filepath)}")

    # Method 1: use bpy.data.images.load to inspect
    try:
        img = bpy.data.images.load(filepath, check_existing=True)
        ch = img.channels
        print(f"    channels via bpy: {ch}")
        print(f"    size: {img.size[0]}x{img.size[1]}")
        # Clean up
        bpy.data.images.remove(img)
    except Exception as e:
        print(f"    probe error: {e}")


def force_enable_passes():
    """Enable Mist/Z/Normal on ALL VFX layer scenes."""
    scene = bpy.context.scene
    if not hasattr(scene, 'vfx'):
        print("ERROR: VFX not registered")
        return

    vfx = scene.vfx
    master = vfx.master_scene or scene
    count = 0

    for layer in vfx.layers:
        for sc in (layer.scene, layer.shadow_scene):
            if sc is None:
                continue
            for vl in sc.view_layers:
                for attr in ('use_pass_mist', 'use_pass_z', 'use_pass_normal'):
                    try:
                        if not getattr(vl, attr):
                            setattr(vl, attr, True)
                            count += 1
                    except Exception:
                        pass

    # Also FOGMAP
    fm = getattr(vfx, 'fog_map_scene', None)
    if fm:
        for vl in fm.view_layers:
            for attr in ('use_pass_mist', 'use_pass_z', 'use_pass_normal'):
                try:
                    if not getattr(vl, attr):
                        setattr(vl, attr, True)
                        count += 1
                except Exception:
                    pass

    # BG
    bg = getattr(vfx, 'bg_scene', None)
    if bg:
        for vl in bg.view_layers:
            for attr in ('use_pass_mist', 'use_pass_z', 'use_pass_normal'):
                try:
                    if not getattr(vl, attr):
                        setattr(vl, attr, True)
                        count += 1
                except Exception:
                    pass

    print(f"Force-enabled {count} pass attributes across all VFX scenes.")
    print("Now re-render to bake passes into EXR files.")


def test_glare_sync():
    """Test Glare node property sync — find what actually works."""
    print("\n" + "=" * 60)
    print("GLARE SYNC TEST")
    print("=" * 60)

    scene = bpy.context.scene
    if not hasattr(scene, 'vfx'):
        print("ERROR: VFX not registered")
        return
    vfx = scene.vfx

    # 1. Create a temp Glare node
    print("\n--- Creating temp CompositorNodeGlare ---")
    test_node = None
    try:
        test_node = bpy.data.node_groups.new("_VFX_TEST", 'CompositorNodeTree')
        ng = test_node
        # Find or create a compositor tree to test in
        master = vfx.master_scene or scene
        nt = None
        for attr in ("node_tree", "compositor_node_tree", "compositing_node_tree"):
            nt = getattr(master, attr, None)
            if nt is not None:
                break
        if nt is None and master.use_nodes:
            nt = master.node_tree
        if nt is None:
            print("ERROR: No compositor node tree found")
            bpy.data.node_groups.remove(test_node)
            return

        gl = nt.nodes.new("CompositorNodeGlare")
        gl.name = "_VFX_TEST_GLARE"
        gl.location = (0, 0)
        print(f"Created: {gl.name}  type={gl.type}  bl_idname={gl.bl_idname}")

        # 2. List ALL properties via rna_type
        print("\n--- ALL rna_type properties ---")
        for prop in gl.rna_type.properties:
            if prop.identifier.startswith("_"):
                continue
            try:
                val = getattr(gl, prop.identifier)
            except Exception:
                val = "<ERROR>"
            print(f"  {prop.identifier:30s} = {val!r:40s}  type={prop.type}")

        # 3. List ALL rna_type enum items for glare_type / type / mode
        print("\n--- Enum properties with items ---")
        for prop in gl.rna_type.properties:
            if prop.type == 'ENUM' and prop.enum_items:
                items = [item.identifier for item in prop.enum_items]
                print(f"  {prop.identifier}: {items}")

        # 4. Try to SET glare_type with various values
        print("\n--- Trying to set glare_type ---")
        target = vfx.glare_type
        print(f"  vfx.glare_type = {target!r}")
        for attr in ("glare_type", "type", "mode"):
            for val in (target, target.lower(), target.replace("_", " ")):
                try:
                    old_val = getattr(gl, attr, "<N/A>")
                    setattr(gl, attr, val)
                    new_val = getattr(gl, attr)
                    print(f"  setattr(gl, {attr!r}, {val!r}) -> OK  ({old_val!r} -> {new_val!r})")
                except Exception as e:
                    print(f"  setattr(gl, {attr!r}, {val!r}) -> FAILED: {e}")

        # 5. Try to SET threshold, size, mix
        print("\n--- Trying to set threshold/size/mix ---")
        for attr, val in (("threshold", 7.0), ("size", 0.5), ("mix", -0.3)):
            try:
                old_val = getattr(gl, attr, "<N/A>")
                setattr(gl, attr, val)
                new_val = getattr(gl, attr)
                print(f"  setattr(gl, {attr!r}, {val!r}) -> OK  ({old_val!r} -> {new_val!r})")
            except Exception as e:
                print(f"  setattr(gl, {attr!r}, {val!r}) -> FAILED: {e}")
            # Also try input socket
            for sock in gl.inputs:
                if sock.name.lower() == attr.lower():
                    try:
                        sock.default_value = val
                        print(f"  input[{sock.name!r}].default_value = {val!r} -> OK")
                    except Exception as e:
                        print(f"  input[{sock.name!r}].default_value = {val!r} -> FAILED: {e}")

        # 6. Test existing VFX_GLARE node if present
        print("\n--- Existing VFX_GLARE node ---")
        existing = nt.nodes.get("VFX_GLARE")
        if existing is not None:
            print(f"  Found: {existing.name} type={existing.type}")
            for prop in existing.rna_type.properties:
                if prop.type == 'ENUM' and prop.enum_items:
                    items = [item.identifier for item in prop.enum_items]
                    print(f"  {prop.identifier}: {items}")
            # Try setting on existing
            for attr, val in (("glare_type", target), ("threshold", 5.0)):
                try:
                    old_val = getattr(existing, attr, "<N/A>")
                    setattr(existing, attr, val)
                    new_val = getattr(existing, attr)
                    print(f"  EXISTING setattr({attr!r}, {val!r}) -> OK  ({old_val!r} -> {new_val!r})")
                except Exception as e:
                    print(f"  EXISTING setattr({attr!r}, {val!r}) -> FAILED: {e}")
        else:
            print("  No VFX_GLARE node found")

        # Cleanup test node
        nt.nodes.remove(gl)

    except Exception as e:
        import traceback
        print(f"TEST ERROR: {e}")
        traceback.print_exc()
    finally:
        # Remove test node group
        try:
            bpy.data.node_groups.remove(test_node)
        except Exception:
            pass

    print("\n" + "=" * 60)
    print("GLARE SYNC TEST DONE")
    print("=" * 60)


def diagnose_colormatch():
    """Detailed Color Match diagnostic."""
    print("\n" + "=" * 60)
    print("COLOR MATCH DIAGNOSTIC")
    print("=" * 60)

    scene = bpy.context.scene
    if not hasattr(scene, 'vfx'):
        print("ERROR: VFX properties not registered")
        return

    vfx = scene.vfx
    master = vfx.master_scene or scene

    # 1. Check VFX properties
    print(f"\n--- VFX Properties ---")
    print(f"  use_color_match: {vfx.use_color_match}")
    print(f"  color_match_preset: {vfx.color_match_preset}")
    print(f"  color_match_strength: {vfx.color_match_strength}")

    # 2. Check node group
    ng = bpy.data.node_groups.get("VFX_ColorMatch")
    if ng is None:
        print("\n--- VFX_ColorMatch Node Group ---")
        print("  *** DOES NOT EXIST ***")
        print("  This means get_or_create_color_match_group() failed.")
        print("  Check console output above for errors.")
        print("  Possible causes:")
        print("    - CompositorNodeColorBalance not available in this Blender version")
        print("    - Node group creation failed")
        print("    - Interface socket creation failed")
    else:
        print(f"\n--- VFX_ColorMatch Node Group ---")
        print(f"  Name: {ng.name}")
        print(f"  Type: {ng.bl_idname}")
        print(f"  Nodes: {len(ng.nodes)}")
        print(f"  Links: {len(ng.links)}")

        # List all nodes
        print("\n  Internal Nodes:")
        for node in ng.nodes:
            print(f"    {node.name} ({node.type}, bl_idname={node.bl_idname})")
            for s in node.inputs:
                linked = "LINKED" if s.is_linked else "free"
                print(f"      IN:  {s.name} (type={s.type}) [{linked}]")
            for s in node.outputs:
                linked = "LINKED" if s.is_linked else "free"
                print(f"      OUT: {s.name} (type={s.type}) [{linked}]")

        # List all links
        print("\n  Internal Links:")
        if not ng.links:
            print("    *** NO LINKS — node group is broken! ***")
        else:
            for link in ng.links:
                print(f"    {link.from_node.name}.{link.from_socket.name} -> {link.to_node.name}.{link.to_socket.name}")

        # Check interface sockets
        print("\n  Interface Sockets:")
        for item in ng.interface.items_tree:
            defval = getattr(item, 'default_value', 'N/A')
            print(f"    {item.name} ({item.in_out}) type={item.socket_type} default={defval}")

        # Validate
        required = {"VFX_CB", "VFX_HS", "VFX_STRENGTH_MIX"}
        present = {n.name for n in ng.nodes}
        missing = required - present
        if missing:
            print(f"\n  *** VALIDATION FAIL: missing nodes {missing} ***")
        elif len(ng.links) < 3:
            print(f"\n  *** VALIDATION FAIL: only {len(ng.links)} links (need >=3) ***")
        else:
            print("\n  Validation: OK")

    # 3. Check VFX_COLORMATCH in comp tree
    print(f"\n--- VFX_COLORMATCH in Comp Tree ---")
    nt = None
    for attr in ("node_tree", "compositor_node_tree", "compositing_node_tree"):
        nt = getattr(master, attr, None)
        if nt is not None:
            break
    if nt is None and master.use_nodes:
        nt = master.node_tree
    if nt is None:
        print("  No comp node tree found")
    else:
        cm = nt.nodes.get("VFX_COLORMATCH")
        if cm is None:
            print("  VFX_COLORMATCH node NOT found in comp tree")
            print("  This means build_comp_assembly() did not create it.")
            print("  Check: use_color_match=True AND preset != NONE")
        else:
            print(f"  Found: {cm.name} (type={cm.type}, bl_idname={cm.bl_idname})")
            print(f"  node_tree: {cm.node_tree.name if cm.node_tree else 'NONE'}")
            print(f"  location: ({cm.location.x}, {cm.location.y})")
            for s in cm.inputs:
                linked = "LINKED" if s.is_linked else "free"
                src = ""
                if s.is_linked:
                    src = f" <- {s.links[0].from_node.name}.{s.links[0].from_socket.name}"
                print(f"  IN:  {s.name} (type={s.type}) [{linked}]{src}")
            for s in cm.outputs:
                linked = "LINKED" if s.is_linked else "free"
                dst = ""
                if s.is_linked:
                    dst = f" -> {s.links[0].to_node.name}.{s.links[0].to_socket.name}"
                print(f"  OUT: {s.name} (type={s.type}) [{linked}]{dst}")

    # 4. Blender version info
    print(f"\n--- Blender Info ---")
    print(f"  Version: {bpy.app.version_string}")
    print(f"  Version tuple: {bpy.app.version}")
    # Check available compositor node types
    print("\n  Available CompositorNodeColorBalance:")
    try:
        test_ng = bpy.data.node_groups.new("_VFX_TEST_CM", 'CompositorNodeTree')
        test_cb = test_ng.nodes.new("CompositorNodeColorBalance")
        print(f"    Created OK: {test_cb.bl_idname}")
        # Check correction_method
        try:
            test_cb.correction_method = 'LIFT_GAMMA_GAIN'
            print(f"    correction_method = {test_cb.correction_method}")
        except Exception as e:
            print(f"    correction_method FAILED: {e}")
        # Check lift/gamma/gain attributes
        for attr in ('lift', 'gamma', 'gain'):
            try:
                val = getattr(test_cb, attr)
                print(f"    {attr} = {tuple(val)} (type={type(val).__name__})")
            except Exception as e:
                print(f"    {attr} FAILED: {e}")
        # Check input sockets
        print("    Input sockets:")
        for s in test_cb.inputs:
            print(f"      {s.name} (type={s.type})")
        print("    Output sockets:")
        for s in test_cb.outputs:
            print(f"      {s.name} (type={s.type})")
        test_ng.nodes.remove(test_cb)
        bpy.data.node_groups.remove(test_ng)
    except Exception as e:
        print(f"    CompositorNodeColorBalance NOT available: {e}")

    print("\n" + "=" * 60)
    print("COLOR MATCH DIAGNOSTIC DONE")
    print("=" * 60)


def run_diagnostic():
    """Run diagnose + glare test + colormatch and return result as string."""
    import io, sys
    old = sys.stdout
    sys.stdout = buf = io.StringIO()
    try:
        diagnose()
        test_glare_sync()
        diagnose_colormatch()
    finally:
        sys.stdout = old
    return buf.getvalue()


# Auto-run diagnose
diagnose()
