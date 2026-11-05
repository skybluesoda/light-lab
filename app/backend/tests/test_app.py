"""API/queue behavior with synthetic artifacts in temp folders, never Blender."""
import copy
import json
from pathlib import Path
import struct
import sys
import threading
import time
from unittest import mock
import zlib
import pytest
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from service import RenderService,RenderRequest,default_request,RESOLUTIONS,request_key
from main import create_app
from lab_config import srgb_to_linear
from preset_lab import write_json


def png(width,height):
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    raw=(b'\0'+b'\x66\x88\xaa'*width)*height
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')


def result(service,identifier,request):
    directory=service.directory(identifier)
    for engine in request.engines:(directory/(engine+'.png')).write_bytes(png(*RESOLUTIONS[request.resolution]))
    lights={key:{'color_linear':srgb_to_linear(value['color_srgb']),
                 'strength' if key=='panel' else 'power_w':value['strength' if key=='panel' else 'power_w']} for key,value in request.light_config().items()}
    report={'eevee_raytracing':False,'eevee_fast_gi':False,'indirect_mode':'approx','resolution':RESOLUTIONS[request.resolution],
            'eevee_render_samples':request.samples,'cycles_samples':request.samples,'independent_lights':lights,
            'cycles_device':'GPU','ambient_approximation':{'lightmaps':False},'lightmap_bake':{'skipped':True},
            'renders':[{'engine':'BLENDER_EEVEE_NEXT' if engine=='eevee' else 'CYCLES','success':True,'seconds':1.23} for engine in request.engines]}
    write_json(directory/'render_report.json',report)
    (directory/'render.log').write_text('TEST FIXTURE ONLY, NOT A BLENDER RENDER')


def wait(service,identifier):
    end=time.monotonic()+3
    while time.monotonic()<end:
        job=service.get(identifier)
        if job['status'] in ('completed','failed','cancelled'):return job
        time.sleep(.01)
    raise AssertionError('job timed out')

@pytest.fixture
def service(tmp_path):
    s=RenderService(tmp_path/'app-data');s.renderer=lambda identifier,request:result(s,identifier,request)
    return s

@pytest.fixture
def client(service):
    with TestClient(create_app(service),base_url='http://localhost') as client:yield client


def test_bootstrap_has_five_independent_defaults_and_example(client):
    response=client.get('/api/bootstrap');assert response.status_code==200
    body=response.json();assert len(body['defaults']['lights'])==5
    assert body['defaults']['lights']['cove_left']['color_srgb']!=body['defaults']['lights']['cove_right']['color_srgb']
    assert body['example']['images']==['eevee']
    assert 'cycles' not in body['example']['measurements']


def test_actual_example_asset_served_without_new_render(client):
    response=client.get('/api/jobs/example/images/eevee.png')
    assert response.status_code==200 and response.content.startswith(b'\x89PNG')
    assert client.get('/api/jobs/example/images/cycles.png').status_code==404


def test_queue_complete_and_cache_hit(client,service):
    request=default_request().model_dump()
    first=client.post('/api/jobs',json=request);assert first.status_code==202
    job=wait(service,first.json()['id']);assert job['status']=='completed'
    assert job['images']==['eevee','cycles']
    for engine in job['images']:assert client.get(f'/api/jobs/{job["id"]}/images/{engine}.png').status_code==200
    second=client.post('/api/jobs',json=request).json()
    assert second['id']==job['id'] and second['cache_hit'] is True
    assert len(service.jobs)==1


def test_independent_change_is_new_job(client,service):
    payload=default_request().model_dump();one=client.post('/api/jobs',json=payload).json();wait(service,one['id'])
    payload['lights']['cove_left']['color_srgb']='#FF0000'
    two=client.post('/api/jobs',json=payload).json();assert two['id']!=one['id']
    assert wait(service,two['id'])['status']=='completed'


def test_invalid_colors_missing_light_nan_and_excess_power_rejected(client):
    for mutate in [lambda d:d['lights']['panel'].update(color_srgb='bad'),
                   lambda d:d['lights'].pop('corner_right'),
                   lambda d:d['lights']['corner_left'].update(intensity=999),
                   lambda d:d.update(resolution='8k'),lambda d:d.update(samples=0)]:
        data=default_request().model_dump();mutate(data)
        assert client.post('/api/jobs',json=data).status_code==422
    assert client.post('/api/jobs',content='{"lights":NaN}',headers={'Content-Type':'application/json'}).status_code==422


