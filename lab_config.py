"""Validated, renderer-independent experiment settings and stable cache keys."""
import copy
import hashlib
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_FILES = ('scene.py', 'lightmaps.py', 'oidn_filter.py', 'lab_config.py')


def number(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + ' must be a finite number')
    if not minimum <= value <= maximum:
        raise ValueError(name + ' is outside the allowed range')
    return value


def integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(name + ' must be an integer')
    return number(value, name, minimum, maximum)


def srgb_to_linear(hex_color):
    if not isinstance(hex_color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', hex_color):
        raise ValueError('Color must be #RRGGBB')
    def channel(v):
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return [channel(int(hex_color[i:i+2], 16) / 255) for i in (1, 3, 5)]


def load_config(path=None):
    config = json.loads(Path(path or ROOT / 'config/presets.json').read_text())
    if config.get('schema_version') != 1 or config.get('scene') != 'gallery-v13-flat-polished-stone':
        raise ValueError('Unsupported preset schema or scene')
    if config.get('blender_version') != '4.5.15 LTS':
        raise ValueError('This stage supports Blender 4.5.15 LTS')
    ids = set()
    if not config.get('colors'):
        raise ValueError('At least one color is required')
    for color in config['colors']:
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', color['id']) or color['id'] in ids:
            raise ValueError('Invalid or duplicate color ID')
        ids.add(color['id']); srgb_to_linear(color['srgb'])
    levels = set()
    for item in config['brightness_levels']:
        integer(item['level'], 'brightness level', 1, 20)
        number(item['multiplier'], 'brightness multiplier', 0, 10)
        if item['level'] in levels:
            raise ValueError('Duplicate brightness level')
        levels.add(item['level'])
    if not levels:
        raise ValueError('At least one brightness level is required')
    if config['lighting'].get('color_scope','panel') not in ('all','panel'):
        raise ValueError('color_scope must be all or panel')
    for key in ('panel_base_strength', 'cove_power_w_each', 'corner_power_w_each'):
        number(config['lighting'][key], key, 0, 100000)
    render = config['render']
    if len(render['resolution']) != 2:
        raise ValueError('Resolution needs width and height')
    for dim in render['resolution']:
        integer(dim, 'resolution', 16, 4096)
    for key in ('eevee_samples', 'cycles_samples', 'lightmap_samples'):
        integer(render[key], key, 1, 8192)
    if render['lightmap_resolution'] not in [128, 256, 512, 1024, 2048]:
        raise ValueError('Unsupported lightmap resolution')
    if render['device'] not in ('METAL', 'CPU'):
        raise ValueError('Unsupported Cycles device')
    if render['eevee_raytracing'] is not False or render['cycles_denoise'] is not True:
        raise ValueError('Comparison requires Eevee RT OFF and Cycles denoise ON')
    if render['seed'] != 42 or render['exposure'] != 0.3 or render['view_transform'] != 'AgX':
        raise ValueError('This scene currently fixes seed 42, exposure 0.3 and AgX')
    return config


def make_request(config, color_id=None, level=3, custom_color=None):
    levels = {x['level']: x['multiplier'] for x in config['brightness_levels']}
    if level not in levels:
        raise ValueError('Unknown brightness level: ' + str(level))
    if custom_color is None:
        colors = {x['id']: x for x in config['colors']}
        if color_id not in colors:
            raise ValueError('Unknown color: ' + str(color_id))
        color = colors[color_id]['srgb']
    else:
        color = custom_color
    linear = srgb_to_linear(color)
    number(config['lighting']['panel_base_strength'] * levels[level], 'panel strength', 0, 100000)
    return {'schema_version': 1, 'scene': config['scene'], 'blender_version': config['blender_version'],
            'panel': {'color_linear': linear,
                      'strength': config['lighting']['panel_base_strength'] * levels[level]},
            'lighting': {'color_scope':config['lighting'].get('color_scope','panel'),
                         'cove_power_w_each': config['lighting']['cove_power_w_each'],
                         'corner_power_w_each': config['lighting']['corner_power_w_each']},
            'render': copy.deepcopy(config['render'])}


def presets(config):
    return {f"{color['id']}-{item['level']}":
            {'label': f"{color['label']} · 밝기 {item['level']}",
             'request': make_request(config, color['id'], item['level'])}
            for color in config['colors'] for item in config['brightness_levels']}


def renderer_fingerprint(root=ROOT):
    return hashlib.sha256(b''.join(name.encode() + (root/name).read_bytes() for name in SOURCE_FILES)).hexdigest()


def cache_key(request, fingerprint):
    raw = json.dumps({'request': request, 'renderer': fingerprint}, sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()
