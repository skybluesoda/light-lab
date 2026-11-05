#!/usr/bin/env python3
"""Local preset preparation and verified image cache. Python standard library only."""
import argparse
import contextlib
import ctypes
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import struct
import subprocess
import sys
import time
import uuid
import zlib
from lab_config import ROOT, load_config, presets, make_request, cache_key, renderer_fingerprint


def now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec='seconds')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def validate_png(path, resolution):
    data = Path(path).read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Invalid PNG: ' + str(path))
    pos = 8; compressed = bytearray(); header = None; ended = False
    while pos + 12 <= len(data):
        size = struct.unpack('>I', data[pos:pos+4])[0]
        kind = data[pos+4:pos+8]; end = pos+8+size
        if end+4 > len(data):
            raise ValueError('Truncated PNG')
        chunk = data[pos+8:end]
        if zlib.crc32(kind+chunk) & 0xffffffff != struct.unpack('>I', data[end:end+4])[0]:
            raise ValueError('PNG checksum failure')
        if kind == b'IHDR': header = struct.unpack('>IIBBBBB', chunk)
        if kind == b'IDAT': compressed.extend(chunk)
        if kind == b'IEND': ended = True; break
        pos = end+4
    if not ended or not header or list(header[:2]) != list(resolution):
        raise ValueError('Missing PNG data or wrong resolution')
    width, height, depth, color, _, _, interlace = header
    channels = {0:1, 2:3, 4:2, 6:4}.get(color)
    if depth not in (8,16) or channels is None or interlace:
        raise ValueError('Unsupported PNG format')
    if len(zlib.decompress(compressed)) != height*(1+width*channels*(depth//8)):
        raise ValueError('Incomplete PNG pixels')


def validate_report(path, request=None):
    report = json.loads(Path(path).read_text())
    by_engine = {x['engine']: x for x in report['renders']}
    for engine in ('BLENDER_EEVEE_NEXT','CYCLES'):
        item = by_engine.get(engine,{})
        if item.get('success') is not True or not isinstance(item.get('seconds'),(int,float)) or not math.isfinite(item['seconds']) or item['seconds'] <= 0:
            raise ValueError('Both successful engine measurements are required')
    if report.get('eevee_raytracing') is not False:
        raise ValueError('This cache requires Eevee ray tracing OFF')
    if request is not None:
        render = request['render']
        expected = {'blender': request['blender_version'], 'resolution': render['resolution'],
                    'eevee_render_samples': render['eevee_samples'], 'cycles_samples': render['cycles_samples'],
                    'panel_strength': request['panel']['strength'],
                    'cove_power_w_each': request['lighting']['cove_power_w_each'],
                    'corner_light_power_w_each': request['lighting']['corner_power_w_each'],
                    'color_scope': request['lighting']['color_scope'],
                    'cycles_adaptive_sampling': False, 'eevee_fast_gi': False}
        for name,value in expected.items():
            if report.get(name) != value:
                raise ValueError('Rendered configuration mismatch: ' + name)
        if any(abs(x-y)>1e-8 for x,y in zip(report.get('color_linear',[]), request['panel']['color_linear'])) or len(report.get('color_linear',[])) != 3:
            raise ValueError('Rendered color mismatch')
        expected_accent = request['panel']['color_linear'] if request['lighting']['color_scope']=='all' else [1,.94,.85]
        sources=report.get('light_sources',[])
        colors=[source.get('color_linear',[]) for source in sources]+[report.get('corner_emission_color_linear',[])]
        if len(sources)!=4 or any(len(color)!=3 or any(abs(x-y)>1e-6 for x,y in zip(color,expected_accent)) for color in colors):
            raise ValueError('Cove/corner light colors do not match the requested color scope')
        bake = report.get('lightmap_bake',{})
        if bake.get('success') is not True or bake.get('resolution_main') != render['lightmap_resolution'] or bake.get('samples') != render['lightmap_samples']:
            raise ValueError('Lightmap bake configuration mismatch')
        if render['device']=='CPU' and report.get('cycles_device')!='CPU':
            raise ValueError('Requested CPU was not used')
        if render['device']=='METAL' and (report.get('cycles_device')!='GPU' or not any(d.get('type')=='METAL' and d.get('enabled') for d in report.get('devices',[]))):
            raise ValueError('Requested Metal GPU was not used')
    return report


class CacheMiss(RuntimeError):
    pass


class Store:
    def __init__(self, root=None):
        self.root = Path(root or ROOT/'cache')

    @contextlib.contextmanager
    def writer_lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root/'.writer.lock').open('a+') as handle:
            try:
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('다른 준비 작업이 실행 중입니다. 완료 후 다시 실행하세요.')
            try: yield
            finally: fcntl.flock(handle,fcntl.LOCK_UN)

    def _load(self, directory, expected_key=None):
        try:
            manifest=json.loads((directory/'manifest.json').read_text())
            if manifest['status']!='ready' or (expected_key and manifest['key']!=expected_key):
                raise ValueError('Not a matching ready cache')
            for filename in ('eevee.png','cycles.png','render_report.json'):
                if digest(directory/filename)!=manifest['checksums'][filename]:
                    raise ValueError('Cache checksum mismatch')
            if manifest['report'] != json.loads((directory/'render_report.json').read_text()):
                raise ValueError('Cached report metadata mismatch')
            return manifest
        except (OSError,ValueError,KeyError,TypeError) as error:
            raise CacheMiss(str(error)) from error

    def lookup(self, request, fingerprint):
        key=cache_key(request,fingerprint)
        directory=self.root/'results'/key
        manifest=self._load(directory,key)
        if manifest['request']!=request or manifest['renderer_fingerprint']!=fingerprint:
            raise CacheMiss('Cache input mismatch')
        return directory,manifest

    def reference(self):
        directory=self.root/'references'/'v13'
        return directory,self._load(directory)

    def import_reference(self, source):
        source=Path(source).resolve()
        report=validate_report(source/'render_report.json')
        if report.get('scene_revision')!='v13-flat-polished-stone-ceiling':
            raise ValueError('Only the verified v13 reference can be imported')
        for filename in ('eevee.png','cycles.png'):
            validate_png(source/filename,report['resolution'])
        target=self.root/'references'/'v13'
        with self.writer_lock():
            if target.exists():
                self._load(target)
                if digest(source/'render_report.json')!=digest(target/'render_report.json'):
                    raise ValueError('Existing reference differs; preserve it and use a separate cache directory')
                return target
            target.parent.mkdir(parents=True,exist_ok=True)
            staging=target.with_name('v13-'+uuid.uuid4().hex+'.tmp');staging.mkdir()
            for filename in ('eevee.png','cycles.png','render_report.json'):
                shutil.copy2(source/filename,staging/filename)
            manifest={'schema_version':1,'status':'ready','kind':'historical_reference','key':'reference-v13',
                      'label':'기존 v13 참고 결과 · Eevee 512 / Cycles 24 samples',
                      'created_at':now(),'source':str(source),'report':report,
                      'timing_label':'사전 측정값 · 현재 프리셋과 다른 샘플 설정',
                      'checksums':{name:digest(staging/name) for name in ('eevee.png','cycles.png','render_report.json')}}
            write_json(staging/'manifest.json',manifest);staging.rename(target)
        return target

    def prepare(self, request, fingerprint, label, force=False, keep_work=False):
        # Caller holds the writer lock across the complete sequential batch.
        if not force:
            try:
                directory,manifest=self.lookup(request,fingerprint)
                return {'cache_hit':True,'directory':str(directory),'manifest':manifest}
            except CacheMiss: pass
        key=cache_key(request,fingerprint);job_id=now().replace(':','-')+'-'+uuid.uuid4().hex[:8]
        staging=self.root/'.staging'/job_id;staging.mkdir(parents=True)
        job_path=self.root/'jobs'/(job_id+'.json')
        job={'id':job_id,'key':key,'label':label,'status':'preparing','started_at':now(),
             'request':request,'work_directory':str(staging),'log':str(staging/'render.log')}
        write_json(job_path,job);write_json(staging/'request.json',request)
        started=time.perf_counter()
        try:
            if renderer_fingerprint()!=fingerprint:
                raise RuntimeError('장면 코드가 변경됐습니다. 새 설정으로 다시 실행하세요.')
            launch_renderer(request,staging,job,job_path)
            if renderer_fingerprint()!=fingerprint:
                raise RuntimeError('렌더 도중 장면 코드가 바뀌어 캐시를 등록하지 않았습니다.')
            report=validate_report(staging/'render_report.json',request)
            for filename in ('eevee.png','cycles.png'):
                validate_png(staging/filename,request['render']['resolution'])
            manifest={'schema_version':1,'kind':'preset','status':'ready','key':key,'label':label,
                      'created_at':now(),'request':request,'renderer_fingerprint':fingerprint,
                      'report':report,'job_seconds':round(time.perf_counter()-started,3),
                      'timing_label':'캐시 생성 시 측정한 시간 · 지금 렌더링한 시간이 아님',
                      'checksums':{name:digest(staging/name) for name in ('eevee.png','cycles.png','render_report.json')}}
            target=self.root/'results'/key;target.parent.mkdir(parents=True,exist_ok=True)
            publish=target.with_name(key+'.tmp-'+uuid.uuid4().hex);publish.mkdir()
            for filename in ('eevee.png','cycles.png','render_report.json','render.log','request.json'):
                shutil.copy2(staging/filename,publish/filename)
            write_json(publish/'manifest.json',manifest)
            if target.exists():
                archive=self.root/'archive'/(key+'-'+uuid.uuid4().hex[:8]);archive.parent.mkdir(parents=True,exist_ok=True)
                target.rename(archive)
            publish.rename(target)
            job.update(status='completed',finished_at=now(),result_directory=str(target),seconds=manifest['job_seconds'],log=str(target/'render.log'))
            write_json(job_path,job)
            if not keep_work: shutil.rmtree(staging)
            return {'cache_hit':False,'directory':str(target),'manifest':manifest}
        except BaseException as error:
            job.update(status='cancelled' if isinstance(error,KeyboardInterrupt) else 'failed',
                       error=str(error) or type(error).__name__,finished_at=now(),seconds=round(time.perf_counter()-started,3))
            write_json(job_path,job)
            raise


def find_blender():
    configured=os.environ.get('BLENDER_BIN')
    candidates=[Path(configured)] if configured else [Path('/Applications/Blender.app/Contents/MacOS/Blender')] + ([Path(found)] if (found := shutil.which('blender')) else [])
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate,os.X_OK): return str(candidate.resolve())
    raise RuntimeError('Blender 실행 파일을 찾지 못했습니다. BLENDER_BIN을 지정하세요.')


