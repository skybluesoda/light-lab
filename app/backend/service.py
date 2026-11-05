"""One local render worker, durable results, and independently controlled lights."""
from __future__ import annotations
import copy
import hashlib
import fcntl
import json
import math
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from lab_config import srgb_to_linear
from preset_lab import write_json, now, validate_png, digest, check_graphics_access, find_blender

GROUPS=('panel','cove_left','cove_right','corner_left','corner_right')
RESOLUTIONS={'preview':[480,360],'hd':[960,540],'fhd':[1920,1080]}
TERMINAL={'completed','failed','cancelled'}

class Light(BaseModel):
    model_config=ConfigDict(extra='forbid')
    color_srgb: str=Field(pattern=r'^#[0-9a-fA-F]{6}$')
    intensity: float=Field(ge=0,le=4000,allow_inf_nan=False)
    enabled: bool=True

class RenderRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    lights: dict[str,Light]
    resolution: Literal['preview','hd','fhd']='preview'
    samples: Literal[32,64,128,256,512]=128
    engines: list[Literal['eevee','cycles']]=Field(default_factory=lambda:['eevee','cycles'])
    ambient_gain: float=Field(default=1,ge=0,le=3,allow_inf_nan=False)

    @model_validator(mode='after')
    def check(self):
        if set(self.lights)!=set(GROUPS):raise ValueError('5개 조명 설정이 모두 필요합니다.')
        for group,light in self.lights.items():
            maximum=30 if group=='panel' else 200 if group.startswith('corner') else 4000
            if light.intensity>maximum:raise ValueError(group+' 밝기 범위를 확인하세요.')
        if not self.engines or len(self.engines)!=len(set(self.engines)):raise ValueError('엔진을 선택하세요.')
        self.engines=[x for x in ('eevee','cycles') if x in self.engines]
        return self

    def light_config(self):
        return {key:{'color_srgb':self.lights[key].color_srgb.upper(),
                     'strength' if key=='panel' else 'power_w':self.lights[key].intensity if self.lights[key].enabled else 0}
                for key in GROUPS}


def default_request():
    source=json.loads((ROOT/'config/approx-preview.json').read_text())
    return RenderRequest(lights={key:{'color_srgb':source[key]['color_srgb'],
                                      'intensity':source[key]['strength' if key=='panel' else 'power_w']}
                                 for key in GROUPS})


def source_fingerprint():
    names=('scene_approx.py','approx_lighting.py','lab_config.py')
    return hashlib.sha256(b''.join(name.encode()+(ROOT/name).read_bytes() for name in names)).hexdigest()


