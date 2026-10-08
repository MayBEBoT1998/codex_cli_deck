"""Modeless collaboration panel: any Agent can be the initiator."""
import uuid

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango

from .coordination import FINISHED, STATUS_NAMES


AGENT_STATUS = {"offline": "未连接", "connecting": "连接中", "idle": "空闲", "active": "执行中",
                "waiting": "等待审批或输入", "notLoaded": "未加载", "systemError": "连接错误"}
DELIVERY_NAMES = {"pending": "等待发起者空闲", "sending": "正在回传", "delivered": "已回传",
                  "disabled": "未开启", "not_delivered": "发起者已断开", "unknown": "送达状态待确认"}


class CoordinationPanel(Gtk.Window):
    def __init__(self, deck):
        super().__init__(title="Agent 协作", transient_for=deck)
        self.deck = deck
        self.manager = deck.coordinator
        self.set_default_size(760, 680)
        self.set_destroy_with_parent(True)
        self.rebuilding = False
        self.last_revision = -1
        self.selection = None
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=20)
        self.add(root)
        title = Gtk.Label(label="选择谁来发起，以及它可以控制哪些 Agent", xalign=0)
        title.get_style_context().add_class("settings-title")
        root.pack_start(title, False, False, 0)
        self.hint = Gtk.Label(label="勾选立即生效。取消勾选会撤销后续派发；已执行的任务可在下方取消。重启或切换会话后需重新勾选。", xalign=0)
        self.hint.set_line_wrap(True)
        self.hint.get_style_context().add_class("muted")
        root.pack_start(self.hint, False, False, 0)
        self.source = Gtk.ComboBoxText()
        self.source.connect("changed", self.source_changed)
        root.pack_start(self.source, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(160)
        self.targets = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroll.add(self.targets)
        root.pack_start(scroll, True, True, 0)
        prompt_hint = Gtk.Label(label="先勾选控制范围，再在发起者终端中描述任务；也可以在这里直接派发。同目录并行任务请分配不同文件。", xalign=0)
        prompt_hint.set_line_wrap(True)
        root.pack_start(prompt_hint, False, False, 0)
        task_row = Gtk.Box(spacing=8)
        self.target = Gtk.ComboBoxText()
        task_row.pack_start(self.target, True, True, 0)
        self.send = Gtk.Button(label="派发任务")
        self.send.get_style_context().add_class("primary")
        self.send.connect("clicked", self.dispatch)
        task_row.pack_end(self.send, False, False, 0)
        root.pack_start(task_row, False, False, 0)
        self.prompt = Gtk.TextView()
        self.prompt.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        prompt_scroll = Gtk.ScrolledWindow()
        prompt_scroll.set_min_content_height(80)
        prompt_scroll.add(self.prompt)
        root.pack_start(prompt_scroll, False, False, 0)
        self.feedback = Gtk.Label(xalign=0)
        self.feedback.set_line_wrap(True)
        root.pack_start(self.feedback, False, False, 0)
        self.store = Gtk.ListStore(str, str, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        for index, text in enumerate(("发起者", "执行者", "状态", "任务"), 1):
            renderer = Gtk.CellRendererText()
            renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
            column = Gtk.TreeViewColumn(text, renderer, text=index)
            column.set_expand(index == 4)
            column.set_max_width(280 if index == 4 else 145)
            self.tree.append_column(column)
        tasks_scroll = Gtk.ScrolledWindow()
        tasks_scroll.set_min_content_height(160)
        tasks_scroll.add(self.tree)
        root.pack_start(tasks_scroll, True, True, 0)
        buttons = Gtk.Box(spacing=8)
        for title, callback in (("查看结果", self.show_result), ("打开执行者", self.open_target), ("取消所选任务", self.cancel), ("重试结果回传", self.retry_delivery)):
            widget = Gtk.Button(label=title)
            widget.connect("clicked", callback)
            buttons.pack_start(widget, False, False, 0)
        root.pack_start(buttons, False, False, 0)
        self.tree.connect("row-activated", lambda *_: self.show_result())
        self.rebuild_agents()
        self.source.set_active_id(deck.active_id or "")
        self.show_all()
        self.refresh()
        self.timer = GLib.timeout_add(300, self.refresh)
        self.connect("destroy", self.destroyed)

    def destroyed(self, *_):
        GLib.source_remove(self.timer)
        self.deck.coordination_panel = None

    def rebuild_agents(self):
        selected = self.source.get_active_id()
        self.rebuilding = True
        self.source.remove_all()
        for agent in self.manager.agents.values():
            self.source.append(agent.id, "发起者：" + agent.name)
        self.source.set_active_id(selected if selected in self.manager.agents else self.deck.active_id or "")
        if self.source.get_active() < 0 and self.manager.agents:
            self.source.set_active(0)
        self.rebuilding = False
        self.build_targets()

    def source_changed(self, *_):
        if not self.rebuilding:
            self.build_targets()

    def build_targets(self):
        for child in self.targets.get_children():
            child.destroy()
        source = self.source.get_active_id()
        saved = self.target.get_active_id()
        self.target.remove_all()
        for agent in self.manager.agents.values():
            if agent.id == source:
                continue
            checked = agent.id in self.manager.grants.get(source, set())
            row = Gtk.Box(spacing=10, margin=4)
            session = self.deck.get_session(agent.id)
            if session:
                row.pack_start(Gtk.Image.new_from_pixbuf(self.deck.avatar_image(session.avatar, 36)), False, False, 0)
            tools = " · 协作工具已就绪" if agent.tools_ready else ""
            text = f"{agent.name}  ·  {AGENT_STATUS.get(agent.state, agent.state)}{tools}\n{agent.cwd}"
            toggle = Gtk.CheckButton(label=text)
            if agent.error:
                toggle.set_tooltip_text(agent.error)
            toggle.set_active(checked)
            toggle.connect("toggled", self.toggle, source, agent.id)
            row.pack_start(toggle, True, True, 0)
            self.targets.pack_start(row, False, False, 0)
            if checked:
                self.target.append(agent.id, agent.name)
        self.target.set_active_id(saved or "")
        if self.target.get_active() < 0:
            self.target.set_active(0)
        self.send.set_sensitive(bool(source and self.target.get_active_id()))
        self.targets.show_all()

    def toggle(self, widget, source, target):
        selected = set(self.manager.grants.get(source, set()))
        (selected.add if widget.get_active() else selected.discard)(target)
        try:
            self.manager.set_grants(source, selected)
            self.feedback.set_text("控制范围已更新；现在可以在发起者终端中让它调用 Deck 协作工具。")
        except ValueError as error:
            self.feedback.set_text(str(error))

    def dispatch(self, *_):
        buffer = self.prompt.get_buffer()
        prompt = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True).strip()
        try:
            task = self.manager.dispatch(self.source.get_active_id(), self.target.get_active_id(), prompt, uuid.uuid4().hex)
            buffer.set_text("")
            self.feedback.set_text(f"已排入任务 {task.id[:8]}，结果将回传给发起者。")
        except ValueError as error:
            self.feedback.set_text(str(error))

    def selected_task(self):
        model, iterator = self.tree.get_selection().get_selected()
        return self.manager.tasks.get(model[iterator][0]) if iterator else None

    def show_result(self, *_):
        task = self.selected_task()
        if not task:
            return
        dialog = Gtk.Dialog(title=f"任务 {task.id[:8]} · {STATUS_NAMES[task.status]}", transient_for=self)
        dialog.add_button("关闭", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(650, 450)
        text = Gtk.TextView(editable=False, cursor_visible=False)
        text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        delivery = DELIVERY_NAMES.get(task.delivery_status, task.delivery_status.replace("failed: ", "失败："))
        text.get_buffer().set_text(f"{task.source_name} → {task.target_name}\n回传：{delivery}\n\n任务：\n{task.prompt}\n\n结果：\n{task.result or '尚无结果'}\n\n{task.error}")
        scroll = Gtk.ScrolledWindow()
        scroll.add(text)
        dialog.get_content_area().pack_start(scroll, True, True, 0)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def open_target(self, *_):
        task = self.selected_task()
        if task and task.target in self.manager.agents:
            self.deck.activate(task.target)
            self.deck.present()

    def cancel(self, *_):
        task = self.selected_task()
        if task:
            try:
                self.manager.cancel(task.id)
                self.feedback.set_text("已处理取消请求；实际状态会在任务列表更新。")
            except ValueError as error:
                self.feedback.set_text(str(error))

    def retry_delivery(self, *_):
        task = self.selected_task()
        if task:
            try:
                self.manager.retry_delivery(task.id)
                self.feedback.set_text("结果已重新排队回传。")
            except ValueError as error:
                self.feedback.set_text(str(error))

    def refresh(self):
        if self.manager.closed:
            return False
        if self.last_revision != self.manager.revision:
            if self.manager.storage_error:
                self.feedback.set_text(self.manager.storage_error)
            self.last_revision = self.manager.revision
            task = self.selected_task()
            selected = task.id if task else None
            self.rebuild_agents()
            self.store.clear()
            for task in reversed(list(self.manager.tasks.values())[-100:]):
                iterator = self.store.append([task.id, task.source_name, task.target_name,
                                              STATUS_NAMES.get(task.status, task.status), task.prompt.replace("\n", " ")[:180]])
                if task.id == selected:
                    self.tree.get_selection().select_iter(iterator)
        return True
