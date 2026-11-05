"""Load the successful v10 scene, replace only its lightmap textures and render."""
import argparse, json, sys, time
from pathlib import Path
import bpy
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--output',required=True)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);out=Path(a.output).resolve()
report=json.loads((out/'lightmaps/denoise_report.json').read_text())
if not report['success']:raise RuntimeError('Lightmap denoise did not complete')
bpy.ops.wm.open_mainfile(filepath=str(Path(a.source).resolve()))
s=bpy.context.scene
assert not s.eevee.use_raytracing and not s.eevee.use_fast_gi
s.eevee.taa_render_samples=512
count=0;loaded={}
for mat in bpy.data.materials:
 if not mat.use_nodes:continue
 for node in mat.node_tree.nodes:
  if node.type!='TEX_IMAGE' or not node.name.startswith('LIGHTMAP_'):continue
  filename=Path(node.image.filepath_raw).name
  path=out/'lightmaps'/filename
  if not path.is_file():raise RuntimeError('Missing denoised lightmap: '+str(path))
  if filename not in loaded:
   image=bpy.data.images.load(str(path),check_existing=False)
   image.colorspace_settings.name='Non-Color';image.pack();loaded[filename]=image
  node.image=loaded[filename];count+=1
if count!=len(report['files']):raise RuntimeError('Lightmap count mismatch')
result={'scene_revision':'v11-denoised-offline-lightmaps','source_scene':str(Path(a.source).resolve()),'eevee_raytracing':False,'eevee_fast_gi':False,'eevee_render_samples':512,'lightmap_denoise':report,'replaced_texture_nodes':count,'rebaked':False,'renders':[],'timing_note':'Denoise preparation excludes the original v10 bake. It is offline preprocessing, not render-time ray tracing.'}
for engine,label in [('BLENDER_EEVEE_NEXT','eevee'),('CYCLES','cycles')]:
 s.render.engine=engine;s.render.filepath=str(out/(label+'.png'))
 s.render.image_settings.file_format='PNG'
 bpy.ops.wm.save_as_mainfile(filepath=str(out/'light_lab.blend'))
 t=time.perf_counter()
 try:
  bpy.ops.render.render(write_still=True)
  result['renders'].append({'engine':engine,'success':True,'seconds':round(time.perf_counter()-t,3)})
 except Exception as e:
  result['renders'].append({'engine':engine,'success':False,'error':str(e)})
  (out/'render_report.json').write_text(json.dumps(result,indent=2));raise
 (out/'render_report.json').write_text(json.dumps(result,indent=2))
