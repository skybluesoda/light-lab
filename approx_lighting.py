"""No-bake, artist-controlled diffuse bounce approximation for an empty gallery.

This is an explicitly nonphysical Eevee shading model. It adds a smooth,
source-colored ambient field; it does not trace visibility or bounce rays.
"""
import json
from pathlib import Path
from lab_config import srgb_to_linear, number

GROUPS = ('panel','cove_left','cove_right','corner_left','corner_right')


def load_lights(path):
    raw=json.loads(Path(path).read_text())
    if set(raw)!=set(GROUPS):
        raise ValueError('Light config must contain exactly: '+', '.join(GROUPS))
    result={}
    for name in GROUPS:
        item=raw[name];key='strength' if name=='panel' else 'power_w'
        if set(item)!={'color_srgb',key}:
            raise ValueError('Invalid settings for '+name)
        result[name]={'color_linear':srgb_to_linear(item['color_srgb']),
                      key:number(item[key],name+' '+key,0,100000)}
    return result


def ambient_specs(lights,width,center_y,back_y):
    # Gains are artistic exposure controls, not radiometric unit conversion.
    return [
      {'id':'cove_left','position':(-width/2+.7,center_y,3.85),'axis_scale':(1,.15,1),'range':10.,'gain':.35*lights['cove_left']['power_w']/1800},
      {'id':'cove_right','position':(width/2-.7,center_y,3.85),'axis_scale':(1,.15,1),'range':10.,'gain':.35*lights['cove_right']['power_w']/1800},
      {'id':'corner_left','position':(-width/2+.3,back_y-.3,2.),'axis_scale':(1,1,1),'range':4.,'gain':.07*lights['corner_left']['power_w']/40},
      {'id':'corner_right','position':(width/2-.3,back_y-.3,2.),'axis_scale':(1,1,1),'range':4.,'gain':.07*lights['corner_right']['power_w']/40},
      {'id':'panel','position':(0,back_y-.25,2.),'axis_scale':(1,1,1),'range':4.,'gain':.18*lights['panel']['strength']/3},
    ]


def evaluate_ambient(point,lights,specs,gain=1):
    """CPU reference for the exact field represented by the shader nodes."""
    rgb=[0.,0.,0.]
    for spec in specs:
        d2=sum(((p-q)*axis)**2 for p,q,axis in zip(point,spec['position'],spec['axis_scale']))
        factor=(.2+.8/(1+d2/spec['range']**2))*spec['gain']*gain
        for channel in range(3):rgb[channel]+=lights[spec['id']]['color_linear'][channel]*factor
    return rgb


def apply_independent_lights(scene,lights):
    """Apply physical light parameters identically before either engine renders."""
    for obj in scene.objects:
        if obj.type=='LIGHT':
            if obj.name.startswith('Hidden upward cove'):
                key='cove_left' if obj.location.x<0 else 'cove_right'
            elif obj.name.startswith('Recessed corner light'):
                key='corner_left' if obj.location.x<0 else 'corner_right'
            else:continue
            obj.data.color=lights[key]['color_linear'];obj.data.energy=lights[key]['power_w']
        elif obj.type=='MESH' and obj.name.startswith('Recessed vertical corner diffuser'):
            key='corner_left' if obj.location.x<0 else 'corner_right'
            material=obj.data.materials[0].copy();obj.data.materials[0]=material
            bs=material.node_tree.nodes.get('Principled BSDF')
            bs.inputs['Base Color'].default_value=(*lights[key]['color_linear'],1)
            bs.inputs['Emission Color'].default_value=(*lights[key]['color_linear'],1)
            bs.inputs['Emission Strength'].default_value=4*lights[key]['power_w']/40
    # Panel color/strength have already been passed into geometry construction.


def attach_ambient(materials,lights,specs,gain=1):
    """Preserve each Cycles output and add a distinct Eevee-only ambient shader."""
    for material in materials:
        n=material.node_tree.nodes;l=material.node_tree.links
        physical=next(node for node in n if node.type=='OUTPUT_MATERIAL')
        source=physical.inputs['Surface'].links[0].from_socket
        physical.target='CYCLES'
        output=n.new('ShaderNodeOutputMaterial');output.target='EEVEE'
        output.name='Eevee no-bake ambient approximation'
        geometry=n.new('ShaderNodeNewGeometry')
        total=n.new('ShaderNodeRGB');total.outputs[0].default_value=(0,0,0,1)
        accumulator=total.outputs[0]
        for spec in specs:
            delta=n.new('ShaderNodeVectorMath');delta.operation='SUBTRACT'
            l.new(geometry.outputs['Position'],delta.inputs[0]);delta.inputs[1].default_value=spec['position']
            stretch=n.new('ShaderNodeVectorMath');stretch.operation='MULTIPLY'
            l.new(delta.outputs['Vector'],stretch.inputs[0]);stretch.inputs[1].default_value=spec['axis_scale']
            squared=n.new('ShaderNodeVectorMath');squared.operation='DOT_PRODUCT'
            l.new(stretch.outputs['Vector'],squared.inputs[0]);l.new(stretch.outputs['Vector'],squared.inputs[1])
            divide=n.new('ShaderNodeMath');divide.operation='DIVIDE'
            l.new(squared.outputs['Value'],divide.inputs[0]);divide.inputs[1].default_value=spec['range']**2
            plus=n.new('ShaderNodeMath');plus.operation='ADD'
            l.new(divide.outputs[0],plus.inputs[0]);plus.inputs[1].default_value=1
            reciprocal=n.new('ShaderNodeMath');reciprocal.operation='DIVIDE'
            reciprocal.inputs[0].default_value=.8;l.new(plus.outputs[0],reciprocal.inputs[1])
            floor=n.new('ShaderNodeMath');floor.operation='ADD'
            l.new(reciprocal.outputs[0],floor.inputs[0]);floor.inputs[1].default_value=.2
            intensity=n.new('ShaderNodeMath');intensity.operation='MULTIPLY'
            l.new(floor.outputs[0],intensity.inputs[0]);intensity.inputs[1].default_value=spec['gain']*gain
            color=n.new('ShaderNodeMixRGB');color.blend_type='MULTIPLY';color.inputs[0].default_value=1
            color.inputs[1].default_value=(*lights[spec['id']]['color_linear'],1)
            l.new(intensity.outputs[0],color.inputs[2])
            add=n.new('ShaderNodeMixRGB');add.blend_type='ADD';add.inputs[0].default_value=1
            l.new(accumulator,add.inputs[1]);l.new(color.outputs[0],add.inputs[2]);accumulator=add.outputs[0]
        albedo=n.new('ShaderNodeMixRGB');albedo.blend_type='MULTIPLY';albedo.inputs[0].default_value=1
        bs=n.get('Principled BSDF');albedo.inputs[1].default_value=bs.inputs['Base Color'].default_value
        l.new(accumulator,albedo.inputs[2])
        emission=n.new('ShaderNodeEmission');emission.inputs['Strength'].default_value=1
        l.new(albedo.outputs[0],emission.inputs['Color'])
        combine=n.new('ShaderNodeAddShader');l.new(source,combine.inputs[0]);l.new(emission.outputs[0],combine.inputs[1])
        l.new(combine.outputs[0],output.inputs['Surface'])
