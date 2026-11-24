"""The header switch and its notice belong to the chat on screen, not the previous one.

Regression: the notice text was written through _render("status"), which also overwrites
_normal_status, so moving from an ignored chat to a normal one left "已忽略此聊天" on the
panel until the next generation finished.
"""
import ast
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ui_style import PALETTE  # noqa: E402

NOTICE = "已忽略此聊天 · 不读取、不判断、不生成"


def harness():
    tree = ast.parse((ROOT / 'src/hud.py').read_text())
    source = next(n for n in tree.body
                  if isinstance(n, ast.ClassDef) and n.name == 'HudController')
    wanted = {'applyChat_', 'applyIgnored_', 'applyForegroundHidden_'}
    body = []
    for node in source.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            node.decorator_list = []
            body.append(node)
    assert {n.name for n in body} == wanted

    body.append(ast.parse(
        "def _render(self, key, text, color=None):\n"
        "    self.rendered.append((key, text))\n"
        "    if key == 'status':\n"
        "        self._normal_status = (text, color)\n"
    ).body[0])
    body.append(ast.parse(
        "def _show_ignore_button(self, title, ignored):\n"
        "    self.button = (title, ignored)\n"
    ).body[0])
    body.append(ast.parse("def _clear_candidates(self):\n    self.cleared = True\n").body[0])
    body.append(ast.parse("def applyHidden_(self, reason):\n    self.hidden = reason\n").body[0])

    klass = ast.ClassDef(name='Switch', bases=[], keywords=[], body=body, decorator_list=[])
    mod = ast.Module(body=[klass], type_ignores=[])
    ast.fix_missing_locations(mod)
    namespace = {'PALETTE': PALETTE}
    exec(compile(mod, '<hud>', 'exec'), namespace)
    obj = namespace['Switch']()
    obj.rendered, obj.button, obj.cleared = [], None, False
    obj.rows = {'cand_header': SimpleNamespace(setStringValue_=lambda v: None)}
    obj.hidden = None
    obj._normal_status = ('分析完成 · jev', None)
    return obj


def test_leaving_an_ignored_chat_restores_the_status_line():
    sw = harness()
    sw.applyIgnored_('平安银行信用卡')
    assert sw.button == ('平安银行信用卡', True)
    assert ('status', NOTICE) in sw.rendered
    assert sw.cleared

    sw.applyChat_('Sunflower')
    assert sw.button == ('Sunflower', False)
    assert ('status', '分析完成 · jev') in sw.rendered
    assert sw._normal_status == ('分析完成 · jev', None)


def test_the_notice_does_not_replace_the_status_it_has_to_restore():
    sw = harness()
    sw.applyIgnored_('平安银行信用卡')
    sw.applyIgnored_('平安银行信用卡')   # repeated renders must be idempotent
    sw.applyChat_('Sunflower')
    assert sw._normal_status == ('分析完成 · jev', None)


def test_leaving_the_chat_app_clears_the_switch():
    sw = harness()
    sw.applyChat_('Eating')
    assert sw.button == ('Eating', False)
    sw.applyForegroundHidden_('聊天应用不在前台')
    assert sw.button == ('', False), 'the eye stayed on an empty header'
    assert sw._painted_title is None, 'the next read must repaint the switch'
    assert sw.hidden == '聊天应用不在前台'
