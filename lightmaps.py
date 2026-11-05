"""Offline diffuse lightmaps for Eevee; Cycles retains the physical material.

Two passes avoid dropping mesh-emitter direct light or double-counting analytic
light direct illumination. Textures store scene-linear outgoing diffuse radiance.
"""
import math
import re
import time
from pathlib import Path
import bpy
import numpy as np


def bake_lightmaps(scene, concrete, output_dir, resolution=512, samples=256):
    start = time.perf_counter()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    receiver_materials = set(concrete) if isinstance(concrete,(list,tuple,set)) else {concrete}
    receivers = [o for o in scene.objects if o.type == 'MESH'
                 and any(slot.material in receiver_materials for slot in o.material_slots)]
    if not receivers:
        raise RuntimeError('No concrete lightmap receivers found')
    records = []
    scene.render.engine = 'CYCLES'
    original_samples = scene.cycles.samples
    original_adaptive = scene.cycles.use_adaptive_sampling
    scene.cycles.samples = samples
    scene.cycles.use_adaptive_sampling = False
    scene.render.bake.use_selected_to_active = False
    scene.render.bake.margin = 2
    analytic_lights = [o.data for o in scene.objects if o.type == 'LIGHT']
    energies = [d.energy for d in analytic_lights]
    stage = {'method': 'Cycles offline diffuse radiance maps; Eevee raster only',
             'resolution_main': resolution, 'samples': samples, 'passes': [],
             'receivers': [], 'success': False}
    try:
        for obj in receivers:
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            # Both engines use the same evaluated geometry after applying modifiers.
            for modifier in list(obj.modifiers):
                bpy.ops.object.modifier_apply(modifier=modifier.name)
            uv = obj.data.uv_layers.active
            if uv is None:
                uv = obj.data.uv_layers.new(name='LightmapUV')
            uv.name = 'LightmapUV'
            uv.active_render = True
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.05)
            bpy.ops.object.mode_set(mode='OBJECT')
            source_material = next(slot.material for slot in obj.material_slots if slot.material in receiver_materials)
            material = source_material.copy()
            material.name = obj.name + ' | physical + Eevee lightmap'
            for slot in obj.material_slots:
                if slot.material == source_material:
                    slot.material = material
            # Large surfaces receive full-size maps; tiny channel pieces use 128px.
            size = resolution if max(obj.dimensions) >= 3 else min(resolution, 128)
            slug = re.sub(r'[^A-Za-z0-9_-]', '_', obj.name)
            textures = {}
            for kind in ('indirect', 'emissive_direct'):
                img = bpy.data.images.new(slug + '_' + kind, width=size,
                                          height=size, alpha=False, float_buffer=True)
                img.colorspace_settings.name = 'Non-Color'
                node = material.node_tree.nodes.new('ShaderNodeTexImage')
                node.name = 'LIGHTMAP_' + kind
                node.label = 'Scene-linear diffuse radiance: ' + kind
                node.image = img
                node.interpolation = 'Linear'
                node.extension = 'EXTEND'
                uv_node = material.node_tree.nodes.get('Lightmap UV')
                if uv_node is None:
                    uv_node = material.node_tree.nodes.new('ShaderNodeUVMap')
                    uv_node.name = 'Lightmap UV'
                    uv_node.uv_map = 'LightmapUV'
                material.node_tree.links.new(uv_node.outputs['UV'], node.inputs['Vector'])
                textures[kind] = (node, img)
            records.append((obj, material, textures, slug))
            stage['receivers'].append({'object': obj.name, 'resolution': size})

        for kind, filters in [('indirect', {'INDIRECT', 'COLOR'}),
                              ('emissive_direct', {'DIRECT', 'COLOR'})]:
            for light, energy in zip(analytic_lights, energies):
                light.energy = 0 if kind == 'emissive_direct' else energy
            bpy.ops.object.select_all(action='DESELECT')
            for obj, material, textures, _ in records:
                obj.select_set(True)
                nodes = material.node_tree.nodes
                for node in nodes:
                    node.select = False
                target = textures[kind][0]
                target.select = True
                nodes.active = target
            bpy.context.view_layer.objects.active = receivers[0]
            bpy.context.view_layer.update()
            stamp = time.perf_counter()
            print('LIGHT_LAB: baking ' + kind + ' lightmaps...', flush=True)
            result = bpy.ops.object.bake(type='DIFFUSE', pass_filter=filters,
                                        use_clear=True, margin=2,
                                        use_selected_to_active=False)
            if 'FINISHED' not in result:
                raise RuntimeError('Lightmap bake did not finish: ' + kind)
            stage['passes'].append({'name': kind, 'seconds': round(time.perf_counter()-stamp, 3)})

        # Restore the light rig before either final renderer runs.
        for light, energy in zip(analytic_lights, energies):
            light.energy = energy
        any_indirect = False
        for record_index, (obj, material, textures, slug) in enumerate(records):
            stats = {}
            for kind, (_, image) in textures.items():
                pixels = np.empty(len(image.pixels), dtype=np.float32)
                image.pixels.foreach_get(pixels)
                rgb = pixels.reshape(-1, 4)[:, :3]
                if not np.isfinite(rgb).all():
                    raise RuntimeError('Non-finite pixels in ' + image.name)
                stats[kind] = {'mean_rgb': [float(v) for v in rgb.mean(axis=0)],
                               'maximum': float(rgb.max())}
                if kind == 'indirect' and float(rgb.max()) > 1e-6:
                    any_indirect = True
                image.file_format = 'OPEN_EXR'
                raw_dir = output_dir / 'raw'
                raw_dir.mkdir(exist_ok=True)
                image.filepath_raw = str(raw_dir / (slug + '_' + kind + '.exr'))
                image.save()
                from oidn_filter import denoise_array
                clean, filter_seconds = denoise_array(pixels.reshape(image.size[1], image.size[0], 4))
                image.pixels.foreach_set(clean.ravel())
                stats[kind]['denoise_seconds'] = filter_seconds
                stats[kind]['denoised_mean_rgb'] = [float(v) for v in clean[...,:3].mean(axis=(0,1))]
                stage['denoise_seconds'] = stage.get('denoise_seconds',0) + filter_seconds
                image.filepath_raw = str(output_dir / (slug + '_' + kind + '.exr'))
                image.save()
                image.pack()  # The saved .blend remains self-contained.
            stage['receivers'][record_index]['maps'] = stats
            nodes = material.node_tree.nodes
            links = material.node_tree.links
            physical = next(n for n in nodes if n.type == 'OUTPUT_MATERIAL')
            physical.target = 'CYCLES'
            source = physical.inputs['Surface'].links[0].from_socket
            eevee_output = nodes.new('ShaderNodeOutputMaterial')
            eevee_output.name = 'Eevee raster + offline diffuse lightmaps'
            eevee_output.target = 'EEVEE'
            add_colors = nodes.new('ShaderNodeMixRGB')
            add_colors.blend_type = 'ADD'
            add_colors.inputs[0].default_value = 1
            add_colors.use_clamp = False
            links.new(textures['indirect'][0].outputs['Color'], add_colors.inputs[1])
            links.new(textures['emissive_direct'][0].outputs['Color'], add_colors.inputs[2])
            emission = nodes.new('ShaderNodeEmission')
            emission.inputs['Strength'].default_value = 1
            links.new(add_colors.outputs['Color'], emission.inputs['Color'])
            add_shader = nodes.new('ShaderNodeAddShader')
            links.new(source, add_shader.inputs[0])
            links.new(emission.outputs[0], add_shader.inputs[1])
            links.new(add_shader.outputs[0], eevee_output.inputs['Surface'])
        if not any_indirect:
            raise RuntimeError('All indirect lightmaps are black; inspect the bake before rendering')
        stage['success'] = True
        return stage
    except Exception as exc:
        stage['error'] = str(exc)
        raise
    finally:
        for light, energy in zip(analytic_lights, energies):
            light.energy = energy
        scene.cycles.samples = original_samples
        scene.cycles.use_adaptive_sampling = original_adaptive
        stage['seconds'] = round(time.perf_counter()-start, 3)
        # Preserve diagnostics even if bake/setup/image saving fails.
        import json
        (output_dir / 'bake_report.json').write_text(json.dumps(stage, indent=2))
