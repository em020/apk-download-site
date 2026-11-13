import base64
import struct
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from publishing import read_icon

class EmbeddedIconTests(unittest.TestCase):
    def icon(self, href):
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><image width="72" height="72" href="{href}"/></svg>'
    def check(self, href):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'icon.svg'; p.write_text(self.icon(href)); return read_icon(p)
    def test_accepts_small_embedded_png(self):
        png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l6sAAAAASUVORK5CYII='
        self.assertIn(b'data:image/png;base64,',self.check('data:image/png;base64,'+png))
    def test_rejects_external_active_and_malformed_images(self):
        for href in ['https://example.com/a.png','data:image/svg+xml;base64,PHN2Zz4=','data:image/png;base64,abc!','data:image/png;base64,AAAA']:
            with self.subTest(href=href),self.assertRaises(ValueError): self.check(href)
    def test_rejects_oversized_png_dimensions(self):
        raw=b'\x89PNG\r\n\x1a\n'+b'\0\0\0\rIHDR'+struct.pack('>II',65535,65535)+b'\0'*9
        with self.assertRaises(ValueError): self.check('data:image/png;base64,'+base64.b64encode(raw).decode())