def test_all_off_maps_to_zero_and_same_cache_identity():
    one=default_request();two=default_request()
    for light in one.lights.values():light.enabled=False
    for light in two.lights.values():light.intensity=0
    assert all(item.get('strength',item.get('power_w'))==0 for item in one.light_config().values())
    assert request_key(one)==request_key(two)


def test_cancel_queued_and_serial_worker(client,service):
    entered=threading.Event();release=threading.Event();calls=[]
    def slow(identifier,request):
        calls.append(identifier);entered.set();release.wait(2);result(service,identifier,request)
    service.renderer=slow
    first=client.post('/api/jobs',json=default_request().model_dump()).json()
    assert entered.wait(1)
    payload=default_request().model_dump();payload['samples']=64
    second=client.post('/api/jobs',json=payload).json()
    assert second['status']=='queued' and len(calls)==1
    assert client.delete('/api/jobs/'+second['id']).json()['status']=='cancelled'
    release.set();assert wait(service,first['id'])['status']=='completed'
    time.sleep(.03);assert len(calls)==1


def test_failed_render_preserves_error_and_log(client,service):
    def fail(identifier,request):
        (service.directory(identifier)/'render.log').write_text('specific failure evidence')
        raise RuntimeError('GPU unavailable for test')
    service.renderer=fail
    response=client.post('/api/jobs',json=default_request().model_dump()).json()
    job=wait(service,response['id']);assert job['status']=='failed' and 'GPU unavailable' in job['error']
    assert 'failure evidence' in client.get(f'/api/jobs/{job["id"]}/log').text
    assert client.get(f'/api/jobs/{job["id"]}/images/eevee.png').status_code==404


def test_cross_origin_mutation_and_bad_host_rejected(client):
    assert client.post('/api/jobs',json=default_request().model_dump(),headers={'Origin':'https://other.example'}).status_code==403
    assert client.get('/api/health',headers={'Host':'other.example'}).status_code==400
    assert client.get('/api/jobs/not-a-valid-id').status_code==404
    assert client.get('/api/jobs/example/images/private.png').status_code==404


def test_wrong_render_settings_rejected(client,service):
    def wrong(identifier,request):
        result(service,identifier,request);path=service.directory(identifier)/'render_report.json'
        report=json.loads(path.read_text());report['eevee_raytracing']=True;write_json(path,report)
    service.renderer=wrong
    job=client.post('/api/jobs',json=default_request().model_dump()).json()
    assert wait(service,job['id'])['status']=='failed'


def test_corrupted_cache_not_reused(client,service):
    payload=default_request().model_dump();first=client.post('/api/jobs',json=payload).json();wait(service,first['id'])
    (service.directory(first['id'])/'eevee.png').write_bytes(b'corrupt')
    second=client.post('/api/jobs',json=payload).json();assert second['id']!=first['id']
    assert wait(service,second['id'])['status']=='completed'


def test_second_server_cannot_modify_active_job_state(tmp_path):
    s=RenderService(tmp_path/'locked');s.start()
    try:
        second=RenderService(tmp_path/'locked')
        with pytest.raises(RuntimeError):second.start()
    finally:s.close()


def test_persisted_result_available_after_restart(tmp_path):
    s=RenderService(tmp_path/'persist');s.renderer=lambda identifier,request:result(s,identifier,request);s.start()
    job=s.submit(default_request());assert wait(s,job['id'])['status']=='completed';s.close()
    restarted=RenderService(tmp_path/'persist');restarted.start()
    try:
        hit=restarted.submit(default_request());assert hit['id']==job['id'] and hit['cache_hit']
    finally:restarted.close()


def test_gpu_preflight_failure_never_starts_blender(client,service):
    service.renderer=service._render
    with mock.patch('service.check_graphics_access',side_effect=RuntimeError('GPU not visible')), mock.patch('service.subprocess.Popen') as process:
        job=client.post('/api/jobs',json=default_request().model_dump()).json()
        assert wait(service,job['id'])['status']=='failed'
        process.assert_not_called()


