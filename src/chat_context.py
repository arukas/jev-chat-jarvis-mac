"""Local conversation data and one bounded model input shared by every reply path."""
from __future__ import annotations

import difflib
import re
import unicodedata
from functools import lru_cache

CONTEXT_CHARS = 8192

# Two frames of the same chat are never byte-identical: Vision reads one glyph
# differently, a context menu occludes a bubble, a caret blinks. Exact tuple equality
# treated every such frame as a brand-new message, so history filled with near-duplicates
# ("他辍学了" four times) and gaps, and the model was handed that as the conversation.
_WS_RE = re.compile(r"[\s\u200b-\u200d\ufeff]+")
_PUNCT_RE = re.compile(
    r"[，。！？；：、,.!?;:'\"“”‘’（）()\[\]【】<>《》…·\-—_~`|/\\]+")


@lru_cache(maxsize=2048)
def normalize(text: str) -> str:
    """Comparison form: width-folded, punctuation-free, space-free, lowercased."""
    return _PUNCT_RE.sub(
        "", _WS_RE.sub("", unicodedata.normalize("NFKC", text))).lower()


def similar(a: str, b: str, threshold: float = 0.82) -> bool:
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return True
    if not na or not nb:
        return False
    longest = max(len(na), len(nb))
    if abs(len(na) - len(nb)) > max(2, int(longest * 0.34)):
        return False        # cheap reject before the O(n*m) matcher
    return difflib.SequenceMatcher(None, na, nb).ratio() >= threshold


def same_sequence(left, right) -> bool:
    """Same speakers, same lines, tolerating the OCR drift between two frames."""
    return (len(left) == len(right)
            and all(l[1] == r[1] and similar(l[0], r[0]) for l, r in zip(left, right)))


def _speaker(m) -> str:
    """Who said it, in words the model can tell apart.

    `sender` is OCR output and only trustworthy when it reads like a display name. A
    bubble's first line used to land there, turning the transcript into nonsense such as
    "可用额度 26261.69元: 交易时间 …" that the model then had to interpret.
    """
    name = (m[2] or "").strip().rstrip("：:")
    if name and len(name) <= 14 and not re.search(r"[\d０-９%¥$€:：，。！？；、]", name):
        return name
    return {"me": "我", "them": "对方"}.get(m[1], "方向未确认")



def message_record(message):
    """Keep old three-field records unchanged; metadata belongs to its message."""
    base = (message.text, message.side, message.sender or "")
    extra = (message.quote, message.quote_sender, message.content_state)
    return base + extra if any(extra) else base


def quote_context(record):
    if len(record) == 3:
        return ""
    quote, author, state = record[3:]
    if state == "mixed":
        return "\n正文与引用待区分：以下为可读原文，不推断拆分或作者。"
    return f"\n引用背景（{author or '作者未知'}，不是新发言）：{quote}" if quote else ""


def recognition_revision(previous, current):
    """Only refine an already observed frame, never infer corrections to old history."""
    if len(previous) != len(current):
        return None
    for old, new in zip(previous, current):
        if (old.x, old.y, old.w, old.h, old.side) != (new.x, new.y, new.w, new.h, new.side):
            return None
        if old.text == new.text:
            continue
        def reading(m):
            return m.text + ("\n" + (m.quote_sender + "：" if m.quote_sender else "") + m.quote
                             if m.quote else "")
        if 'mixed' not in (old.content_state, new.content_state) or reading(old) != reading(new):
            return None
    return [message_record(m) for m in previous]


def model_message(text: str, context: str | None = None) -> str:
    return text[:max(0, CONTEXT_CHARS - len(context or ""))]


