"""Refine existing EXRs on CPU without loading bpy or a GPU."""
import argparse, json, time
from pathlib import Path
import numpy as np
import OpenImageIO as oiio
from oidn_filter import denoise_array
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--output',required=True)
a=p.parse_args();source=Path(a.source).resolve();out=Path(a.output).resolve();out.mkdir(parents=True,exist_ok=True)
files=sorted(source.glob('*.exr'))
if not files:raise RuntimeError('No source lightmaps')
r={'method':'OIDN CPU, HDR color-only, offline lightmap preprocessing','source':str(source),'files':[],'success':False}
t=time.perf_counter()
try:
 for path in files:
  image=oiio.ImageBuf(str(path));pixels=image.get_pixels(oiio.FLOAT)
  if pixels is None:raise RuntimeError(image.geterror())
  clean,seconds=denoise_array(pixels)
  spec=oiio.ImageSpec(clean.shape[1],clean.shape[0],clean.shape[2],oiio.FLOAT)
  spec.attribute('oiio:ColorSpace','Linear')
  output=oiio.ImageBuf(spec)
  roi=oiio.ROI(0,clean.shape[1],0,clean.shape[0],0,1,0,clean.shape[2])
  output.set_pixels(roi,clean)
  if not output.write(str(out/path.name)):raise RuntimeError(output.geterror())
  r['files'].append({'name':path.name,'seconds':seconds,'mean_before':float(pixels[...,:3].mean()),'mean_after':float(clean[...,:3].mean())})
  print(path.name,seconds,flush=True)
 r['success']=True
finally:
 r['seconds']=round(time.perf_counter()-t,3)
 (out/'denoise_report.json').write_text(json.dumps(r,indent=2))