def test_partial_engine_result_is_available_while_next_runs(client,service):
    entered=threading.Event();release=threading.Event()
    def partial(identifier,request):
        result(service,identifier,request)
        path=service.directory(identifier)/'render_report.json';report=json.loads(path.read_text())
        report['renders']=report['renders'][:1];write_json(path,report)
        service.progress(identifier,'Cycles 렌더 중');entered.set();release.wait(2)
        result(service,identifier,request)
    service.renderer=partial
    job=client.post('/api/jobs',json=default_request().model_dump()).json();assert entered.wait(1)
    current=client.get('/api/jobs/'+job['id']).json()
    assert current['images']==['eevee'] and current['status']=='running'
    assert client.get(f'/api/jobs/{job["id"]}/images/eevee.png').status_code==200
    assert client.get(f'/api/jobs/{job["id"]}/images/cycles.png').status_code==404
    release.set();assert wait(service,job['id'])['status']=='completed'


def test_same_in_progress_request_is_not_duplicated(client,service):
    entered=threading.Event();release=threading.Event()
    def slow(identifier,request):entered.set();release.wait(2);result(service,identifier,request)
    service.renderer=slow
    payload=default_request().model_dump();first=client.post('/api/jobs',json=payload).json();assert entered.wait(1)
    second=client.post('/api/jobs',json=payload).json()
    assert first['id']==second['id'] and len(service.jobs)==1
    release.set();assert wait(service,first['id'])['status']=='completed'


def test_settings_roundtrip_validation_and_restart(client,service):
    document={'format_version':1,'name':'따뜻한 갤러리','request':default_request().model_dump()}
    document['request']['lights']['corner_right']['enabled']=False
    saved=client.post('/api/settings',json=document);assert saved.status_code==201
    identifier=saved.json()['id']
    exported=client.get(f'/api/settings/{identifier}/download')
    assert exported.json()==document and 'attachment' in exported.headers['Content-Disposition']
    assert client.get('/api/settings').json()[0]['request']['lights']['corner_right']['enabled'] is False
    restarted=RenderService(service.data);assert restarted.settings()[0]['id']==identifier
    bad=copy.deepcopy(document);bad['request']['lights']['panel']['color_srgb']='bad'
    assert client.post('/api/settings',json=bad).status_code==422
    assert client.post('/api/settings',json={**document,'format_version':2}).status_code==422
    assert client.get('/api/settings/invalid/download').status_code==404


def test_zip_contains_both_distinct_images_and_reusable_settings(client,service):
    import io,zipfile,csv
    payload=default_request().model_dump();job=client.post('/api/jobs',json=payload).json();wait(service,job['id'])
    response=client.get(f'/api/jobs/{job["id"]}/download');assert response.status_code==200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert {'eevee.png','cycles.png','settings.json','render_report.json','measurements.csv'}<=set(archive.namelist())
        for engine in ('eevee','cycles'):assert archive.read(engine+'.png')==service.image(job['id'],engine).read_bytes()
        document=json.loads(archive.read('settings.json'));assert document['request']==payload
        assert client.post('/api/settings',json=document).status_code==201
        rows=list(csv.DictReader(io.StringIO(archive.read('measurements.csv').decode('utf-8-sig'))));assert len(rows)==2
    (service.directory(job['id'])/'cycles.png').write_bytes(b'corrupt')
    assert client.get(f'/api/jobs/{job["id"]}/download').status_code==409
    assert client.get('/api/jobs/example/download').status_code==409


def test_fresh_measurement_bypasses_completed_and_pending_cache(client,service):
    payload=default_request().model_dump();first=client.post('/api/jobs',json=payload).json();wait(service,first['id'])
    second=client.post('/api/jobs?fresh=true',json=payload).json()
    assert first['id']!=second['id'] and second['cache_hit'] is False
    assert wait(service,second['id'])['status']=='completed'


def wait_benchmark(service,identifier):
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        record=service.benchmark(identifier)
        if record['status']!='running':return record
        time.sleep(.01)
    raise AssertionError('benchmark timed out')


