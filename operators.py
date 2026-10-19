"""VFX Layer Tools — operators."""

import bpy
import math
import time
import os
import subprocess
import sys
from bpy.props import (
    StringProperty, BoolProperty, IntProperty, FloatProperty, EnumProperty,
)

from .core import (
    uid, get_project, active_layer, collect_objects, default_layer_name,
    ensure_root, ensure_camera_collection, ensure_light_collection,
    create_empty_scene, exclude_collection_in_master, link_collection_to_scene,
    sync_scene_settings, remove_scene_safe, remove_shadow_collections,
    rename_layer, link_lights_to_all_scenes, sync_master_lights,
    ensure_parent_empties, ensure_cycles_gpu, optimize_shadow_scene_cycles,
    link_camera_to_all_scenes, ensure_camera_dependencies,
)
from .shadow import (
    set_shadow_catcher, set_only_shadow_caster, refresh_shadow_proxies,
    create_default_catcher, setup_shadow_isolation_comp, sync_layer_bounce_proxy,
)
from .occlusion import (
    rebuild_all_occlusion, rebuild_layer_occlusion, remove_occlusion_collection,
    compute_composite_order, occlusion_self_check,
)
from .compositor import (
    get_comp_tree, rebuild_comp, rebuild_comp_from_files,
    build_comp_assembly, ensure_render_node, remove_comp_node,
    _setup_fog_passes, _ensure_fogmap, _get_mist_socket,
    _ensure_fogmap_scene_comp,
)
from .materials import _trigger_rebuild, _trigger_comp, update_layer_material_adjust
from .lightgroups import auto_assign_light_groups, enable_light_groups_on_view_layer


class VFX_OT_reset_lighting(bpy.types.Operator):
    bl_idname = "vfx.reset_lighting"
    bl_label = "Reset Lighting"
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = active_layer(vfx)
        if not layer:
            self.report({'ERROR'}, "No active layer")
            return {'CANCELLED'}
        layer.exposure = 1.0
        layer.contrast = 1.0
        layer.saturation = 1.0
        layer.tint_color = (1.0, 1.0, 1.0, 1.0)
        layer.tint_strength = 0.0
        update_layer_material_adjust(layer)
        self.report({'INFO'}, "Lighting reset")
        return {'FINISHED'}


class VFX_OT_set_master(bpy.types.Operator):
    bl_idname = "vfx.set_master"
    bl_label = "Use Current Scene As Master"
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        context.scene.vfx.master_scene = context.scene
        self.report({'INFO'}, f"Master scene set to {context.scene.name}")
        return {'FINISHED'}


class VFX_OT_create_layer(bpy.types.Operator):
    bl_idname = "vfx.create_layer"
    bl_label = "Create Layer From Selected"
    bl_options = {'REGISTER', 'UNDO'}
    layer_name: StringProperty(name="Layer Name", default="")
    include_children: BoolProperty(name="Include Children", default=True)
    def invoke(self, context, event):
        vfx, master = get_project(context)
        if not context.selected_objects:
            self.report({'ERROR'}, "No selected objects")
            return {'CANCELLED'}
        if not self.layer_name:
            self.layer_name = default_layer_name(context) or vfx.new_layer_name
        self.include_children = vfx.include_children
        return context.window_manager.invoke_props_dialog(self)
    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        name = self.layer_name.strip()
        if not name:
            self.report({'ERROR'}, "Layer name is empty")
            return {'CANCELLED'}
        vfx.new_layer_name = name
        vfx.include_children = self.include_children
        selected = collect_objects(context.selected_objects or [], self.include_children)
        if not selected:
            self.report({'ERROR'}, "No selected objects")
            return {'CANCELLED'}
        root = ensure_root(master)
        cam_col = ensure_camera_collection(master, root)
        light_col = ensure_light_collection(master, root)
        col = bpy.data.collections.new(f"VFX_{name}")
        root.children.link(col)
        layer_id = uid()
        col["vfx_id"] = layer_id
        col["vfx_pass"] = "OBJECT"
        for obj in selected:
            if col.objects.get(obj.name) is None:
                col.objects.link(obj)
        # parent Empties must come along, or children lose their transform
        ensure_parent_empties(col, selected)
        scene = create_empty_scene(f"VFX_{name}", master)
        scene["vfx_id"] = layer_id
        scene["vfx_pass"] = "OBJECT"
        scene.vfx.master_scene = master
        link_collection_to_scene(scene, col)
        link_collection_to_scene(scene, cam_col)
        link_collection_to_scene(scene, light_col)
        if master.camera:
            scene.camera = master.camera
        sync_scene_settings(master, scene)
        try:
            scene.render.engine = vfx.objects_engine
        except Exception:
            pass
        item = vfx.layers.add()
        item.id = layer_id
        item.layer_name = name
        item.collection = col
        item.scene = scene
        item.enabled = True
        vfx.layers.move(len(vfx.layers) - 1, 0)
        vfx.active_layer_index = 0
        try:
            rebuild_all_occlusion(vfx, master)  # new layer => occlusion changes everywhere
        except Exception as e:
            print("VFX occlusion error:", e)
        rebuild_comp(vfx, master)
        self.report({'INFO'}, f"Created VFX layer: {name}")
        return {'FINISHED'}


class VFX_OT_add_selected_to_layer(bpy.types.Operator):
    bl_idname = "vfx.add_selected_to_layer"
    bl_label = "Add Selected To Active Layer"
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = active_layer(vfx)
        if not layer or not layer.collection:
            self.report({'ERROR'}, "Active layer has no collection")
            return {'CANCELLED'}
        selected = collect_objects(context.selected_objects or [], vfx.include_children)
        if not selected:
            self.report({'ERROR'}, "No selected objects")
            return {'CANCELLED'}
        count = 0
        for obj in selected:
            if layer.collection.objects.get(obj.name) is None:
                layer.collection.objects.link(obj)
                count += 1
        if count:
            # parent Empties must come along, or children lose their transform
            ensure_parent_empties(layer.collection, selected)
            try:
                rebuild_all_occlusion(vfx, master)  # layer content changed
            except Exception as e:
                print("VFX occlusion error:", e)
        self.report({'INFO'}, f"Added {count} object(s) to {layer.layer_name}")
        return {'FINISHED'}


class VFX_OT_remove_selected_from_layer(bpy.types.Operator):
    bl_idname = "vfx.remove_selected_from_layer"
    bl_label = "Remove Selected From Active Layer"
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = active_layer(vfx)
        if not layer or not layer.collection:
            self.report({'ERROR'}, "Active layer has no collection")
            return {'CANCELLED'}
        selected = collect_objects(context.selected_objects or [], vfx.include_children)
        if not selected:
            self.report({'ERROR'}, "No selected objects")
            return {'CANCELLED'}
        count = 0
        for obj in selected:
            if layer.collection.objects.get(obj.name) is not None:
                layer.collection.objects.unlink(obj)
                count += 1
        if count:
            try:
                rebuild_all_occlusion(vfx, master)  # layer content changed
            except Exception as e:
                print("VFX occlusion error:", e)
        self.report({'INFO'}, f"Removed {count} object(s) from {layer.layer_name}")
        return {'FINISHED'}


class VFX_OT_select_layer_objects(bpy.types.Operator):
    bl_idname = "vfx.select_layer_objects"
    bl_label = "Select Layer Objects in 3D Viewport"
    bl_description = "Select all objects that belong to this layer in the 3D viewport"
    bl_options = {'REGISTER', 'UNDO'}

    layer_index: IntProperty(name="Layer Index", default=-1)

    def execute(self, context):
        vfx, master = get_project(context, allow_write=False)
        idx = self.layer_index
        if idx >= 0 and idx < len(vfx.layers):
            layer = vfx.layers[idx]
        else:
            layer = active_layer(vfx)

        if not layer or not layer.collection:
            self.report({'WARNING'}, "Layer has no collection")
            return {'CANCELLED'}

        # Deselect all currently selected objects in active view layer
        bpy.ops.object.select_all(action='DESELECT')

        count = 0
        active_candidate = None
        for obj in layer.collection.objects:
            # Skip holdout proxies or empties if desired, but user wants to see layer contents
            if obj.get("vfx_proxy"):
                continue
            try:
                obj.select_set(True)
                active_candidate = obj
                count += 1
            except Exception:
                pass

        if active_candidate:
            context.view_layer.objects.active = active_candidate

        self.report({'INFO'}, f"Selected {count} object(s) from layer '{layer.layer_name}'")
        return {'FINISHED'}


class VFX_OT_add_selected_lights(bpy.types.Operator):
    bl_idname = "vfx.add_selected_lights"
    bl_label = "Add Selected Lights"
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        root = ensure_root(master)
        col = ensure_light_collection(master, root)
        lights = [o for o in (context.selected_objects or []) if o.type == 'LIGHT']
        if not lights:
            self.report({'ERROR'}, "No selected lights")
            return {'CANCELLED'}
        count = 0
        for obj in lights:
            for c in list(obj.users_collection):
                try:
                    c.objects.unlink(obj)
                except Exception:
                    pass
            if col.objects.get(obj.name) is None:
                col.objects.link(obj)
                count += 1
        link_lights_to_all_scenes(vfx, col)
        self.report({'INFO'}, f"Moved {count} light(s) into VFX_Lights")
        return {'FINISHED'}


def create_shadow_pass_for_layer(layer, vfx, master, mode='CAST'):
    if not layer or not layer.collection or len(layer.collection.objects) == 0:
        return None
    if layer.shadow_scene:
        return layer.shadow_scene
    root = ensure_root(master)
    cam_col = ensure_camera_collection(master, root)
    light_col = ensure_light_collection(master, root)
    cast_col = bpy.data.collections.new(f"VFX_{layer.layer_name}_SHD_CAST")
    catch_col = bpy.data.collections.new(f"VFX_{layer.layer_name}_SHD_CATCH")
    root.children.link(cast_col)
    root.children.link(catch_col)
    layer.shadow_cast_collection = cast_col
    layer.shadow_catch_collection = catch_col
    layer.shadow_mode = mode
    cast_col["vfx_id"] = layer.id
    cast_col["vfx_pass"] = "SHADOW_CAST"
    catch_col["vfx_id"] = layer.id
    catch_col["vfx_pass"] = "SHADOW_CATCH"
    exclude_collection_in_master(master, cast_col)
    exclude_collection_in_master(master, catch_col)
    drawable = {'MESH', 'CURVE', 'VOLUME', 'SURFACE', 'META'}
    made_cast = 0
    made_catch = 0
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
        return proxy
    if mode == 'RECEIVE':
        for obj in layer.collection.objects:
            if obj.type not in drawable:
                continue
            p = make_proxy(obj, "_VFXCatch", catch_col)
            set_shadow_catcher(p, True)
            made_catch += 1
        for other in vfx.layers:
            if other.id == layer.id or not other.enabled or not other.collection:
                continue
            for obj in other.collection.objects:
                if obj.type not in drawable:
                    continue
                p = make_proxy(obj, "_VFXShadowCaster", cast_col)
                set_only_shadow_caster(p)
                made_cast += 1
    else:
        catcher = layer.shadow_catcher
        if not catcher:
            catcher = create_default_catcher(layer, master)
            layer.shadow_catcher = catcher
        for obj in layer.collection.objects:
            if catcher and obj == catcher:
                continue
            if obj.type not in drawable:
                continue
            p = make_proxy(obj, "_VFXShadowCaster", cast_col)
            set_only_shadow_caster(p)
            made_cast += 1
        if catch_col.objects.get(catcher.name) is None:
            catch_col.objects.link(catcher)
        set_shadow_catcher(catcher, True)
        made_catch += 1
    scene = create_empty_scene(f"VFX_{layer.layer_name}_SHD", master)
    scene["vfx_id"] = layer.id
    scene["vfx_pass"] = "SHADOW"
    scene.vfx.master_scene = master
    link_collection_to_scene(scene, cast_col)
    link_collection_to_scene(scene, catch_col)
    link_collection_to_scene(scene, cam_col)
    link_collection_to_scene(scene, light_col)
    if master.camera:
        scene.camera = master.camera
    sync_scene_settings(master, scene)
    try:
        scene.render.engine = vfx.shadows_engine
    except Exception:
        pass
    if scene.render.engine == 'CYCLES':
        optimize_shadow_scene_cycles(scene, samples=getattr(layer, 'shadow_samples', 8))
    layer.shadow_scene = scene
    layer.use_shadow = True
    try:
        sync_layer_bounce_proxy(layer, master)
    except Exception as e:
        print(f"VFX bounce proxy error: {e}")
    rebuild_comp(vfx, master)
    return scene


