"""Controlled comparisons from the saved v8 scene; never overwrite its files."""
import bpy, json, sys, time, argparse
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--source',required=True)
p.add_argument('--output',required=True)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
out=Path(a.output).resolve();out.mkdir(parents=True,exist_ok=True)
bpy.ops.wm.open_mainfile(filepath=str(Path(a.source).resolve()))
s=bpy.context.scene;s.render.engine='BLENDER_EEVEE_NEXT'
s.render.image_settings.file_format='PNG'
lights=[o.data for o in s.objects if o.type=='LIGHT']
probes=[o for o in s.objects if o.type=='LIGHT_PROBE']
shadows=[d.use_shadow for d in lights];hidden=[o.hide_render for o in probes]
base_samples=s.eevee.taa_render_samples
report={'source':str(Path(a.source).resolve()),'baseline':{'samples':base_samples,'use_raytracing':s.eevee.use_raytracing,'use_fast_gi':s.eevee.use_fast_gi,'shadow_enabled':shadows,'probe_hidden':hidden},'renders':[]}
for label in ['01_baseline','02_samples_512','03_no_light_shadows','04_no_indirect_probe']:
 s.eevee.taa_render_samples=512 if label=='02_samples_512' else base_samples
 for d,original in zip(lights,shadows):d.use_shadow=False if label=='03_no_light_shadows' else original
 for o,original in zip(probes,hidden):o.hide_render=True if label=='04_no_indirect_probe' else original
 bpy.context.view_layer.update()
 s.render.filepath=str(out/(label+'.png'))
 print('LIGHT_LAB_DIAG: '+label,flush=True)
 t=time.perf_counter()
 try:
  bpy.ops.render.render(write_still=True)
  report['renders'].append({'variant':label,'success':True,'seconds':round(time.perf_counter()-t,3)})
 except Exception as e:
  report['renders'].append({'variant':label,'success':False,'error':str(e)})
  (out/'diagnostic_report.json').write_text(json.dumps(report,indent=2));raise
 (out/'diagnostic_report.json').write_text(json.dumps(report,indent=2))
print('LIGHT_LAB_DIAG_COMPLETE',flush=True)
