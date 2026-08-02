import unittest
from patterns.horizontal import horizontal
class Tests(unittest.TestCase):
 def test_layout(self):
  self.assertEqual(horizontal(list(range(10)),3,4,32,32),{0:(32,32),1:(181,32),2:(331,32),3:(480,32),4:(32,192),5:(181,192),6:(331,192),7:(480,192),8:(32,352),9:(480,352)})
 def test_center(self): self.assertEqual(horizontal([7],1,4,32,32),{7:(256,192)})
 def test_sort(self): self.assertEqual(horizontal([9,2,5],1,3,0,0),{2:(0,192),5:(256,192),9:(512,192)})
 def test_capacity(self):
  with self.assertRaises(ValueError): horizontal(range(5),2,2,32,32)
if __name__=="__main__":unittest.main()