class VFX_OT_create_shadow_pass(bpy.types.Operator):
    bl_idname = "vfx.create_shadow_pass"
    bl_label = "Create Shadow Pass"
    bl_options = {'REGISTER', 'UNDO'}
    mode: EnumProperty(
        name="Mode",
        items=(('CAST', "Cast shadow", "Layer objects cast shadow onto a catcher"),
               ('RECEIVE', "Receive shadow", "Layer receives shadows from other layers (separate pass)")),
        default='CAST')
    layer_index: IntProperty(name="Layer Index", default=-1)

    def invoke(self, context, event):
        vfx, master = get_project(context)
        idx = self.layer_index
        layer = vfx.layers[idx] if (0 <= idx < len(vfx.layers)) else active_layer(vfx)
        if not layer:
            self.report({'ERROR'}, "No active layer")
            return {'CANCELLED'}
        if layer.shadow_scene:
            self.report({'WARNING'}, "Shadow pass already exists")
            return {'CANCELLED'}
        if layer.shadow_catcher:
            self.mode = 'CAST'
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        idx = self.layer_index
        layer = vfx.layers[idx] if (0 <= idx < len(vfx.layers)) else active_layer(vfx)
        if not layer:
            self.report({'ERROR'}, "No active layer")
            return {'CANCELLED'}
        if not layer.collection or len(layer.collection.objects) == 0:
            self.report({'ERROR'}, "Active layer has no objects")
            return {'CANCELLED'}
        if layer.shadow_scene:
            self.report({'WARNING'}, "Shadow pass already exists")
            return {'CANCELLED'}
        scene = create_shadow_pass_for_layer(layer, vfx, master, mode=self.mode)
        if scene:
            self.report({'INFO'}, f"Shadow pass created: {scene.name}")
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, "Could not create shadow pass")
            return {'CANCELLED'}


class VFX_OT_enable_gpu_compute(bpy.types.Operator):
    bl_idname = "vfx.enable_gpu_compute"
    bl_label = "Enable GPU Compute for Cycles"
    bl_description = "Enable OptiX / CUDA GPU acceleration in Cycles for fast rendering"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        ensure_cycles_gpu(master)
        for sc in bpy.data.scenes:
            if hasattr(sc, 'cycles'):
                sc.cycles.device = 'GPU'
        try:
            bpy.ops.wm.save_userpref()
        except Exception:
            pass
        self.report({'INFO'}, "GPU OptiX / CUDA ускорение успешно активировано для Cycles!")
        return {'FINISHED'}


class VFX_OT_delete_layer(bpy.types.Operator):
    bl_idname = "vfx.delete_layer"
    bl_label = "Delete Active Layer"
    bl_options = {'REGISTER', 'UNDO'}
    confirm: BoolProperty(name="Delete layer, its scenes and collections?", default=True)
    def invoke(self, context, event):
        vfx, master = get_project(context)
        layer = active_layer(vfx)
        if not layer:
            self.report({'WARNING'}, "No active layer")
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self)
    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = active_layer(vfx)
        if not layer:
            self.report({'ERROR'}, "No active layer")
            return {'CANCELLED'}
        if not self.confirm:
            return {'CANCELLED'}
        remove_comp_node(master, f"VFX_RL_{layer.id}")
        remove_comp_node(master, f"VFX_RL_{layer.id}_SHD")
        remove_comp_node(master, f"VFX_SHD_RAMP_{layer.id}")
        remove_comp_node(master, f"VFX_SHD_BLUR_{layer.id}")
        if layer.scene and layer.scene != master:
            remove_scene_safe(context, layer.scene, master)
        if layer.shadow_scene and layer.shadow_scene != master:
            remove_scene_safe(context, layer.shadow_scene, master)
        remove_shadow_collections(layer)
        if layer.collection:
            for obj in list(layer.collection.objects):
                if len(obj.users_collection) <= 1:
                    try:
                        master.collection.objects.link(obj)
                    except Exception:
                        pass
            try:
                bpy.data.collections.remove(layer.collection)
            except Exception:
                pass
        layer_name = layer.layer_name
        idx = vfx.active_layer_index
        vfx.layers.remove(idx)
        vfx.active_layer_index = max(0, min(idx, len(vfx.layers) - 1))
        try:
            rebuild_all_occlusion(vfx, master)  # drop stale occluders/proxies
        except Exception as e:
            print("VFX occlusion error:", e)
        rebuild_comp(vfx, master)
        self.report({'INFO'}, f"Deleted layer: {layer_name}")
        return {'FINISHED'}


class VFX_OT_toggle_layer_expand(bpy.types.Operator):
    bl_idname = "vfx.toggle_layer_expand"
    bl_label = "Toggle Expand"
    bl_options = set()
    index: IntProperty(default=0)
    def execute(self, context):
        vfx, master = get_project(context)
        if 0 <= self.index < len(vfx.layers):
            vfx.layers[self.index].expanded = not vfx.layers[self.index].expanded
        return {'FINISHED'}


class VFX_OT_toggle_fog_expand(bpy.types.Operator):
    bl_idname = "vfx.toggle_fog_expand"
    bl_label = "Toggle Fog Settings"
    bl_options = set()
    def execute(self, context):
        vfx, master = get_project(context)
        vfx.fog_expanded = not vfx.fog_expanded
        return {'FINISHED'}


class VFX_OT_preview_this_mask(bpy.types.Operator):
    bl_idname = "vfx.preview_this_mask"
    bl_label = "Show This Mask"
    bl_description = "Show this effect's mask in the viewer"
    bl_options = {'REGISTER', 'UNDO'}
    source: EnumProperty(
        name="Mask Source",
        items=(('FOG', "Fog", ""), ('DOF', "DOF", ""), ('GLARE', "Glare", ""), ('GRADE', "Grade", "")),
    )
    def execute(self, context):
        vfx, master = get_project(context)
        vfx.mask_source = self.source
        vfx.use_mask = True
        return {'FINISHED'}


class VFX_OT_pick_cryptomatte(bpy.types.Operator):
    """Pick an object in the 3D view; its name becomes an EXT mask source"""
    bl_idname = "vfx.pick_cryptomatte"
    bl_label = "Pick Object (pipette)"
    bl_description = "Click an object in the 3D view to use it as mask"
    bl_options = {'REGISTER'}

    target: EnumProperty(
        name="Target",
        items=(('NONE', "Global", "Store in scene VFX crypto_pick_name"),
               ('LAYER', "Active Layer", "Store in the active layer's grade Ext Node"),
               ('INFLUENCE', "Crypto Influence",
                "Add objects to one effect's crypto influence set (click several)")),
        default='NONE')
    # which effect's object set to fill in INFLUENCE mode:
    # FOG / DOF / GLARE / GRADE / CM
    fx_key: StringProperty(default="")

    _FX_PROPS = {
        'FOG': 'fog_crypto_objects',
        'DOF': 'dof_crypto_objects',
        'GLARE': 'glare_crypto_objects',
        'GRADE': 'grade_crypto_objects',
        'CM': 'cm_crypto_objects',
    }

    def invoke(self, context, event):
        if context.area and context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "Run from the 3D Viewport")
            return {'CANCELLED'}
        if self.target == 'INFLUENCE':
            vfx, master = get_project(context)
            prop = self._FX_PROPS.get(self.fx_key)
            if prop:
                setattr(vfx, prop, "")  # start a fresh set for this effect
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "VFX pipette: LMB pick object | ESC/RMB cancel")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        context.area.tag_redraw()
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            context.workspace.status_text_set(None)
            if self.target == 'INFLUENCE':
                try:
                    vfx = context.scene.vfx
                    prop = self._FX_PROPS.get(self.fx_key, "")
                    raw = getattr(vfx, prop, "") if prop else ""
                    n = len([x for x in (raw or "").split("|") if x.strip()])
                    self.report({'INFO'}, f"Crypto set ({self.fx_key}): {n} object(s)")
                except Exception:
                    pass
            return {'FINISHED'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            obj = self._pick(context, event)
            if obj is None:
                self.report({'WARNING'}, "Nothing under the cursor")
                return {'RUNNING_MODAL'}
            self._apply(context, obj.name)
            if self.target != 'INFLUENCE':
                context.workspace.status_text_set(None)
                self.report({'INFO'}, f"Mask object: {obj.name}")
                return {'FINISHED'}
            # INFLUENCE mode: keep picking, accumulate names
            try:
                vfx = context.scene.vfx
                prop = self._FX_PROPS.get(self.fx_key, "")
                raw = getattr(vfx, prop, "") if prop else ""
                n = len([x for x in (raw or "").split("|") if x.strip()])
                context.workspace.status_text_set(
                    f"VFX {self.fx_key}: {n} picked — keep clicking, ESC to finish")
            except Exception:
                pass
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE':
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}

    def _pick(self, context, event):
        """Project all visible objects to screen space, nearest wins.
        (scene.ray_cast is unreliable in Blender 5.2 — per AGENT.md)"""
        import mathutils
        region = context.region
        rv3d = context.region_data
        if region is None or rv3d is None:
            return None
        mx, my = event.mouse_region_x, event.mouse_region_y
        best, best_d = None, 48.0  # px radius around the click
        dg = context.evaluated_depsgraph_get()
        for obj in context.visible_objects:
            if obj.type not in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT'}:
                continue
            try:
                ev = obj.evaluated_get(dg)
                # proper perspective projection of the bounding box
                prj = rv3d.perspective_matrix @ ev.matrix_world.to_4x4()
                corners = [prj @ mathutils.Vector(c[:] + (1.0,)) for c in ev.bound_box]
                vis = False
                for c in corners:
                    if c.w > 0:
                        vis = True
                        break
                if not vis:
                    continue
                ndc_x = sum(c.x / c.w for c in corners) / len(corners)
                ndc_y = sum(c.y / c.w for c in corners) / len(corners)
                sx = region.width * (ndc_x * 0.5 + 0.5)
                sy = region.height * (ndc_y * 0.5 + 0.5)
                d = math.hypot(sx - mx, sy - my)
                if d < best_d:
                    best_d = d
                    best = obj
            except Exception:
                continue
        return best

    def _apply(self, context, name):
        vfx, master = get_project(context, allow_write=True)
        if self.target == 'INFLUENCE':
            # accumulate into ONE effect's own object set
            prop = self._FX_PROPS.get(self.fx_key)
            if not prop:
                return
            cur = [x.strip() for x in (getattr(vfx, prop, "") or "").split("|") if x.strip()]
            if name not in cur:
                cur.append(name)
            setattr(vfx, prop, "|".join(cur))
            return
        vfx.crypto_pick_name = name
        if self.target == 'LAYER':
            layer = active_layer(vfx)
            if layer is not None:
                layer.grade_mask_source = 'EXT'
                layer.grade_mask_ext_node = "VFX_CRYPTO_PICK"
        else:
            # Master grade: enable it and point its Ext mask at the crypto node
            if not vfx.m_grade_enable:
                vfx.m_grade_enable = True
            if vfx.grade_mask_source in {'NONE', 'EXT'}:
                vfx.grade_mask_source = 'EXT'
                vfx.grade_mask_ext_node = "VFX_CRYPTO_PICK"
        from .compositor import ensure_crypto_mask_node
        ensure_crypto_mask_node(vfx, master, name)
        from .materials import _trigger_comp as _tc
        try:
            _tc(context)
        except Exception:
            pass


