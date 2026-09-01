"""VFX Layer Tools — operators (layer, render, comp, shadow, etc.)."""

import bpy
import time
from bpy.props import (
    StringProperty, BoolProperty, IntProperty, FloatProperty, EnumProperty,
)

from .core import (
    uid, get_project, active_layer, collect_objects, default_layer_name,
    ensure_root, ensure_camera_collection, ensure_light_collection,
    create_empty_scene, exclude_collection_in_master, link_collection_to_scene,
    sync_scene_settings, remove_scene_safe, remove_shadow_collections,
    rename_layer, link_lights_to_all_scenes, sync_master_lights,
)
from .shadow import (
    set_shadow_catcher, set_only_shadow_caster, refresh_shadow_proxies,
    create_default_catcher,
)
from .compositor import (
    get_comp_tree, rebuild_comp, rebuild_comp_from_files,
    build_comp_assembly, ensure_render_node, remove_comp_node,
    _setup_fog_passes, _ensure_fogmap, _get_mist_socket,
)
from .materials import _trigger_rebuild, _trigger_comp, update_layer_material_adjust


# ---------------------------------------------------------------------
# OPERATORS
# ---------------------------------------------------------------------

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

        self.report({'INFO'}, f"Removed {count} object(s) from {layer.layer_name}")
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


class VFX_OT_create_shadow_pass(bpy.types.Operator):
    bl_idname = "vfx.create_shadow_pass"
    bl_label = "Create Shadow Pass"
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(
        name="Mode",
        items=(
            ('CAST', "Cast shadow",
             "Layer objects cast shadow onto a catcher"),
            ('RECEIVE', "Receive shadow",
             "Layer receives shadows from other layers (separate pass)"),
        ),
        default='CAST'
    )

    def invoke(self, context, event):
        vfx, master = get_project(context)
        layer = active_layer(vfx)

        if not layer:
            self.report({'ERROR'}, "No active layer")
            return {'CANCELLED'}

        if layer.shadow_scene:
            self.report({'WARNING'}, "Shadow pass already exists")
            return {'CANCELLED'}

        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        layer = active_layer(vfx)

        if not layer:
            self.report({'ERROR'}, "No active layer")
            return {'CANCELLED'}

        if not layer.collection or len(layer.collection.objects) == 0:
            self.report({'ERROR'}, "Active layer has no objects")
            return {'CANCELLED'}

        if layer.shadow_scene:
            self.report({'WARNING'}, "Shadow pass already exists")
            return {'CANCELLED'}

        root = ensure_root(master)
        cam_col = ensure_camera_collection(master, root)
        light_col = ensure_light_collection(master, root)

        cast_col = bpy.data.collections.new(f"VFX_{layer.layer_name}_SHD_CAST")
        catch_col = bpy.data.collections.new(f"VFX_{layer.layer_name}_SHD_CATCH")

        root.children.link(cast_col)
        root.children.link(catch_col)

        layer.shadow_cast_collection = cast_col
        layer.shadow_catch_collection = catch_col
        layer.shadow_mode = self.mode

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

        if self.mode == 'RECEIVE':
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
        try:
            if scene.render.engine == 'CYCLES':
                scene.cycles.samples = 32
        except Exception:
            pass

        layer.shadow_scene = scene
        layer.use_shadow = True

        rebuild_comp(vfx, master)

        self.report({'INFO'}, f"Shadow pass ({self.mode}): {made_cast} casters, {made_catch} catchers")
        return {'FINISHED'}


