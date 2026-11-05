"""Portable settings, verified exports, and serial fresh-render measurements."""
import copy
import csv
import io
import json
import statistics
import uuid
import zipfile
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

class SettingsDocument(BaseModel):
    model_config=ConfigDict(extra='forbid')
    format_version: Literal[1]=1
    name: str=Field(min_length=1,max_length=64,pattern=r'\S')
    request: 'RenderRequest'


def bind_models(request_model):
    SettingsDocument.model_rebuild(_types_namespace={'RenderRequest':request_model})


def json_bytes(value):return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()


def csv_bytes(rows):
    output=io.StringIO(newline='')
    writer=csv.DictWriter(output,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    return output.getvalue().encode('utf-8-sig')


class Experiments:
    def _document_path(self,kind,identifier):
        self.directory(identifier)  # Apply the same strict identifier validation.
        return self.data/kind/(identifier+'.json')

    def save_setting(self,document):
        from service import write_json,now
        record={**document.model_dump(),'id':uuid.uuid4().hex,'created_at':now()}
        with self.lock:write_json(self._document_path('settings',record['id']),record)
        return record

    def settings(self):
        records=[]
        for path in (self.data/'settings').glob('*.json'):
            try:
                record=json.loads(path.read_text());SettingsDocument.model_validate({k:record[k] for k in ('format_version','name','request')});records.append(record)
            except (ValueError,KeyError,OSError):continue
        return sorted(records,key=lambda x:x['created_at'],reverse=True)

    def setting(self,identifier):
        try:return json.loads(self._document_path('settings',identifier).read_text())
        except (OSError,ValueError):raise KeyError(identifier)

    def export_job(self,identifier):
        from service import RenderRequest
        with self.lock:
            job=self.get(identifier)
            if identifier=='example' or job['status']!='completed':raise RuntimeError('완료된 렌더 결과를 선택하세요.')
            if not self._valid_cached(job):raise RuntimeError('결과 파일이 손상되거나 없어 다운로드할 수 없습니다. 다시 렌더해 주세요.')
            report=self.validate_result(identifier,RenderRequest.model_validate(job['request']))
            output=io.BytesIO()
            rows=[{'engine':engine,'render_seconds':job['measurements'][engine],'samples':job['request']['samples'],
                   'width':job['resolution_pixels'][0],'height':job['resolution_pixels'][1],
                   'created_at':job['created_at'],'job_total_seconds':job.get('total_seconds','')} for engine in job['request']['engines']]
            with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
                for engine in job['request']['engines']:archive.writestr(engine+'.png',self.image(identifier,engine).read_bytes())
                archive.writestr('settings.json',json_bytes({'format_version':1,'name':'LIGHT LAB '+identifier[:8],'request':job['request']}))
                archive.writestr('render_report.json',json_bytes(report))
                archive.writestr('measurements.csv',csv_bytes(rows))
                archive.writestr('README.txt','LIGHT LAB\nImages and timings belong to this saved render. Opening a cache is not a new measurement.\nEevee: rasterization with indirect-light approximation. Cycles: path tracing.\nEqual sample settings do not imply equal image quality or computation.\n')
            return output.getvalue()

    def start_benchmark(self,request):
        from service import TERMINAL,write_json,now
        if request.engines!=['eevee','cycles']:raise RuntimeError('반복 비교에는 Eevee + Cycles를 선택하세요.')
        with self.lock:
            if any(j['status'] not in TERMINAL for j in self.jobs.values()):raise RuntimeError('진행 중인 작업이 끝난 뒤 반복 측정을 시작하세요.')
            identifier=uuid.uuid4().hex
            record={'id':identifier,'created_at':now(),'request':request.model_dump(),'status':'running','job_ids':[],
                    'protocol':{'warmup_runs':1,'measured_runs':3,'cache_reuse':False,'engine_order':['eevee','cycles'],
                                'process':'new Blender process per pair','metric':'render call seconds; not interactive FPS'}}
            # Holding the worker lock makes the four jobs an atomic serial group.
            for index in range(4):
                job=self.submit(request,force=True)
                actual=self.jobs[job['id']]
                actual.update(benchmark_id=identifier,benchmark_phase='warmup' if index==0 else 'measured',
                              label='반복 측정 · 준비' if index==0 else f'반복 측정 · {index}/3')
                self._save(actual);record['job_ids'].append(job['id'])
            write_json(self._document_path('benchmarks',identifier),record)
            return self.benchmark(identifier)

    def _refresh_benchmarks(self):
        from service import write_json,now,TERMINAL
        # Called under the worker lock before the next queued render can begin.
        for path in (self.data/'benchmarks').glob('*.json'):
            record=json.loads(path.read_text())
            if record['status'] in TERMINAL:continue
            jobs=[self.jobs[x] for x in record['job_ids']]
            failed=any(j['status']=='failed' for j in jobs)
            cancelled=any(j['status']=='cancelled' for j in jobs)
            if failed or cancelled:
                record.update(status='failed' if failed else 'cancelled',finished_at=now())
                for job in jobs:
                    if job['status']=='queued':job.update(status='cancelled',stage='측정 중단',finished_at=now());self._save(job)
                write_json(path,record)
            elif all(j['status']=='completed' for j in jobs):
                record.update(status='completed',finished_at=now());write_json(path,record)

    def benchmark(self,identifier):
        from service import TERMINAL
        with self.lock:
            try:record=json.loads(self._document_path('benchmarks',identifier).read_text())
            except (ValueError,OSError):raise KeyError(identifier)
            jobs=[copy.deepcopy(self.jobs[x]) for x in record['job_ids']]
            record['runs']=[{'id':j['id'],'phase':j['benchmark_phase'],'status':j['status'],
                             'measurements':j.get('measurements',{}),'error':j.get('error')} for j in jobs]
            record['completed_count']=sum(j['status']=='completed' for j in jobs)
            record['statistics']={}
            # Publish summary only when all three independent measured pairs completed.
            if record['status']=='completed':
                for engine in ('eevee','cycles'):
                    values=[j['measurements'][engine] for j in jobs[1:]]
                    record['statistics'][engine]={'min':min(values),'median':statistics.median(values),'max':max(values),'count':len(values)}
            return record

    def benchmarks(self):
        with self.lock:return [self.benchmark(path.stem) for path in sorted((self.data/'benchmarks').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)][:20]

    def cancel_benchmark(self,identifier):
        with self.lock:
            record=self.benchmark(identifier)
            identifiers=record['job_ids']
            # Cancel all queued runs before releasing the active process.
            for job_id in identifiers:
                job=self.jobs[job_id]
                if job['status']=='queued':job.update(status='cancelled',stage='측정 취소');self._save(job)
            active=next((x for x in identifiers if self.jobs[x]['status']=='running'),None)
        if active:self.cancel(active)
        with self.lock:self._refresh_benchmarks()
        return self.benchmark(identifier)

    def benchmark_csv(self,identifier):
        record=self.benchmark(identifier)
        rows=[]
        for index,run in enumerate(record['runs']):
            for engine in ('eevee','cycles'):
                rows.append({'run':index,'phase':run['phase'],'included_in_statistics':run['phase']=='measured' and record['status']=='completed',
                             'status':run['status'],'engine':engine,'render_seconds':run['measurements'].get(engine,''),
                             'job_id':run['id'],'resolution':record['request']['resolution'],'samples':record['request']['samples'],
                             'engine_order':'eevee then cycles','cache_reuse':False,'new_process_per_pair':True})
        return csv_bytes(rows)
