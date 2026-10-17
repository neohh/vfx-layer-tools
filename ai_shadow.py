"""ai_shadow.py — AI Shadow Generation & ComfyUI integration for VFX Layer Tools.

Handles:
1. Exporting input passes for ComfyUI (2D plane shape silhouette mask + clean background plate).
2. ComfyUI REST API connector (queue prompt, check history, fetch rendered shadows).
3. Linking AI-generated shadow sequence into the VFX pipeline and compositing node graph.
"""

import os
import json
import urllib.request
import urllib.error
import bpy


def get_ai_shadow_folder(vfx, layer):
    """Return absolute path to AI shadow folder for a specific layer."""
    out_dir = bpy.path.abspath(vfx.output_dir)
    layer_name = layer.layer_name if layer else "Layer"
    folder = os.path.join(out_dir, f"VFX_{layer_name}_AI_SHD")
    return os.path.normpath(folder)


def get_comfy_inputs_folder(vfx, layer):
    """Return absolute path to input passes prepared for ComfyUI."""
    out_dir = bpy.path.abspath(vfx.output_dir)
    layer_name = layer.layer_name if layer else "Layer"
    folder = os.path.join(out_dir, f"VFX_{layer_name}_COMFY_INPUTS")
    return os.path.normpath(folder)


def check_comfy_connection(url="http://127.0.0.1:8188"):
    """Check if ComfyUI instance is alive and responding."""
    base_url = url.rstrip("/")
    target = f"{base_url}/system_stats"
    try:
        req = urllib.request.Request(target, headers={"User-Agent": "Blender-VFX-Layer-Tools"})
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return True, "Подключено (ComfyUI активен)"
    except urllib.error.URLError as e:
        return False, f"Ошибка подключения: {e.reason}"
    except Exception as e:
        return False, f"Недоступен: {e}"
    return False, "Нет ответа"


def trigger_comfy_prompt(prompt_workflow, url="http://127.0.0.1:8188", client_id="vfx_layer_tools"):
    """Queue a workflow prompt via ComfyUI REST API."""
    base_url = url.rstrip("/")
    target = f"{base_url}/prompt"
    payload = {
        "prompt": prompt_workflow,
        "client_id": client_id
    }
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        target,
        data=data_bytes,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Blender-VFX-Layer-Tools"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            prompt_id = res.get("prompt_id")
            return True, prompt_id
    except Exception as e:
        return False, str(e)


def get_prompt_status(prompt_id, url="http://127.0.0.1:8188"):
    """Check status of a queued prompt in ComfyUI history."""
    base_url = url.rstrip("/")
    target = f"{base_url}/history/{prompt_id}"
    try:
        req = urllib.request.Request(target, headers={"User-Agent": "Blender-VFX-Layer-Tools"})
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if prompt_id in data:
                return True, data[prompt_id]
            return False, "В очереди / Выполняется"
    except Exception as e:
        return False, str(e)
