"""Behavior checks use synthetic PNGs in temporary folders; never launch Blender."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import lab_config as config
import preset_lab as lab


def png(width,height):
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    pixels=b''.join(b'\0'+b'\x70\x90\xb0'*width for _ in range(height))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')


def fixture_renderer(request,directory,job,job_path):
    r=request['render'];p=request['panel'];lights=request['lighting']
    for name in ('eevee.png','cycles.png'):(directory/name).write_bytes(png(*r['resolution']))
    (directory/'render.log').write_text('Synthetic test fixture; not a real render')
    report={'blender':request['blender_version'],'resolution':r['resolution'],
            'eevee_raytracing':False,'eevee_fast_gi':False,'cycles_adaptive_sampling':False,
            'eevee_render_samples':r['eevee_samples'],'cycles_samples':r['cycles_samples'],
            'panel_strength':p['strength'],'color_linear':p['color_linear'],
            'cove_power_w_each':lights['cove_power_w_each'],'corner_light_power_w_each':lights['corner_power_w_each'],
            'color_scope':lights['color_scope'],
            'light_sources':[{'color_linear':p['color_linear'] if lights['color_scope']=='all' else [1,.94,.85]} for _ in range(4)],
            'corner_emission_color_linear':p['color_linear'] if lights['color_scope']=='all' else [1,.94,.85],
            'cycles_device':'GPU','devices':[{'type':'METAL','enabled':True}],
            'lightmap_bake':{'success':True,'resolution_main':r['lightmap_resolution'],'samples':r['lightmap_samples']},
            'renders':[{'engine':e,'seconds':1.2,'success':True} for e in ('BLENDER_EEVEE_NEXT','CYCLES')]}
    lab.write_json(directory/'render_report.json',report)


class PresetTests(unittest.TestCase):
    def setUp(self):
        self.config=config.load_config()
        self.config['render']['resolution']=[16,16]
        self.request=config.make_request(self.config,'ice',3)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=lab.Store(Path(self.temp.name)/'cache')

    def prepare(self,**kwargs):
        with self.store.writer_lock(), mock.patch('preset_lab.renderer_fingerprint',return_value='renderer-a'):
            return self.store.prepare(self.request,'renderer-a','test',**kwargs)

    def test_40_presets_and_independent_requests(self):
        rows=config.presets(self.config)
        self.assertEqual(len(rows),40)
        rows['ice-3']['request']['render']['eevee_samples']=7
        self.assertEqual(rows['blue-3']['request']['render']['eevee_samples'],128)

    def test_srgb_conversion(self):
        self.assertEqual(config.srgb_to_linear('#000000'),[0,0,0])
        self.assertEqual(config.srgb_to_linear('#FFFFFF'),[1,1,1])
        self.assertAlmostEqual(config.srgb_to_linear('#808080')[0],0.2158605,places=6)
        with self.assertRaises(ValueError):config.srgb_to_linear('blue;anything')

    def test_brightness_applies_to_panel_only(self):
        low=config.make_request(self.config,'ice',1);high=config.make_request(self.config,'ice',5)
        self.assertEqual(high['panel']['strength']/low['panel']['strength'],8)
        self.assertEqual(low['lighting'],high['lighting'])

    def test_input_rejection(self):
        with self.assertRaises(ValueError):config.make_request(self.config,'ice',99)
        with self.assertRaises(ValueError):config.make_request(self.config,'missing',3)
        bad=copy.deepcopy(self.config);bad['render']['eevee_raytracing']=True
        path=Path(self.temp.name)/'bad.json';path.write_text(json.dumps(bad))
        with self.assertRaises(ValueError):config.load_config(path)
        with self.assertRaises(ValueError):config.number(float('nan'),'value',0,10)
        with self.assertRaises(ValueError):config.integer(True,'value',0,10)

    def test_cache_invalidation(self):
        base=config.cache_key(self.request,'renderer-a')
        for change in ('color','strength','samples','lightmap','power'):
            other=copy.deepcopy(self.request)
            if change=='color':other['panel']['color_linear'][0]=0.1
            if change=='strength':other['panel']['strength']=4
            if change=='samples':other['render']['cycles_samples']=64
            if change=='lightmap':other['render']['lightmap_samples']=128
            if change=='power':other['lighting']['cove_power_w_each']=1200
            self.assertNotEqual(base,config.cache_key(other,'renderer-a'))
        self.assertNotEqual(base,config.cache_key(self.request,'renderer-b'))

    @mock.patch('preset_lab.launch_renderer',side_effect=fixture_renderer)
    def test_cached_hit_never_launches_renderer(self,renderer):
        first=self.prepare();second=self.prepare()
        self.assertFalse(first['cache_hit']);self.assertTrue(second['cache_hit'])
        self.assertEqual(renderer.call_count,1)
        shown=lab.show_result(self.store,self.request,'renderer-a')
        self.assertEqual(shown['source'],'cached_precomputed_images')
        self.assertEqual(shown['historical_report']['renders'][0]['seconds'],1.2)

    @mock.patch('preset_lab.launch_renderer',side_effect=fixture_renderer)
    def test_corrupt_cache_not_served(self,_):
        result=self.prepare();(Path(result['directory'])/'eevee.png').write_bytes(b'corrupt')
        with self.assertRaises(lab.CacheMiss):self.store.lookup(self.request,'renderer-a')

    def test_truncated_png_rejected(self):
        path=Path(self.temp.name)/'partial.png';path.write_bytes(png(16,16)[:-10])
        with self.assertRaises(ValueError):lab.validate_png(path,[16,16])

    def test_wrong_report_not_published(self):
        def wrong(*args):
            fixture_renderer(*args);path=args[1]/'render_report.json'
            report=json.loads(path.read_text());report['cycles_samples']=999;lab.write_json(path,report)
        with mock.patch('preset_lab.launch_renderer',side_effect=wrong):
            with self.assertRaises(ValueError):self.prepare()
        self.assertFalse((self.store.root/'results').exists())
        job=json.loads(next((self.store.root/'jobs').glob('*.json')).read_text())
        self.assertEqual(job['status'],'failed')
        self.assertTrue(Path(job['work_directory']).exists())

    def test_missing_gpu_stops_before_process_launch(self):
        fake=mock.Mock();fake.MTLCreateSystemDefaultDevice.return_value=None
        with mock.patch('preset_lab.platform.system',return_value='Darwin'), mock.patch('preset_lab.ctypes.CDLL',return_value=fake), mock.patch('preset_lab.subprocess.Popen') as process:
            with self.assertRaises(RuntimeError):lab.launch_renderer(self.request,Path(self.temp.name),{},Path(self.temp.name)/'job.json')
            process.assert_not_called()

    @mock.patch('preset_lab.launch_renderer',side_effect=KeyboardInterrupt)
    def test_cancelled_job_keeps_record(self,_):
        with self.assertRaises(KeyboardInterrupt):self.prepare()
        job=json.loads(next((self.store.root/'jobs').glob('*.json')).read_text())
        self.assertEqual(job['status'],'cancelled')

    @mock.patch('preset_lab.launch_renderer',side_effect=fixture_renderer)
    def test_force_archives_previous_result(self,renderer):
        self.prepare();self.prepare(force=True)
        self.assertEqual(renderer.call_count,2)
        self.assertEqual(len(list((self.store.root/'archive').iterdir())),1)

    def test_second_writer_is_rejected(self):
        with self.store.writer_lock():
            with self.assertRaises(RuntimeError):
                with self.store.writer_lock():pass

    def test_source_changed_during_job_is_not_cached(self):
        with self.store.writer_lock(), mock.patch('preset_lab.renderer_fingerprint',side_effect=['renderer-a','renderer-b']), mock.patch('preset_lab.launch_renderer',side_effect=fixture_renderer):
            with self.assertRaises(RuntimeError):self.store.prepare(self.request,'renderer-a','test')
        self.assertFalse((self.store.root/'results').exists())

    def test_color_scope_changes_cache_identity(self):
        request=copy.deepcopy(self.request)
        request['lighting']['color_scope']='panel'
        self.assertNotEqual(config.cache_key(self.request,'same-source'),config.cache_key(request,'same-source'))

    def test_white_cove_result_rejected_for_linked_red_lights(self):
        def wrong(*args):
            fixture_renderer(*args);path=args[1]/'render_report.json'
            report=json.loads(path.read_text());report['light_sources'][0]['color_linear']=[1,1,1]
            lab.write_json(path,report)
        self.request=config.make_request(self.config,'red',3)
        with mock.patch('preset_lab.launch_renderer',side_effect=wrong):
            with self.assertRaises(ValueError):self.prepare()
        self.assertFalse((self.store.root/'results').exists())

    def test_renderer_argument_mapping(self):
        args=lab.blender_args(self.request,Path(self.temp.name))
        self.assertEqual(args[args.index('--eevee-samples')+1],'128')
        self.assertEqual(args[args.index('--cycles-samples')+1],'128')
        self.assertEqual(args[args.index('--panel-strength')+1],'3.0')
        self.assertNotIn('--no-raytracing',args)
        self.assertEqual(args[args.index('--color-scope')+1],'all')

    def test_reference_is_labelled_and_distinct_from_presets(self):
        source=Path(self.temp.name)/'legacy';source.mkdir()
        fixture_renderer(self.request,source,{},source/'job.json')
        report=json.loads((source/'render_report.json').read_text())
        report.update(scene_revision='v13-flat-polished-stone-ceiling',eevee_render_samples=512,cycles_samples=24)
        lab.write_json(source/'render_report.json',report)
        self.store.import_reference(source)
        result=lab.catalog(self.store,self.config,'renderer-a')
        self.assertEqual(result['ready'],0)
        self.assertEqual(result['reference']['id'],'reference-v13')


if __name__=='__main__':unittest.main()