def check_graphics_access():
    if platform.system()=='Darwin':
        library=ctypes.CDLL('/System/Library/Frameworks/Metal.framework/Metal')
        library.MTLCreateSystemDefaultDevice.restype=ctypes.c_void_p
        if not library.MTLCreateSystemDefaultDevice():
            raise RuntimeError('현재 실행 세션에서 Metal 장치가 보이지 않습니다. Blender를 시작하지 않았습니다. 일반 macOS 터미널에서 ./lab.sh prepare를 실행하세요.')


def blender_args(request, directory):
    panel=request['panel'];light=request['lighting'];r=request['render']
    return ['-b','--factory-startup','--python-exit-code','1','--python',str(ROOT/'scene.py'),'--',
            '--output',str(directory),'--color',*[str(x) for x in panel['color_linear']],
            '--color-scope',light['color_scope'],'--panel-strength',str(panel['strength']),'--cove-power',str(light['cove_power_w_each']),
            '--corner-power',str(light['corner_power_w_each']),
            '--eevee-samples',str(r['eevee_samples']),'--cycles-samples',str(r['cycles_samples']),
            '--resolution',*[str(x) for x in r['resolution']],
            '--lightmap-resolution',str(r['lightmap_resolution']),'--lightmap-samples',str(r['lightmap_samples']),
            '--device',r['device']]