class VFX_OT_drag_layer(bpy.types.Operator):
    bl_idname = "vfx.drag_layer"
    bl_label = "Drag Layer"
    index: IntProperty(default=0)
    def invoke(self, context, event):
        vfx, master = get_project(context)
        if self.index < 0 or self.index >= len(vfx.layers):
            return {'CANCELLED'}
        vfx.active_layer_index = self.index
        self.start_index = self.index
        self.current_index = self.index
        self.start_y = event.mouse_y
        try:
            ui_scale = context.preferences.system.ui_scale
        except Exception:
            ui_scale = 1.0
        self.row_height = max(16, 24 * ui_scale)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}
    def modal(self, context, event):
        vfx, master = get_project(context)
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.finish(context)
            return {'FINISHED'}
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            self.finish(context)
            return {'CANCELLED'}
        if event.type == 'MOUSEMOVE':
            dy = event.mouse_y - self.start_y
            steps = int(dy / self.row_height)
            target = self.start_index - steps
            target = max(0, min(len(vfx.layers) - 1, target))
            if target != self.current_index:
                vfx.layers.move(self.current_index, target)
                self.current_index = target
                vfx.active_layer_index = target
                if context.area:
                    context.area.tag_redraw()
        return {'RUNNING_MODAL'}
    def finish(self, context):
        vfx, master = get_project(context, allow_write=True)
        vfx.composite_sort_mode = 'MANUAL'
        rebuild_comp(vfx, master)  # order follows list order in MANUAL mode


class VFX_OT_move_layer_up(bpy.types.Operator):
    bl_idname = "vfx.move_layer_up"
    bl_label = "Move Layer Up (Forward)"
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        i = vfx.active_layer_index
        if i <= 0 or i >= len(vfx.layers):
            return {'CANCELLED'}
        vfx.layers.move(i, i - 1)
        vfx.active_layer_index = i - 1
        vfx.composite_sort_mode = 'MANUAL'
        rebuild_comp(vfx, master)
        return {'FINISHED'}


class VFX_OT_move_layer_down(bpy.types.Operator):
    bl_idname = "vfx.move_layer_down"
    bl_label = "Move Layer Down (Back)"
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        i = vfx.active_layer_index
        if i < 0 or i >= len(vfx.layers) - 1:
            return {'CANCELLED'}
        vfx.layers.move(i, i + 1)
        vfx.active_layer_index = i + 1
        vfx.composite_sort_mode = 'MANUAL'
        rebuild_comp(vfx, master)
        return {'FINISHED'}


class VFX_OT_rename_layer(bpy.types.Operator):
    bl_idname = "vfx.rename_layer"
    bl_label = "Rename Layer"
    bl_options = {'REGISTER', 'UNDO'}
    new_name: StringProperty(name="New Name", default="")
    def invoke(self, context, event):
        vfx, master = get_project(context)
        layer = active_layer(vfx)
        if not layer:
            self.report({'ERROR'}, "No active layer")
            return {'CANCELLED'}
        if not self.new_name:
            self.new_name = layer.layer_name
        return context.window_manager.invoke_props_dialog(self)
    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = active_layer(vfx)
        if not layer:
            self.report({'ERROR'}, "No active layer")
            return {'CANCELLED'}
        name = self.new_name.strip()
        if not name:
            self.report({'ERROR'}, "Empty name")
            return {'CANCELLED'}
        rename_layer(layer, name)
        rebuild_comp(vfx, master)
        self.report({'INFO'}, f"Layer renamed to: {name}")
        return {'FINISHED'}


class VFX_OT_debug_layer(bpy.types.Operator):
    bl_idname = "vfx.debug_layer"
    bl_label = "Debug Active Layer Scene"
    def execute(self, context):
        vfx, master = get_project(context)
        layer = active_layer(vfx)
        if not layer or not layer.scene:
            self.report({'ERROR'}, "No active layer scene")
            return {'CANCELLED'}
        sc = layer.scene
        print("=" * 60)
        print("VFX DEBUG layer:", layer.layer_name)
        print("scene:", sc.name)
        print("engine:", sc.render.engine)
        print("=" * 60)
        self.report({'INFO'}, "Debug info printed to console")
        return {'FINISHED'}


_RENDER_STATE = {"op": None}


def _vfx_render_post(scene, *args):
    op = _RENDER_STATE["op"]
    if op is not None:
        op.frames_done += 1


def _vfx_render_complete(scene, *args):
    op = _RENDER_STATE["op"]
    if op is not None:
        op.job_done = True


def _vfx_render_cancel(scene, *args):
    op = _RENDER_STATE["op"]
    if op is not None:
        op.cancelled = True
        op.job_done = True


def get_existing_frame_numbers(folder, ext=None):
    if not os.path.exists(folder):
        return set()
    nums = set()
    try:
        for f in os.listdir(folder):
            base, fext = os.path.splitext(f)
            if ext and fext.lower() != ext.lower():
                continue
            if not ext and fext.lower() not in ('.exr', '.png', '.tif', '.tiff'):
                continue
            try:
                nums.add(int(base))
            except ValueError:
                pass
    except Exception:
        pass
    return nums


