#!/usr/bin/env python3
"""The chat window: past chats on the left, the conversation on the right.

pi does the talking to models (see pi_client.py and pi-extension/modes.ts); this
window shows the conversation, the model, the mode and whether the chat is
private, and asks you when pi needs a decision.
"""

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Xdp", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango, Xdp  # noqa: E402

import sessions  # noqa: E402
from markdown_view import MarkdownView  # noqa: E402
from model_picker import ModelPicker, model_key  # noqa: E402
from pi_client import WORK_FOLDER, Pi  # noqa: E402

STYLE = Path(__file__).resolve().parent / "style.css"
STYLE_DARK = STYLE.with_name("style-dark.css")
PREFERENCES = Path(GLib.get_user_config_dir()) / "local-llm-chat" / "preferences.json"
# The drawer's width, and its width with the chat list pinned beside the conversation.
DRAWER_WIDTH = 420
PINNED_WIDTH = DRAWER_WIDTH + 300
# The GNOME Shell extension in gnome-extension/ shows this window as a drawer.
DRAWER = ("org.gnome.Shell", "/io/github/nicolasmahn/LocalLlmChat", "io.github.nicolasmahn.LocalLlmChat.Drawer")
# The drawer slides away before a screenshot, so it is not in the picture.
SLIDE_OUT_MS = 300
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
LARGEST_ATTACHMENT = 20 * 1024 ** 2
TOOL_ICONS = {
    "fetch_url": "x-office-document-symbolic",
    "browse_open": "web-browser-symbolic",
    "browse_click": "input-mouse-symbolic",
    "browse_type": "input-keyboard-symbolic",
    "browse_scroll": "go-down-symbolic",
    "browse_back": "go-previous-symbolic",
    "browse_read": "x-office-document-symbolic",
    "switch_to_work": "folder-symbolic",
    "bash": "utilities-terminal-symbolic",
    "edit": "document-edit-symbolic",
    "write": "document-edit-symbolic",
    "grep": "system-search-symbolic",
    "find": "system-search-symbolic",
}


@dataclass
class Attachment:
    mime: str
    name: str
    data: bytes

    def part(self):
        return {"type": "image", "data": base64.b64encode(self.data).decode(), "mimeType": self.mime}


def picture(data, size):
    """An attached image at a fixed size, or an icon if GTK cannot draw it."""
    try:
        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        # An Image keeps a fixed size (keeping proportions); a Picture would grow to fill.
        return Gtk.Image(paintable=texture, pixel_size=size, css_classes=["card"])
    except GLib.Error:
        return Gtk.Image(icon_name="image-x-generic-symbolic", pixel_size=size // 2,
                         width_request=size, height_request=size, css_classes=["card"])


def describe_tool(name, args):
    """One line on what a tool call does, shown in the conversation."""
    match name:
        case "fetch_url":
            return f"Reading {args.get('url', '')}"
        case "browse_open":
            return f"Opening {args.get('url', '')}"
        case "browse_click":
            return f"Clicking {args.get('number', '')}"
        case "browse_type":
            return f"Typing “{args.get('text', '')}” into {args.get('number', '')}"
        case "browse_scroll":
            return f"Scrolling {args.get('direction', 'down')}"
        case "browse_back":
            return "Going back"
        case "browse_read":
            return "Reading the page"
        case "switch_to_work":
            return "Asking to switch to work mode"
        case "bash":
            return args.get("command", "")
        case _:
            return f"{name} {args.get('path', '')}".strip()


def home_relative(path):
    return f"~/{path.relative_to(Path.home())}"


class ChatWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, default_width=860, default_height=620)
        self.pending = []  # attachments for the next message
        self.replying = False  # pi answers one prompt at a time
        self.reply = None  # the answer being streamed in, and its text so far
        self.reply_text = ""
        self.session_file = None
        self.model = None  # key of pi's current model
        self.models = []  # models in the picker, in its order
        self.mode = "chat"
        self.private = False
        self.syncing = False  # set while showing pi's state, so it is not sent back as a choice
        self.switching = False  # set until pi has opened another chat and set it up

        self.model_picker = ModelPicker()
        self.model_picker.connect("chosen", self.on_model_chosen)
        self.toasts = Adw.ToastOverlay()

        # Sidebar: past chats, newest first.
        self.chat_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.chat_list.connect("row-activated", lambda _, row: self.open_chat(row.session))
        sidebar = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=self.chat_list, vexpand=True))
        self.sidebar_header = Adw.HeaderBar(show_title=False)
        self.pin = Gtk.ToggleButton(icon_name="view-pin-symbolic", visible=False,
                                    tooltip_text="Keep the chats beside the conversation")
        self.pin.connect("toggled", lambda pin: self.set_pinned(pin.get_active()))
        self.sidebar_header.pack_start(self.pin)
        sidebar.add_top_bar(self.sidebar_header)

        # Conversation, or an empty page saying what this chat can do.
        self.messages = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                                margin_start=16, margin_end=16, margin_top=16, margin_bottom=16)
        self.scroller = Gtk.ScrolledWindow(vexpand=True, child=Adw.Clamp(child=self.messages, maximum_size=760))
        # The view follows a reply as it grows while you are at the bottom; scroll up to
        # read, and it stays where you are until you scroll back down.
        self.following = True
        scrolling = self.scroller.get_vadjustment()
        scrolling.connect("value-changed", self.on_scrolled)
        scrolling.connect("changed", self.on_content_grown)
        self.empty_icon = Gtk.Image(pixel_size=48, css_classes=["dim-label"])
        self.empty_text = Gtk.Label(use_markup=True, wrap=True, justify=Gtk.Justification.CENTER,
                                    css_classes=["dim-label"])
        self.empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER,
                             margin_start=32, margin_end=32)
        self.empty.append(self.empty_icon)
        self.empty.append(self.empty_text)
        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.pages.add_named(self.empty, "empty")
        self.pages.add_named(self.scroller, "chat")

        self.header = header = Adw.HeaderBar(title_widget=self.model_picker)
        self.hide_button = Gtk.Button(icon_name="go-next-symbolic", tooltip_text="Hide", visible=False)
        self.hide_button.connect("clicked", lambda _: self.hide_drawer())
        header.pack_start(self.hide_button)
        # Leaving the drawer sits apart, at the left; the chats are at the right, where
        # their list slides in.
        sidebar_toggle = Gtk.ToggleButton(icon_name="sidebar-show-right-symbolic", tooltip_text="Chats")
        new_chat = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="New chat")
        new_chat.connect("clicked", lambda _: self.new_chat())
        header.pack_end(sidebar_toggle)
        header.pack_end(new_chat)

        content = Adw.ToolbarView(content=self.pages)
        content.add_top_bar(header)
        content.add_bottom_bar(Adw.Clamp(child=self.build_composer(), maximum_size=760))

        self.split = Adw.OverlaySplitView(sidebar=sidebar, content=content, min_sidebar_width=280,
                                          max_sidebar_width=300, sidebar_position=Gtk.PackType.END)
        self.split.connect("notify::show-sidebar", self.on_sidebar_shown)
        self.split.bind_property("show-sidebar", sidebar_toggle, "active",
                                 GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.BIDIRECTIONAL)
        # Narrow windows, such as the drawer, show one side at a time.
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 560sp"))
        narrow.add_setter(self.split, "collapsed", True)
        # Switching layout takes the message field off screen for a moment, and GTK moves the
        # focus on to a button; typing would land there. Put it back once the field returns.
        self.connect("notify::current-breakpoint", lambda *_: self.entry.connect("map", self.refocus))
        self.add_breakpoint(narrow)

        self.toasts.set_child(self.split)
        self.set_content(self.toasts)

        self.in_drawer = False  # set once the shell's drawer extension answers
        escape = Gtk.ShortcutController(scope=Gtk.ShortcutScope.MANAGED)
        escape.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
                                         action=Gtk.CallbackAction.new(self.on_escape)))
        self.add_controller(escape)
        self.portal = Xdp.Portal()
        self.pi = Pi(self.on_pi_event)
        self.connect("close-request", lambda _: self.pi.stop())
        # Local models come and go while the window lives; look again whenever it gets focus.
        self.connect("notify::is-active", lambda *_: self.is_active() and self.refresh_models())
        self.refresh_models()
        self.find_drawer()
        self.reload_chats()
        self.show_empty()
        self.update_send_button()
        self.entry.grab_focus()

    def find_drawer(self):
        """Asks the shell whether the drawer extension runs, now and whenever that changes."""
        shell, path, interface = DRAWER
        bus = self.get_application().get_dbus_connection()
        bus.signal_subscribe(shell, interface, "Present", path, None, Gio.DBusSignalFlags.NONE,
                             lambda *args: self.set_drawer(args[5][0]))
        bus.call(shell, path, "org.freedesktop.DBus.Introspectable", "Introspect", None,
                 GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 1000, None, self.on_drawer_found)

    def on_drawer_found(self, bus, result):
        try:
            self.set_drawer(DRAWER[2] in bus.call_finish(result)[0])
        except GLib.Error:
            self.set_drawer(False)

    def set_drawer(self, present):
        """Looks like a drawer while the shell shows it as one, and like a window otherwise."""
        self.in_drawer = present
        self.set_css_class("drawer", present)
        # The shell hides the drawer and brings it back; a close button at the screen's
        # corner would quit it by accident when you reach for another window's.
        for header in (self.header, self.sidebar_header):
            header.set_show_start_title_buttons(not present)
            header.set_show_end_title_buttons(not present)
        self.hide_button.set_visible(present)
        self.pin.set_visible(present)
        if present and self.preferences().get("pinned", False):
            self.pin.set_active(True)
            # Again even if it was pinned already: a restarted drawer has forgotten the width.
            self.set_pinned(True)

    def on_escape(self, *_):
        if not self.in_drawer:
            return False
        if self.split.get_collapsed() and self.split.get_show_sidebar():
            self.split.set_show_sidebar(False)
        else:
            self.hide_drawer()
        return True

    def hide_drawer(self):
        self.call_drawer("Hide")

    def call_drawer(self, method, parameters=None):
        shell, path, interface = DRAWER
        self.get_application().get_dbus_connection().call(
            shell, path, interface, method, parameters, None, Gio.DBusCallFlags.NONE, -1, None, None)

    def set_pinned(self, pinned):
        """Widens the drawer so the chat list fits beside the conversation, or narrows it."""
        self.save_preferences(pinned=pinned)
        if not pinned:
            self.split.set_show_sidebar(False)
        width = PINNED_WIDTH if pinned else DRAWER_WIDTH
        self.call_drawer("SetWidth", GLib.Variant("(u)", (width,)))
        if pinned:
            self.split.set_show_sidebar(True)

    def on_sidebar_shown(self, split, _param):
        # Closing a pinned list with the chats button unpins it, and narrows the drawer again.
        if self.pin.get_active() and not split.get_show_sidebar():
            self.pin.set_active(False)

    def preferences(self):
        try:
            return json.loads(PREFERENCES.read_text())
        except (OSError, ValueError):
            return {}

    def save_preferences(self, **changes):
        PREFERENCES.parent.mkdir(parents=True, exist_ok=True)
        PREFERENCES.write_text(json.dumps(self.preferences() | changes))

    def build_composer(self):
        """The message field, with what goes along with a message below it."""
        # The text starts where the icons below it do.
        self.entry = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                                  top_margin=8, bottom_margin=4, left_margin=9, right_margin=9)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.entry.add_controller(keys)
        # A TextView has no placeholder of its own.
        self.placeholder = Gtk.Label(label="Message", xalign=0, valign=Gtk.Align.START, margin_start=9,
                                     margin_top=8, can_target=False, css_classes=["dim-label"])
        self.entry.get_buffer().connect("changed", lambda _: self.update_send_button())
        # A visible scrollbar would keep the field two lines tall even when empty; long
        # messages still scroll, without one.
        field = Gtk.Overlay(child=Gtk.ScrolledWindow(
            child=self.entry, propagate_natural_height=True, max_content_height=200,
            hscrollbar_policy=Gtk.PolicyType.NEVER, vscrollbar_policy=Gtk.PolicyType.EXTERNAL))
        field.add_overlay(self.placeholder)

        self.attachments = Gtk.Box(spacing=6, margin_start=6, margin_top=6, visible=False)

        attach = Gtk.Button(icon_name="mail-attachment-symbolic", tooltip_text="Attach a picture",
                            valign=Gtk.Align.CENTER, css_classes=["flat", "circular"])
        attach.connect("clicked", self.on_attach)
        screenshot = Gtk.Button(icon_name="camera-photo-symbolic", tooltip_text="Attach a screenshot",
                                valign=Gtk.Align.CENTER, css_classes=["flat", "circular"])
        screenshot.connect("clicked", self.on_screenshot)
        self.mode_picker = Adw.ToggleGroup(css_classes=["round"], valign=Gtk.Align.CENTER)
        self.mode_picker.add(Adw.Toggle(name="chat", label="Chat",
                                        tooltip="Answers, and reads web pages unless private"))
        self.mode_picker.add(Adw.Toggle(name="work", label="Work",
                                        tooltip=f"Changes files and runs commands in {home_relative(WORK_FOLDER)}"))
        self.mode_picker.connect("notify::active-name", self.on_mode_chosen)
        self.private_toggle = Gtk.ToggleButton(
            icon_name="changes-prevent-symbolic", tooltip_text="Private: local models only, and no internet",
            valign=Gtk.Align.CENTER, css_classes=["flat", "circular", "private-toggle"])
        self.private_toggle.connect("toggled", self.on_private_chosen)
        self.send_button = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Send",
                                      valign=Gtk.Align.CENTER, css_classes=["circular", "suggested-action"])
        self.send_button.connect("clicked", lambda _: self.stop() if self.replying else self.send())

        # What you add to a message on the left; how it is sent on the right.
        additions = Gtk.Box(spacing=2, hexpand=True)
        additions.append(attach)
        additions.append(screenshot)
        sending = Gtk.Box(spacing=6)
        sending.append(self.private_toggle)
        sending.append(self.mode_picker)
        sending.append(self.send_button)
        controls = Gtk.Box()
        controls.append(additions)
        controls.append(sending)

        self.composer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=12,
                                margin_end=12, margin_bottom=12, margin_top=6, css_classes=["composer"])
        self.composer.append(self.attachments)
        self.composer.append(field)
        self.composer.append(controls)
        return self.composer

    def on_key(self, _controller, keyval, _keycode, state):
        # Enter sends; Shift+Enter starts a new line.
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not state & Gdk.ModifierType.SHIFT_MASK:
            if not self.replying:
                self.send()
            return True
        return False

    def refocus(self, entry):
        entry.disconnect_by_func(self.refocus)
        GLib.idle_add(self.focus_entry)

    def focus_entry(self):
        self.entry.grab_focus()
        return GLib.SOURCE_REMOVE

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))

    # --- Models, mode and privacy

    def refresh_models(self):
        self.pi.prompt("/local-models", on_response=lambda _: self.pi.send(
            {"type": "get_available_models"}, self.show_models))

    def show_models(self, response):
        models = response.get("data", {}).get("models", [])
        if self.private:
            models = [model for model in models if model["provider"] == "local"]
        # Local models first: they are the default, and the only ones in a private chat.
        self.models = sorted(models, key=lambda model: (model["provider"] != "local", model_key(model)))
        self.sync_state()

    def sync_state(self):
        """Shows the model and chat pi is on; it can change them itself."""
        self.pi.send({"type": "get_state"}, self.show_state)

    def show_state(self, response):
        state = response.get("data", {})
        self.session_file = state.get("sessionFile")
        self.model = model_key(state["model"]) if state.get("model") else None
        self.model_picker.show_models(self.models, self.model)
        self.show_empty()
        self.select_current_chat()

    def selected_model(self):
        return next((model for model in self.models if model_key(model) == self.model), None)

    def on_model_chosen(self, _picker, model):
        if model_key(model) != self.model:
            self.pi.send({"type": "set_model", "provider": model["provider"], "modelId": model["id"]},
                         lambda _: self.sync_state())

    def on_mode_chosen(self, *_):
        if not self.syncing:
            self.pi.prompt(f"/mode {self.mode_picker.get_active_name()}")

    def on_private_chosen(self, toggle):
        if not self.syncing:
            self.pi.prompt(f"/private {'on' if toggle.get_active() else 'off'}")

    def show_mode(self, mode):
        self.mode = mode
        self.syncing = True
        self.mode_picker.set_active_name(mode)
        self.syncing = False
        self.set_css_class("work", mode == "work")
        self.show_empty()

    def show_private(self, private):
        self.private = private
        self.syncing = True
        self.private_toggle.set_active(private)
        self.syncing = False
        self.set_css_class("private", private)
        self.placeholder.set_label("Private message" if private else "Message")
        self.refresh_models()

    def set_css_class(self, name, on):
        if on:
            self.add_css_class(name)
        else:
            self.remove_css_class(name)

    def show_empty(self):
        """What a new chat can do, since that depends on the mode, privacy and models."""
        if self.private and not self.models:
            icon, text = "changes-prevent-symbolic", "No local model is running. Start one with <tt>llm up</tt>."
        elif self.mode == "work":
            folder = home_relative(WORK_FOLDER)
            icon, text = "folder-symbolic", f"Changes files and runs commands in <tt>{folder}</tt>."
            if self.private:
                text += " Commands have no internet."
        elif self.private:
            icon, text = "changes-prevent-symbolic", "Local models only. Nothing leaves this computer."
        else:
            icon, text = "chat-message-new-symbolic", "How can I help?"
        self.empty_icon.set_from_icon_name(icon)
        self.empty_text.set_label(text)
        self.pages.set_visible_child_name("chat" if self.messages.get_first_child() else "empty")

    # --- Events from pi

    def on_pi_event(self, event):
        match event["type"]:
            case "message_start" if event["message"]["role"] == "assistant":
                self.reply = self.reply or self.add_reply()
                self.reply_text = ""
            case "message_update" if self.reply:
                update = event["assistantMessageEvent"]
                if update["type"] == "text_delta":
                    self.reply_text += update["delta"]
                    self.reply.set_markdown(self.reply_text.lstrip())
            case "message_end" if self.reply and event["message"]["role"] == "assistant":
                self.end_reply(event["message"])
            case "tool_execution_start":
                self.add_tool_note(event["toolName"], event["args"])
            case "tool_execution_end" if event["isError"]:
                self.add_tool_error(sessions.text_of(event["result"]["content"]))
            case "tool_execution_end":
                self.add_tool_images(event["result"])
            case "agent_settled":
                self.set_replying(False)
                self.reload_chats()
                self.sync_state()
            case "extension_ui_request":
                self.on_ui_request(event)
            case "extension_error":
                self.toast(event.get("error", "The pi extension failed"))
            case "exit":
                self.set_replying(False)
                self.add_error("pi has stopped; open the chat again to restart it.")

    def end_reply(self, message):
        if message.get("stopReason") == "error":
            self.reply.show_note(message.get("errorMessage") or "Failed", "error")
        elif not self.reply_text.strip():
            self.messages.remove(self.reply)  # stopped, or only tool calls; their notes follow
        self.reply = None

    def on_ui_request(self, request):
        match request["method"]:
            case "setStatus" if request.get("statusKey") == "mode":
                self.show_mode(request["statusText"])
            case "setStatus" if request.get("statusKey") == "private":
                self.show_private(request["statusText"] == "on")
            case "notify":
                self.toast(request["message"])
                # pi turned the message away without a run (for example, private without a
                # local model), so no answer will come.
                if request.get("notifyType") == "error" and self.reply and not self.reply_text:
                    self.messages.remove(self.reply)
                    self.reply = None
                    self.set_replying(False)
            case "confirm":
                dialog = Adw.AlertDialog(heading=request["title"], body=request.get("message", ""))
                dialog.add_response("no", "No")
                dialog.add_response("yes", "Yes")
                dialog.set_response_appearance("yes", Adw.ResponseAppearance.SUGGESTED)
                dialog.connect("response", lambda _, answer: self.pi.send(
                    {"type": "extension_ui_response", "id": request["id"], "confirmed": answer == "yes"}))
                dialog.present(self)
            case "select" | "input" | "editor":
                # Nothing in modes.ts asks these; answer so pi does not wait forever.
                self.pi.send({"type": "extension_ui_response", "id": request["id"], "cancelled": True})

    # --- Chat history

    def reload_chats(self):
        self.chat_list.remove_all()
        for session in sessions.load_all():
            created = datetime.fromisoformat(session.created).astimezone()
            row = Adw.ActionRow(title=session.title, subtitle=created.strftime("%Y-%m-%d %H:%M"),
                                use_markup=False, activatable=True, title_lines=1,
                                css_classes=["chat-row"])
            # Plain chats get an empty icon, so all titles line up.
            icon = Gtk.Image(pixel_size=16)
            # In the color the window takes for that kind of chat.
            if session.private and session.work:
                icon.set_from_icon_name("changes-prevent-symbolic")
                icon.set_tooltip_text("Private work")
                icon.add_css_class("private-work-chat")
            elif session.private:
                icon.set_from_icon_name("changes-prevent-symbolic")
                icon.set_tooltip_text("Private")
                icon.add_css_class("private-chat")
            elif session.work:
                icon.set_from_icon_name("folder-symbolic")
                icon.set_tooltip_text("Work")
                icon.add_css_class("work-chat")
            row.add_prefix(icon)
            delete = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete",
                                valign=Gtk.Align.CENTER, css_classes=["flat", "delete"])
            delete.connect("clicked", lambda _, session=session: self.delete_chat(session))
            row.add_suffix(delete)
            row.session = session
            self.chat_list.append(row)
        self.select_current_chat()

    def select_current_chat(self):
        index = 0
        while row := self.chat_list.get_row_at_index(index):
            if str(row.session.path) == self.session_file:
                self.chat_list.select_row(row)
                return
            index += 1
        self.chat_list.unselect_all()

    def new_chat(self):
        if self.busy():
            return
        # A new chat keeps the mode and privacy you chose; pi starts each one in plain chat.
        # One step after the other, so nothing is sent into a chat half set up.
        mode, private = self.mode, self.private
        self.switching = True
        self.pi.send({"type": "new_session"}, lambda _: self.pi.prompt(
            f"/mode {mode}", on_response=lambda _: self.pi.prompt(
                f"/private {'on' if private else 'off'}", on_response=lambda _: self.show_chat([]))))

    def open_chat(self, session):
        if self.busy():
            return
        self.switching = True
        self.pi.send({"type": "switch_session", "sessionPath": str(session.path)}, lambda _: self.pi.send(
            {"type": "get_messages"}, lambda response: self.show_chat(response["data"]["messages"])))

    def delete_chat(self, session):
        session.trash()
        if str(session.path) == self.session_file:
            self.new_chat()
        else:
            self.reload_chats()

    def busy(self):
        if self.replying:
            self.toast("Wait for the answer first")
        return self.replying or self.switching

    def show_chat(self, messages):
        self.switching = False
        self.following = True
        while child := self.messages.get_first_child():
            self.messages.remove(child)
        for message in messages:
            if message["role"] == "user":
                content = message["content"]
                images = [] if isinstance(content, str) else [
                    base64.b64decode(part["data"]) for part in content if part["type"] == "image"]
                self.add_user_message(sessions.text_of(content), images)
            elif message["role"] == "assistant":
                text = "".join(part["text"] for part in message["content"] if part["type"] == "text")
                if text.strip():
                    self.add_reply(text.strip())
                for part in message["content"]:
                    if part["type"] == "toolCall":
                        self.add_tool_note(part["name"], part["arguments"])
                if message.get("stopReason") == "error":
                    self.add_error(message.get("errorMessage") or "Failed")
            elif message["role"] == "toolResult":
                self.add_tool_images(message)
        self.sync_state()
        self.reload_chats()
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)
        self.entry.grab_focus()

    # --- Messages

    def append(self, widget):
        self.messages.append(widget)
        self.pages.set_visible_child_name("chat")

    def add_user_message(self, text, images=()):
        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.END,
                         margin_start=48, css_classes=["user-message"])
        if images:
            row = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                              halign=Gtk.Align.END)
            for data in images:
                row.append(picture(data, 120))
            bubble.append(row)
        if text:
            bubble.append(Gtk.Label(label=text, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                    selectable=True, xalign=0))
        self.following = True  # what you send, and the answer to it, is what you want to see
        self.append(bubble)

    def add_reply(self, markdown=""):
        reply = MarkdownView(markdown)
        self.append(reply)
        return reply

    def add_tool_note(self, name, args):
        # One quiet line per step; the whole of it on hover.
        text = describe_tool(name, args)
        note = Gtk.Box(spacing=8, css_classes=["note", "dim-label"], tooltip_text=text)
        note.append(Gtk.Image(icon_name=TOOL_ICONS.get(name, "document-open-symbolic")))
        note.append(Gtk.Label(label=text, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                              css_classes=["monospace"] if name == "bash" else []))
        self.append(note)

    def add_tool_images(self, result):
        """What the browser showed, so you see it too: click a step to see it whole."""
        screenshot = (result.get("details") or {}).get("screenshot")
        if not screenshot:
            return
        data = base64.b64decode(screenshot)
        image = picture(data, 200)
        image.set_halign(Gtk.Align.START)
        image.set_cursor(Gdk.Cursor.new_from_name("zoom-in"))
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self.show_picture(data))
        image.add_controller(click)
        self.append(image)

    def show_picture(self, data):
        # As large as the window allows, and whole: in the drawer that is its width.
        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        view = Adw.ToolbarView(content=Gtk.Picture(paintable=texture, content_fit=Gtk.ContentFit.CONTAIN))
        view.add_top_bar(Adw.HeaderBar(show_title=False))
        Adw.Dialog(child=view, content_width=texture.get_width(), content_height=texture.get_height()).present(self)

    def add_tool_error(self, text):
        # Tool output can be long; the model reads all of it and explains, so one line does here.
        lines = [line for line in text.splitlines() if line.strip()] or ["Failed"]
        note = Gtk.Box(spacing=8, css_classes=["note", "error"], tooltip_text=text[-2000:])
        note.append(Gtk.Image(icon_name="dialog-warning-symbolic"))
        note.append(Gtk.Label(label=lines[-1], xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
        self.append(note)

    def add_error(self, text):
        self.append(Gtk.Label(label=text, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, xalign=0,
                              selectable=True, css_classes=["note", "error"]))

    def on_scrolled(self, scrolling):
        self.following = scrolling.get_value() >= scrolling.get_upper() - scrolling.get_page_size() - 24

    def on_content_grown(self, scrolling):
        if self.following:
            scrolling.set_value(scrolling.get_upper() - scrolling.get_page_size())

    def set_replying(self, replying):
        self.replying = replying
        self.update_send_button()

    def update_send_button(self):
        """Send while there is something to send; stop while pi answers."""
        empty = self.entry.get_buffer().get_char_count() == 0
        self.placeholder.set_visible(empty)
        self.send_button.set_sensitive(self.replying or not empty or bool(self.pending))
        self.send_button.set_icon_name("media-playback-stop-symbolic" if self.replying else "go-up-symbolic")
        self.send_button.set_tooltip_text("Stop" if self.replying else "Send")

    def send(self):
        buffer = self.entry.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).strip()
        model = self.selected_model()
        if not model or not (text or self.pending) or self.busy():
            return
        if self.private and model["provider"] != "local":
            self.toast("Private chats use local models only")  # the model list is still being updated
            return
        if self.pending and "image" not in model["input"]:
            self.toast(f"{model['name']} cannot see pictures")
            return
        images = [attachment.part() for attachment in self.pending]
        self.add_user_message(text, [attachment.data for attachment in self.pending])
        self.pending = []
        self.show_pending()
        buffer.set_text("")
        self.reply = self.add_reply()
        self.reply.show_waiting()
        self.reply_text = ""
        self.set_replying(True)
        self.pi.prompt(text, images, on_response=self.on_prompt_response)

    def stop(self):
        self.pi.send({"type": "abort"})

    def on_prompt_response(self, response):
        if not response["success"]:
            self.set_replying(False)
            if self.reply:
                self.reply.show_note(response.get("error", "pi did not take the message"), "error")
                self.reply = None

    # --- Attachments

    def can_see_pictures(self):
        model = self.selected_model()
        if not model or "image" not in model["input"]:
            self.toast("This model cannot see pictures")
            return False
        return True

    def on_attach(self, _button):
        if not self.can_see_pictures():
            return
        images = Gtk.FileFilter(name="Pictures")
        for mime in IMAGE_TYPES:
            images.add_mime_type(mime)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(images)
        dialog = Gtk.FileDialog(title="Attach", filters=filters, default_filter=images)
        dialog.open_multiple(self, None, self.on_files_chosen)

    def on_screenshot(self, _button):
        # GNOME's own screenshot tool, as with the Print key: you pick an area, a
        # window or a whole screen, on any monitor.
        if not self.can_see_pictures():
            return
        if self.in_drawer:
            self.hide_drawer()
            GLib.timeout_add(SLIDE_OUT_MS, self.take_screenshot)
        else:
            self.take_screenshot()

    def take_screenshot(self):
        self.portal.take_screenshot(None, Xdp.ScreenshotFlags.INTERACTIVE, None, self.on_screenshot_taken)
        return GLib.SOURCE_REMOVE

    def on_screenshot_taken(self, portal, result):
        if self.in_drawer:
            self.call_drawer("Show")
        try:
            uri = portal.take_screenshot_finish(result)
        except GLib.Error:
            return  # cancelled
        self.add_attachment(Gio.File.new_for_uri(uri))

    def on_files_chosen(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error:
            return  # Closed without choosing.
        for index in range(files.get_n_items()):
            self.add_attachment(files.get_item(index))

    def add_attachment(self, file):
        info = file.query_info("standard::content-type,standard::size,standard::display-name",
                               Gio.FileQueryInfoFlags.NONE, None)
        name = info.get_display_name()
        mime = Gio.content_type_get_mime_type(info.get_content_type())
        if mime not in IMAGE_TYPES:
            self.toast(f"{name}: only PNG, JPEG, WebP or GIF")
            return
        if info.get_size() > LARGEST_ATTACHMENT:
            self.toast(f"{name} is larger than {LARGEST_ATTACHMENT // 1024 ** 2} MB")
            return
        data = file.load_bytes(None)[0].get_data()
        self.pending.append(Attachment(mime, name, data))
        self.show_pending()

    def show_pending(self):
        while child := self.attachments.get_first_child():
            self.attachments.remove(child)
        for attachment in self.pending:
            chip = Gtk.Overlay(child=picture(attachment.data, 56), tooltip_text=attachment.name)
            remove = Gtk.Button(icon_name="window-close-symbolic", halign=Gtk.Align.END,
                                valign=Gtk.Align.START, css_classes=["circular", "osd"])
            remove.connect("clicked", lambda _, a=attachment: self.remove_attachment(a))
            chip.add_overlay(remove)
            self.attachments.append(chip)
        self.attachments.set_visible(bool(self.pending))
        self.update_send_button()

    def remove_attachment(self, attachment):
        self.pending.remove(attachment)
        self.show_pending()


def present(app):
    window = app.props.active_window or ChatWindow(app)
    window.present()


def startup(_app):
    display = Gdk.Display.get_default()
    light = Gtk.CssProvider()
    light.load_from_path(str(STYLE))
    Gtk.StyleContext.add_provider_for_display(display, light, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    # GTK's prefers-color-scheme media query does not follow libadwaita's style, so the dark
    # colors come and go with the style manager, as Adwaita's own style-dark.css does.
    dark = Gtk.CssProvider()
    dark.load_from_path(str(STYLE_DARK))
    styles = Adw.StyleManager.get_default()

    def follow_style(*_):
        if styles.get_dark():
            Gtk.StyleContext.add_provider_for_display(display, dark, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        else:
            Gtk.StyleContext.remove_provider_for_display(display, dark)

    styles.connect("notify::dark", follow_style)
    follow_style()


def main():
    app = Adw.Application(application_id="io.github.nicolasmahn.LocalLlmChat")
    app.connect("startup", startup)
    app.connect("activate", present)
    app.run()


if __name__ == "__main__":
    main()