def launch_renderer(request, directory, job, job_path):
    check_graphics_access()  # Prevent the repeated Metal startup crashes seen earlier.
    binary=find_blender()
    job['status']='running';write_json(job_path,job)
    print('  렌더 시작 · 로그: '+str(directory/'render.log'),flush=True)
    with (directory/'render.log').open('w') as log:
        process=subprocess.Popen([binary]+blender_args(request,directory),stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT,text=True,bufsize=1,start_new_session=True)
        try:
            for line in process.stdout:
                log.write(line);log.flush()
                if line.startswith('LIGHT_LAB:'):
                    job['stage']=line.strip();write_json(job_path,job)
                    print('  '+line.strip(),flush=True)
            code=process.wait()
            if code:raise RuntimeError(f'Blender 종료 코드 {code}. 로그: {directory / "render.log"}')
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL);process.wait()
            raise


def catalog(store, config, fingerprint):
    rows=[]
    for identifier,item in presets(config).items():
        row={'id':identifier,'label':item['label'],'key':cache_key(item['request'],fingerprint)}
        try:
            directory,manifest=store.lookup(item['request'],fingerprint)
            row.update(status='ready',directory=str(directory),created_at=manifest['created_at'])
        except CacheMiss:row['status']='missing'
        rows.append(row)
    result={'presets':rows,'ready':sum(x['status']=='ready' for x in rows),'total':len(rows)}
    try:
        directory,manifest=store.reference()
        result['reference']={'id':'reference-v13','directory':str(directory),'label':manifest['label']}
    except CacheMiss:result['reference']=None
    return result