class VFX_OT_render_all_layers(bpy.types.Operator):
    bl_idname = "vfx.render_all_layers"
    bl_label = "Render All Layers"
    bl_description = "Render all enabled layer scenes as EXR (objects + shadows + background)"
    post_files: BoolProperty(name="Then build comp from EXR", default=False)
    include_layer_shadow: BoolProperty(name="Include active layer shadow pass", default=False)
    only_layer: IntProperty(name="Only layer index", default=-1)
    only_shadow_for_layer: IntProperty(name="Only shadow of layer index", default=-1)
    only_background: BoolProperty(name="Only background", default=False)
    only_fog_map: BoolProperty(name="Only fog map scene", default=False)
    only_selected: BoolProperty(name="Only selected layers", default=False)
    refresh_after: BoolProperty(name="Refresh EXR comp after", default=False)
    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        auto_sync_settings(vfx, master)
        only = getattr(self, "only_layer", -1)
        only_shd = getattr(self, "only_shadow_for_layer", -1)
        only_bg = getattr(self, "only_background", False)
        only_fog = getattr(self, "only_fog_map", False)
        only_sel = getattr(self, "only_selected", False)
        if vfx.render_running:
            job_alive = False
            try:
                job_alive = bpy.app.is_job_running('RENDER')
            except Exception:
                job_alive = _RENDER_STATE["op"] is not None
            if job_alive:
                self.report({'WARNING'}, "Render already running")
                return {'CANCELLED'}
            vfx.render_running = False
            _RENDER_STATE["op"] = None
            for h in (_vfx_render_post, _vfx_render_complete, _vfx_render_cancel):
                try:
                    bpy.app.handlers.render_post.remove(h)
                except Exception:
                    pass
            try:
                bpy.app.handlers.render_complete.remove(_vfx_render_complete)
                bpy.app.handlers.render_cancel.remove(_vfx_render_cancel)
            except Exception:
                pass
        scenes = []
        if only_fog:
            _setup_fog_passes(vfx, master, force=True)
            fm = getattr(vfx, "fog_map_scene", None)
            if fm is not None:
                scenes.append(fm)
        elif only_bg and getattr(vfx, "bg_scene", None) is not None:
            scenes.append(vfx.bg_scene)
        elif only_shd >= 0 and only_shd < len(vfx.layers):
            layer = vfx.layers[only_shd]
            if layer.shadow_catcher and not layer.shadow_scene:
                try:
                    create_shadow_pass_for_layer(layer, vfx, master, mode=getattr(layer, "shadow_mode", 'CAST') or 'CAST')
                except Exception as e:
                    print(f"VFX auto shadow error: {e}")
            if layer.shadow_scene:
                scenes.append(layer.shadow_scene)
        elif only >= 0 and only < len(vfx.layers):
            layer = vfx.layers[only]
            if self.include_layer_shadow and layer.shadow_catcher and not layer.shadow_scene:
                try:
                    create_shadow_pass_for_layer(layer, vfx, master, mode=getattr(layer, "shadow_mode", 'CAST') or 'CAST')
                except Exception as e:
                    print(f"VFX auto shadow error: {e}")
            if layer.scene:
                scenes.append(layer.scene)
            if self.include_layer_shadow and layer.shadow_scene:
                scenes.append(layer.shadow_scene)
        elif only_sel:
            sel_layers = [l for l in vfx.layers if getattr(l, "selected", False)]
            if not sel_layers:
                act = active_layer(vfx)
                if act:
                    sel_layers = [act]
            for layer in sel_layers:
                if self.include_layer_shadow and layer.shadow_catcher and not layer.shadow_scene:
                    try:
                        create_shadow_pass_for_layer(layer, vfx, master, mode=getattr(layer, "shadow_mode", 'CAST') or 'CAST')
                    except Exception as e:
                        print(f"VFX auto shadow error: {e}")
                if layer.scene:
                    scenes.append(layer.scene)
                if self.include_layer_shadow and layer.shadow_scene:
                    scenes.append(layer.shadow_scene)
        else:
            for layer in vfx.layers:
                if not layer.enabled:
                    continue
                if layer.shadow_catcher and not layer.shadow_scene:
                    try:
                        create_shadow_pass_for_layer(layer, vfx, master, mode=getattr(layer, "shadow_mode", 'CAST') or 'CAST')
                    except Exception as e:
                        print(f"VFX auto shadow error: {e}")
                if layer.scene:
                    scenes.append(layer.scene)
                if layer.shadow_scene:
                    scenes.append(layer.shadow_scene)
            bg = getattr(vfx, "bg_scene", None)
            if bg is not None:
                scenes.insert(0, bg)
            if getattr(vfx, "use_fog", False) or getattr(vfx, "use_dof", False):
                _setup_fog_passes(vfx, master, force=True)
                fm = getattr(vfx, "fog_map_scene", None)
                if fm is not None and fm not in scenes:
                    scenes.append(fm)
        if not scenes:
            self.report({'ERROR'}, "No enabled layer scenes")
            return {'CANCELLED'}
        self.mode = 'ANIMATION'
        # Shadow proxies are NOT auto-refreshed before render to preserve
        # manual visibility edits. Use "Refresh Shadow Proxies" button instead.
        # try:
        #     refresh_shadow_proxies(vfx, master)
        # except Exception as e:
        #     print("VFX proxy refresh error:", e)
        try:
            rebuild_all_occlusion(vfx, master)  # holdouts fresh for every render
        except Exception as e:
            print("VFX occlusion refresh error:", e)
        self.steps = []
        total_frames = 0
        already_done_frames = 0
        resume = getattr(vfx, "resume_render", True)

        for sc in scenes:
            if getattr(sc.render, "engine", "") == 'CYCLES':
                ensure_cycles_gpu(sc)
        for layer in vfx.layers:
            if layer.shadow_scene and getattr(layer.shadow_scene.render, "engine", "") == 'CYCLES':
                optimize_shadow_scene_cycles(layer.shadow_scene, samples=getattr(layer, 'shadow_samples', 8))

        for sc in scenes:
            is_fog_sc = (sc.name == "VFX_FOGMAP" or sc == getattr(vfx, "fog_map_scene", None))
            sc_fmt = getattr(vfx, "fog_map_format", 'PNG') if is_fog_sc else getattr(vfx, "render_format", 'OPEN_EXR')
            sc_target_ext = '.png' if sc_fmt == 'PNG' else '.exr'

            # Determine if this scene is static (single frame hold)
            is_static_scene = False
            target_static_frame = 1
            if getattr(vfx, "bg_scene", None) and sc == vfx.bg_scene:
                if getattr(vfx, "bg_is_static", True):
                    is_static_scene = True
                    target_static_frame = getattr(vfx, "bg_static_frame", 1)
            elif is_fog_sc:
                # Fog map is animated along with camera and objects
                is_static_scene = False
            else:
                for layer in vfx.layers:
                    if layer.scene and sc == layer.scene:
                        if getattr(layer, "is_static", False):
                            is_static_scene = True
                            target_static_frame = getattr(layer, "static_frame", 1)
                        break
                    elif layer.shadow_scene and sc == layer.shadow_scene:
                        # Shadow pass is dynamic (tracks animated casters onto static catcher)
                        is_static_scene = False
                        break

            out_dir = bpy.path.abspath(f"{vfx.output_dir}{sc.name}/")
            existing = get_existing_frame_numbers(out_dir, sc_target_ext)

            if is_static_scene:
                if resume and target_static_frame in existing:
                    already_done_frames += 1
                    continue
                sc.frame_start = target_static_frame
                sc.frame_end = target_static_frame
            else:
                p_start = master.frame_start
                p_end = master.frame_end
                if resume and existing:
                    cur = p_start
                    while cur <= p_end and cur in existing:
                        cur += 1
                    if cur > p_end:
                        # Entire layer already finished!
                        already_done_frames += (p_end - p_start + 1)
                        continue
                    sc.frame_start = cur
                    sc.frame_end = p_end
                    already_done_frames += (cur - p_start)
                else:
                    sc.frame_start = p_start
                    sc.frame_end = p_end

            try:
                if sc_fmt == 'PNG':
                    sc.render.image_settings.file_format = 'PNG'
                    sc.render.image_settings.color_mode = 'RGBA'
                    sc.render.image_settings.color_depth = '16'
                else:
                    sc.render.image_settings.file_format = 'OPEN_EXR'
                    sc.render.image_settings.color_mode = 'RGBA'
                    sc.render.image_settings.color_depth = '32'
                sc.render.filepath = f"{vfx.output_dir}{sc.name}/"
            except Exception:
                pass
            for vl in sc.view_layers:
                for attr in ('use_pass_mist', 'use_pass_z', 'use_pass_normal'):
                    try:
                        setattr(vl, attr, True)
                    except Exception:
                        pass
            n = (sc.frame_end - sc.frame_start + 1)
            total_frames += n
            self.steps.append((sc.name, n))

        if not self.steps:
            self.report({'INFO'}, "Все выбранные слои уже полностью отрендерены!")
            if getattr(self, "post_files", False) or getattr(self, "refresh_after", False):
                try:
                    m_sc = vfx.master_scene or context.scene
                    vfx.comp_mode = 'FILES'
                    rebuild_comp_from_files(vfx, m_sc)
                except Exception as e:
                    print("VFX rebuild comp error:", e)
            vfx.render_running = False
            vfx.render_progress = 1.0
            vfx.render_status = "Done (all frames already exist)"
            return {'FINISHED'}

        self.total_frames = max(1, total_frames + already_done_frames)
        self.step_index = 0
        self.frames_done = already_done_frames
        self.user_cancel = False
        self.cancelled = False
        self.job_done = False
        vfx.render_running = True
        vfx.render_progress = min(1.0, self.frames_done / self.total_frames)
        vfx.render_status = "Starting..."
        _RENDER_STATE["op"] = self
        self.parallel_procs = []
        self.is_parallel_step = False
        bpy.app.handlers.render_post.append(_vfx_render_post)
        bpy.app.handlers.render_complete.append(_vfx_render_complete)
        bpy.app.handlers.render_cancel.append(_vfx_render_cancel)
        self._timer = context.window_manager.event_timer_add(0.25, window=context.window)
        context.window_manager.modal_handler_add(self)
        self._start_step(context)
        return {'RUNNING_MODAL'}
    def _start_step(self, context):
        vfx = context.scene.vfx
        name, n = self.steps[self.step_index]
        sc = bpy.data.scenes.get(name)
        if sc is None:
            self.job_done = True
            return
        try:
            sc.render.use_persistent_data = True
        except Exception:
            pass

        # Ensure correct compositor node group for this scene:
        # Shadow scenes MUST use their shadow isolation comp, NEVER VFX_Compositor!
        # Regular layer scenes should NOT use any compositor node group.
        is_shadow_sc = False
        master_sc = getattr(vfx, "master_scene", None) or context.scene
        for l in vfx.layers:
            if l.shadow_scene and l.shadow_scene == sc:
                is_shadow_sc = True
                try:
                    tree = setup_shadow_isolation_comp(l, master_sc)
                    if tree and hasattr(sc, "compositing_node_group"):
                        sc.compositing_node_group = tree
                except Exception as e:
                    print(f"VFX: error attaching shadow isolation comp for {sc.name}:", e)
                break

        if not is_shadow_sc and sc != master_sc:
            if sc.name == "VFX_FOGMAP" or sc == getattr(vfx, "fog_map_scene", None):
                _ensure_fogmap_scene_comp(sc)
            elif hasattr(sc, "compositing_node_group") and sc.compositing_node_group:
                # Regular layer scenes render raw geometry without compositing
                sc.compositing_node_group = None
        self.job_done = False
        self.step_start = time.time()
        self.is_parallel_step = False

        # Parallel background render for animations (RTX 5080 multi-instance)
        if getattr(vfx, "use_parallel_render", False) and n > 1:
            k = min(max(2, getattr(vfx, "parallel_instances", 2)), n)
            if bpy.data.is_dirty or not bpy.data.filepath:
                try:
                    bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath, check_existing=False)
                except Exception:
                    pass
            out_dir = bpy.path.abspath(f"{vfx.output_dir}{sc.name}/")
            os.makedirs(out_dir, exist_ok=True)
            is_fog_sc = (sc.name == "VFX_FOGMAP" or sc == getattr(vfx, "fog_map_scene", None))
            fmt = getattr(vfx, "fog_map_format", 'PNG') if is_fog_sc else getattr(vfx, "render_format", 'OPEN_EXR')
            target_ext = '.png' if fmt == 'PNG' else '.exr'
            try:
                self.initial_folder_files = len([f for f in os.listdir(out_dir) if f.lower().endswith(target_ext)])
            except Exception:
                self.initial_folder_files = 0

            out_pattern = os.path.join(out_dir, "####")
            chunk_size = math.ceil(n / k)
            self.parallel_procs = []
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            for i in range(k):
                c_start = sc.frame_start + i * chunk_size
                c_end = min(sc.frame_end, c_start + chunk_size - 1)
                if c_start > sc.frame_end:
                    break
                cmd = [
                    bpy.app.binary_path, "-b", bpy.data.filepath,
                    "--scene", name,
                    "-F", "PNG" if fmt == 'PNG' else "OPEN_EXR",
                    "-o", out_pattern,
                    "-s", str(c_start),
                    "-e", str(c_end),
                    "-a"
                ]
                try:
                    p = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=flags
                    )
                    self.parallel_procs.append((p, c_start, c_end))
                except Exception as e:
                    print(f"VFX parallel worker error ({c_start}-{c_end}):", e)

            if self.parallel_procs:
                self.is_parallel_step = True
                pct = int((self.frames_done / self.total_frames) * 100)
                vfx.render_status = f"{pct}% ({self.frames_done}/{self.total_frames}) [{name}] x{len(self.parallel_procs)}"
                for window in bpy.context.window_manager.windows:
                    for area in window.screen.areas:
                        area.tag_redraw()
                return

        vfx.render_progress = min(1.0, self.frames_done / self.total_frames)
        vfx.render_status = f"{self.frames_done}/{self.total_frames}  {name}"
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
        win = context.window
        if win is None and bpy.context.window_manager.windows:
            win = bpy.context.window_manager.windows[0]
        try:
            sc.render.filepath = f"{vfx.output_dir}{sc.name}/"
        except Exception:
            pass
        try:
            with bpy.context.temp_override(window=win):
                bpy.ops.render.render('INVOKE_DEFAULT', animation=True, scene=name)
        except Exception as e:
            print("VFX render start error:", name, e)
            try:
                bpy.ops.render.render(animation=True, scene=name)
            except Exception as e2:
                print("VFX render fallback error:", name, e2)
                self.job_done = True
    def modal(self, context, event):
        vfx = context.scene.vfx

        # --- Parallel render modal branch ---
        if getattr(self, "is_parallel_step", False):
            if event.type == 'ESC' and event.value == 'PRESS':
                self.user_cancel = True
                for p, _, _ in getattr(self, "parallel_procs", []):
                    try:
                        p.terminate()
                    except Exception:
                        pass
                self.parallel_procs.clear()
                self._finish(context)
                return {'FINISHED'}

            if event.type == 'TIMER':
                name, n = self.steps[self.step_index]
                out_dir = bpy.path.abspath(f"{vfx.output_dir}{name}/")
                new_frames = 0
                try:
                    is_fog = (name == "VFX_FOGMAP" or (vfx.fog_map_scene and vfx.fog_map_scene.name == name))
                    fmt = getattr(vfx, "fog_map_format", 'PNG') if is_fog else getattr(vfx, "render_format", 'OPEN_EXR')
                    target_ext = '.png' if fmt == 'PNG' else '.exr'
                    now_files = len([f for f in os.listdir(out_dir) if f.lower().endswith(target_ext)])
                    new_frames = max(0, min(n, now_files - getattr(self, "initial_folder_files", 0)))
                except Exception:
                    pass

                prog = min(1.0, (self.frames_done + new_frames) / self.total_frames)
                vfx.render_progress = prog
                pct = int(prog * 100)
                vfx.render_status = f"{pct}% ({self.frames_done + new_frames}/{self.total_frames}) [{name}: {new_frames}/{n}] x{len(self.parallel_procs)}"
                for window in bpy.context.window_manager.windows:
                    for area in window.screen.areas:
                        area.tag_redraw()

                all_done = all(p.poll() is not None for p, _, _ in self.parallel_procs)
                if all_done:
                    self.parallel_procs.clear()
                    self.is_parallel_step = False
                    self.frames_done += n
                    self.step_index += 1
                    if self.user_cancel or self.cancelled or self.step_index >= len(self.steps):
                        self._finish(context)
                        return {'FINISHED'}
                    self._start_step(context)
            return {'PASS_THROUGH'}

        # --- Standard sequential modal branch ---
        if event.type == 'ESC' and event.value == 'PRESS':
            self.user_cancel = True
            try:
                bpy.ops.render.view_cancel()
            except Exception:
                pass
        if event.type == 'TIMER':
            prog = min(1.0, self.frames_done / self.total_frames)
            if abs(prog - vfx.render_progress) > 0.001:
                vfx.render_progress = prog
                if self.step_index < len(self.steps):
                    vfx.render_status = (
                        f"{self.frames_done}/{self.total_frames}  "
                        f"{self.steps[self.step_index][0]}"
                    )
                for window in bpy.context.window_manager.windows:
                    for area in window.screen.areas:
                        area.tag_redraw()
            try:
                alive = bpy.app.is_job_running('RENDER')
            except Exception:
                alive = True
            if (not alive) and (not self.job_done) and (time.time() - getattr(self, "step_start", 0)) > 3.0:
                self.job_done = True
            if self.job_done:
                self.job_done = False
                self.step_index += 1
                if self.user_cancel or self.cancelled or self.step_index >= len(self.steps):
                    self._finish(context)
                    return {'FINISHED'}
                self._start_step(context)
        return {'PASS_THROUGH'}
    def _finish(self, context):
        vfx = context.scene.vfx
        for p, _, _ in getattr(self, "parallel_procs", []):
            try:
                p.terminate()
            except Exception:
                pass
        self.parallel_procs = []
        self.is_parallel_step = False
        stopped = self.user_cancel or self.cancelled
        _RENDER_STATE["op"] = None
        try:
            bpy.app.handlers.render_post.remove(_vfx_render_post)
            bpy.app.handlers.render_complete.remove(_vfx_render_complete)
            bpy.app.handlers.render_cancel.remove(_vfx_render_cancel)
        except Exception:
            pass
        try:
            context.window_manager.event_timer_remove(self._timer)
        except Exception:
            pass
        vfx.render_running = False
        if not stopped:
            vfx.render_progress = 1.0
        vfx.render_status = "Stopped (ESC)" if stopped else "Done"
        # Rebuild comp whenever it was requested — also after ESC/stop,
        # as long as at least one frame reached the disk (a partial
        # sequence is valid input; comp reads whatever EXRs exist).
        rendered_any = self.frames_done > 0
        if (getattr(self, "post_files", False) or getattr(self, "refresh_after", False)) \
                and (not stopped or rendered_any):
            try:
                master = vfx.master_scene or context.scene
                vfx.comp_mode = 'FILES'
                rebuild_comp_from_files(vfx, master)
                vfx.render_status = \
                    "Stopped + comp from EXR (partial)" if stopped else "Done + comp from EXR"
            except Exception as e:
                print("VFX post-files error:", e)
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()