class VFX_OT_delete_layer(bpy.types.Operator):
    bl_idname = "vfx.delete_layer"
    bl_label = "Delete Active Layer"
    bl_options = {'REGISTER', 'UNDO'}

    confirm: BoolProperty(
        name="Delete layer, its scenes and collections?",
        default=True
    )

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

        idx = vfx.active_layer_index
        vfx.layers.remove(idx)
        vfx.active_layer_index = max(0, min(idx, len(vfx.layers) - 1))

        rebuild_comp(vfx, master)

        self.report({'INFO'}, f"Deleted layer: {layer.layer_name}")
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
        rebuild_comp(vfx, master)


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
        print("film_transparent:", sc.render.film_transparent)
        print("camera:", sc.camera.name if sc.camera else None)
        print("world:", sc.world.name if sc.world else None)

        for vl in sc.view_layers:
            print("view_layer:", vl.name)

            def walk(lc, depth=0):
                print("   " * (depth + 1), lc.collection.name,
                      "exclude=", lc.exclude,
                      "holdout=", lc.holdout,
                      "indirect_only=", lc.indirect_only)
                for ch in lc.children:
                    walk(ch, depth + 1)

            walk(vl.layer_collection)

        print("objects in scene:")
        for obj in sc.objects:
            print("  ", obj.name, obj.type,
                  "hide_render=", obj.hide_render)
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


class VFX_OT_render_all_layers(bpy.types.Operator):
    bl_idname = "vfx.render_all_layers"
    bl_label = "Render All Layers"

    post_files: BoolProperty(
        name="Then build comp from EXR",
        default=False
    )
    only_layer: IntProperty(
        name="Only layer index",
        default=-1
    )
    only_shadow_for_layer: IntProperty(
        name="Only shadow of layer index",
        default=-1
    )
    only_background: BoolProperty(
        name="Only background",
        default=False
    )
    refresh_after: BoolProperty(
        name="Refresh EXR comp after",
        default=False
    )

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)

        auto_sync_settings(vfx, master)

        only = getattr(self, "only_layer", -1)
        only_shd = getattr(self, "only_shadow_for_layer", -1)
        only_bg = getattr(self, "only_background", False)

        if vfx.render_running:
            job_alive = False
            try:
                job_alive = bpy.app.is_job_running('RENDER')
            except Exception:
                job_alive = _RENDER_STATE["op"] is not None

            if job_alive:
                self.report({'WARNING'}, "Render already running")
                return {'CANCELLED'}

            print("VFX: stale render flag detected, resetting")
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
        if only_bg and getattr(vfx, "bg_scene", None) is not None:
            scenes.append(vfx.bg_scene)
        elif only_shd >= 0 and only_shd < len(vfx.layers):
            layer = vfx.layers[only_shd]
            if layer.shadow_scene:
                scenes.append(layer.shadow_scene)
        elif only >= 0 and only < len(vfx.layers):
            layer = vfx.layers[only]
            if layer.scene:
                scenes.append(layer.scene)
        else:
            for layer in vfx.layers:
                if not layer.enabled:
                    continue
                if layer.scene:
                    scenes.append(layer.scene)
                if layer.shadow_scene:
                    scenes.append(layer.shadow_scene)
            bg = getattr(vfx, "bg_scene", None)
            if bg is not None:
                scenes.insert(0, bg)

        if not scenes:
            self.report({'ERROR'}, "No enabled layer scenes")
            return {'CANCELLED'}

        self.mode = 'ANIMATION'

        try:
            refresh_shadow_proxies(vfx, master)
        except Exception as e:
            print("VFX proxy refresh error:", e)

        self.steps = []
        total_frames = 0
        for sc in scenes:
            try:
                sc.frame_start = master.frame_start
                sc.frame_end = master.frame_end
            except Exception:
                pass
            try:
                sc.render.image_settings.file_format = 'OPEN_EXR'
                sc.render.image_settings.color_mode = 'RGBA'
                sc.render.image_settings.color_depth = '32'
                sc.render.filepath = f"{vfx.output_dir}{sc.name}/"
            except Exception:
                pass
            # Ensure render passes are enabled for multi-channel EXR
            for vl in sc.view_layers:
                for attr in ('use_pass_mist', 'use_pass_z', 'use_pass_normal'):
                    try:
                        setattr(vl, attr, True)
                    except Exception:
                        pass
            n = (sc.frame_end - sc.frame_start + 1)
            total_frames += n
            self.steps.append((sc.name, n))

        self.total_frames = max(1, total_frames)
        self.step_index = 0
        self.frames_done = 0
        self.user_cancel = False
        self.cancelled = False
        self.job_done = False

        vfx.render_running = True
        vfx.render_progress = 0.0
        vfx.render_status = "Starting..."

        _RENDER_STATE["op"] = self
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

        self.job_done = False
        self.step_start = time.time()
        vfx.render_progress = min(1.0, self.frames_done / self.total_frames)
        vfx.render_status = f"{self.frames_done}/{self.total_frames}  •  {name}  •  frames {sc.frame_start}-{sc.frame_end}"

        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()

        win = context.window
        if win is None and bpy.context.window_manager.windows:
            win = bpy.context.window_manager.windows[0]
        try:
            sc.render.filepath = f"{vfx.output_dir}{sc.name}/"
            print("VFX: rendering", name, "->",
                  bpy.path.abspath(sc.render.filepath))
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
                for window in bpy.context.window_manager.windows:
                    for area in window.screen.areas:
                        area.tag_redraw()

            try:
                alive = bpy.app.is_job_running('RENDER')
            except Exception:
                alive = True
            if (not alive) and (not self.job_done) and \
                    (time.time() - getattr(self, "step_start", 0)) > 3.0:
                print("VFX: render job died without event, skipping step")
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

        if not stopped and (getattr(self, "post_files", False) or getattr(self, "refresh_after", False)):
            try:
                master = vfx.master_scene or context.scene
                vfx.comp_mode = 'FILES'
                rebuild_comp_from_files(vfx, master)
                vfx.render_status = "Done + comp from EXR"
            except Exception as e:
                print("VFX post-files error:", e)

        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()


