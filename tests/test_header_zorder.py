"""The header icons must stay the topmost siblings of the panel's content view.

AppKit delivers a click to the topmost view whose frame contains the point, and a
sibling's z position is simply the order it was added in. The three header icons are
built before everything else, so each later addSubview_ — the decorative surfaces and
the chat-title/status labels — puts a new view above them. That is not a cosmetic
detail: with the title field on top, clicking the eye switch selected the chat name
instead of toggling it, and only the sliver of the button outside the label's frame
worked. These tests pin the two properties that made that possible.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'src' / 'hud.py').read_text()
PANEL_W = 360


def _build():
    tree = ast.parse(SOURCE)
    controller = next(n for n in tree.body
                      if isinstance(n, ast.ClassDef) and n.name == 'HudController')
    return next(n for n in controller.body
                if isinstance(n, ast.FunctionDef) and n.name == '_build_panel')


def _subview_additions(node):
    """Every `view.addSubview_(X)` under `node`, as (line, col, source text of X)."""
    calls = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if not (isinstance(func, ast.Attribute) and func.attr == 'addSubview_'):
            continue
        if sub.args:
            calls.append((sub.lineno, sub.col_offset, ast.unparse(sub.args[0])))
    return [text for _line, _col, text in sorted(calls)]


def _eval(node):
    return eval(compile(ast.Expression(body=node), '<hud>', 'eval'), {'PANEL_W': PANEL_W})


class HeaderZOrderTest(unittest.TestCase):
    def test_icons_are_added_last(self):
        build = _build()
        added = _subview_additions(build)
        self.assertEqual(
            added[-1], 'button',
            'the header icons must be re-added after every other subview, or the title '
            'label above them eats the clicks meant for the switch')
        loop = next(
            (n for n in ast.walk(build)
             if isinstance(n, ast.For) and ast.unparse(n.target) == 'button'
             and ast.unparse(n.iter) == '(self.settings_button, self.calibration_button, '
                                       'self.ignore_button)'),
            None)
        self.assertIsNotNone(loop, 'the icon re-add loop is gone')
        last_line = max(n.lineno for n in ast.walk(build)
                        if isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and n.func.attr == 'addSubview_')
        self.assertGreaterEqual(last_line, loop.lineno,
                                'something is added after the icons again')

    def test_title_label_does_not_reach_the_ignore_switch(self):
        build = _build()
        chat = ignore = None
        for node in ast.walk(build):
            # ("chat", 20, 14, PANEL_W - 140, 20, 15, PALETTE["accent"], True)
            if isinstance(node, ast.Tuple) and len(node.elts) == 8:
                head = node.elts[0]
                if isinstance(head, ast.Constant) and head.value == 'chat':
                    chat = (_eval(node.elts[1]), _eval(node.elts[3]))
            # self.ignore_button = self._make_button(PANEL_W - 119, 3, 38, 34, ...)
            if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == 'self.ignore_button':
                call = node.value
                ignore = (_eval(call.args[0]), _eval(call.args[2]))
        self.assertIsNotNone(chat, 'chat label geometry moved; update this test')
        self.assertIsNotNone(ignore, 'ignore button geometry moved; update this test')
        self.assertLessEqual(chat[0] + chat[1], ignore[0],
                             'the title field overlaps the eye switch, so its right end '
                             'is unclickable even when the button is on top')

    def test_header_readouts_are_not_selectable(self):
        text = ast.unparse(_build())
        self.assertIn('setSelectable_(False)', text,
                      'a selectable title turns a near-miss click into a text selection')

    def test_accessibility_palette_skips_all_header_icons(self):
        tree = ast.parse(SOURCE)
        controller = next(n for n in tree.body
                          if isinstance(n, ast.ClassDef) and n.name == 'HudController')
        fn = next(n for n in controller.body
                  if isinstance(n, ast.FunctionDef) and n.name == '_apply_accessibility_palette')
        body = ast.unparse(fn)
        for name in ('self.settings_button', 'self.calibration_button', 'self.ignore_button'):
            self.assertIn(name, body)

    def test_settings_model_deleted_reenables_delete_button(self):
        tree = ast.parse((ROOT / 'src' / 'settings.py').read_text())
        controller = next(n for n in tree.body
                          if isinstance(n, ast.ClassDef) and n.name == 'SettingsController')
        fn = next(n for n in controller.body
                  if isinstance(n, ast.FunctionDef) and n.name == 'modelDeleted_')
        self.assertIn('self.offline_delete_btn.setEnabled_(True)', ast.unparse(fn))


if __name__ == '__main__':
    unittest.main()