class VFX_OT_stop_render(bpy.types.Operator):
    bl_idname = "vfx.stop_render"
    bl_label = "Остановить рендер"
    bl_description = "Прервать текущий рендер и сохранить готовые кадры"

    def execute(self, context):
        op = _RENDER_STATE.get("op")
        if op:
            op.user_cancel = True
            for p, _, _ in getattr(op, "parallel_procs", []):
                try:
                    p.terminate()
                except Exception:
                    pass
            try:
                bpy.ops.render.view_cancel()
            except Exception:
                pass
            self.report({'INFO'}, "Рендер останавливается...")
        else:
            context.scene.vfx.render_running = False
            _RENDER_STATE["op"] = None
        return {'FINISHED'}


class VFX_OT_one_click_exr(bpy.types.Operator):
    bl_idname = "vfx.one_click_exr"
    bl_label = "Render EXR + Comp From Files"
    bl_description = "Render all layer scenes as EXR animation, then build comp from files"
    def execute(self, context):
        bpy.ops.vfx.render_all_layers('INVOKE_DEFAULT', post_files=True)
        return {'FINISHED'}


class VFX_OT_render_fog_map(bpy.types.Operator):
    bl_idname = "vfx.render_fog_map"
    bl_label = "Рендер секвенции Mist"
    bl_description = "Быстро отрендерить последовательность кадров Mist (карты глубины) в EEVEE для тумана и размытия"
    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        _setup_fog_passes(vfx, master, force=True)
        bpy.ops.vfx.render_all_layers('INVOKE_DEFAULT', only_fog_map=True, post_files=True)
        return {'FINISHED'}


class VFX_OT_one_click_exr_single(bpy.types.Operator):
    bl_idname = "vfx.one_click_exr_single"
    bl_label = "Render Selected Layer(s) EXR + Comp"
    bl_description = (
        "Render selected layers (or active layer if none selected) as EXR animation, "
        "then rebuild comp from files. Uses render settings below "
        "(objects engine, shadows engine). Shadow passes of layers "
        "are rendered too, if enabled"
    )
    include_layer_shadow: BoolProperty(
        name="Include shadow pass",
        description="Also render the shadow pass of selected layers",
        default=True,
    )

    def invoke(self, context, event):
        vfx, master = get_project(context)
        sel_layers = [l for l in vfx.layers if getattr(l, "selected", False) and l.scene]
        if not sel_layers:
            layer = active_layer(vfx)
            if not layer:
                self.report({'ERROR'}, "No active or selected layer")
                return {'CANCELLED'}
            if not layer.scene:
                self.report({'ERROR'}, f"Layer '{layer.layer_name}' has no scene")
                return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        vfx, master = get_project(context)
        sel_layers = [l for l in vfx.layers if getattr(l, "selected", False) and l.scene]
        if not sel_layers:
            layer = active_layer(vfx)
            if not layer or not layer.scene:
                self.report({'ERROR'}, "No active or selected layer with scene")
                return {'CANCELLED'}
            index = -1
            for i, item in enumerate(vfx.layers):
                if item.id == layer.id:
                    index = i
                    break
            if index < 0:
                self.report({'ERROR'}, "Layer not found")
                return {'CANCELLED'}
            bpy.ops.vfx.render_all_layers(
                'INVOKE_DEFAULT',
                post_files=True,
                only_layer=index,
                include_layer_shadow=self.include_layer_shadow,
            )
        else:
            bpy.ops.vfx.render_all_layers(
                'INVOKE_DEFAULT',
                post_files=True,
                only_selected=True,
                include_layer_shadow=self.include_layer_shadow,
            )
        return {'FINISHED'}


class VFX_OT_add_background(bpy.types.Operator):
    bl_idname = "vfx.add_background"
    bl_label = "Добавить слой фона"
    bl_description = "Добавить фоновый слой (видео, картинка, Movie Clip или 3D сцена) под всеми слоями"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        vfx.use_bg = True
        vfx.bg_expanded = True
        # If project has a movie clip loaded and no clip chosen, default to it
        if not vfx.bg_movie_clip and bpy.data.movieclips:
            vfx.bg_movie_clip = bpy.data.movieclips[0]
            vfx.bg_source_type = 'MOVIE_CLIP'
        _trigger_comp(context)
        self.report({'INFO'}, "Фоновый слой добавлен (задник)")
        return {'FINISHED'}


class VFX_OT_browse_bg_file(bpy.types.Operator):
    bl_idname = "vfx.browse_bg_file"
    bl_label = "Выбрать файл фона"
    bl_description = "Выбрать видеофайл или изображение для задника"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(
        name="Файл",
        subtype='FILE_PATH'
    )
    file_type: EnumProperty(
        name="Тип",
        items=(
            ('VIDEO', "Видео", ""),
            ('IMAGE', "Картинка", ""),
        ),
        default='VIDEO'
    )

    def invoke(self, context, event):
        vfx, master = get_project(context, allow_write=False)
        curr = vfx.bg_video_path if self.file_type == 'VIDEO' else vfx.bg_image_path
        if curr:
            p = bpy.path.abspath(curr)
            self.filepath = p
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        if not self.filepath:
            return {'CANCELLED'}
        vfx.use_bg = True
        if self.file_type == 'VIDEO':
            vfx.bg_video_path = self.filepath
            vfx.bg_source_type = 'VIDEO'
            ext = os.path.splitext(self.filepath)[1].lower()
            if ext in ('.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'):
                try:
                    clip = bpy.data.movieclips.load(self.filepath, check_existing=True)
                    vfx.bg_movie_clip = clip
                except Exception:
                    pass
        else:
            vfx.bg_image_path = self.filepath
            vfx.bg_source_type = 'IMAGE'
        _trigger_comp(context)
        self.report({'INFO'}, f"Фон установлен: {os.path.basename(self.filepath)}")
        return {'FINISHED'}


class VFX_OT_create_background(bpy.types.Operator):
    bl_idname = "vfx.create_background"
    bl_label = "Создать 3D сцену фона (World Only)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        vfx.use_bg = True
        vfx.bg_source_type = 'SCENE'
        if vfx.bg_scene:
            _trigger_comp(context)
            self.report({'INFO'}, "3D сцена фона уже существует")
            return {'FINISHED'}
        scene = create_empty_scene("VFX_BG", master)
        scene["vfx_pass"] = "BACKGROUND"
        scene.vfx.master_scene = master
        root = ensure_root(master)
        cam_col = ensure_camera_collection(master, root)
        link_collection_to_scene(scene, cam_col)
        if master.camera:
            scene.camera = master.camera
        sync_scene_settings(master, scene)
        try:
            scene.render.engine = vfx.objects_engine
        except Exception:
            pass
        try:
            scene.render.film_transparent = False
        except Exception:
            pass
        try:
            scene.world = master.world
        except Exception:
            pass
        vfx.bg_scene = scene
        rebuild_comp(vfx, master)
        self.report({'INFO'}, "Создана 3D сцена фона: VFX_BG")
        return {'FINISHED'}


class VFX_OT_delete_background(bpy.types.Operator):
    bl_idname = "vfx.delete_background"
    bl_label = "Удалить фон"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        vfx.use_bg = False
        nt = get_comp_tree(master, create=False)
        if nt:
            try:
                from .compositor import _cleanup_bg_nodes
                _cleanup_bg_nodes(nt)
            except Exception:
                pass
        sc = getattr(vfx, "bg_scene", None)
        if sc:
            vfx.bg_scene = None
            if sc != master:
                remove_scene_safe(context, sc, master)
        rebuild_comp(vfx, master)
        self.report({'INFO'}, "Фоновый слой удален")
        return {'FINISHED'}


class VFX_OT_delete_shadow_pass(bpy.types.Operator):
    bl_idname = "vfx.delete_shadow_pass"
    bl_label = "Delete Shadow Pass"
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = active_layer(vfx)
        if not layer or not layer.shadow_scene:
            self.report({'WARNING'}, "No shadow pass on active layer")
            return {'CANCELLED'}
        remove_comp_node(master, f"VFX_RL_{layer.id}_SHD")
        remove_comp_node(master, f"VFX_SHD_RAMP_{layer.id}")
        remove_comp_node(master, f"VFX_SHD_BLUR_{layer.id}")
        if layer.shadow_scene and layer.shadow_scene != master:
            remove_scene_safe(context, layer.shadow_scene, master)
        remove_shadow_collections(layer)
        layer.shadow_scene = None
        layer.use_shadow = False
        layer.shadow_cast_collection = None
        layer.shadow_catch_collection = None
        rebuild_comp(vfx, master)
        self.report({'INFO'}, f"Shadow pass removed from {layer.layer_name}")
        return {'FINISHED'}


