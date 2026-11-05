import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from approx_lighting import load_lights,ambient_specs,evaluate_ambient,GROUPS
ROOT=Path(__file__).resolve().parents[1]

class ApproximationTests(unittest.TestCase):
    def setUp(self):
        self.lights=load_lights(ROOT/'config/approx-preview.json')
        self.specs=ambient_specs(self.lights,12,-1.5,5)

    def test_all_five_groups_are_independent(self):
        self.assertEqual(set(self.lights),set(GROUPS))
        self.assertNotEqual(self.lights['cove_left']['color_linear'],self.lights['cove_right']['color_linear'])
        self.assertNotEqual(self.lights['corner_left']['color_linear'],self.lights['corner_right']['color_linear'])

    def test_all_sources_off_means_no_ambient(self):
        for key in self.lights:
            self.lights[key]['strength' if key=='panel' else 'power_w']=0
        specs=ambient_specs(self.lights,12,-1.5,5)
        self.assertEqual(evaluate_ambient((0,0,0),self.lights,specs),[0,0,0])

    def test_global_gain_is_linear(self):
        base=evaluate_ambient((0,0,0),self.lights,self.specs)
        doubled=evaluate_ambient((0,0,0),self.lights,self.specs,2)
        for a,b in zip(base,doubled):self.assertAlmostEqual(2*a,b)

    def test_blue_field_is_stronger_on_left(self):
        for key in self.lights:
            if key!='cove_left':self.lights[key]['strength' if key=='panel' else 'power_w']=0
        specs=ambient_specs(self.lights,12,-1.5,5)
        left=evaluate_ambient((-5,-1.5,1),self.lights,specs)
        right=evaluate_ambient((5,-1.5,1),self.lights,specs)
        self.assertGreater(left[2],right[2])

    def test_invalid_configuration_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bad.json';path.write_text(json.dumps({'panel':{}}))
            with self.assertRaises(ValueError):load_lights(path)

if __name__=='__main__':unittest.main()