def test_benchmark_new_runs_excludes_warmup_and_persists(client,service):
    import csv,io
    payload=default_request().model_dump();cached=client.post('/api/jobs',json=payload).json();wait(service,cached['id'])
    calls=[]
    def timed(identifier,request):
        calls.append(identifier);result(service,identifier,request)
        path=service.directory(identifier)/'render_report.json';report=json.loads(path.read_text())
        for row in report['renders']:row['seconds']=[99,4,2,3][len(calls)-1]*(2 if row['engine']=='CYCLES' else 1)
        write_json(path,report)
    service.renderer=timed
    response=client.post('/api/benchmarks',json=payload);assert response.status_code==202
    record=wait_benchmark(service,response.json()['id'])
    assert len(calls)==4 and cached['id'] not in calls and len(set(calls))==4
    assert record['completed_count']==4 and record['statistics']['eevee']=={'min':2,'median':3,'max':4,'count':3}
    assert record['statistics']['cycles']['median']==6
    csv_response=client.get(f'/api/benchmarks/{record["id"]}/download')
    rows=list(csv.DictReader(io.StringIO(csv_response.content.decode('utf-8-sig'))))
    assert len(rows)==8 and all(row['included_in_statistics']=='False' for row in rows[:2])
    assert all(row['included_in_statistics']=='True' for row in rows[2:])
    restarted=RenderService(service.data);assert restarted.benchmarks()[0]['statistics']==record['statistics']


def test_benchmark_failure_stops_remaining_jobs_without_summary(client,service):
    calls=[]
    def fail(identifier,request):calls.append(identifier);raise RuntimeError('render failed in test')
    service.renderer=fail
    record=client.post('/api/benchmarks',json=default_request().model_dump()).json()
    final=wait_benchmark(service,record['id'])
    assert final['status']=='failed' and len(calls)==1 and final['statistics']=={}
    assert [r['status'] for r in final['runs']]==['failed','cancelled','cancelled','cancelled']


def test_cancel_benchmark_stops_queue_and_does_not_publish_statistics(client,service):
    entered=threading.Event();release=threading.Event();calls=[]
    def slow(identifier,request):calls.append(identifier);entered.set();release.wait(2);result(service,identifier,request)
    service.renderer=slow
    record=client.post('/api/benchmarks',json=default_request().model_dump()).json();assert entered.wait(1)
    cancelled=client.delete('/api/benchmarks/'+record['id']).json()
    assert cancelled['status']=='cancelled' and cancelled['statistics']=={}
    assert all(run['status']=='cancelled' for run in cancelled['runs'])
    release.set();time.sleep(.04);assert len(calls)==1


def test_benchmark_requires_both_engines_and_idle_queue(client,service):
    payload=default_request().model_dump();payload['engines']=['eevee']
    assert client.post('/api/benchmarks',json=payload).status_code==409
    entered=threading.Event();release=threading.Event()
    def slow(identifier,request):entered.set();release.wait(2);result(service,identifier,request)
    service.renderer=slow
    job=client.post('/api/jobs',json=default_request().model_dump()).json();assert entered.wait(1)
    assert client.post('/api/benchmarks',json=default_request().model_dump()).status_code==409
    release.set();wait(service,job['id'])


def test_interrupted_benchmark_recovers_without_repeating_renders(tmp_path):
    original=RenderService(tmp_path/'interrupted')
    record=original.start_benchmark(default_request())  # Persisted queue; worker never started.
    restarted=RenderService(original.data);restarted.start()
    try:
        restored=restarted.benchmark(record['id'])
        assert restored['status']=='failed' and restored['statistics']=={}
        assert all(run['status']=='failed' for run in restored['runs'])
    finally:restarted.close()


def test_fhd_request_exports_full_resolution_fixture(client,service):
    import io,zipfile
    payload=default_request().model_dump();payload['resolution']='fhd';payload['samples']=64
    job=client.post('/api/jobs',json=payload).json();completed=wait(service,job['id'])
    assert completed['status']=='completed' and completed['resolution_pixels']==[1920,1080]
    with zipfile.ZipFile(io.BytesIO(client.get(f'/api/jobs/{job["id"]}/download').content)) as archive:
        assert json.loads(archive.read('render_report.json'))['resolution']==[1920,1080]


def test_example_resolution_matches_recorded_pixels(client):
    example = client.get('/api/bootstrap').json()['example']
    assert RESOLUTIONS[example['request']['resolution']] == example['resolution_pixels']