class VFX_OT_focus_shadow_ramp(bpy.types.Operator):
    bl_idname = "vfx.focus_shadow_ramp"
    bl_label = "Focus Shadow ColorRamp"
    bl_description = "Выделить и открыть ноду ColorRamp для этой тени в окне Compositor"
    bl_options = {'REGISTER', 'UNDO'}

    layer_id: StringProperty(name="Layer ID", default="")

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = None
        if self.layer_id:
            layer = next((l for l in vfx.layers if l.id == self.layer_id), None)
        if not layer:
            layer = active_layer(vfx)
        if not layer:
            self.report({'WARNING'}, "Нет активного слоя")
            return {'CANCELLED'}

        rebuild_comp(vfx, master)
        nt = get_comp_tree(master)
        if not nt:
            self.report({'WARNING'}, "Дерево композитора не найдено")
            return {'CANCELLED'}

        ramp_name = f"VFX_SHD_RAMP_{layer.id}"
        ramp = nt.nodes.get(ramp_name)
        if not ramp:
            self.report({'WARNING'}, f"Нода {ramp_name} не найдена. Убедитесь, что ColorRamp включен.")
            return {'CANCELLED'}

        for n in nt.nodes:
            n.select = False
        ramp.select = True
        nt.nodes.active = ramp

        for area in context.screen.areas:
            if area.type == 'NODE_EDITOR':
                for space in area.spaces:
                    if space.type == 'NODE_EDITOR':
                        try:
                            space.tree_type = 'CompositorNodeTree'
                            space.node_tree = nt
                        except Exception:
                            pass
                break

        self.report({'INFO'}, f"ColorRamp для '{layer.layer_name}' выделен в Compositor")
        return {'FINISHED'}


def auto_sync_settings(vfx, master):
    try:
        root = ensure_root(master)
        ccol = ensure_camera_collection(master, root)
        link_camera_to_all_scenes(vfx, ccol)
        lcol = ensure_light_collection(master, root)
        sync_master_lights(master, root)
        link_lights_to_all_scenes(vfx, lcol)
    except Exception as e:
        print("VFX auto_sync_settings lights/camera error:", e)

    for layer in vfx.layers:
        if layer.scene:
            try:
                sync_scene_settings(master, layer.scene)
                layer.scene.render.engine = vfx.objects_engine
            except Exception:
                pass
        if layer.shadow_scene:
            try:
                sync_scene_settings(master, layer.shadow_scene)
                layer.shadow_scene.render.engine = vfx.shadows_engine
            except Exception:
                pass
        for c in (layer.shadow_cast_collection, layer.shadow_catch_collection,
                  getattr(layer, "occlusion_collection", None)):
            if c:
                exclude_collection_in_master(master, c)
    bg = getattr(vfx, "bg_scene", None)
    if bg:
        try:
            sync_scene_settings(master, bg)
            bg.render.engine = vfx.objects_engine
            bg.render.film_transparent = False
        except Exception:
            pass


class VFX_OT_match_scene_fps(bpy.types.Operator):
    bl_idname = "vfx.match_scene_fps"
    bl_label = "Синхронизировать FPS с видео"
    bl_description = "Выставить FPS проекта и всех слоев в точное соответствие с исходным видео, чтобы исключить рассинхрон и тряску"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        clip = getattr(vfx, "bg_movie_clip", None)
        if clip is None and bpy.data.movieclips:
            for mc in bpy.data.movieclips:
                if "matte" not in mc.name.lower() and mc.fps > 0:
                    clip = mc
                    break
            if clip is None:
                clip = bpy.data.movieclips[0]
        if clip and clip.fps > 0:
            target_fps = clip.fps
            master.render.fps = round(target_fps)
            master.render.fps_base = round(target_fps) / target_fps if target_fps != round(target_fps) else 1.0
            auto_sync_settings(vfx, master)
            self.report({'INFO'}, f"FPS проекта синхронизирован: {target_fps} fps (как у {clip.name})")
            return {'FINISHED'}
        self.report({'WARNING'}, "Клип с известным FPS не найден")
        return {'CANCELLED'}


class VFX_OT_match_frame_start(bpy.types.Operator):
    bl_idname = "vfx.match_frame_start"
    bl_label = "Выставить старт сцены с 1 кадра"
    bl_description = "Выставить frame_start=1 для проекта и всех слоев, чтобы трекинг камеры и видео совпадали"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        clip = getattr(vfx, "bg_movie_clip", None)
        if clip is None and bpy.data.movieclips:
            for mc in bpy.data.movieclips:
                if "matte" not in mc.name.lower():
                    clip = mc
                    break
            if clip is None:
                clip = bpy.data.movieclips[0]
        st = clip.frame_start if clip else 1
        master.frame_start = st
        auto_sync_settings(vfx, master)
        self.report({'INFO'}, f"Frame start проекта установлен в {st}")
        return {'FINISHED'}


class VFX_OT_refresh_proxies(bpy.types.Operator):
    bl_idname = "vfx.refresh_proxies"
    bl_label = "Refresh Shadow Proxies"
    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        refresh_shadow_proxies(vfx, master)
        rebuild_all_occlusion(vfx, master, report=True)  # built into Refresh per TZ
        self.report({'INFO'}, "Shadow + occlusion proxies refreshed")
        return {'FINISHED'}


class VFX_OT_refresh_occlusion(bpy.types.Operator):
    bl_idname = "vfx.refresh_occlusion"
    bl_label = "Refresh Occlusion Proxies"
    bl_description = "Rebuild holdout proxies of the active layer and print occlusion report"
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = active_layer(vfx)
        if not layer:
            self.report({'ERROR'}, "No active layer")
            return {'CANCELLED'}
        n = rebuild_layer_occlusion(vfx, master, layer)
        occlusion_self_check(vfx, master)
        self.report({'INFO'}, f"Occlusion rebuilt: {n} holdout proxy(ies) on '{layer.layer_name}'")
        return {'FINISHED'}


class VFX_OT_diagnostic(bpy.types.Operator):
    bl_idname = "vfx.diagnostic"
    bl_label = "VFX Diagnostic"
    bl_description = "Run diagnostic tests and copy results to clipboard"
    def execute(self, context):
        import traceback
        try:
            from .diagnostic import run_diagnostic
            text = run_diagnostic()
        except Exception as e:
            text = f"Diagnostic import error: {e}\n{traceback.format_exc()}"
        context.window_manager.clipboard = text
        print(text)
        self.report({'INFO'}, "Diagnostic copied to clipboard")
        return {'FINISHED'}


class VFX_OT_rebuild_comp(bpy.types.Operator):
    bl_idname = "vfx.rebuild_comp"
    bl_label = "Rebuild Comp Nodes"
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        import traceback
        import importlib
        try:
            from . import compositor
            importlib.reload(compositor)
        except Exception:
            pass
        vfx, master = get_project(context, allow_write=True)
        try:
            if getattr(vfx, "comp_mode", 'LIVE') == 'FILES':
                from .compositor import rebuild_comp_from_files
                rebuild_comp_from_files(vfx, master)
                self.report({'INFO'}, "Comp rebuilt from EXR files")
            else:
                from .compositor import rebuild_comp
                rebuild_comp(vfx, master)
                self.report({'INFO'}, "VFX compositing nodes rebuilt")
        except Exception as e:
            print("VFX rebuild error:", e)
            traceback.print_exc()
            self.report({'ERROR'}, f"Ошибка пересборки: {e}")
        return {'FINISHED'}



class VFX_OT_setup_light_groups(bpy.types.Operator):
    bl_idname = "vfx.setup_light_groups"
    bl_label = "Setup Light Groups"
    bl_description = "Auto-assign lights to groups (Key/Fill/Rim/Env) based on direction"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        count = auto_assign_light_groups(vfx, master)
        enable_light_groups_on_view_layer(master)
        for layer in vfx.layers:
            if layer.scene:
                enable_light_groups_on_view_layer(layer.scene)
            if layer.shadow_scene:
                enable_light_groups_on_view_layer(layer.shadow_scene)
        self.report({'INFO'}, f"Light groups: {count} lights assigned")
        return {'FINISHED'}


class VFX_OT_apply_color_preset(bpy.types.Operator):
    bl_idname = "vfx.apply_color_preset"
    bl_label = "Apply Color Preset"
    bl_description = "Apply a color correction preset to the comp"

    preset: bpy.props.EnumProperty(
        name="Preset",
        items=(
            ('NONE', "Off", ""),
            ('WARM', "Warm", ""),
            ('TEAL_ORANGE', "Teal & Orange", ""),
            ('COOL', "Cool", ""),
            ('FILM', "Film", ""),
        ),
        default='NONE'
    )

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        vfx.color_match_preset = self.preset
        vfx.use_color_match = self.preset != 'NONE'
        _trigger_comp(context)
        self.report({'INFO'}, f"Color preset: {self.preset}")
        return {'FINISHED'}


class VFX_OT_reset_layer_grade(bpy.types.Operator):
    bl_idname = "vfx.reset_layer_grade"
    bl_label = "Reset Layer Grade"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = active_layer(vfx)
        if not layer:
            self.report({'ERROR'}, "No active layer")
            return {'CANCELLED'}
        layer.l_exposure = 0.0
        layer.l_temp = 0.0
        layer.l_tint = 0.0
        layer.l_lift = (0.0, 0.0, 0.0)
        layer.l_gain = (1.0, 1.0, 1.0)
        layer.l_sat = 1.0
        layer.l_contrast = 1.0
        rebuild_comp(vfx, master)
        self.report({'INFO'}, "Layer grade reset")
        return {'FINISHED'}


class VFX_OT_reset_master_grade(bpy.types.Operator):
    bl_idname = "vfx.reset_master_grade"
    bl_label = "Reset Master Grade"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        vfx.m_exposure = 0.0
        vfx.m_temp = 0.0
        vfx.m_tint = 0.0
        vfx.m_lift = (0.0, 0.0, 0.0)
        vfx.m_gain = (1.0, 1.0, 1.0)
        vfx.m_saturation = 1.0
        vfx.m_contrast = 1.0
        rebuild_comp(vfx, master)
        self.report({'INFO'}, "Master grade reset")
        return {'FINISHED'}


class VFX_OT_copy_master_grade(bpy.types.Operator):
    bl_idname = "vfx.copy_master_grade"
    bl_label = "Copy Master Grade to All Layers"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        for layer in vfx.layers:
            if not layer.enabled:
                continue
            layer.grade_enable = True
            layer.l_exposure = vfx.m_exposure
            layer.l_temp = vfx.m_temp
            layer.l_tint = vfx.m_tint
            layer.l_lift = vfx.m_lift[:]
            layer.l_gain = vfx.m_gain[:]
            layer.l_sat = vfx.m_saturation
            layer.l_contrast = vfx.m_contrast
        rebuild_comp(vfx, master)
        self.report({'INFO'}, f"Copied master grade to {len(vfx.layers)} layers")
        return {'FINISHED'}


class VFX_OT_toggle_viewport_compositor(bpy.types.Operator):
    bl_idname = "vfx.toggle_viewport_compositor"
    bl_label = "Toggle Viewport Compositor"
    bl_description = "Toggle real-time viewport compositing (shows fog, grade, effects live in 3D viewport)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        # Ensure compositor tree is built and fresh
        rebuild_comp(vfx, master)

        # Check current viewport shading in active space
        found = False
        target_mode = 'ALWAYS'
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        shading = getattr(space, "shading", None)
                        if shading and hasattr(shading, "use_compositor"):
                            current = shading.use_compositor
                            target_mode = 'DISABLED' if current in ('ALWAYS', 'CAMERA') else 'ALWAYS'
                            found = True
                            break
                if found:
                    break

        # Apply to all 3D viewports
        count = 0
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        shading = getattr(space, "shading", None)
                        if shading and hasattr(shading, "use_compositor"):
                            shading.use_compositor = target_mode
                            count += 1
                area.tag_redraw()

        status = "ON (Always)" if target_mode == 'ALWAYS' else "OFF"
        self.report({'INFO'}, f"Viewport Compositor: {status}")
        return {'FINISHED'}


