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


def test_apply_ignored_clears_stale_message_and_verdict_fields():
    sw = harness()
    sw.applyIgnored_('平安银行信用卡')
    for key in ('message', 'sender', 'intent', 'confidence', 'risk', 'actions'):
        assert (key, '') in sw.rendered
    assert sw._last_intent == ''
    assert sw._last_risk == 0.0


def test_work_inner_pushes_apply_ignored_and_retires_previous_chat():
    import tempfile
    import threading
    from unittest.mock import Mock, patch
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from support_hud import Harness, HUD
    import chat_context

    with tempfile.TemporaryDirectory() as td:
        store = chat_context.Conversations(Path(td) / 'c.json')
        store.set_muted('公众号通知', True)
        h = Harness()
        pushed = []
        for name, value in dict(
            _active_context='ctx', conversations=store, history_enabled=False, context_limit=20,
            _observed_messages=[], _observed_offset=0, _context_version=0,
            _context_lock=threading.RLock(), _reply_key=('wechat', '张三', '在吗', None, ()),
            _reply_epoch=1, _reply_worker=threading.local(), last_seen='在吗', analyzed_text='在吗',
            _win_wid=None, _fingerprint=None, _burst_left=3, _last_full=None, _show_boxes=False,
            _read_once=True, _gen_epoch=1, _last_skip_reason=None, _prejudge_req=('x',),
            _prejudge_result=('y',), _pregen_req=('z',), _pregen_result=('w',),
            _pregen_running=False, _prejudging=False, _paused=False, _analyzing=False,
            _regenerating=False, _stable_n=0, last_change_ts=0, last_analyze_ts=0,
            _app=HUD['FAKE_APP'], _asked_accessibility=False, _foreground_epoch=0,
            _read_fail_since=None, _read_fail_hidden=False, _empty_frame_since=None,
            _prejudge_event=threading.Event(), _pregen_event=threading.Event(),
            slot_tones=['normal'], _stream_rows={}, _painted_title='张三',
            _conversation_revision=store.revision,
        ).items():
            setattr(h, name, value)
        h.performSelectorOnMainThread_withObject_waitUntilDone_ = lambda s, p, w: pushed.append((s, p))
        HUD['frontmost_app'].return_value = HUD['FAKE_APP']
        HUD['screen_capture_ok'].return_value = True
        HUD['read_conversation'].return_value = {
            'ok': True, 'unchanged': False, 'fingerprint': b'fp1', 'layout': (1, 800, 600, 0.7),
            'window': {'wid': 1, 'x': 0, 'y': 0, 'w': 800, 'h': 600},
            'chat_title': '公众号通知', 'messages': [], 'n_blocks': 0, 'input_rect': None,
        }
        with patch('input_region.locate_visual_input', return_value=None), \
             patch('visual_fill.chat_signature', return_value=None):
            h._work_inner()
            assert ('applyIgnored:', '公众号通知') in pushed
            assert h._reply_epoch == 2
            assert h._reply_key is None
            assert h.last_seen is None
            assert h.analyzed_text is None
            # 静止帧不重复刷 applyIgnored:
            pushed.clear()
            HUD['read_conversation'].return_value = {
                'ok': True, 'unchanged': True, 'fingerprint': b'fp1', 'layout': (1, 800, 600, 0.7),
                'window': {'wid': 1, 'x': 0, 'y': 0, 'w': 800, 'h': 600},
                'chat_title': '', 'messages': [], 'n_blocks': 0, 'input_rect': None,
            }
            h._work_inner()
            assert ('applyIgnored:', '公众号通知') not in pushed
