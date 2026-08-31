"""VFX Layer Tools — diagnostic script.
Run in Blender Python console: exec(open(r'PATH_TO_THIS_FILE').read())
Or click 'Diagnostics' button in VFX panel.
"""
import bpy
import os


def _find_comp_tree(master):
    """Find compositor node tree the same way compositor.py does."""
    for attr in ("node_tree", "compositor_node_tree", "compositing_node_tree"):
        tree = getattr(master, attr, None)
        if tree is not None:
            return tree
    # Fallback: search by attribute name
    for attr in dir(master):
        low = attr.lower()
        if "node" in low or "comp" in low:
            try:
                val = getattr(master, attr)
            except Exception:
                continue
            if isinstance(val, bpy.types.NodeTree):
                return val
    return None


def diagnose():
    scene = bpy.context.scene

    if not hasattr(scene, 'vfx'):
        print("ERROR: VFX properties not registered. Re-enable the addon.")
        return

    vfx = scene.vfx
    master = vfx.master_scene or scene

    # Resolve output_dir once
    resolved_output = bpy.path.abspath(vfx.output_dir)

    print("\n" + "=" * 60)
    print("VFX DIAGNOSTIC")
    print("=" * 60)
    print(f"Master scene  : {master.name}")
    print(f"Comp mode     : {vfx.comp_mode}")
    print(f"Output dir    : {vfx.output_dir}")
    print(f"Output resolved: {resolved_output}")
    print(f"Output exists : {os.path.isdir(resolved_output)}")
    print(f"Layers count  : {len(vfx.layers)}")

    # --- Check output directory ---
    print(f"\n--- Output Directory Contents ---")
    if os.path.isdir(resolved_output):
        entries = sorted(os.listdir(resolved_output))
        folders = [e for e in entries if os.path.isdir(os.path.join(resolved_output, e))]
        files = [e for e in entries if os.path.isfile(os.path.join(resolved_output, e))]
        print(f"  Subfolders: {len(folders)}")
        for f in folders:
            sub = os.path.join(resolved_output, f)
            exr_count = len([x for x in os.listdir(sub) if x.lower().endswith('.exr')]) if os.path.isdir(sub) else 0
            print(f"    {f}/  ({exr_count} EXR files)")
        if files:
            print(f"  Files: {len(files)}")
            for f in files[:10]:
                print(f"    {f}")
    else:
        print(f"  Directory does NOT exist: {resolved_output}")
        print("  --> Create it or render first with 'Render All Layers'")

    # --- Check each layer ---
    for i, layer in enumerate(vfx.layers):
        print(f"\n--- Layer {i}: {layer.layer_name} (enabled={layer.enabled}) ---")

        # Scene
        sc = layer.scene
        if sc is None:
            print("  scene: NONE  <-- PROBLEM: no render scene")
            continue
        print(f"  scene: {sc.name}  engine={sc.render.engine}")
        print(f"  render filepath: {sc.render.filepath}")

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

        # EXR folder — show what the addon EXPECTS vs what EXISTS
        expected_folder = os.path.join(resolved_output, sc.name)
        print(f"  expected EXR path: {expected_folder}")

        if os.path.isdir(expected_folder):
            exr_files = sorted(f for f in os.listdir(expected_folder) if f.lower().endswith('.exr'))
            print(f"  EXR files found : {len(exr_files)}")
            if exr_files:
                print(f"    first: {exr_files[0]}")
                print(f"    last : {exr_files[-1]}")
                # Probe first EXR for channels
                _probe_exr(os.path.join(expected_folder, exr_files[0]))
            else:
                print("  <-- Folder exists but NO .exr files inside!")
                print("  --> Run 'Render All Layers' to generate EXR files.")
        else:
            print(f"  EXR folder NOT FOUND")
            # List what IS in the output dir
            if os.path.isdir(resolved_output):
                actual_folders = [e for e in os.listdir(resolved_output)
                                  if os.path.isdir(os.path.join(resolved_output, e))]
                if actual_folders:
                    print(f"  Available folders: {actual_folders}")
                    print(f"  --> Scene name mismatch? Check if '{sc.name}' should be one of these.")
                else:
                    print("  --> Output dir is empty. Run 'Render All Layers' first.")

    # --- Check compositor ---
    print(f"\n--- Compositor (master={master.name}) ---")
    print(f"  use_nodes: {master.use_nodes}")

    nt = _find_comp_tree(master)
    if nt is None:
        print("  node_tree: NONE")
        if not master.use_nodes:
            print("  <-- Compositor is disabled! Enable it in Render Properties > Compositor.")
        else:
            print("  <-- Compositor enabled but no node tree found.")
        print("=" * 60)
        print("DONE")
        print("=" * 60)
        return

    print(f"  node_tree: {nt.name} (type={nt.bl_idname})")
    print(f"  nodes ({len(nt.nodes)}):")
    for node in nt.nodes:
        ntype = node.type
        outputs = [s.name for s in node.outputs]
        label = getattr(node, 'label', '')
        print(f"    [{ntype}] '{node.name}' label='{label}' outputs={outputs}")

        if ntype == 'IMAGE':
            img = node.image
            if img is None:
                print(f"      ** NO IMAGE ASSIGNED ** <-- This node will have no output!")
            else:
                print(f"      image: {img.name}  source={img.source}")
                print(f"      filepath: {img.filepath}")
                print(f"      filepath_raw: {img.filepath_raw}")
                print(f"      channels: {img.channels}  size: {img.size[0]}x{img.size[1]}")
                if img.source == 'SEQUENCE':
                    print(f"      frame_start: {img.frame_start}  frame_duration: {img.frame_duration}")
                # Check if file actually exists on disk
                resolved = bpy.path.abspath(img.filepath)
                print(f"      resolved path: {resolved}")
                print(f"      file exists: {os.path.isfile(resolved)}")
                if os.path.isfile(resolved):
                    _probe_exr(resolved)

    # --- Summary ---
    print(f"\n--- Summary ---")
    issues = []
    for i, layer in enumerate(vfx.layers):
        if layer.scene:
            folder = os.path.join(resolved_output, layer.scene.name)
            if not os.path.isdir(folder):
                issues.append(f"Layer '{layer.layer_name}': no EXR folder at {folder}")
            elif not any(f.endswith('.exr') for f in os.listdir(folder)):
                issues.append(f"Layer '{layer.layer_name}': folder empty, no EXR files")

    for layer in vfx.layers:
        if layer.scene:
            for vl in layer.scene.view_layers:
                if not getattr(vl, 'use_pass_mist', False):
                    issues.append(f"Layer '{layer.layer_name}': Mist pass OFF on '{vl.name}'")

    if issues:
        print("  ISSUES FOUND:")
        for issue in issues:
            print(f"    - {issue}")
    else:
        print("  No obvious issues found.")

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)


def _probe_exr(filepath):
    """Try to open EXR to check channels."""
    print(f"  Probing: {os.path.basename(filepath)}")
    try:
        img = bpy.data.images.load(filepath, check_existing=True)
        ch = img.channels
        print(f"    channels via bpy: {ch}")
        print(f"    size: {img.size[0]}x{img.size[1]}")
        # Check if it has render passes (multi-channel EXR)
        # In Blender, multi-channel EXR shows extra outputs on Image node
        # when loaded. We can check the image's render_passes attribute.
        if hasattr(img, 'render_passes'):
            print(f"    render_passes: {img.render_passes}")
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


# Auto-run only when executed directly (not when imported as module)
if __name__ == "__main__":
    diagnose()