# ---------------------------------------------------------------------
# FINAL VIDEO EXPORT & SPEEDUP RESTORE HANDLER
# ---------------------------------------------------------------------

_orig_engine_restore = {}

@bpy.app.handlers.persistent
def _vfx_restore_engine_on_render_end(*args):
    global _orig_engine_restore
    if not _orig_engine_restore:
        return
    for sc_name, orig in list(_orig_engine_restore.items()):
        sc = bpy.data.scenes.get(sc_name)
        if sc:
            try:
                sc.render.engine = orig
                print(f"[VFX] Restored render engine for scene '{sc_name}' to {orig}")
            except Exception as e:
                print(f"[VFX] Error restoring engine: {e}")
    _orig_engine_restore.clear()


def _ensure_restore_handlers():
    if _vfx_restore_engine_on_render_end not in bpy.app.handlers.render_complete:
        bpy.app.handlers.render_complete.append(_vfx_restore_engine_on_render_end)
    if _vfx_restore_engine_on_render_end not in bpy.app.handlers.render_cancel:
        bpy.app.handlers.render_cancel.append(_vfx_restore_engine_on_render_end)


class VFX_OT_export_final_video(bpy.types.Operator):
    bl_idname = "vfx.export_final_video"
    bl_label = "Рендер итогового видео (Композ)"
    bl_description = "Собрать и отрендерить финальное видео или секвенцию из композитора"
    bl_options = {'REGISTER'}

    animation: BoolProperty(
        name="Анимация",
        description="Рендерить всю анимацию (иначе только текущий кадр)",
        default=True,
    )

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        if not vfx or not master:
            self.report({'ERROR'}, "Мастер-сцена не найдена")
            return {'CANCELLED'}

        # Ensure master is active
        if context.window and context.window.scene != master:
            context.window.scene = master

        # Check if EXR cache exists when using speedup
        out_abs = bpy.path.abspath(vfx.output_dir)
        has_any_exr = False
        if os.path.isdir(out_abs):
            candidate_names = ["VFX_BG"] + [
                (lyr.scene.name if lyr.scene else f"VFX_{lyr.layer_name}")
                for lyr in vfx.layers
            ]
            for sc_name in candidate_names:
                lyr_dir = os.path.join(out_abs, sc_name)
                if os.path.isdir(lyr_dir):
                    try:
                        if any(f.lower().endswith(('.exr', '.png', '.tif', '.tiff')) for f in os.listdir(lyr_dir)):
                            has_any_exr = True
                            break
                    except Exception:
                        pass

        # Setup compositor mode
        if vfx.final_speedup:
            if not has_any_exr and len(vfx.layers) > 0:
                self.report(
                    {'WARNING'},
                    "В папке рендера нет EXR слоев! Для Турбо-сборки сначала отрендерите слои (1-Клик: Рендер ВСЕХ слоев)."
                )
            vfx.comp_mode = 'FILES'
            rebuild_comp_from_files(vfx, master)
        else:
            if vfx.comp_mode == 'FILES':
                rebuild_comp_from_files(vfx, master)
            else:
                rebuild_comp(vfx, master)

        # Enable compositor and sequencer output on master scene
        master.render.use_compositing = True
        master.render.use_sequencer = True
        try:
            master.use_nodes = True
        except Exception:
            pass

        # Prepare output path
        raw_path = (vfx.final_output_path or "").strip()
        if not raw_path:
            raw_path = "//final_render/output.mp4"
            vfx.final_output_path = raw_path

        abs_target = bpy.path.abspath(raw_path)
        fmt = vfx.final_format

        if raw_path.endswith(('/', '\\')):
            os.makedirs(abs_target, exist_ok=True)
            if fmt in ('MP4', 'PRORES'):
                master.render.filepath = raw_path + "final_"
            else:
                master.render.filepath = raw_path
        else:
            parent_dir = os.path.dirname(abs_target)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            base, ext = os.path.splitext(raw_path)
            if ext.lower() in ('.mp4', '.mov', '.png', '.exr', '.avi', '.mkv'):
                master.render.filepath = base + ("" if base.endswith('_') else "_")
            else:
                master.render.filepath = raw_path

        # Configure format and quality
        img = master.render.image_settings
        qual = vfx.final_quality

        if fmt == 'MP4':
            img.media_type = 'VIDEO'
            img.file_format = 'FFMPEG'
            ff = master.render.ffmpeg
            ff.format = 'MPEG4'
            ff.codec = 'H264'
            if qual == 'LOSSLESS':
                ff.constant_rate_factor = 'LOSSLESS'
                ff.ffmpeg_preset = 'GOOD'
            elif qual == 'MEDIUM':
                ff.constant_rate_factor = 'MEDIUM'
                ff.ffmpeg_preset = 'REALTIME'
            else:
                ff.constant_rate_factor = 'HIGH'
                ff.ffmpeg_preset = 'GOOD'
            # Enable audio if audio strips exist in sequencer or sounds are loaded
            try:
                has_audio = (
                    master.sequence_editor and any(
                        getattr(s, 'type', '') == 'SOUND'
                        for s in getattr(master.sequence_editor, 'strips', [])
                    )
                ) or len(bpy.data.sounds) > 0
                if has_audio:
                    ff.audio_codec = 'AAC'
                    ff.audio_bitrate = 192
                else:
                    ff.audio_codec = 'NONE'
            except Exception:
                ff.audio_codec = 'AAC'

        elif fmt == 'PRORES':
            img.media_type = 'VIDEO'
            img.file_format = 'FFMPEG'
            ff = master.render.ffmpeg
            ff.format = 'QUICKTIME'
            ff.codec = 'PRORES'
            if qual == 'LOSSLESS':
                ff.constant_rate_factor = 'LOSSLESS'
            elif qual == 'MEDIUM':
                ff.constant_rate_factor = 'MEDIUM'
            else:
                ff.constant_rate_factor = 'HIGH'
            try:
                ff.audio_codec = 'AAC'
                ff.audio_bitrate = 192
            except Exception:
                pass

        elif fmt == 'PNG':
            img.media_type = 'IMAGE'
            img.file_format = 'PNG'
            img.color_mode = 'RGBA'
            if qual == 'LOSSLESS':
                img.color_depth = '16'
            else:
                img.color_depth = '8'

        elif fmt == 'EXR':
            img.media_type = 'IMAGE'
            img.file_format = 'OPEN_EXR'
            img.color_mode = 'RGBA'
            if qual == 'LOSSLESS':
                img.color_depth = '32'
            else:
                img.color_depth = '16'

        # Apply turbo speedup (Workbench) if requested
        if vfx.final_speedup and vfx.comp_mode == 'FILES':
            _ensure_restore_handlers()
            _orig_engine_restore[master.name] = master.render.engine
            master.render.engine = 'BLENDER_WORKBENCH'

        # Invoke render
        try:
            if self.animation:
                bpy.ops.render.render('INVOKE_DEFAULT', animation=True)
                self.report({'INFO'}, f"Запущен финальный экспорт ({fmt}) -> {master.render.filepath}")
            else:
                bpy.ops.render.render('INVOKE_DEFAULT', animation=False, write_still=True)
                self.report({'INFO'}, f"Сохранен текущий кадр ({fmt}) -> {master.render.filepath}")
        except Exception:
            try:
                if self.animation:
                    bpy.ops.render.render(animation=True)
                else:
                    bpy.ops.render.render(animation=False, write_still=True)
                self.report({'INFO'}, f"Экспорт завершен ({fmt}) -> {master.render.filepath}")
            except Exception as err:
                orig = _orig_engine_restore.pop(master.name, None)
                if orig:
                    master.render.engine = orig
                self.report({'ERROR'}, f"Ошибка рендера: {err}")
                return {'CANCELLED'}

        return {'FINISHED'}