def request_key(request: RenderRequest):
    # Enabled=false and zero intensity have the same effective physical request.
    canonical={'lights':request.light_config(),'resolution':request.resolution,'samples':request.samples,
               'engines':request.engines,'ambient_gain':request.ambient_gain,'renderer':source_fingerprint()}
    return hashlib.sha256(json.dumps(canonical,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def runtime_status():
    try:binary=find_blender();installed=True
    except RuntimeError:binary=None;installed=False
    try:check_graphics_access();graphics=True
    except (RuntimeError,OSError):graphics=False
    return {'blender_available':installed,'graphics_available':graphics,'can_render':installed and graphics,
            'message':'로컬 렌더링 준비됨' if installed and graphics else '미리보기 연결됨 · 새 렌더는 일반 macOS 터미널에서 앱을 실행해야 합니다.'}


def linear_hex(rgb):
    def c(x):
        y=12.92*x if x<=.0031308 else 1.055*x**(1/2.4)-.055
        return max(0,min(255,round(y*255)))
    return '#'+''.join(f'{c(x):02X}' for x in rgb)


from experiments import Experiments,bind_models
bind_models(RenderRequest)


class RenderService(Experiments):
    def __init__(self,data_dir=None,renderer=None):
        self.data=Path(data_dir or os.environ.get('LIGHT_LAB_DATA_DIR') or ROOT/'app/data')
        self.data.mkdir(parents=True,exist_ok=True)
        self.jobs={};self.lock=threading.RLock();self.pending=queue.Queue()
        self.process=None;self.active_id=None;self.thread=None;self.closed=False;self._lock_handle=None
        self.renderer=renderer or self._render
        for path in sorted((self.data/'jobs').glob('*/job.json')):
            try:
                job=json.loads(path.read_text())
                if not re.fullmatch(r'[a-f0-9]{32}',job['id']):continue
                self.jobs[job['id']]=job
            except (ValueError,KeyError,OSError):continue
        self.example=self._example()

    def _example(self):
        directory=ROOT/'docs/images'
        try:
            report=json.loads((directory/'render_report.json').read_text())
            if report.get('eevee_raytracing') is not False or report.get('indirect_mode')!='approx':return None
            measured=next(row for row in report['renders'] if row['engine']=='BLENDER_EEVEE_NEXT' and row['success'])
            validate_png(directory/'eevee.png',report['resolution'])
            lights=report['independent_lights']
            req=RenderRequest(lights={key:{'color_srgb':linear_hex(lights[key]['color_linear']),
                                          'intensity':lights[key]['strength' if key=='panel' else 'power_w']} for key in GROUPS},
                              samples=report['eevee_render_samples'],engines=['eevee'],
                              resolution=next(k for k,v in RESOLUTIONS.items() if v==report['resolution']))
            return {'id':'example','status':'completed','kind':'example','label':'확인한 기준 미리보기',
                    'request':req.model_dump(),'created_at':None,'stage':'기존 실험 이미지',
                    'measurements':{'eevee':measured['seconds']},'images':['eevee'],
                    'resolution_pixels':report['resolution'],'cache_hit':False,
                    'timing_note':'기존 실험에서 측정한 시간 · Cycles 비교 결과는 아직 없습니다.'}
        except (ValueError,KeyError,OSError,StopIteration):return None

    def directory(self,identifier):
        if not re.fullmatch(r'[a-f0-9]{32}',identifier):raise KeyError(identifier)
        return self.data/'jobs'/identifier

    def _save(self,job):write_json(self.directory(job['id'])/'job.json',job)

    def start(self):
        if self.thread:return
        self._lock_handle=(self.data/'.server.lock').open('a+')
        try:fcntl.flock(self._lock_handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock_handle.close();self._lock_handle=None
            raise RuntimeError('같은 데이터 폴더를 사용하는 앱이 이미 실행 중입니다.')
        for job in self.jobs.values():
            if job['status'] not in TERMINAL:
                job.update(status='failed',error='이전 앱 실행이 종료되어 중단됐습니다. 다시 렌더링해 주세요.',finished_at=now());self._save(job)
        self._refresh_benchmarks()
        self.thread=threading.Thread(target=self._worker,name='light-lab-render',daemon=True);self.thread.start()

    def close(self):
        with self.lock:
            self.closed=True
            for job in self.jobs.values():
                if job['status']=='queued':job.update(status='cancelled',finished_at=now());self._save(job)
            process=self.process
        self._stop_process(process)
        self.pending.put(None)
        if self.thread:self.thread.join(timeout=7)
        if self._lock_handle:
            fcntl.flock(self._lock_handle,fcntl.LOCK_UN);self._lock_handle.close();self._lock_handle=None

    @staticmethod
    def _stop_process(process):
        if process is not None and process.poll() is None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:return
            try:process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait()

    def _valid_cached(self,job):
        try:
            for engine in job['request']['engines']:
                path=self.directory(job['id'])/(engine+'.png')
                if digest(path)!=job['checksums'][engine]:return False
            return True
        except (KeyError,OSError):return False

    def submit(self,request,force=False):
        key=request_key(request)
        with self.lock:
            if self.closed:raise RuntimeError('앱이 종료 중입니다.')
            for job in reversed(list(self.jobs.values())):
                if force or job.get('key')!=key:continue
                if job['status']=='completed' and self._valid_cached(job):
                    result=copy.deepcopy(job);result['cache_hit']=True;return result
                if job['status'] not in TERMINAL:return copy.deepcopy(job)
            if sum(job['status'] not in TERMINAL for job in self.jobs.values())>=4:
                raise RuntimeError('대기 중인 작업이 많습니다. 완료 후 다시 실행하세요.')
            identifier=uuid.uuid4().hex
            job={'id':identifier,'key':key,'kind':'render','label':'비교 렌더' if len(request.engines)==2 else 'Eevee 렌더' if request.engines==['eevee'] else 'Cycles 렌더',
                 'request':request.model_dump(),'status':'queued','stage':'대기 중','created_at':now(),
                 'measurements':{},'images':[],'cache_hit':False,'renderer_fingerprint':source_fingerprint(),
                 'resolution_pixels':RESOLUTIONS[request.resolution]}
            self.jobs[identifier]=job;self._save(job);self.pending.put(identifier)
            return copy.deepcopy(job)

    def get(self,identifier):
        with self.lock:
            if identifier=='example' and self.example:return copy.deepcopy(self.example)
            self.directory(identifier)
            return copy.deepcopy(self.jobs[identifier])

    def history(self):
        with self.lock:
            rows=[copy.deepcopy(j) for j in sorted(self.jobs.values(),key=lambda row:row['created_at'],reverse=True)][:30]
            if self.example:rows.append(copy.deepcopy(self.example))
            return rows

    def image(self,identifier,engine):
        if engine not in ('eevee','cycles'):raise KeyError(engine)
        job=self.get(identifier)
        if engine not in job['images']:raise KeyError(engine)
        path=(ROOT/'docs/images' if identifier=='example' else self.directory(identifier))/(engine+'.png')
        if not path.is_file():raise KeyError(engine)
        if identifier!='example' and job.get('checksums',{}).get(engine) and digest(path)!=job['checksums'][engine]:raise KeyError(engine)
        return path

    def cancel(self,identifier):
        with self.lock:
            job=self.jobs[identifier]
            if job['status'] in TERMINAL:return copy.deepcopy(job)
            job.update(status='cancelled',stage='취소됨',finished_at=now());self._save(job)
            self._refresh_benchmarks()
            process=self.process if self.active_id==identifier else None
        self._stop_process(process)
        return self.get(identifier)

    def progress(self,identifier,stage):
        with self.lock:
            job=self.jobs[identifier]
            if job['status'] in TERMINAL:return
            job.update(stage=stage)
            report_path=self.directory(identifier)/'render_report.json'
            if report_path.exists():
                try:
                    report=json.loads(report_path.read_text())
                    for row in report.get('renders',[]):
                        engine={'BLENDER_EEVEE_NEXT':'eevee','CYCLES':'cycles'}.get(row['engine'])
                        if engine and row.get('success'):
                            validate_png(self.directory(identifier)/(engine+'.png'),job['resolution_pixels'])
                            job['measurements'][engine]=row['seconds']
                            if engine not in job['images']:job['images'].append(engine)
                except (OSError,ValueError,KeyError):pass
            self._save(job)

    def _worker(self):
        while True:
            identifier=self.pending.get()
            if identifier is None:return
            with self.lock:
                job=self.jobs[identifier]
                if self.closed or job['status']=='cancelled':continue
                job.update(status='running',stage='장면 준비 중',started_at=now());self.active_id=identifier;self._save(job)
            t=time.perf_counter()
            try:
                request=RenderRequest.model_validate(job['request'])
                if job['renderer_fingerprint']!=source_fingerprint():raise RuntimeError('장면 코드가 바뀌었습니다. 다시 실행해 주세요.')
                self.renderer(identifier,request)
                if job['renderer_fingerprint']!=source_fingerprint():raise RuntimeError('렌더 도중 코드가 바뀌어 결과를 등록하지 않았습니다.')
                report=self.validate_result(identifier,request)
                with self.lock:
                    if job['status']=='cancelled':continue
                    job.update(status='completed',stage='완료',finished_at=now(),
                               total_seconds=round(time.perf_counter()-t,3),
                               measurements={('eevee' if row['engine']=='BLENDER_EEVEE_NEXT' else 'cycles'):row['seconds'] for row in report['renders'] if row['success']},
                               images=request.engines,checksums={engine:digest(self.directory(identifier)/(engine+'.png')) for engine in request.engines},
                               timing_note='생성 당시 측정값 · 장면 준비와 최종 렌더 시간을 구분합니다.')
                    self._save(job)
            except Exception as error:
                with self.lock:
                    if job['status']!='cancelled':
                        job.update(status='failed',stage='실행 실패',error=str(error),finished_at=now());self._save(job)
            finally:
                with self.lock:
                    self.process=None;self.active_id=None
                    self._refresh_benchmarks()

    def validate_result(self,identifier,request):
        directory=self.directory(identifier);report=json.loads((directory/'render_report.json').read_text())
        if report.get('eevee_raytracing') is not False or report.get('eevee_fast_gi') is not False:raise ValueError('Eevee 레이트레이싱 설정이 다릅니다.')
        if report.get('indirect_mode')!='approx':raise ValueError('간접광 실험 모드가 아닙니다.')
        if report['resolution']!=RESOLUTIONS[request.resolution]:raise ValueError('해상도 설정이 다릅니다.')
        if report['eevee_render_samples']!=request.samples or report['cycles_samples']!=request.samples:raise ValueError('샘플 설정이 다릅니다.')
        lights=request.light_config()
        for key,item in lights.items():
            rendered=report['independent_lights'][key];intensity='strength' if key=='panel' else 'power_w'
            if len(rendered['color_linear'])!=3 or not math.isfinite(rendered[intensity]) or abs(rendered[intensity]-item[intensity])>1e-5 or any(not math.isfinite(x) or abs(x-y)>1e-6 for x,y in zip(rendered['color_linear'],srgb_to_linear(item['color_srgb']))):raise ValueError('조명 설정이 일치하지 않습니다.')
        if 'eevee' in request.engines and (report.get('ambient_approximation',{}).get('lightmaps') is not False or report.get('lightmap_bake',{}).get('skipped') is not True):raise ValueError('라이트맵 없는 설정을 확인할 수 없습니다.')
        if 'cycles' in request.engines and report.get('cycles_device')!='GPU':raise ValueError('Cycles GPU 실행 기록을 확인할 수 없습니다.')
        by_engine={row['engine']:row for row in report['renders']}
        for engine in request.engines:
            row=by_engine.get('BLENDER_EEVEE_NEXT' if engine=='eevee' else 'CYCLES',{})
            if row.get('success') is not True or not isinstance(row.get('seconds'),(int,float)) or not 0<row['seconds']<1e9:raise ValueError('렌더 완료 기록이 없습니다.')
            validate_png(directory/(engine+'.png'),RESOLUTIONS[request.resolution])
        return report

    def _render(self,identifier,request):
        check_graphics_access()  # Never start Blender with inaccessible Metal.
        directory=self.directory(identifier);write_json(directory/'lights.json',request.light_config())
        args=[find_blender(),'-b','--factory-startup','--python-exit-code','1','--python',str(ROOT/'scene_approx.py'),'--',
              '--output',str(directory),'--indirect-mode','approx','--light-config',str(directory/'lights.json'),
              '--ambient-gain',str(request.ambient_gain),'--eevee-samples',str(request.samples),'--cycles-samples',str(request.samples),
              '--resolution',*[str(v) for v in RESOLUTIONS[request.resolution]],'--engines',*request.engines,
              '--device','METAL' if 'cycles' in request.engines else 'CPU']
        with (directory/'render.log').open('w') as log:
            process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1,start_new_session=True)
            with self.lock:
                self.process=process
                already_cancelled=self.jobs[identifier]['status']=='cancelled' or self.closed
            if already_cancelled:self._stop_process(process);return
            try:
                for line in process.stdout:
                    log.write(line);log.flush()
                    if line.startswith('LIGHT_LAB_ENGINE_BEGIN'):
                        self.progress(identifier,'Eevee 렌더 중' if 'eevee' in line else 'Cycles 렌더 중')
                    elif line.startswith('LIGHT_LAB_ENGINE_END'):
                        self.progress(identifier,'엔진 결과 저장 중')
                code=process.wait()
                if code:raise RuntimeError(f'Blender가 종료 코드 {code}로 중단됐습니다. 로그를 확인해 주세요.')
            except BaseException:
                self._stop_process(process);raise