class VFX_OT_one_click_exr(bpy.types.Operator):
    bl_idname = "vfx.one_click_exr"
    bl_label = "Render EXR + Comp From Files"
    bl_description = "Render all layer scenes as EXR animation, then build comp from files"

    def execute(self, context):
        bpy.ops.vfx.render_all_layers('INVOKE_DEFAULT', post_files=True)
        return {'FINISHED'}


class VFX_OT_create_background(bpy.types.Operator):
    bl_idname = "vfx.create_background"
    bl_label = "Bake Background (world only)"

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)

        if vfx.bg_scene:
            self.report({'WARNING'}, "Background already exists")
            return {'CANCELLED'}

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

        rebuild_comp_from_files(vfx, master)

        self.report({'INFO'}, "Background scene created: VFX_BG")
        return {'FINISHED'}


class VFX_OT_delete_background(bpy.types.Operator):
    bl_idname = "vfx.delete_background"
    bl_label = "Delete Background"

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        if not vfx.bg_scene:
            return {'CANCELLED'}

        remove_comp_node(master, "VFX_RL_BG")
        sc = vfx.bg_scene
        vfx.bg_scene = None
        if sc and sc != master:
            remove_scene_safe(context, sc, master)

        rebuild_comp(vfx, master)

        self.report({'INFO'}, "Background removed")
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


def auto_sync_settings(vfx, master):
    """Перед рендером: тянет настройки из master во все слой-сцены."""
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
        for c in (layer.shadow_cast_collection, layer.shadow_catch_collection):
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


class VFX_OT_refresh_proxies(bpy.types.Operator):
    bl_idname = "vfx.refresh_proxies"
    bl_label = "Refresh Shadow Proxies"

    def execute(self, context):
        vfx, master = get_project(context, allow_write=True)
        refresh_shadow_proxies(vfx, master)
        self.report({'INFO'}, "Shadow proxies refreshed")
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
        vfx, master = get_project(context, allow_write=True)
        try:
            if getattr(vfx, "comp_mode", 'LIVE') == 'FILES':
                rebuild_comp_from_files(vfx, master)
                self.report({'INFO'}, "Comp rebuilt from EXR files")
            else:
                rebuild_comp(vfx, master)
                self.report({'INFO'}, "VFX compositing nodes rebuilt")
        except Exception as e:
            print("VFX rebuild error:", e)
            traceback.print_exc()
        return {'FINISHED'}

