"""LIGHT LAB: deterministic scene, identical geometry/lights for both engines."""
import bpy, math, json, time, argparse, sys, platform
from pathlib import Path
from mathutils import Vector

# Metres; width : height of the panel is 1 : 1.618.
ROOM_WIDTH = 12.0
BACK_Y = 5.0
FRONT_Y = -8.0
ROOM_DEPTH = BACK_Y - FRONT_Y
ROOM_CENTER_Y = (BACK_Y + FRONT_Y) / 2
ROOM_HEIGHT = 4.0
PANEL_WIDTH = 1.6
PANEL_HEIGHT = PANEL_WIDTH * 1.618
SCENE_REVISION = "v16-no-bake-ambient-experiment"
STONE_ROUGHNESS = .16
CORNER_X = ROOM_WIDTH/2 - .15
CORNER_SLOT_WIDTH = .10
CORNER_LINE_HEIGHT = 3.6
CORNER_LIGHT_POWER_W = 40
COVE_POWER_W = 1800
EXPOSURE = 0.3
p=argparse.ArgumentParser()
p.add_argument('--output',default=str(Path(__file__).parent))
p.add_argument('--color',nargs=3,type=float,default=[0.45,0.7,1.0],help='Linear RGB, 0..1')
p.add_argument('--color-scope',choices=['all','panel'],default='all',help='Apply selected color to all lights or panel only')
p.add_argument('--indirect-mode',choices=['lightmap','approx'],default='approx')
p.add_argument('--light-config',type=Path)
p.add_argument('--ambient-gain',type=float,default=1.0)
p.add_argument('--engines',nargs='+',choices=['eevee','cycles'],default=['eevee','cycles'])
p.add_argument('--bump-strength',type=float,default=0.0,help='0 disables normal perturbation; previous scene used 0.16')
p.add_argument('--lightmap-resolution',type=int,default=512,choices=[128,256,512,1024,2048])
p.add_argument('--lightmap-samples',type=int,default=256)
p.add_argument('--panel-strength',type=float,default=3.0)
p.add_argument('--cove-power',type=float,default=COVE_POWER_W)
p.add_argument('--corner-power',type=float,default=CORNER_LIGHT_POWER_W)
p.add_argument('--eevee-samples',type=int,default=128)
p.add_argument('--cycles-samples',type=int,default=128)
p.add_argument('--resolution',nargs=2,type=int,default=[480,360])
p.add_argument('--device',choices=['METAL','CPU'],default='METAL')
a=p.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
if any(not 0<=x<=1 for x in a.color): p.error('color must be 0..1')
if not 0<=a.bump_strength<=1: p.error('bump-strength must be 0..1')
if a.lightmap_samples<1: p.error('lightmap-samples must be positive')
if any(not math.isfinite(x) or not 0<=x<=100000 for x in [a.panel_strength,a.cove_power,a.corner_power]): p.error('invalid light strength')
if any(not 1<=x<=8192 for x in [a.eevee_samples,a.cycles_samples]): p.error('samples must be 1..8192')
if any(not 16<=x<=4096 for x in a.resolution): p.error('resolution must be 16..4096')
if not math.isfinite(a.ambient_gain) or not 0<=a.ambient_gain<=10: p.error('ambient-gain must be 0..10')
sys.path.insert(0,str(Path(__file__).resolve().parent))
independent_lights=None
if a.light_config:
 from approx_lighting import load_lights
 independent_lights=load_lights(a.light_config)
 a.color=independent_lights['panel']['color_linear'];a.panel_strength=independent_lights['panel']['strength']
