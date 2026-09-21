import ctypes as C
import mmap
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'launcher'))
from photo_worker import bridge_api, extent, ROOT


@unittest.skipUnless((ROOT/'runtime/libphoto_bridge.so').exists(), 'Build photo bridge first')
class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)/'shm.bin'
        self.lib = bridge_api()
        self.handle = self.lib.photo_open(bytes(self.path))
        self.assertTrue(self.handle)
        self.file = self.path.open('r+b')
        self.memory = mmap.mmap(self.file.fileno(), 0)

    def tearDown(self):
        self.memory.close(); self.file.close()
        self.lib.photo_close(self.handle)
        self.directory.cleanup()

    def read(self, offset):
        return struct.unpack_from('<I', self.memory, offset)[0]

    def test_frame_copy_and_publish_order(self):
        width, height = 64, 64
        image = bytes((i % 256 for i in range(width*height*4)))
        self.memory[65536:65536+len(image)] = image
        struct.pack_into('<II', self.memory, 16, width, height)
        struct.pack_into('<I', self.memory, 8, 1)
        buffer = C.create_string_buffer(len(image))
        w, h = C.c_uint32(), C.c_uint32()
        seq = self.lib.photo_take(self.handle, buffer, len(image), C.byref(w), C.byref(h))
        self.assertEqual((seq,w.value,h.value), (1,width,height))
        self.assertEqual(buffer.raw, image)
        self.assertEqual(self.read(12), 0, 'Reading must not acknowledge an unfinished frame')
        self.lib.photo_answer(self.handle, seq, buffer, width, height, 20.5)
        offset = 65536 + 7680*4320*8
        self.assertEqual(self.memory[offset:offset+len(image)], image)
        self.assertEqual(self.read(12), 1)
        self.assertEqual(self.read(1872), 1)
        self.assertEqual(self.lib.photo_take(self.handle, buffer, len(image), C.byref(w), C.byref(h)),0)

    def test_invalid_frame_is_acknowledged_without_success(self):
        struct.pack_into('<II', self.memory, 16, 99999, 99999)
        struct.pack_into('<I', self.memory, 8, 1)
        w,h=C.c_uint32(),C.c_uint32()
        buffer=C.create_string_buffer(16)
        self.assertEqual(self.lib.photo_take(self.handle,buffer,16,C.byref(w),C.byref(h)),0)
        self.assertEqual(self.read(12),1)
        self.assertEqual(self.read(1872),0)

    def test_only_one_worker_can_own_the_transport(self):
        self.assertFalse(self.lib.photo_open(bytes(self.path)))

    def test_extent_preserves_aspect_and_latent_alignment(self):
        self.assertEqual(extent(1920,1080,384),(384,216))
        self.assertEqual(extent(1080,1920,384),(216,384))
        self.assertEqual(extent(2560,1440,256),(256,144))


if __name__ == '__main__':
    unittest.main()
