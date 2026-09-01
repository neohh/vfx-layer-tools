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


def run_diagnostic():
    """Run diagnose and return result as string (for clipboard)."""
    import io, sys
    old = sys.stdout
    sys.stdout = buf = io.StringIO()
    try:
        diagnose()
    finally:
        sys.stdout = old
    return buf.getvalue()


# Auto-run diagnose
diagnose()