COVE_POWER_W=a.cove_power; CORNER_LIGHT_POWER_W=a.corner_power
ACCENT_COLOR=tuple(a.color) if a.color_scope=='all' else (1,.94,.85)
out=Path(a.output).resolve(); out.mkdir(parents=True,exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
s=bpy.context.scene
s.render.resolution_x=a.resolution[0]; s.render.resolution_y=a.resolution[1]; s.render.resolution_percentage=100
s.render.image_settings.file_format='PNG'
s.view_settings.view_transform='AgX'
s.view_settings.exposure=EXPOSURE
s.eevee.taa_render_samples=a.eevee_samples
s.eevee.use_raytracing=False
s.eevee.use_fast_gi=False
s.world=bpy.data.worlds.new('Gallery world'); s.world.use_nodes=True
s.world.node_tree.nodes['Background'].inputs['Color'].default_value=(0.8,0.8,0.8,1)
s.world.node_tree.nodes['Background'].inputs['Strength'].default_value=0.0
m=bpy.data.materials.new('Polished light stone — walls, floor and ceiling'); m.use_nodes=True
bs=m.node_tree.nodes.get('Principled BSDF')
bs.inputs['Base Color'].default_value=(.70,.70,.68,1)
bs.inputs['Roughness'].default_value=STONE_ROUGHNESS
bs.inputs['Metallic'].default_value=0
bs.inputs['IOR'].default_value=1.5
bs.inputs['Coat Weight'].default_value=.25
bs.inputs['Coat Roughness'].default_value=.10
fascia_mat=bpy.data.materials.new('Matte white cove fascia'); fascia_mat.use_nodes=True
fascia_bs=fascia_mat.node_tree.nodes.get('Principled BSDF')
fascia_bs.inputs['Base Color'].default_value=(.72,.72,.72,1)
fascia_bs.inputs['Roughness'].default_value=.65
def box(name,loc,scale,mat=m):
 bpy.ops.mesh.primitive_cube_add(size=1,location=loc); o=bpy.context.object; o.name=name; o.dimensions=scale
 bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
 o.data.materials.append(mat); bevel=o.modifiers.new('Soft concrete edges','BEVEL'); bevel.width=.035; bevel.segments=3
 o.modifiers.new('Normals','WEIGHTED_NORMAL'); return o
box('Floor',(0,ROOM_CENTER_Y,-.12),(ROOM_WIDTH,ROOM_DEPTH,.24))
# Split the back wall to form real recessed channels beside the side walls.
# Solid concrete is absent from each 10 cm-wide slot, not overlaid by a lamp.
box('Back wall center',(0,BACK_Y,ROOM_HEIGHT/2),(2*(CORNER_X-CORNER_SLOT_WIDTH/2),.2,ROOM_HEIGHT))
for side in [-1,1]:
 x=side*CORNER_X
 edge_width=ROOM_WIDTH/2-CORNER_X-CORNER_SLOT_WIDTH/2
 box('Back wall corner edge',(side*(ROOM_WIDTH/2-edge_width/2),BACK_Y,ROOM_HEIGHT/2),(edge_width,.2,ROOM_HEIGHT))
 for z in [.1,ROOM_HEIGHT-.1]:
  box('Corner channel end',(x,BACK_Y,z),(CORNER_SLOT_WIDTH,.2,.2))
box('Left wall',(-ROOM_WIDTH/2,ROOM_CENTER_Y,ROOM_HEIGHT/2),(.2,ROOM_DEPTH,ROOM_HEIGHT))
box('Right wall',(ROOM_WIDTH/2,ROOM_CENTER_Y,ROOM_HEIGHT/2),(.2,ROOM_DEPTH,ROOM_HEIGHT))
# Restore the original flat 20 cm slab, with the same stone as the walls/floor.
box('Ceiling',(0,ROOM_CENTER_Y,ROOM_HEIGHT+.1),(ROOM_WIDTH,ROOM_DEPTH,.2),m)
em=bpy.data.materials.new('Panel color — controlled by --color'); em.use_nodes=True
bs=em.node_tree.nodes.get('Principled BSDF'); bs.inputs['Base Color'].default_value=(*a.color,1); bs.inputs['Emission Color'].default_value=(*a.color,1); bs.inputs['Emission Strength'].default_value=a.panel_strength
box('Emissive panel',(0,BACK_Y-.16,ROOM_HEIGHT/2),(PANEL_WIDTH,.06,PANEL_HEIGHT),em)
def area(name,loc,target,energy,color,size,size_y=None):
 d=bpy.data.lights.new(name,'AREA'); d.energy=energy; d.color=color; d.shape='RECTANGLE'; d.size=size; d.size_y=size_y or size
 o=bpy.data.objects.new(name,d); s.collection.objects.link(o); o.location=loc; o.rotation_euler=(Vector(target)-o.location).to_track_quat('-Z','Y').to_euler()
# Concealed upper left/right cove lights remain unchanged.
# Panel remains an emissive surface, with no direct-light proxy.
for x in [-ROOM_WIDTH/2+.35, ROOM_WIDTH/2-.35]:
 box('Cove fascia',(x,ROOM_CENTER_Y,3.5),(.35,ROOM_DEPTH-.3,.28),fascia_mat)
 area('Hidden upward cove',(x,ROOM_CENTER_Y,3.65),(x,ROOM_CENTER_Y,4.1),COVE_POWER_W,ACCENT_COLOR,.18,ROOM_DEPTH-1)
# Recessed, straight corner strips: emissive diffuser behind the wall face.
# Small matching area proxies let Eevee illuminate adjacent surfaces as well.
line_mat=bpy.data.materials.new('Warm white recessed strip'); line_mat.use_nodes=True
line_bs=line_mat.node_tree.nodes.get('Principled BSDF')
line_bs.inputs['Base Color'].default_value=(*ACCENT_COLOR,1)
line_bs.inputs['Emission Color'].default_value=(*ACCENT_COLOR,1)
line_bs.inputs['Emission Strength'].default_value=4
backing=bpy.data.materials.new('Dark channel backing'); backing.diffuse_color=(.035,.035,.035,1)
for side in [-1,1]:
 x=side*CORNER_X
 channel=box('Recessed corner channel',(x,BACK_Y-.005,ROOM_HEIGHT/2),(.098,.03,CORNER_LINE_HEIGHT),backing)
 channel.modifiers['Soft concrete edges'].width=.002
 strip=box('Recessed vertical corner diffuser',(x,BACK_Y-.06,ROOM_HEIGHT/2),(.06,.012,CORNER_LINE_HEIGHT),line_mat)
 strip.modifiers['Soft concrete edges'].width=.002
 area('Recessed corner light',(x,BACK_Y-.082,ROOM_HEIGHT/2),(0,0,ROOM_HEIGHT/2),CORNER_LIGHT_POWER_W,ACCENT_COLOR,.055,CORNER_LINE_HEIGHT)
if independent_lights:
 from approx_lighting import apply_independent_lights
 apply_independent_lights(s,independent_lights)
c=bpy.data.cameras.new('Fixed camera'); cam=bpy.data.objects.new('Fixed camera',c); s.collection.objects.link(cam)
cam.location=(0,-7.2,2.0); cam.rotation_euler=(Vector((0,BACK_Y,2.0))-cam.location).to_track_quat('-Z','Y').to_euler(); c.lens=26; s.camera=cam
s.cycles.samples=a.cycles_samples; s.cycles.use_adaptive_sampling=False; s.cycles.use_denoising=True; s.cycles.seed=42
report={'scene_revision':SCENE_REVISION,'color_scope':'independent' if independent_lights else a.color_scope,'indirect_mode':a.indirect_mode,'independent_lights':independent_lights,'light_sources':[{'name':o.name,'color_linear':list(o.data.color),'power_w':o.data.energy} for o in s.objects if o.type=='LIGHT'],'corner_emission_color_linear':list(line_bs.inputs['Emission Color'].default_value)[:3],'room_dimensions_m':[ROOM_WIDTH,ROOM_DEPTH,ROOM_HEIGHT],'panel_dimensions_m':[PANEL_WIDTH,PANEL_HEIGHT],'lighting':'Two side coves, two recessed corner strips with area proxies, emissive panel; world zero; no fill or panel proxy','exhibit_objects':[],'surface_finish':'Matching polished dielectric stone on walls, floor and flat ceiling','stone_roughness':STONE_ROUGHNESS,'ceiling_type':'flat','ceiling_underside_height_m':ROOM_HEIGHT,'bump_strength':0,'eevee_render_samples':a.eevee_samples,'panel_strength':a.panel_strength,'cycles_adaptive_sampling':False,'eevee_raytracing':False,'eevee_fast_gi':False,'indirect_method':'Source-colored ambient field for Eevee, physical indirect light for Cycles' if a.indirect_mode=='approx' else 'Offline Cycles diffuse lightmaps; no Eevee volume probes','camera_location_m':list(cam.location),'camera_lens_mm':c.lens,'corner_strip_dimensions_m':[.06,CORNER_LINE_HEIGHT],'corner_light_power_w_each':CORNER_LIGHT_POWER_W,'cove_power_w_each':COVE_POWER_W,'exposure':EXPOSURE,'blender':bpy.app.version_string,'platform':platform.platform(),'resolution':list(a.resolution),'color_linear':a.color,'cycles_samples':a.cycles_samples,'timing_definition':'perf_counter around bpy.ops.render.render(write_still=True), includes engine initialization and PNG save; single cold run, not benchmark','devices':[],'renders':[]}
if independent_lights:
 report['emissive_surfaces']=[{'name':o.name,'color_linear':list(o.data.materials[0].node_tree.nodes.get('Principled BSDF').inputs['Emission Color'].default_value)[:3]} for o in s.objects if o.type=='MESH' and (o.name.startswith('Recessed vertical corner diffuser') or o.name=='Emissive panel')]
 cove_powers=[independent_lights[k]['power_w'] for k in ['cove_left','cove_right']]
 corner_powers=[independent_lights[k]['power_w'] for k in ['corner_left','corner_right']]
 report['cove_power_w_each']=cove_powers[0] if cove_powers[0]==cove_powers[1] else None
 report['corner_light_power_w_each']=corner_powers[0] if corner_powers[0]==corner_powers[1] else None
 report['corner_emission_color_linear']=None  # The two strips have independent colors.
 report['lighting']='Five independently controlled sources; Eevee-only emission proxy added in approximate mode'
prefs=bpy.context.preferences.addons['cycles'].preferences
if a.device=='METAL':
 prefs.compute_device_type='METAL'; prefs.get_devices()
 for d in prefs.devices:
  d.use=d.type=='METAL'; report['devices'].append({'name':d.name,'type':d.type,'enabled':bool(d.use)})
 if not any(d.type=='METAL' for d in prefs.devices): raise RuntimeError('No Metal Cycles device; explicitly rerun --device CPU')
 s.cycles.device='GPU'
else: s.cycles.device='CPU'
report['cycles_device']=s.cycles.device
# Offline preprocessing only. Final Eevee uses rasterization, direct lights
# and these precomputed scene-linear maps; no screen-space ray tracing.
if 'eevee' in a.engines and a.indirect_mode=='lightmap':
 sys.path.insert(0,str(Path(__file__).resolve().parent))
 from lightmaps import bake_lightmaps
 try:
  report['lightmap_bake']=bake_lightmaps(s,[m,fascia_mat],out/'lightmaps',a.lightmap_resolution,a.lightmap_samples)
 except Exception as e:
  report['lightmap_bake']={'success':False,'error':str(e),'details':'lightmaps/bake_report.json'}
  (out/'render_report.json').write_text(json.dumps(report,indent=2)); raise
 (out/'render_report.json').write_text(json.dumps(report,indent=2))
if 'eevee' in a.engines and a.indirect_mode=='approx':
 from approx_lighting import ambient_specs,attach_ambient
 if independent_lights is None:
  independent_lights={'panel':{'color_linear':list(a.color),'strength':a.panel_strength},
    'cove_left':{'color_linear':list(ACCENT_COLOR),'power_w':COVE_POWER_W},
    'cove_right':{'color_linear':list(ACCENT_COLOR),'power_w':COVE_POWER_W},
    'corner_left':{'color_linear':list(ACCENT_COLOR),'power_w':CORNER_LIGHT_POWER_W},
    'corner_right':{'color_linear':list(ACCENT_COLOR),'power_w':CORNER_LIGHT_POWER_W}}
 specs=ambient_specs(independent_lights,ROOM_WIDTH,ROOM_CENTER_Y,BACK_Y)
 attach_ambient([m,fascia_mat],independent_lights,specs,a.ambient_gain)
 # Raster renderer's direct-light proxy for the emissive panel. Cycles already
 # evaluates mesh emission and must not receive this extra analytic source.
 area('Eevee-only panel emission proxy',(0,BACK_Y-.24,ROOM_HEIGHT/2),(0,0,ROOM_HEIGHT/2),
      160*independent_lights['panel']['strength']/3,independent_lights['panel']['color_linear'],PANEL_WIDTH,PANEL_HEIGHT)
 approx_proxy=bpy.data.objects['Eevee-only panel emission proxy']
 report['indirect_method']='Eevee source-colored analytic ambient field; no visibility or bounce tracing'
 report['ambient_approximation']={'gain':a.ambient_gain,'fields':specs,'bake_seconds':0,'lightmaps':False,'volume_probes':False,'physical_accuracy':False}
 report['lightmap_bake']={'success':True,'skipped':True,'seconds':0}
else:approx_proxy=None
# Raster-captured environment reflection, not screen-space ray tracing.
if 'eevee' in a.engines:
 reflection=bpy.data.lightprobes.new('Room glossy reflection','SPHERE')
 reflection.influence_type='BOX'; reflection.influence_distance=1.0
 reflection.clip_start=.05; reflection.clip_end=40
 reflection.use_custom_parallax=True; reflection.parallax_type='BOX'; reflection.parallax_distance=1.0
 probe=bpy.data.objects.new('Room glossy reflection',reflection); s.collection.objects.link(probe)
 probe.location=(0,ROOM_CENTER_Y,ROOM_HEIGHT/2)
 probe.scale=(ROOM_WIDTH/2+.1,ROOM_DEPTH/2+.1,ROOM_HEIGHT/2+.4)
 report['reflection_method']='Eevee sphere light probe, box parallax approximation; Cycles physical reflection'
for engine,label in [('BLENDER_EEVEE_NEXT','eevee'),('CYCLES','cycles')]:
 if label not in a.engines: continue
 if approx_proxy: approx_proxy.hide_render=(engine=='CYCLES')
 s.render.engine=engine; s.render.filepath=str(out/(label+'.png'))
 bpy.ops.wm.save_as_mainfile(filepath=str(out/'light_lab.blend'))
 print('LIGHT_LAB_ENGINE_BEGIN '+label,flush=True)
 start=time.perf_counter()
 try:
  bpy.ops.render.render(write_still=True)
  report['renders'].append({'engine':engine,'seconds':round(time.perf_counter()-start,3),'image':label+'.png','success':True})
 except Exception as e:
  report['renders'].append({'engine':engine,'seconds':round(time.perf_counter()-start,3),'success':False,'error':str(e)})
  (out/'render_report.json').write_text(json.dumps(report,indent=2)); raise
 (out/'render_report.json').write_text(json.dumps(report,indent=2))
 print('LIGHT_LAB_ENGINE_END '+label,flush=True)
if report.get('lightmap_bake',{}).get('success'):
 eevee_seconds=next((r['seconds'] for r in report['renders'] if r['engine']=='BLENDER_EEVEE_NEXT' and r['success']),None)
 if eevee_seconds is not None:
  report['eevee_bake_plus_render_seconds']=round(report['lightmap_bake']['seconds']+eevee_seconds,3)
 (out/'render_report.json').write_text(json.dumps(report,indent=2))
print('LIGHT_LAB_REPORT',json.dumps(report))