class VFX_OT_open_output_folder(bpy.types.Operator):
    bl_idname = "vfx.open_output_folder"
    bl_label = "Открыть папку экспорта"
    bl_description = "Открыть папку с отрендеренным видео или секвенцией в проводнике"
    bl_options = {'REGISTER'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=False)
        raw = (vfx.final_output_path or "").strip()
        if not raw:
            raw = "//final_render/"
        abs_p = bpy.path.abspath(raw)

        if not abs_p.endswith(('/', '\\')):
            folder = os.path.dirname(abs_p)
        else:
            folder = abs_p

        if not os.path.exists(folder):
            try:
                os.makedirs(folder, exist_ok=True)
            except Exception:
                pass

        if not os.path.exists(folder):
            self.report({'WARNING'}, f"Папка не существует: {folder}")
            return {'CANCELLED'}

        try:
            if os.name == 'nt':
                os.startfile(folder)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', folder])
            else:
                subprocess.Popen(['xdg-open', folder])
            self.report({'INFO'}, f"Папка открыта: {folder}")
        except Exception as e:
            self.report({'ERROR'}, f"Не удалось открыть папку: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}


# -------------------------------------------------------------------------
# AI SHADOW & COMFYUI OPERATORS
# -------------------------------------------------------------------------

class VFX_OT_comfy_test_connection(bpy.types.Operator):
    bl_idname = "vfx.comfy_test_connection"
    bl_label = "Проверить связь с ComfyUI"
    bl_description = "Проверить доступность локального REST API сервера ComfyUI"
    bl_options = {'REGISTER'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        if not vfx:
            return {'CANCELLED'}
        from .ai_shadow import check_comfy_connection
        ok, msg = check_comfy_connection(vfx.comfy_url)
        vfx.comfy_status = msg
        if ok:
            self.report({'INFO'}, f"ComfyUI: {msg}")
        else:
            self.report({'WARNING'}, f"ComfyUI: {msg}")
        return {'FINISHED'}


class VFX_OT_export_comfy_inputs(bpy.types.Operator):
    bl_idname = "vfx.export_comfy_inputs"
    bl_label = "Экспорт входных пассов для ComfyUI"
    bl_description = "Подготовить и открыть папку входных пассов для генерации нейротеней в ComfyUI"
    bl_options = {'REGISTER'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=False)
        layer = active_layer(vfx)
        if not vfx or not layer:
            self.report({'WARNING'}, "Выберите активный слой")
            return {'CANCELLED'}

        from .ai_shadow import get_comfy_inputs_folder, get_ai_shadow_folder
        in_folder = get_comfy_inputs_folder(vfx, layer)
        out_folder = get_ai_shadow_folder(vfx, layer)
        os.makedirs(in_folder, exist_ok=True)
        os.makedirs(out_folder, exist_ok=True)

        try:
            if os.name == 'nt':
                os.startfile(in_folder)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', in_folder])
            else:
                subprocess.Popen(['xdg-open', in_folder])
            self.report({'INFO'}, f"Папка для ComfyUI подготовлена: {in_folder}")
        except Exception as e:
            self.report({'ERROR'}, f"Не удалось открыть папку: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}


class VFX_OT_open_ai_shadow_folder(bpy.types.Operator):
    bl_idname = "vfx.open_ai_shadow_folder"
    bl_label = "Открыть папку готовых нейротеней"
    bl_description = "Открыть папку, куда складываются готовые EXR/PNG кадры теней от ComfyUI"
    bl_options = {'REGISTER'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=False)
        layer = active_layer(vfx)
        if not vfx or not layer:
            self.report({'WARNING'}, "Выберите активный слой")
            return {'CANCELLED'}

        from .ai_shadow import get_ai_shadow_folder
        folder = get_ai_shadow_folder(vfx, layer)
        os.makedirs(folder, exist_ok=True)

        try:
            if os.name == 'nt':
                os.startfile(folder)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', folder])
            else:
                subprocess.Popen(['xdg-open', folder])
            self.report({'INFO'}, f"Папка открыта: {folder}")
        except Exception as e:
            self.report({'ERROR'}, f"Не удалось открыть папку: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}


# ---------------------------------------------------------------------
# LAYER HOLDOUT MATTE OPERATORS
# ---------------------------------------------------------------------

class VFX_OT_browse_layer_matte_folder(bpy.types.Operator):
    bl_idname = "vfx.browse_layer_matte_folder"
    bl_label = "Выбрать папку с PNG масками"
    bl_description = "Выбрать папку с последовательностью PNG файлов для выреза слоя"
    bl_options = {'REGISTER', 'UNDO'}

    directory: StringProperty(
        name="Папка с масками",
        subtype='DIR_PATH',
    )
    layer_id: StringProperty(
        name="ID слоя",
        default=""
    )

    def invoke(self, context, event):
        vfx, master = get_project(context, allow_write=False)
        layer = active_layer(vfx)
        if self.layer_id and vfx:
            l = next((x for x in vfx.layers if x.id == self.layer_id), None)
            if l:
                layer = l
        if layer and layer.holdout_matte_path:
            p = bpy.path.abspath(layer.holdout_matte_path)
            self.directory = p if os.path.isdir(p) else os.path.dirname(p)
        elif vfx:
            for other in vfx.layers:
                if other.holdout_matte_path:
                    p = bpy.path.abspath(other.holdout_matte_path)
                    self.directory = p if os.path.isdir(p) else os.path.dirname(p)
                    break
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        vfx, master = get_project(context, allow_write=False)
        layer = active_layer(vfx)
        if self.layer_id and vfx:
            l = next((x for x in vfx.layers if x.id == self.layer_id), None)
            if l:
                layer = l
        if not layer:
            self.report({'WARNING'}, "Слой не выбран")
            return {'CANCELLED'}

        if not self.directory:
            return {'CANCELLED'}

        layer.holdout_matte_path = self.directory
        layer.use_holdout_matte = True
        _trigger_comp(context)
        self.report({'INFO'}, f"Маска подключена к слою '{layer.layer_name}': {self.directory}")
        return {'FINISHED'}


class VFX_OT_copy_matte_from_layer(bpy.types.Operator):
    bl_idname = "vfx.copy_matte_from_layer"
    bl_label = "Copy Matte from Another Layer"
    bl_description = "Скопировать путь к PNG-маске и настройки выреза из другого слоя проекта"
    bl_options = {'REGISTER', 'UNDO'}

    source_layer_id: StringProperty(name="Source Layer ID", default="")

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = active_layer(vfx)
        if not layer:
            self.report({'WARNING'}, "Слой не выбран")
            return {'CANCELLED'}

        src = None
        if self.source_layer_id and vfx:
            src = next((l for l in vfx.layers if l.id == self.source_layer_id), None)
        if src is None and vfx:
            src = next((l for l in vfx.layers if l.id != layer.id and l.holdout_matte_path), None)

        if not src or not src.holdout_matte_path:
            self.report({'WARNING'}, "В проекте нет слоев с настроенной маской")
            return {'CANCELLED'}

        layer.holdout_matte_path = src.holdout_matte_path
        layer.use_holdout_matte = True
        layer.holdout_matte_choke = src.holdout_matte_choke
        layer.holdout_matte_feather = src.holdout_matte_feather
        layer.holdout_matte_invert = src.holdout_matte_invert
        layer.holdout_matte_mode = src.holdout_matte_mode
        layer.holdout_matte_frame_start = src.holdout_matte_frame_start
        layer.holdout_matte_frame_offset = src.holdout_matte_frame_offset
        layer.holdout_matte_mask_shadow = src.holdout_matte_mask_shadow

        _trigger_comp(context)
        self.report({'INFO'}, f"Маска скопирована из слоя '{src.layer_name}'")
        return {'FINISHED'}


class VFX_OT_reload_layer_mask(bpy.types.Operator):
    bl_idname = "vfx.reload_layer_mask"
    bl_label = "Обновить маску слоя"
    bl_description = "Перезагрузить файлы PNG-маски с диска и обновить композитор"
    bl_options = {'REGISTER', 'UNDO'}

    layer_id: StringProperty(default="")

    def execute(self, context):
        vfx, master = get_project(context, allow_write=False)
        layer = active_layer(vfx)
        if self.layer_id and vfx:
            l = next((x for x in vfx.layers if x.id == self.layer_id), None)
            if l:
                layer = l
        if not layer:
            self.report({'WARNING'}, "Слой не выбран")
            return {'CANCELLED'}

        img_name = f"VFX_MATTE_SEQ_{layer.id}"
        img = bpy.data.images.get(img_name)
        if img:
            try:
                img.reload()
            except Exception:
                pass
        try:
            import importlib
            from . import compositor
            importlib.reload(compositor)
        except Exception:
            pass
        _trigger_comp(context)
        self.report({'INFO'}, f"Маска слоя '{layer.layer_name}' обновлена")
        return {'FINISHED'}


class VFX_OT_open_layer_matte_folder(bpy.types.Operator):
    bl_idname = "vfx.open_layer_matte_folder"
    bl_label = "Открыть папку масок"
    bl_description = "Открыть папку с PNG-масками в проводнике Windows"
    bl_options = {'REGISTER'}

    layer_id: StringProperty(default="")

    def execute(self, context):
        vfx, master = get_project(context, allow_write=False)
        layer = active_layer(vfx)
        if self.layer_id and vfx:
            l = next((x for x in vfx.layers if x.id == self.layer_id), None)
            if l:
                layer = l
        if not layer or not layer.holdout_matte_path:
            self.report({'WARNING'}, "Папка маски не указана")
            return {'CANCELLED'}

        abs_p = bpy.path.abspath(layer.holdout_matte_path)
        folder = abs_p if os.path.isdir(abs_p) else os.path.dirname(abs_p)
        if not os.path.exists(folder):
            self.report({'ERROR'}, f"Папка не существует: {folder}")
            return {'CANCELLED'}

        try:
            if os.name == 'nt':
                os.startfile(folder)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', folder])
            else:
                subprocess.Popen(['xdg-open', folder])
            self.report({'INFO'}, f"Папка открыта: {folder}")
        except Exception as e:
            self.report({'ERROR'}, f"Не удалось открыть: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


def _get_vfx_layer_items(self, context):
    vfx, master = get_project(context, allow_write=False)
    if not vfx or not vfx.layers:
        return [('NONE', "Нет слоев", "")]
    return [(l.id, l.layer_name, f"Слой '{l.layer_name}'") for l in vfx.layers]


class VFX_OT_assign_layer_mask_dialog(bpy.types.Operator):
    bl_idname = "vfx.assign_layer_mask_dialog"
    bl_label = "Применить маску выреза к слою"
    bl_description = "Выбрать слой и применить к нему внешнюю PNG-маску из папки (вычитание/holdout)"
    bl_options = {'REGISTER', 'UNDO'}

    target_layer_id: EnumProperty(
        name="Целевой 3D-слой",
        items=_get_vfx_layer_items,
        description="Слой, из которого будет вычитаться маска"
    )
    folder_path: StringProperty(
        name="Папка с PNG",
        description="Папка, содержащая набор PNG кадров маски",
        subtype='DIR_PATH'
    )
    invert: BoolProperty(
        name="Инвертировать (белое оставляет)",
        description="По умолчанию белое вырезает 3D слой, черное оставляет. При инверсии наоборот.",
        default=False
    )
    mode: EnumProperty(
        name="Канал маски",
        items=(
            ('LUMA', "Яркость (RGB / ч/б)", "Маска по яркости"),
            ('ALPHA', "Альфа (PNG Alpha)", "Маска по альфа-каналу"),
        ),
        default='LUMA'
    )
    feather: FloatProperty(
        name="Смягчение (px)",
        default=0.0,
        min=0.0,
        max=100.0
    )
    choke: IntProperty(
        name="Сжатие маски (px)",
        description="Сжатие (отрицательные значения, e.g. -3 px) или расширение маски",
        default=-2,
        min=-100,
        max=100
    )
    frame_start: IntProperty(
        name="Старт маски",
        description="Номер кадра сцены, с которого начинается маска",
        default=1,
        min=-10000,
        max=10000
    )
    frame_offset: IntProperty(
        name="Сдвиг кадров",
        description="Смещение кадров маски относительно таймлайна",
        default=0,
        min=-1000,
        max=1000
    )

    def invoke(self, context, event):
        vfx, master = get_project(context, allow_write=False)
        layer = active_layer(vfx)
        if layer and hasattr(self, "target_layer_id"):
            self.target_layer_id = layer.id
            if layer.holdout_matte_path:
                self.folder_path = layer.holdout_matte_path
            self.invert = layer.holdout_matte_invert
            self.mode = layer.holdout_matte_mode
            self.feather = layer.holdout_matte_feather
            self.choke = layer.holdout_matte_choke
            self.frame_start = layer.holdout_matte_frame_start
            self.frame_offset = layer.holdout_matte_frame_offset
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.prop(self, "target_layer_id")
        col.prop(self, "folder_path")
        col.separator()
        row_s = col.row(align=True)
        row_s.prop(self, "frame_start", text="Старт")
        row_s.prop(self, "frame_offset", text="Сдвиг кадров")
        col.separator()
        col.prop(self, "mode")
        col.prop(self, "invert")
        row_e = col.row(align=True)
        row_e.prop(self, "choke", text="Сжатие (px)")
        row_e.prop(self, "feather", text="Смягчение (px)")

    def execute(self, context):
        vfx, master = get_project(context, allow_write=False)
        if not vfx:
            return {'CANCELLED'}
        layer = next((l for l in vfx.layers if l.id == self.target_layer_id), None)
        if not layer:
            self.report({'WARNING'}, "Целевой слой не найден")
            return {'CANCELLED'}

        layer.use_holdout_matte = True
        if self.folder_path:
            layer.holdout_matte_path = self.folder_path
        layer.holdout_matte_invert = self.invert
        layer.holdout_matte_mode = self.mode
        layer.holdout_matte_feather = self.feather
        layer.holdout_matte_choke = self.choke
        layer.holdout_matte_frame_start = self.frame_start
        layer.holdout_matte_frame_offset = self.frame_offset

        _trigger_comp(context)
        self.report({'INFO'}, f"Маска успешно применена к слою '{layer.layer_name}'")
        return {'FINISHED'}


class VFX_OT_select_override_layer(bpy.types.Operator):
    bl_idname = "vfx.select_override_layer"
    bl_label = "Select Override Layer"
    bl_description = "Switch active layer for per-layer FX overrides"

    layer_index: IntProperty(default=0)

    def execute(self, context):
        vfx, master = get_project(context, allow_write=False)
        if vfx and 0 <= self.layer_index < len(vfx.layers):
            vfx.active_layer_index = self.layer_index
            vfx.fx_override_layer_idx = self.layer_index
        return {'FINISHED'}


class VFX_OT_set_active_layer(bpy.types.Operator):
    bl_idname = "vfx.set_active_layer"
    bl_label = "Set Active Layer"
    bl_description = "Select this layer as active"

    layer_index: IntProperty(default=0)

    def execute(self, context):
        vfx, master = get_project(context, allow_write=False)
        if vfx and 0 <= self.layer_index < len(vfx.layers):
            vfx.active_layer_index = self.layer_index
        return {'FINISHED'}


