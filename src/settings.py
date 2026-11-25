"""Native settings: models require a restart; conversation changes apply on save."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import AppKit as A
import objc
from Foundation import NSObject, NSMakeRect

import builtin
import judge
import styles
import userconfig
import settings_config as config
import ui_style


PALETTE = ui_style.PALETTE


class SettingsController(NSObject):
    @objc.python_method
    def build(self, hud=None):
        self.hud = hud
        self.path = userconfig.env_files()[0]
        self.original = config.read_document(self.path)
        values = userconfig.parse_env_file(self.path)
        self.file_values = values
        self.initial = {}
        self.fields = {}
        self.controls = []
        self.busy = False
        self.window = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 760, 648),
            A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable,
            A.NSBackingStoreBuffered, False)
        self.window.setAppearance_(A.NSAppearance.appearanceNamed_(A.NSAppearanceNameAqua))
        self.window.setTitle_("设置 · 模型与会话上下文")
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(A.NSColor.clearColor())
        self.window.setHasShadow_(True)
        # The HUD and OCR overlay float above normal windows; settings must sit above both.
        self.window.setLevel_(A.NSFloatingWindowLevel + 1)
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)
        view = A.NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, 760, 648))
        view.setMaterial_(getattr(
            A, "NSVisualEffectMaterialSidebar",
            getattr(A, "NSVisualEffectMaterialLight", 1)))
        view.setBlendingMode_(A.NSVisualEffectBlendingModeBehindWindow)
        view.setState_(A.NSVisualEffectStateActive)
        view.setWantsLayer_(True)
        view.layer().setBackgroundColor_(PALETTE["bg"].CGColor())
        self.window.setContentView_(view)

        title = self.label(view, "模型与会话设置", 24, 598, 710, 28, 22)
        title.setFont_(A.NSFont.boldSystemFontOfSize_(22))
        title.setTextColor_(PALETTE["text"])
        self.label(view, "编辑文件：" + str(self.path).replace(str(Path.home()), "~"),
                   24, 570, 710, 20, 11, PALETTE["muted"])

        restart_box = ui_style.make_surface(
            10, PALETTE["amber"].colorWithAlphaComponent_(0.10),
            PALETTE["amber"].colorWithAlphaComponent_(0.18))
        restart_box.setFrame_(NSMakeRect(24, 530, 710, 34))
        view.addSubview_(restart_box)
        restart_notice = self.label(view, "模型配置保存后重启 · 话术、会话与软件开关保存后立即生效",
                                    38, 536, 680, 20, 13, PALETTE["amber"])
        restart_notice.setFont_(A.NSFont.boldSystemFontOfSize_(13))

        tab_surface = ui_style.make_surface(14, PALETTE["surface"], PALETTE["edge"])
        tab_surface.setFrame_(NSMakeRect(16, 176, 728, 342))
        view.addSubview_(tab_surface)
        self.tabs = A.NSTabView.alloc().initWithFrame_(NSMakeRect(24, 184, 712, 326))
        if hasattr(self.tabs, "setDrawsBackground_"):
            self.tabs.setDrawsBackground_(False)
        titles = ("判断 · Jev", "生成 · OpenAI 兼容", "生成 · Anthropic 兼容")
        for index, (prefix, title) in enumerate(zip(config.PREFIXES, titles)):
            item = A.NSTabViewItem.alloc().initWithIdentifier_(prefix)
            item.setLabel_(title)
            panel = A.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 690, 300))
            summary, source = self.current_source(prefix)
            source_surface = ui_style.make_surface(10, PALETTE["row"], PALETTE["edge"])
            source_surface.setFrame_(NSMakeRect(12, 212, 666, 76))
            panel.addSubview_(source_surface)
            badge = self.label(panel, summary, 26, 254, 638, 20, 14, PALETTE["green"])
            badge.setFont_(A.NSFont.boldSystemFontOfSize_(14))
            self.label(panel, source, 26, 220, 638, 34, 11, PALETTE["muted"])
            fields = {}
            for name, label, y in (("API_KEY", "密钥", 166), ("BASE_URL", "服务地址", 120), ("MODEL", "模型", 74)):
                row_label = self.label(panel, label, 26, y + 3, 78, 24, 11, PALETTE["text"])
                row_label.setFont_(A.NSFont.boldSystemFontOfSize_(11))
                cls = A.NSSecureTextField if name == "API_KEY" else A.NSComboBox if name == "MODEL" else A.NSTextField
                field = cls.alloc().initWithFrame_(NSMakeRect(112, y, 552, 30))
                default = "" if name == "API_KEY" else config.DEFAULTS[prefix][name == "MODEL"]
                value = values.get(f"{prefix}_{name}", default)
                if name == "API_KEY" and ("$(" in value or "`" in value):
                    value = ""  # Do not evaluate or rewrite shell/keychain expressions.
                    field.setToolTip_("此密钥由 shell 表达式提供；留空保留原行，输入新密钥才会替换。")
                field.setStringValue_(value)
                self.style_field(field)
                field.setDelegate_(self)
                field.setAccessibilityLabel_(title + " " + label)
                if name == "API_KEY":
                    field.setPlaceholderString_("由 shell 表达式提供：留空保留原行，输入新密钥才替换"
                                               if "$(" in values.get(f"{prefix}_{name}", "") or "`" in values.get(f"{prefix}_{name}", "")
                                               else "仅显示此文件中的密钥；不会复制环境变量中的密钥")
                if name == "MODEL":
                    self.set_models(field, [])
                    field.setCompletes_(False)
                    field.setPlaceholderString_("获取模型列表后选择，或手动填写模型名称")
                panel.addSubview_(field)
                fields[name] = field
                self.initial[f"{prefix}_{name}"] = value
                self.controls.append(field)
            self.fields[prefix] = fields
            if prefix == "OPENAI":
                self.label(panel, "每种话术候选数", 26, 43, 138, 20, 11, PALETTE["text"])
                self.candidate_count = A.NSPopUpButton.alloc().initWithFrame_pullsDown_(
                    NSMakeRect(170, 39, 78, 28), False)
                self.candidate_count.addItemsWithTitles_([
                    str(value) for value in range(styles.MIN_PER_TONE, styles.MAX_PER_TONE + 1)])
                self.candidate_count.selectItemWithTitle_(str(styles.PER_TONE))
                self.candidate_count.setAccessibilityLabel_("每种话术候选数，1 到 5 条")
                self.candidate_count.setToolTip_("每种话术生成的候选回复条数；保存后重启生效。")
                self.candidate_count.setTarget_(self)
                self.candidate_count.setAction_("candidateCountChanged:")
                panel.addSubview_(self.candidate_count)
                self.label(panel, "条（保存后重启生效）", 258, 43, 170, 20, 11, PALETTE["muted"])
                self.initial["JEV_CANDIDATES_PER_TONE"] = str(styles.PER_TONE)
                if userconfig.source_of("JEV_CANDIDATES_PER_TONE") == "环境变量":
                    self.candidate_count.setEnabled_(False)
                    self.candidate_count.setToolTip_("由启动环境变量控制；修改环境变量后重启。")
            else:
                hint = ("Jev 地址带不带 /v1 都行，网关动作不同时可填完整动作路径；列表接口不可用时可手填模型。" if prefix == "TYPESAFE"
                        else "使用 Anthropic 消息接口，支持自定义兼容服务地址。")
                self.label(panel, hint, 26, 43, 638, 20, 11, PALETTE["muted"])
            for text, action, x in (("获取模型列表", "fetchModels:", 372), ("测试连接", "testConnection:", 524)):
                button = self.button(panel, text, action, x, 4, 140)
                button.setTag_(index)
                self.controls.append(button)
            item.setView_(panel)
            self.tabs.addTabViewItem_(item)
        self.build_tones_tab()
        self.build_context_tab()
        self.build_apps_tab()
        view.addSubview_(self.tabs)
        # #38: 离线判断模型管理。删除是显式确认动作；「启用」只写选择，真正的
        # 下载发生在下次启动的预热——设置窗口里不藏一个 7 GB 的下载按钮。
        offline_surface = ui_style.make_surface(10, PALETTE["row"], PALETTE["edge"])
        offline_surface.setFrame_(NSMakeRect(24, 126, 710, 44))
        view.addSubview_(offline_surface)
        self.offline_label = self.label(view, "", 36, 140, 540, 20, 11, PALETTE["text"])
        self.offline_delete_btn = self.button(view, "删除模型…", "deleteOfflineModel:",
                                              596, 132, 118)
        self.offline_enable_btn = self.button(view, "启用离线判断…", "enableOfflineModel:",
                                              596, 132, 118)
        self.controls.append(self.offline_delete_btn)
        self.controls.append(self.offline_enable_btn)
        self.refresh_offline_section()
        priority_surface = ui_style.make_surface(10, PALETTE["row"], PALETTE["edge"])
        priority_surface.setFrame_(NSMakeRect(24, 74, 710, 44))
        view.addSubview_(priority_surface)
        self.label(view, "优先级：环境变量 > 用户 env > 项目 .env > 内置；两组生成密钥同时存在时 OpenAI 优先。\n清空此文件的密钥不屏蔽其他来源；切换服务需清除原来源中的优先密钥。", 36, 80, 686, 32, 11, PALETTE["muted"])
        self.status = self.label(view, "测试会发送固定问候语，不读取聊天内容；可能产生少量服务费用。", 24, 26, 550, 38, 11, PALETTE["muted"])
        self.set_status(self.status.stringValue())
        self.save_button = self.button(view, "保存配置", "saveSettings:", 602, 29, 132, True)
        self.controls.append(self.save_button)
        self.window.center()
        self.refresh_contexts()
        return self

    @objc.python_method
    def build_context_tab(self):
        import chat_context
        item = A.NSTabViewItem.alloc().initWithIdentifier_("CONTEXT")
        item.setLabel_("会话记录与背景")
        panel = A.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 690, 300))
        group = ui_style.make_surface(10, PALETTE["row"], PALETTE["edge"])
        group.setFrame_(NSMakeRect(12, 168, 666, 120))
        panel.addSubview_(group)
        title = self.label(panel, "记录聊天历史", 26, 254, 490, 20, 13)
        title.setFont_(A.NSFont.boldSystemFontOfSize_(13))
        self.label(panel, "每个会话保留最近 100 条，关闭后保留已有记录", 26, 235, 530, 18, 11, PALETTE["muted"])
        self.history_switch = A.NSSwitch.alloc().initWithFrame_(NSMakeRect(610, 242, 46, 28))
        self.history_switch.setAccessibilityLabel_("记录聊天历史")
        self.history_switch.setTarget_(self)
        self.history_switch.setAction_("contextControlChanged:")
        enabled = self.hud.history_enabled if self.hud else userconfig.get("JEV_HISTORY") == "1"
        self.history_switch.setState_(A.NSOnState if enabled else A.NSOffState)
        panel.addSubview_(self.history_switch)
        divider = ui_style.make_surface(0, PALETTE["edge"])
        divider.setFrame_(NSMakeRect(26, 228, 638, 1))
        panel.addSubview_(divider)
        title = self.label(panel, "模型参考条数", 26, 204, 490, 20, 13)
        title.setFont_(A.NSFont.boldSystemFontOfSize_(13))
        self.label(panel, "使用最近的消息，包含当前消息（1—100 条）", 26, 185, 530, 18, 11, PALETTE["muted"])
        self.context_count = A.NSTextField.alloc().initWithFrame_(NSMakeRect(580, 194, 56, 28))
        try:
            count = self.hud.context_limit if self.hud else chat_context.message_limit(
                userconfig.get("JEV_CONTEXT_MESSAGES") or "20")
        except ValueError:
            count = 20
        self.context_count.setStringValue_(str(count))
        self.style_field(self.context_count)
        self.context_count.setAlignment_(A.NSTextAlignmentCenter)
        self.context_count.setDelegate_(self)
        self.context_count.setAccessibilityLabel_("模型参考条数，1 到 100")
        panel.addSubview_(self.context_count)
        self.label(panel, "条", 642, 197, 22, 20, 11, PALETTE["muted"])
        self.initial.update({"JEV_HISTORY": "1" if enabled else "0", "JEV_CONTEXT_MESSAGES": str(count)})
        for key, control in (("JEV_HISTORY", self.history_switch),
                             ("JEV_CONTEXT_MESSAGES", self.context_count)):
            if userconfig.source_of(key) == "环境变量":
                control.setEnabled_(False)
                control.setToolTip_("由启动环境变量控制；修改环境变量后重启。")
        title = self.label(panel, "聊天背景", 18, 138, 300, 22, 13)
        title.setFont_(A.NSFont.boldSystemFontOfSize_(13))
        self.context_picker = A.NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(428, 136, 244, 28), False)
        self.context_picker.setFont_(A.NSFont.systemFontOfSize_(12))
        self.context_picker.setAccessibilityLabel_("选择要编辑背景的会话")
        self.context_picker.setTarget_(self)
        self.context_picker.setAction_("selectContext:")
        panel.addSubview_(self.context_picker)
        scroll = A.NSScrollView.alloc().initWithFrame_(NSMakeRect(18, 34, 650, 94))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(A.NSNoBorder)
        scroll.setWantsLayer_(True)
        scroll.layer().setCornerRadius_(ui_style.RADIUS_FIELD)
        scroll.layer().setBorderColor_(PALETTE["edge"].CGColor())
        scroll.layer().setBorderWidth_(0.75)
        scroll.layer().setMasksToBounds_(True)
        self.background_editor = A.NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 630, 94))
        self.background_editor.setRichText_(False)
        self.background_editor.setFont_(A.NSFont.systemFontOfSize_(12))
        self.background_editor.setTextColor_(PALETTE["text"])
        self.background_editor.setBackgroundColor_(PALETTE["field"])
        self.background_editor.setTextContainerInset_((10, 8))
        self.background_editor.setAccessibilityLabel_("所选会话背景，可输入多行，清空后保存可删除背景")
        self.background_editor.setToolTip_("例如：AAA 是群主，BBB 是公司老板。背景不占消息条数；清空后保存可删除背景。")
        self.background_editor.setDelegate_(self)
        self.context_title = ""
        self.background_initial = {}
        self.background_drafts = {}
        scroll.setDocumentView_(self.background_editor)
        panel.addSubview_(scroll)
        self.label(panel, "记录在本机保存，推理时发送给所选模型服务。", 18, 5, 500, 18, 10, PALETTE["muted"])
        self.history_menu = A.NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(546, 0, 126, 28), True)
        self.history_menu.setFont_(A.NSFont.systemFontOfSize_(11))
        self.history_menu.addItemsWithTitles_(["管理记录", "清空所选会话记录", "清空全部记录", "恢复所选会话默认话术"])
        self.history_menu.menu().setAutoenablesItems_(False)
        self.history_menu.setToolTip_("清空立即生效，保留聊天背景；也可恢复所选会话为全局默认话术。")
        for index, action in enumerate(("clearCurrentHistory:", "clearAllHistory:", "resetCurrentChatTones:"), 1):
            entry = self.history_menu.itemAtIndex_(index)
            entry.setTarget_(self)
            entry.setAction_(action)
        panel.addSubview_(self.history_menu)
        item.setView_(panel)
        self.tabs.addTabViewItem_(item)

    @objc.python_method
    def build_tones_tab(self):
        item = A.NSTabViewItem.alloc().initWithIdentifier_("TONES")
        item.setLabel_("候选话术")
        panel = A.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 690, 300))

        group = ui_style.make_surface(10, PALETTE["row"], PALETTE["edge"])
        group.setFrame_(NSMakeRect(12, 194, 666, 94))
        panel.addSubview_(group)
        title = self.label(panel, "全局默认候选回复类型", 26, 260, 490, 20, 13)
        title.setFont_(A.NSFont.boldSystemFontOfSize_(13))
        self.label(panel, "新会话默认使用以下槽位；在悬浮窗切换话术会自动按每个聊天单独记忆（对应 JEV_DEFAULT_TONES）。",
                   26, 240, 638, 18, 11, PALETTE["muted"])
        default_env_locked = userconfig.source_of(styles.DEFAULT_SLOTS_VAR) == "环境变量"
        current_defaults = styles.default_slots()
        self.default_tone_pickers = []
        for slot in range(styles.MAX_SLOTS):
            x = 26 + slot * 218
            self.label(panel, f"槽位 {slot + 1}", x, 208, 42, 20, 11, PALETTE["muted"])
            pop = A.NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(x + 42, 203, 162, 28), False)
            pop.setFont_(A.NSFont.systemFontOfSize_(12))
            pop.setTarget_(self)
            pop.setAction_("defaultToneChanged:")
            pop.setAccessibilityLabel_(f"全局默认候选槽位 {slot + 1}")
            if default_env_locked:
                pop.setEnabled_(False)
                pop.setToolTip_("由启动环境变量 JEV_DEFAULT_TONES 控制；修改环境变量后重启。")
            panel.addSubview_(pop)
            self.default_tone_pickers.append(pop)
        self.initial[styles.DEFAULT_SLOTS_VAR] = styles.format_default_slots(current_defaults)

        title = self.label(panel, "话术提示词（全局）", 18, 158, 132, 22, 13)
        title.setFont_(A.NSFont.boldSystemFontOfSize_(13))
        self.tone_prompt_picker = A.NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(150, 155, 190, 28), False)
        self.tone_prompt_picker.setFont_(A.NSFont.systemFontOfSize_(12))
        self.tone_prompt_picker.setAccessibilityLabel_("选择要编辑提示词的话术")
        self.tone_prompt_picker.setTarget_(self)
        self.tone_prompt_picker.setAction_("selectTonePrompt:")
        panel.addSubview_(self.tone_prompt_picker)
        self.tone_prompt_badge = self.label(panel, "", 348, 160, 128, 18, 11, PALETTE["muted"])
        self.tone_reset_btn = self.button(panel, "恢复默认", "resetTonePrompt:", 482, 153, 88)
        self.tone_add_btn = self.button(panel, "新增话术…", "addCustomTone:", 578, 153, 90)
        self.controls.extend((self.tone_reset_btn, self.tone_add_btn))

        scroll = A.NSScrollView.alloc().initWithFrame_(NSMakeRect(18, 34, 650, 110))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(A.NSNoBorder)
        scroll.setWantsLayer_(True)
        scroll.layer().setCornerRadius_(ui_style.RADIUS_FIELD)
        scroll.layer().setBorderColor_(PALETTE["edge"].CGColor())
        scroll.layer().setBorderWidth_(0.75)
        scroll.layer().setMasksToBounds_(True)
        self.tone_prompt_editor = A.NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 630, 110))
        self.tone_prompt_editor.setRichText_(False)
        self.tone_prompt_editor.setFont_(A.NSFont.systemFontOfSize_(12))
        self.tone_prompt_editor.setTextColor_(PALETTE["text"])
        self.tone_prompt_editor.setBackgroundColor_(PALETTE["field"])
        self.tone_prompt_editor.setTextContainerInset_((10, 8))
        self.tone_prompt_editor.setAccessibilityLabel_("所选话术的全局提示词")
        self.tone_prompt_editor.setToolTip_("直接喂给生成模型的指令：写清「什么语气 + 别变成什么」，至少 10 字，不含 |。")
        self.tone_prompt_editor.setDelegate_(self)
        scroll.setDocumentView_(self.tone_prompt_editor)
        panel.addSubview_(scroll)

        self.label(panel, "提示词全局生效，至少 10 字且不含 | 字符；修改后点击「保存配置」立即生效（对应 JEV_TONES）。",
                   18, 8, 650, 18, 10, PALETTE["muted"])

        self.tone_env_locked = userconfig.source_of(styles.CUSTOM_VAR) == "环境变量"
        self.tone_prompt_initial = dict(styles.PRESETS)
        self.tone_prompt_drafts = dict(styles.PRESETS)
        self.tone_prompt_selected = ""
        self._refresh_tone_pickers(selected=current_defaults[0] if current_defaults[0] in self.tone_prompt_drafts else None,
                                   default_slots=current_defaults)
        if self.tone_env_locked:
            self.tone_prompt_editor.setEditable_(False)
            self.tone_prompt_editor.setToolTip_("由启动环境变量 JEV_TONES 控制；修改环境变量后重启。")
            self.tone_reset_btn.setEnabled_(False)
            self.tone_add_btn.setEnabled_(False)
        item.setView_(panel)
        self.tabs.addTabViewItem_(item)

    @objc.python_method
    def _sync_tone_prompt_draft(self):
        name = getattr(self, "tone_prompt_selected", "")
        if name and name in self.tone_prompt_drafts and self.tone_prompt_editor.isEditable():
            self.tone_prompt_drafts[name] = str(self.tone_prompt_editor.string())

    @objc.python_method
    def _refresh_tone_pickers(self, selected=None, default_slots=None):
        names = list(self.tone_prompt_drafts.keys())
        current_defaults = (list(default_slots) if default_slots is not None
                            else [str(p.titleOfSelectedItem() or styles.NONE_LABEL) for p in self.default_tone_pickers])
        slot_items = names + [styles.NONE_LABEL]
        for slot, pop in enumerate(self.default_tone_pickers):
            pop.removeAllItems()
            pop.addItemsWithTitles_(slot_items)
            wanted = current_defaults[slot] if slot < len(current_defaults) else styles.NONE_LABEL
            pop.selectItemWithTitle_(wanted if wanted in slot_items else styles.NONE_LABEL)

        target = selected if selected in names else (names[0] if names else "")
        self.tone_prompt_selected = ""
        self.tone_prompt_picker.removeAllItems()
        self.tone_prompt_picker.addItemsWithTitles_(names or ["暂无话术"])
        if target in names:
            self.tone_prompt_picker.selectItemWithTitle_(target)
        self.tone_prompt_selected = target
        self.tone_prompt_editor.setString_(self.tone_prompt_drafts.get(target, ""))
        self._update_tone_badge()

    @objc.python_method
    def _update_tone_badge(self):
        name = getattr(self, "tone_prompt_selected", "")
        if not name or name not in self.tone_prompt_drafts:
            self.tone_prompt_badge.setStringValue_("")
            self.tone_reset_btn.setEnabled_(False)
            return
        if name in styles.BUILTIN:
            modified = styles.normalize_prompt(self.tone_prompt_drafts[name]) != styles.BUILTIN[name]
            self.tone_prompt_badge.setStringValue_("内置 · 已修改" if modified else "内置默认")
            self.tone_prompt_badge.setTextColor_(PALETTE["amber"] if modified else PALETTE["muted"])
            self.tone_reset_btn.setTitle_("恢复默认")
            self.tone_reset_btn.setEnabled_(modified and not self.tone_env_locked and not self.busy)
        else:
            self.tone_prompt_badge.setStringValue_("自定义话术")
            self.tone_prompt_badge.setTextColor_(PALETTE["green"])
            self.tone_reset_btn.setTitle_("删除话术")
            self.tone_reset_btn.setEnabled_(not self.tone_env_locked and not self.busy)

    @objc.python_method
    def changed_tone_prompts(self) -> bool:
        if not hasattr(self, "tone_prompt_drafts") or getattr(self, "tone_env_locked", False):
            return False
        self._sync_tone_prompt_draft()
        cur = {k: styles.normalize_prompt(v) for k, v in self.tone_prompt_drafts.items()}
        init = {k: styles.normalize_prompt(v) for k, v in self.tone_prompt_initial.items()}
        return cur != init

    def defaultToneChanged_(self, sender):
        self.set_status("全局默认话术已修改，点击「保存配置」后立即生效。")

    def selectTonePrompt_(self, sender):
        self._sync_tone_prompt_draft()
        name = str(sender.titleOfSelectedItem())
        self.tone_prompt_selected = name if name in self.tone_prompt_drafts else ""
        self.tone_prompt_editor.setString_(self.tone_prompt_drafts.get(self.tone_prompt_selected, ""))
        self._update_tone_badge()

    def tonePromptEdited_(self, sender):
        self._sync_tone_prompt_draft()
        self._update_tone_badge()
        self.set_status("话术提示词已修改，点击「保存配置」后立即生效。")

    def resetTonePrompt_(self, sender):
        name = getattr(self, "tone_prompt_selected", "")
        if not name or self.tone_env_locked:
            return
        if name in styles.BUILTIN:
            self.tone_prompt_drafts[name] = styles.BUILTIN[name]
            self.tone_prompt_editor.setString_(styles.BUILTIN[name])
            self._update_tone_badge()
            self.set_status(f"「{name}」已恢复内置提示词，点击「保存配置」后生效。")
            return
        self.tone_prompt_drafts.pop(name, None)
        self._refresh_tone_pickers()
        self.set_status(f"已移除自定义话术「{name}」，点击「保存配置」后生效。")

    def addCustomTone_(self, sender):
        if self.tone_env_locked:
            return
        self._sync_tone_prompt_draft()
        alert = A.NSAlert.alloc().init()
        alert.setMessageText_("新增自定义话术")
        alert.setInformativeText_("输入话术名称（不含 | 或 =，不能叫「不用」），创建后请在下方填写至少 10 字的提示词。")
        name_field = A.NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 260, 26))
        name_field.setPlaceholderString_("例如：摸鱼大师")
        alert.setAccessoryView_(name_field)
        alert.addButtonWithTitle_("创建")
        alert.addButtonWithTitle_("取消")
        if alert.runModal() != A.NSAlertFirstButtonReturn:
            return
        name = str(name_field.stringValue()).strip()
        if not name or name == styles.NONE_LABEL or any(ch in name for ch in ("|", "=", "\r", "\n", "\0")):
            self.set_status("话术名称无效：不能为空，不能叫「不用」，也不能含 | 或 =。", "error")
            return
        if name in self.tone_prompt_drafts:
            self._refresh_tone_pickers(selected=name)
            self.set_status(f"话术「{name}」已存在，已切换到该话术进行编辑。")
            return
        self.tone_prompt_drafts[name] = ""
        self._refresh_tone_pickers(selected=name)
        self.window.makeFirstResponder_(self.tone_prompt_editor)
        self.set_status(f"已新建话术「{name}」，请填写至少 {styles.MIN_TONE_DESC_CHARS} 字提示词后点击「保存配置」。")

    @objc.python_method
    def build_apps_tab(self):
        from apps import registry
        item = A.NSTabViewItem.alloc().initWithIdentifier_("APPS")
        item.setLabel_("启用软件")
        panel = A.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 690, 300))
        group = ui_style.make_surface(10, PALETTE["row"], PALETTE["edge"])
        group.setFrame_(NSMakeRect(12, 92, 666, 196))
        panel.addSubview_(group)
        title = self.label(panel, "全局启用哪些聊天软件", 26, 256, 490, 20, 13)
        title.setFont_(A.NSFont.boldSystemFontOfSize_(13))
        self.label(panel, "未勾选的软件在前台时不会读屏、不弹出悬浮窗，也不预热其读屏引擎（对应 JEV_ENABLED_APPS）。",
                   26, 234, 638, 18, 11, PALETTE["muted"])
        divider = ui_style.make_surface(0, PALETTE["edge"])
        divider.setFrame_(NSMakeRect(26, 224, 638, 1))
        panel.addSubview_(divider)
        enabled = set(registry.enabled_app_keys())
        env_locked = userconfig.source_of(registry.ENABLED_APPS_VAR) == "环境变量"
        self.app_switches: dict[str, object] = {}
        y = 182
        for app in registry.APPS:
            btn = A.NSButton.alloc().initWithFrame_(NSMakeRect(26, y, 240, 22))
            btn.setButtonType_(A.NSButtonTypeSwitch)
            label = f"{registry.app_label(app)}（{app.key}）"
            btn.setTitle_(label)
            btn.setFont_(A.NSFont.boldSystemFontOfSize_(12))
            btn.setState_(A.NSControlStateValueOn if app.key in enabled else A.NSControlStateValueOff)
            btn.setTarget_(self)
            btn.setAction_("appToggleChanged:")
            btn.setAccessibilityLabel_(f"启用 {label}")
            if env_locked:
                btn.setEnabled_(False)
                btn.setToolTip_("由启动环境变量 JEV_ENABLED_APPS 控制；修改环境变量后重启。")
            panel.addSubview_(btn)
            self.app_switches[app.key] = btn
            desc = ("截图 + Vision OCR（需屏幕录制权限）" if app.needs_screen_capture
                    else "系统无障碍文本接口直读（需辅助功能权限，不截图）")
            self.label(panel, desc, 48, y - 18, 600, 16, 11, PALETTE["muted"])
            y -= 52
        self.initial[registry.ENABLED_APPS_VAR] = registry.format_enabled_apps(enabled)
        self.label(panel, "保存后立即生效，无需重启应用；全部取消勾选将全局暂停对所有聊天软件的自动响应。",
                   18, 56, 650, 24, 11, PALETTE["muted"])
        item.setView_(panel)
        self.tabs.addTabViewItem_(item)

    def appToggleChanged_(self, sender):
        self.set_status("启用软件已修改，点击「保存配置」后立即生效。")

    @objc.python_method
    def refresh_contexts(self):
        drafts = self.changed_backgrounds()
        store = self.hud.reload_conversations() if self.hud else None
        data = store.data if store is not None else {}
        current = ((self.hud._last_full or {}).get("chat_title") or "") if self.hud else ""
        drafts = {title: text for title, text in drafts.items()
                  if text != data.get(title, {}).get('background', '')}
        titles = sorted(set(data) | set(drafts) | ({current} if current else set())) if store is not None else []
        selected = self.context_title if self.context_title in titles else current
        self.background_initial = {title: data.get(title, {}).get('background', '') for title in titles}
        self.background_drafts = drafts
        # Untouched cached text is not a draft and must not revive an externally deleted background.
        self.context_title = ""
        self.context_picker.removeAllItems()
        self.context_picker.addItemsWithTitles_(titles or ["暂无已保存的会话"])
        if selected in titles:
            self.context_picker.selectItemWithTitle_(selected)
        self.context_picker.setEnabled_(bool(titles))
        self.background_editor.setEditable_(bool(titles))
        self.selectContext_(self.context_picker)
        self.history_menu.setEnabled_(store is not None and not store.error)
        self.history_menu.itemAtIndex_(1).setEnabled_(bool(titles))
        self.history_menu.itemAtIndex_(2).setEnabled_(store is not None)
        if self.history_menu.numberOfItems() > 3:
            self.history_menu.itemAtIndex_(3).setEnabled_(bool(titles))
        error = store.error if store is not None else ""
        if error:
            self.set_status(error, "error")
        elif getattr(self, '_context_error', ''):
            self.set_status("本地会话文件已恢复。" + ("背景草稿仍需点击「保存配置」。" if drafts else ""))
        self._context_error = error

    def windowDidBecomeKey_(self, notification):
        self.refresh_contexts()

    def selectContext_(self, sender):
        if self.context_title:
            self.background_drafts[self.context_title] = str(self.background_editor.string())
        self.context_title = str(sender.titleOfSelectedItem()) if sender.isEnabled() else ""
        self.background_editor.setString_(self.background_drafts.get(
            self.context_title, self.background_initial.get(self.context_title, "")))

    @objc.python_method
    def changed_backgrounds(self):
        if self.context_title:
            self.background_drafts[self.context_title] = str(self.background_editor.string())
        return {title: text for title, text in self.background_drafts.items()
                if text != self.background_initial[title]}

    def contextControlChanged_(self, sender):
        self.set_status("会话记录与背景已修改，点击「保存配置」后生效。")

    def candidateCountChanged_(self, sender):
        self.set_status("候选数量已修改，点击「保存配置」后重启生效。")

    def textDidChange_(self, notification):
        if notification is not None and notification.object() == getattr(self, "tone_prompt_editor", None):
            self.tonePromptEdited_(None)
            return
        self.contextControlChanged_(None)

    def clearCurrentHistory_(self, sender):
        self.context_action("current")

    def clearAllHistory_(self, sender):
        self.context_action("all")

    def resetCurrentChatTones_(self, sender):
        if not self.hud or not self.context_title:
            return
        try:
            self.hud.reset_chat_tones(self.context_title)
        except (OSError, ValueError):
            self.set_status((self.hud.conversations.error if self.hud.conversations else "")
                            or "恢复失败，请检查本地数据权限及可用磁盘空间。", "error")
            return
        self.set_status(f"「{self.context_title}」已恢复跟随全局默认话术。", "success")

    @objc.python_method
    def context_action(self, action):
        try:
            self.hud.clear_history(self.context_title if action == "current" else None)
        except (OSError, ValueError):
            self.set_status((self.hud.conversations.error if self.hud.conversations else "")
                            or "保存失败，请检查本地数据权限及可用磁盘空间。", "error")
            return
        self.refresh_contexts()
        self.set_status("记录已清空，背景保留；后续有效读屏按开关继续记录。", "success")

    @objc.python_method
    def set_status(self, text, kind="info"):
        colors = {"info": PALETTE["muted"],
                  "success": PALETTE["green"],
                  "error": PALETTE["red"]}
        self.status.setStringValue_(text)
        self.status.setTextColor_(colors[kind])
        self.status.setFont_(A.NSFont.boldSystemFontOfSize_(11))

    @objc.python_method
    def set_models(self, combo, models):
        current = combo.stringValue()
        combo.removeAllItems()
        combo.addItemsWithObjectValues_(models or ["暂无"])
        combo.setStringValue_(current)

    def comboBoxWillPopUp_(self, notification):
        self.model_before_popup = notification.object().stringValue()

    def comboBoxSelectionDidChange_(self, notification):
        combo = notification.object()
        if list(combo.objectValues()) == ["暂无"]:
            combo.deselectItemAtIndex_(0)
            combo.setStringValue_(getattr(self, "model_before_popup", ""))
        else:
            self.set_status("模型已修改，请重新测试；保存后重启生效。")

    @objc.python_method
    def current_source(self, prefix):
        if prefix == "TYPESAFE":
            source = userconfig.source_of("TYPESAFE_API_KEY", "JEV_API_KEY")
            summary = ("本次启动：正在使用自己的 Jev 密钥" if source != "none"
                       else "本次启动：正在使用本地判断模型，未使用 Jev 密钥")
        else:
            oai = userconfig.provider("OPENAI")
            anth = userconfig.provider("ANTHROPIC")
            selected = "OPENAI" if oai["key"] else "ANTHROPIC" if anth["key"] else None
            if selected:
                name = "OpenAI 兼容" if selected == "OPENAI" else "Anthropic 兼容"
                summary = "本次启动：正在使用自己的密钥（" + name + "）"
                source = (oai if selected == "OPENAI" else anth)["source"]
                if selected != prefix:
                    source += "；本页服务当前未启用"
            else:
                summary = ("本次启动：正在使用内置共享密钥" if builtin.API_KEY
                           else "本次启动：未配置生成密钥")
                source = "应用内置" if builtin.API_KEY else "none"
        detail = "来源：" + source.replace(str(Path.home()), "~") + "\n以下编辑内容保存后，需重启应用才会生效。"
        return summary, detail

    @objc.python_method
    def label(self, view, text, x, y, w, h, size=13, color=None):
        field = ui_style.make_label(text, x, y, w, h, size, color)
        field.cell().setWraps_(True)
        view.addSubview_(field)
        return field

    @objc.python_method
    def style_field(self, field):
        field.setFont_(A.NSFont.systemFontOfSize_(12))
        field.setTextColor_(PALETTE["text"])
        field.setBackgroundColor_(PALETTE["field"])
        field.setWantsLayer_(True)
        field.layer().setBorderColor_(PALETTE["edge"].CGColor())
        field.layer().setBorderWidth_(0.75)
        field.layer().setCornerRadius_(ui_style.RADIUS_FIELD)

    @objc.python_method
    def button(self, view, title, action, x, y, width, primary=False):
        button = A.NSButton.alloc().initWithFrame_(NSMakeRect(x, y, width, 32))
        button.setTitle_(title)
        ui_style.style_button(button, font_size=11, radius=16, primary=primary)
        button.setTarget_(self)
        button.setAction_(action)
        view.addSubview_(button)
        return button

    @objc.python_method
    def refresh_offline_section(self):
        cached = judge.model_cached()
        if cached:
            text = f"离线判断模型：已下载（{judge.model_disk_usage() / 1e9:.1f} GB 磁盘占用）"
            if userconfig.get("JUDGE_BACKEND").strip().lower() == "cloud":
                text += " · 当前选择在线判断"
        else:
            text = "离线判断模型：未下载 · 启用后下次启动预热时下载（约 3.8 GB）"
        self.offline_label.setStringValue_(text)
        self.offline_delete_btn.setHidden_(not cached)
        self.offline_enable_btn.setHidden_(cached)

    def deleteOfflineModel_(self, sender):
        alert = A.NSAlert.alloc().init()
        alert.setMessageText_("删除离线判断模型？")
        alert.setInformativeText_("之后使用离线判断需重新下载（约 3.8 GB）。正在运行的应用不受影响，重启后生效。")
        alert.addButtonWithTitle_("删除")
        alert.addButtonWithTitle_("取消")
        if alert.runModal() != A.NSAlertFirstButtonReturn:
            return
        sender.setEnabled_(False)
        self.set_status("正在删除离线判断模型…")
        threading.Thread(target=self._delete_model_work, daemon=True).start()

    @objc.python_method
    def _delete_model_work(self):
        import shutil
        error = ""
        try:
            shutil.rmtree(judge.model_cache_dir())
        except OSError as e:
            error = str(e)
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "modelDeleted:", error, False)

    def modelDeleted_(self, error):
        self.offline_delete_btn.setEnabled_(True)
        self.refresh_offline_section()
        if error:
            self.set_status(f"删除失败：{error[:80]}", "error")
        else:
            self.set_status("已删除离线判断模型。正在运行的判断不受影响；删除的文件不可恢复。", "success")

    def enableOfflineModel_(self, sender):
        alert = A.NSAlert.alloc().init()
        alert.setMessageText_("启用离线判断？")
        alert.setInformativeText_("下次启动的预热将下载判断模型（约 3.8 GB，一次性），之后判断完全离线进行。")
        alert.addButtonWithTitle_("启用")
        alert.addButtonWithTitle_("取消")
        if alert.runModal() != A.NSAlertFirstButtonReturn:
            return
        try:
            self.original = config.write_settings(self.path, self.original,
                                                  {"JUDGE_BACKEND": "local"})
        except ValueError as e:
            self.set_status(str(e), "error")
            return
        except OSError:
            self.set_status("保存失败：请检查文件权限及可用磁盘空间。", "error")
            return
        self.file_values["JUDGE_BACKEND"] = "local"
        self.refresh_offline_section()
        self.set_status("已启用离线判断（写入 JUDGE_BACKEND=local）。请退出并重新打开应用，预热时开始下载。",
                        "success")

    @objc.python_method
    def show(self):
        self.refresh_contexts()
        self.window.makeKeyAndOrderFront_(None)
        A.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    @objc.python_method
    def values(self, prefix):
        return {k: str(v.stringValue()) for k, v in self.fields[prefix].items()}

    @objc.python_method
    def changed(self):
        from apps import registry
        values = {f"{p}_{k}": v for p in config.PREFIXES for k, v in self.values(p).items()}
        if self.history_switch.isEnabled():
            values["JEV_HISTORY"] = "1" if self.history_switch.state() == A.NSOnState else "0"
        if self.context_count.isEnabled():
            values["JEV_CONTEXT_MESSAGES"] = str(self.context_count.stringValue()).strip()
        if self.candidate_count.isEnabled():
            values["JEV_CANDIDATES_PER_TONE"] = str(self.candidate_count.titleOfSelectedItem())
        if hasattr(self, "default_tone_pickers") and any(p.isEnabled() for p in self.default_tone_pickers):
            picked_defaults = [str(p.titleOfSelectedItem() or styles.NONE_LABEL) for p in self.default_tone_pickers]
            values[styles.DEFAULT_SLOTS_VAR] = styles.format_default_slots(picked_defaults)
        if hasattr(self, "app_switches") and any(btn.isEnabled() for btn in self.app_switches.values()):
            picked = [key for key, btn in self.app_switches.items()
                      if btn.state() == A.NSControlStateValueOn]
            values[registry.ENABLED_APPS_VAR] = registry.format_enabled_apps(picked)
        diff = {k: v for k, v in values.items() if v != self.initial[k]}
        if self.changed_tone_prompts():
            entries = []
            for name, raw_desc in self.tone_prompt_drafts.items():
                desc = styles.normalize_prompt(raw_desc)
                if name in styles.BUILTIN and desc == styles.BUILTIN[name]:
                    continue
                entries.append(f"{name}={desc}")
            diff[styles.CUSTOM_VAR] = "|".join(entries)
        return diff

    def controlTextDidChange_(self, notification):
        field = notification.object()
        if field == self.context_count:
            self.contextControlChanged_(field)
            return
        for fields in self.fields.values():
            if field in (fields["API_KEY"], fields["BASE_URL"]):
                combo = fields["MODEL"]
                self.set_models(combo, [])
        self.set_status("配置已修改，请重新测试；保存后重启生效。")

    def saveSettings_(self, sender):
        from chat_context import message_limit
        self.window.makeFirstResponder_(None)
        self.refresh_contexts()
        changes = self.changed()
        backgrounds = self.changed_backgrounds()
        if not changes and not backgrounds:
            if not self._context_error:
                self.set_status("没有需要保存的修改。")
            return
        # Persist missing displayed defaults for edited services, but keep untouched key lines.
        for prefix in config.PREFIXES:
            if any(k.startswith(prefix + "_") for k in changes):
                changes.update({f"{prefix}_{k}": v for k, v in self.values(prefix).items()
                                if k != "API_KEY" and f"{prefix}_{k}" not in self.file_values})
        try:
            for prefix in config.PREFIXES:
                if any(k.startswith(prefix + "_") for k in changes):
                    vals = self.values(prefix)
                    if vals["API_KEY"] and (not vals["BASE_URL"].strip() or not vals["MODEL"].strip()):
                        raise ValueError("填写密钥后，请同时填写该服务的地址和模型。")
            for key, value in changes.items():
                if key.endswith("_BASE_URL") and value:
                    config.validate_endpoint(value)
            if changes:
                self.original = config.write_settings(self.path, self.original, changes)
        except ValueError as e:
            self.set_status(str(e), "error")
            return
        except OSError:
            self.set_status("保存失败：请检查文件权限及可用磁盘空间。", "error")
            return
        self.initial.update({k: v for k, v in changes.items() if k in self.initial})
        self.file_values.update(changes)
        context_changes = {k: v for k, v in changes.items() if k in ("JEV_HISTORY", "JEV_CONTEXT_MESSAGES")}
        tone_changes = {k: v for k, v in changes.items() if k in (styles.DEFAULT_SLOTS_VAR, styles.CUSTOM_VAR)}
        app_change = changes.get("JEV_ENABLED_APPS")
        for key, value in context_changes.items():
            userconfig.session_override(key, value)
        for key, value in tone_changes.items():
            userconfig.session_override(key, value)
        if app_change is not None:
            userconfig.session_override("JEV_ENABLED_APPS", app_change)
        if tone_changes:
            styles.reload()
            self.tone_prompt_initial = dict(styles.PRESETS)
            self.tone_prompt_drafts = dict(styles.PRESETS)
            self.initial[styles.DEFAULT_SLOTS_VAR] = styles.format_default_slots(styles.default_slots())
            self._refresh_tone_pickers(
                selected=getattr(self, "tone_prompt_selected", None),
                default_slots=styles.default_slots())
            if self.hud and hasattr(self.hud, "configure_tones"):
                self.hud.configure_tones()
        if context_changes and self.hud:
            try:
                effective_count = message_limit(userconfig.get("JEV_CONTEXT_MESSAGES") or "20")
            except ValueError:
                effective_count = 20
            self.hud.configure_context(userconfig.get("JEV_HISTORY") == "1", str(effective_count))
        if app_change is not None and self.hud and hasattr(self.hud, "configure_enabled_apps"):
            self.hud.configure_enabled_apps()
        saved = bool(changes)
        try:
            for title, text in backgrounds.items():
                self.hud.save_background(title, text)
                self.background_initial[title] = text
                saved = True
        except (ValueError, OSError):
            self.set_status(("部分配置已保存；" if saved else "保存失败；")
                            + ((self.hud.conversations.error if self.hud.conversations else "")
                               or "请检查本地数据权限或磁盘空间后重试。") + "背景草稿仍保留。", "error")
            return
        live_keys = (set(context_changes)
                     | set(tone_changes)
                     | ({"JEV_ENABLED_APPS"} if app_change is not None else set()))
        model_changes = changes.keys() - live_keys
        if model_changes and not (live_keys or backgrounds):
            self.set_status("已保存。请退出并重新打开应用；当前会话继续使用启动时的配置。", "success")
        else:
            self.set_status("话术、会话与软件设置已保存并立即生效。" + ("模型配置需退出并重新打开应用。" if model_changes else ""), "success")

    def fetchModels_(self, sender):
        self.start_request(sender.tag(), True)

    def testConnection_(self, sender):
        self.start_request(sender.tag(), False)

    @objc.python_method
    def start_request(self, index, listing):
        if self.busy:
            return
        self.window.makeFirstResponder_(None)
        prefix = config.PREFIXES[index]
        values = self.values(prefix)
        try:
            config.validate_endpoint(values["BASE_URL"])
            if not values["API_KEY"]:
                raise ValueError("请填写密钥；Ollama 可填写 ollama。")
            if not listing and not values["MODEL"].strip():
                raise ValueError("请填写模型后再测试。")
            extra = None
            if not listing and prefix == "OPENAI":
                # Match generation's current extra-body setting, without changing it.
                raw = userconfig.get("OPENAI_EXTRA_BODY") or builtin.EXTRA_BODY
                extra = json.loads(raw) if raw else {}
                if not isinstance(extra, dict):
                    raise ValueError("OPENAI_EXTRA_BODY 必须是 JSON 对象。")
        except json.JSONDecodeError:
            self.set_status("OPENAI_EXTRA_BODY 不是有效 JSON，请先修正该配置。", "error")
            return
        except ValueError as e:
            self.set_status(str(e), "error")
            return
        if listing:
            combo = self.fields[prefix]["MODEL"]
            self.set_models(combo, [])
        self.busy = True
        for control in self.controls:
            control.setEnabled_(False)
        self.set_status("正在获取模型列表…" if listing else "正在测试所填服务与模型…")

        def work():
            result = {"index": index, "listing": listing}
            try:
                args = (prefix, values["BASE_URL"], values["API_KEY"])
                if listing:
                    result["models"] = config.list_models(*args)
                else:
                    config.test_connection(*args, values["MODEL"], extra)
            except Exception as e:
                result["error"] = config.error_message(e)
            self.performSelectorOnMainThread_withObject_waitUntilDone_("requestFinished:", result, False)
        threading.Thread(target=work, daemon=True).start()

    def requestFinished_(self, result):
        self.busy = False
        for control in self.controls:
            control.setEnabled_(True)
        if hasattr(self, "tone_env_locked") and self.tone_env_locked:
            self.tone_reset_btn.setEnabled_(False)
            self.tone_add_btn.setEnabled_(False)
        elif hasattr(self, "_update_tone_badge"):
            self._update_tone_badge()
        if result.get("error"):
            self.set_status(result["error"] + (" 模型仍可手填。" if result["listing"] else ""), "error")
        elif result["listing"]:
            combo = self.fields[config.PREFIXES[result["index"]]]["MODEL"]
            self.set_models(combo, result["models"])
            self.set_status(f"已获取 {len(result['models'])} 个模型。请从下拉列表选择或手填，再测试连接。", "success")
        else:
            self.set_status("连接成功：所填服务与模型返回了有效结果。配置尚需保存并重启生效。", "success")

    def windowShouldClose_(self, sender):
        if self.busy:
            self.set_status("请求进行中，请等待结果后关闭。")
            return False
        if self.changed() or self.changed_backgrounds():
            alert = A.NSAlert.alloc().init()
            alert.setMessageText_("放弃尚未保存的配置？")
            alert.addButtonWithTitle_("继续编辑")
            alert.addButtonWithTitle_("放弃修改")
            return alert.runModal() == A.NSAlertSecondButtonReturn
        return True


if __name__ == "__main__":
    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyRegular)
    userconfig.load()
    controller = SettingsController.alloc().init().build()
    controller.show()
    app.run()