def context_text(messages, target: int, limit: int = 20, background: str = "",
                 truncated: bool | None = None) -> str | None:
    """Messages have three legacy fields plus optional quote/author/split state.

    ponytail: character budget (not provider tokens); revisit for smaller model windows.
    History loses whole oldest messages first; persisted originals are never shortened.
    """
    current = messages[target]
    prior_all = [m for i, m in enumerate(messages) if i != target]
    if truncated is None:
        truncated = len(prior_all) > max(0, limit - 1)
    prior = prior_all[-(limit - 1):] if limit > 1 else []
    header = (f"会话背景：\n{background}\n\n" if background else "")
    # A gap in the transcript is not prose. Without saying so, the model answers as if it
    # had read the whole conversation.
    if truncated:
        header += "（更早的消息没有显示，下面是能看到的最近一段）\n"
    who = _speaker(current)
    speaker = f"当前待回复发言人：{who if who not in ('我', '方向未确认') else '对方'}"
    speaker += "\n（只回最后这一条；标着「我」的是我自己说的，只是背景）"
    mixed = len(current) == 6 and current[5] == 'mixed'
    if len(current) == 6:
        # Classification is necessary to interpret the raw text and cannot be
        # displaced by a long user-supplied conversation background.
        header = speaker + (quote_context(current) if mixed else '') + ('\n' + header if header else '')
    else:
        header += speaker
    lines = [f"{_speaker(m)}: {m[0]}{quote_context(m)}" for m in prior]
    # If the two priority fields alone overflow, reserve up to half for the message;
    # a shorter field leaves its unused space to the other.
    if len(current[0]) + len(header) > CONTEXT_CHARS:
        message_size = min(len(current[0]), max(CONTEXT_CHARS // 2, CONTEXT_CHARS - len(header)))
        header = header[:CONTEXT_CHARS - message_size]
    else:
        message_size = len(current[0])
    budget = CONTEXT_CHARS - message_size
    # Body gets its existing reservation before an attached quote can use space.
    if not mixed:
        header += quote_context(current)[:max(0, budget-len(header))]
    dropped = 0
    while lines and len(header) + 1 + sum(len(line) + 1 for line in lines) > budget:
        lines.pop(0)
        dropped += 1
    if dropped and lines:
        lines.insert(0, f"…（此前的 {dropped} 条因长度限制被省略）")
    return header + ("\n" + "\n".join(lines) if lines else "")


class Conversations:
    """Atomic, private local data; no network and no writes from model workers."""

    def __init__(self, path=None):
        from pathlib import Path
        self._anchors = {}
        self.data = {}
        self.error = ""
        self.revision = 0
        self._stamp = object()
        # One home for source runs and the packaged app alike — the directory that already
        # holds the venv and the env fallback (userconfig.config_dirs); tests pass paths.
        self.path = path or Path.home() / 'Library/Application Support/jev-jarvis/conversations.json'
        self.reload()

    def _file_stamp(self, fd=None):
        import os
        try:
            stat = self.path.stat() if fd is None else os.fstat(fd)
        except FileNotFoundError:
            return None
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    def reload(self):
        """Refresh external edits, including deletion; never retain unreadable private data."""
        import json
        stamp = ()
        error = ""
        try:
            stamp = self._file_stamp()
            if stamp == self._stamp and not self.error:
                return False
            raw = self.path.read_text(encoding='utf-8') if stamp is not None else ''
            data = json.loads(raw) if raw.strip() else {}
            if not isinstance(data, dict) or any(
                not isinstance(title, str) or not title.strip() or not isinstance(chat, dict)
                or not isinstance(chat.get('background', ''), str)
                or ('tones' in chat and (not isinstance(chat['tones'], list)
                    or not all(isinstance(t, str) for t in chat['tones'])))
                or not isinstance(chat.get('messages', []), list)
                or not isinstance(chat.get('breaks', []), list)
                or any(type(i) is not int or not 0 < i < len(chat.get('messages', []))
                       for i in chat.get('breaks', []))
                or any(not isinstance(m, list) or len(m) not in (3, 6)
                       or not all(isinstance(v, str) for v in m)
                       or (len(m) == 6 and m[5] not in ('', 'mixed'))
                       for m in chat.get('messages', []))
                for title, chat in data.items()
            ):
                raise ValueError('会话数据格式无效')
        except (OSError, ValueError):
            data = {}
            error = '本地会话文件读取失败或格式无效，已暂停写入；请修复文件或检查权限后重试。'
        changed = stamp != self._stamp or error != self.error
        if changed:
            self.data = data
            self._anchors.clear()
            self.revision += 1
        self._stamp, self.error = stamp, error
        return changed

    def _save(self, data):
        import json
        import os
        import tempfile
        if self.error:
            raise ValueError(self.error)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix='.conversations-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(data, out, ensure_ascii=False)
                out.flush()
                os.fsync(out.fileno())
                # An external edit during preparation must not be overwritten by this snapshot.
                if self._file_stamp() != self._stamp:
                    self.reload()
                    raise OSError('会话文件已在外部修改，请重试')
                os.replace(name, self.path)
                self._stamp = self._file_stamp(out.fileno())
        finally:
            if os.path.exists(name):
                os.unlink(name)
        self.data = data

    def resolve_title(self, title):
        """Return the stored key this OCR reading belongs to, tolerating drift."""
        if not title or not title.strip():
            return title
        self.reload()
        if title in self.data:
            return title
        probe = normalize(title)
        if not probe:
            return title
        best, score = title, 0.0
        for key in self.data:
            folded = normalize(key)
            if not folded:
                continue
            if folded == probe:
                return key
            ratio = difflib.SequenceMatcher(None, probe, folded).ratio()
            if ratio > score:
                best, score = key, ratio
        return best if score >= 0.80 else title

    def is_muted(self, title):
        """Whether this conversation is excluded from reading and generation."""
        if not title or not title.strip():
            return False
        self.reload()
        return bool(self.data.get(self.resolve_title(title), {}).get('muted'))

    def set_muted(self, title, muted=True):
        if not title or not title.strip():
            raise ValueError('尚未确认聊天标题')
        self.reload()
        key = self.resolve_title(title)
        chat = dict(self.data.get(key, {}), muted=bool(muted))
        self._save(dict(self.data, **{key: chat}))
        return bool(muted)

    def muted_titles(self):
        self.reload()
        return sorted(name for name, chat in self.data.items() if chat.get('muted'))

    def background(self, title):
        self.reload()
        return self.data.get(self.resolve_title(title), {}).get('background', '') if title else ''

    def save_background(self, title, text):
        if not title or not title.strip():
            raise ValueError('尚未确认聊天标题')
        self.reload()
        title = self.resolve_title(title)
        chat = dict(self.data.get(title, {}), background=text)
        self._save(dict(self.data, **{title: chat}))

    def tones(self, title):
        """Return this chat's remembered candidate tone slots, or None to use global defaults."""
        if not title or not title.strip():
            return None
        self.reload()
        saved = self.data.get(self.resolve_title(title), {}).get('tones')
        return list(saved) if isinstance(saved, list) and saved else None

    def save_tones(self, title, tones):
        """Remember per-chat candidate tone slots (or pass None to fall back to global defaults)."""
        if not title or not title.strip():
            raise ValueError('尚未确认聊天标题')
        self.reload()
        key = self.resolve_title(title)
        chat = dict(self.data.get(key, {}))
        if tones is None:
            chat.pop('tones', None)
        else:
            if not isinstance(tones, (list, tuple)) or not all(isinstance(t, str) for t in tones):
                raise ValueError('会话话术格式无效')
            chat['tones'] = [str(t) for t in tones]
        self._save(dict(self.data, **{key: chat}))
        return list(chat['tones']) if 'tones' in chat else None

    def history(self, title):
        self.reload()
        chat = self.data.get(self.resolve_title(title), {}) if title else {}
        return [tuple(m) for m in chat.get('messages', [])][-200:]

    def observe(self, title, visible, record=True, previous=None):
        """Return a reliable observed sequence and this frame's starting index.

        ponytail: exact sequence overlap only; OCR corrections or disjoint frames may
        underrecord. Add better alignment only with evidence from synthetic regressions.
        """
        if not title or not title.strip() or not visible:
            return visible, 0
        if self.is_muted(title):
            return visible, 0      # ignored conversation: no history, no model input
        title = self.resolve_title(title)
        old = self.history(title)
        breaks = sorted(set(self.data.get(title, {}).get('breaks', [])))
        if record and previous and previous != visible and len(previous) == len(visible):
            matches = [i for i in range(len(old)-len(previous)+1)
                       if same_sequence(old[i:i+len(previous)], previous)
                       and not any(i < boundary < i+len(previous) for boundary in breaks)]
            if len(matches) == 1:
                start = matches[0]
                updated = old[:start] + visible + old[start+len(previous):]
                chat = dict(self.data.get(title, {}), messages=updated)
                self._save(dict(self.data, **{title: chat}))
                old = updated
        matches = [i for i in range(len(old) - len(visible) + 1)
                   if same_sequence(old[i:i + len(visible)], visible)
                   and not any(i < boundary < i + len(visible) for boundary in breaks)]
        if matches:
            if record:
                self._anchors.pop(title, None)
            if len(matches) != 1:
                return visible, 0
            start = matches[0]
            segment_start = max([0] + [i for i in breaks if i <= start])
            return old[segment_start:start + len(visible)], start - segment_start
        segment_start = breaks[-1] if breaks else 0
        tail = old[segment_start:]
        overlap = next((n for n in range(min(len(tail), len(visible)), 0, -1)
                        if same_sequence(tail[-n:], visible[:n])), 0)
        if old and not overlap:
            # A gap is not evidence of chronology. Wait for a subsequent advancing
            # frame, then retain it as a separate observation segment, never model
            # context spanning the unknown gap. No anchor can survive an explicit clear.
            anchor = self._anchors.get(title, [])
            overlap = next((n for n in range(min(len(anchor), len(visible)), 0, -1)
                            if same_sequence(anchor[-n:], visible[:n])), 0)
            if not record or not overlap or overlap == len(visible):
                if record:
                    self._anchors[title] = visible
                return visible, 0
            segment_start = len(old)
            breaks.append(segment_start)
            tail = anchor
        combined_segment = tail + visible[overlap:]
        start = len(tail) - overlap
        combined = old[:segment_start] + combined_segment
        if record:
            removed = max(0, len(combined) - 200)
            chat = dict(self.data.get(title, {}), messages=combined[-200:],
                        breaks=[i - removed for i in breaks if i > removed])
            self._save(dict(self.data, **{title: chat}))
            self._anchors.pop(title, None)
        return combined_segment, start

    def clear_history(self, title=None):
        if title is not None and not title.strip():
            raise ValueError('尚未确认聊天标题')
        self.reload()
        self._save({name: dict(chat, messages=[], breaks=[]) if title is None or name == title else chat
                    for name, chat in self.data.items()})
        if title is None:
            self._anchors.clear()
        else:
            self._anchors.pop(title, None)


def message_limit(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 100:
        raise ValueError('上下文条数必须是 1—100 的整数')
    return int(value)