def show_result(store, request, fingerprint, reference=False):
    t=time.perf_counter()
    directory,manifest=store.reference() if reference else store.lookup(request,fingerprint)
    return {'source':'cached_precomputed_images','lookup_ms':round((time.perf_counter()-t)*1000,3),
            'directory':str(directory),'images':{x:str(directory/(x+'.png')) for x in ('eevee','cycles')},
            'timing_label':manifest['timing_label'],'historical_report':manifest['report']}


def main(argv=None):
    parser=argparse.ArgumentParser(description='LIGHT LAB · 프리셋 사전 생성과 결과 캐시')
    parser.add_argument('--config',type=Path,default=ROOT/'config/presets.json')
    parser.add_argument('--cache-dir',type=Path,default=ROOT/'cache')
    sub=parser.add_subparsers(dest='command',required=True)
    listing=sub.add_parser('list');listing.add_argument('--json',action='store_true')
    sub.add_parser('catalog')
    jobs=sub.add_parser('jobs');jobs.add_argument('--json',action='store_true')
    importing=sub.add_parser('import-reference');importing.add_argument('--source',type=Path,default=ROOT/'renders-v13')
    show=sub.add_parser('show');show.add_argument('preset');show.add_argument('--json',action='store_true')
    prepare=sub.add_parser('prepare');prepare.add_argument('presets',nargs='*');prepare.add_argument('--all',action='store_true');prepare.add_argument('--dry-run',action='store_true');prepare.add_argument('--force',action='store_true');prepare.add_argument('--keep-work',action='store_true')
    custom=sub.add_parser('render');custom.add_argument('--color',required=True);custom.add_argument('--level',type=int,default=3);custom.add_argument('--dry-run',action='store_true');custom.add_argument('--keep-work',action='store_true')
    args=parser.parse_args(argv)
    try:
        config=load_config(args.config);fingerprint=renderer_fingerprint();store=Store(args.cache_dir);available=presets(config)
        if args.command=='jobs':
            entries=[]
            for path in sorted((store.root/'jobs').glob('*.json'),reverse=True)[:10]:
                try:entries.append(json.loads(path.read_text()))
                except (OSError,ValueError):continue
            if args.json:print(json.dumps(entries,indent=2,ensure_ascii=False))
            else:
                if not entries:print('작업 기록이 없습니다.')
                for entry in entries:
                    print(entry['status']+' · '+entry['label']+' · '+entry.get('error',entry.get('stage','')))
                    print('  기록: '+str(store.root/'jobs'/(entry['id']+'.json')))
            return 0
        if args.command=='import-reference':
            print('참고 결과 등록: '+str(store.import_reference(args.source)));return 0
        if args.command in ('list','catalog'):
            result=catalog(store,config,fingerprint)
            if args.command=='catalog':
                write_json(store.root/'catalog.json',result);print(store.root/'catalog.json')
            elif args.json:print(json.dumps(result,indent=2,ensure_ascii=False))
            else:
                scope_label='전체 조명 색상 · 패널 밝기' if config['lighting'].get('color_scope')=='all' else '패널 색상/밝기'
                print(f"사전 생성 완료 {result['ready']}/{result['total']} · {scope_label} 프리셋")
                for row in result['presets']:print(f"{row['id']:12} {'준비됨' if row['status']=='ready' else '생성 대기':8} {row['label']}")
                if result['reference']:print('reference-v13 · 기존 참고 결과 있음 (512 / 24 samples)')
            return 0
        if args.command=='show':
            if args.preset!='reference-v13' and args.preset not in available:raise ValueError('Unknown preset ID')
            request=available[args.preset]['request'] if args.preset in available else None
            try:result=show_result(store,request,fingerprint,args.preset=='reference-v13')
            except CacheMiss:
                print('준비된 결과가 없습니다. 먼저 ./lab.sh prepare '+args.preset,file=sys.stderr);return 2
            if args.json:print(json.dumps(result,indent=2,ensure_ascii=False))
            else:
                print('사전 계산된 결과 · 조회 '+str(result['lookup_ms'])+'ms')
                print(result['timing_label'])
                for label,path in result['images'].items():print(label+': '+path)
                for row in result['historical_report']['renders']:print(row['engine']+': '+str(row['seconds'])+'초 (생성 당시)')
            return 0
        if args.command=='render':
            batch=[('custom',{'label':args.color+' · 밝기 '+str(args.level),'request':make_request(config,level=args.level,custom_color=args.color)})]
        else:
            if args.all and args.presets:raise ValueError('Choose preset IDs or --all')
            ids=list(available) if args.all else args.presets
            if not ids:raise ValueError('프리셋 ID 또는 --all이 필요합니다. 예: ./lab.sh prepare ice-3')
            if len(set(ids))!=len(ids) or any(x not in available for x in ids):raise ValueError('Unknown or duplicate preset ID')
            batch=[(identifier,available[identifier]) for identifier in ids]
        if args.dry_run:
            for identifier,item in batch:
                if getattr(args,'force',False):
                    print(identifier+' · 강제 새 렌더 · '+cache_key(item['request'],fingerprint));continue
                try:store.lookup(item['request'],fingerprint);state='캐시 재사용'
                except CacheMiss:state='새 렌더 필요'
                print(identifier+' · '+state+' · '+cache_key(item['request'],fingerprint))
            print('실제 렌더링은 실행하지 않았습니다.');return 0
        with store.writer_lock():
            for index,(identifier,item) in enumerate(batch,1):
                print(f'[{index}/{len(batch)}] {identifier} · {item["label"]}',flush=True)
                result=store.prepare(item['request'],fingerprint,item['label'],getattr(args,'force',False),args.keep_work)
                print(('캐시 재사용: ' if result['cache_hit'] else '생성 완료: ')+result['directory'],flush=True)
        write_json(store.root/'catalog.json',catalog(store,config,fingerprint))
        return 0
    except (OSError,ValueError,KeyError,TypeError,RuntimeError) as error:
        print('오류: '+str(error),file=sys.stderr);return 1
    except KeyboardInterrupt:
        print('취소했습니다. 작업 기록과 로그는 보존했습니다.',file=sys.stderr);return 130


if __name__=='__main__':
    raise SystemExit(main())
