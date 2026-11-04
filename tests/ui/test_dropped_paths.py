from __future__ import annotations

import unittest

try:
    from samyuj.ui.gtk.assets import extract_dropped_paths
    _HAVE_GTK = True
except Exception:
    extract_dropped_paths = None
    _HAVE_GTK = False

class _FakeSelection:
    def __init__(self, uris=None, text=""):
        self._uris = uris
        self._text = text

    def get_uris(self):
        if self._uris is None:
            raise Exception("no uri target")
        return self._uris

    def get_text(self):
        return self._text

@unittest.skipUnless(_HAVE_GTK, "GTK (gi) not available in this environment")
class ExtractDroppedPathsTest(unittest.TestCase):
    def test_multiple_uris(self) -> None:
        sel = _FakeSelection(uris=["file:///a/one.txt", "file:///a/two.txt"])
        self.assertEqual(extract_dropped_paths(sel), ["/a/one.txt", "/a/two.txt"])

    def test_percent_decoding(self) -> None:
        sel = _FakeSelection(uris=["file:///home/me/My%20Docs/r%C3%A9sum%C3%A9.pdf"])
        self.assertEqual(extract_dropped_paths(sel),
                         ["/home/me/My Docs/résumé.pdf"])

    def test_text_fallback_newline_separated(self) -> None:
        sel = _FakeSelection(text="file:///a/one.txt\nfile:///a/two.txt\n")
        self.assertEqual(extract_dropped_paths(sel), ["/a/one.txt", "/a/two.txt"])

    def test_plain_paths_in_text(self) -> None:
        sel = _FakeSelection(text="/a/one.txt\n/a/two.txt")
        self.assertEqual(extract_dropped_paths(sel), ["/a/one.txt", "/a/two.txt"])

    def test_non_local_and_duplicates_dropped(self) -> None:
        sel = _FakeSelection(
            uris=["https://example.com/x", "file:///a/one.txt", "file:///a/one.txt"])
        self.assertEqual(extract_dropped_paths(sel), ["/a/one.txt"])

    def test_empty_selection(self) -> None:
        self.assertEqual(extract_dropped_paths(_FakeSelection(uris=[])), [])
        self.assertEqual(extract_dropped_paths(_FakeSelection(text="")), [])

if __name__ == "__main__":
    unittest.main()
