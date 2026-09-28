#!/usr/bin/env python3
"""The chat window: past chats on the left, the conversation on the right.

Pictures and videos can be attached for models that can see them.
"""

import base64
import threading
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

import chats  # noqa: E402
import client  # noqa: E402

# The formats Gemma was tested with; everything else is turned away at the door.
MEDIA_TYPES = {
    "image/png": "image", "image/jpeg": "image", "image/webp": "image", "image/gif": "image",
    "video/mp4": "video", "video/webm": "video",
}
LARGEST_ATTACHMENT = 50 * 1024 ** 2


@dataclass
class Attachment:
    kind: str  # "image" or "video"
    mime: str
    name: str
    data: bytes

    def part(self):
        url = f"data:{self.mime};base64,{base64.b64encode(self.data).decode()}"
        return {"type": f"{self.kind}_url", f"{self.kind}_url": {"url": url}}


def preview(kind, data, size):
    """A picture of an attachment: the image itself, or an icon for a video."""
    if kind == "image":
        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
            # An Image keeps a fixed size (keeping proportions); a Picture would grow to fill.
            return Gtk.Image(paintable=texture, pixel_size=size, css_classes=["card"])
        except GLib.Error:
            pass  # A format GTK cannot draw still goes to the model; show an icon instead.
    icon = "video-x-generic-symbolic" if kind == "video" else "image-x-generic-symbolic"
    return Gtk.Image(icon_name=icon, pixel_size=size // 2, width_request=size, height_request=size,
                     css_classes=["card"])


def media_of(content):
    """(kind, bytes) of each attachment in a saved message."""
    if isinstance(content, str):
        return []
    media = []
    for part in content:
        if part["type"] in ("image_url", "video_url"):
            kind = part["type"].removesuffix("_url")
            data = base64.b64decode(part[part["type"]]["url"].split(",", 1)[1])
            media.append((kind, data))
    return media


class ChatWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, default_width=860, default_height=620)
        self.chat = chats.Chat()
        self.pending = []  # attachments for the next message
        self.replying = False  # one answer at a time keeps the saved turns in order

        self.model_picker = Gtk.DropDown()
        self.toasts = Adw.ToastOverlay()

        # Sidebar: past chats, newest first.
        new_chat = Gtk.Button(icon_name="chat-message-new-symbolic", tooltip_text="New chat")
        new_chat.connect("clicked", lambda _: self.open_chat(chats.Chat()))
        sidebar_header = Adw.HeaderBar(show_title=False)
        sidebar_header.pack_start(new_chat)
        self.chat_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.chat_list.connect("row-activated", lambda _, row: self.open_chat(row.chat))
        sidebar = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=self.chat_list, vexpand=True))
        sidebar.add_top_bar(sidebar_header)

        # Conversation.
        self.messages = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                                margin_start=12, margin_end=12, margin_top=12, margin_bottom=12)
        self.scroller = Gtk.ScrolledWindow(vexpand=True, child=self.messages)

        self.attachments = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_top=6,
                                   visible=False)
        self.entry = Gtk.Entry(hexpand=True, placeholder_text="Message")
        self.entry.connect("activate", lambda _: self.send())
        attach = Gtk.Button(icon_name="mail-attachment-symbolic", tooltip_text="Attach a picture or video")
        attach.connect("clicked", self.on_attach)
        send = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Send", css_classes=["suggested-action"])
        send.connect("clicked", lambda _: self.send())

        input_row = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_top=12, margin_bottom=12)
        for widget in (attach, self.entry, send):
            input_row.append(widget)
        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bottom.append(self.attachments)
        bottom.append(input_row)

        self.split = Adw.OverlaySplitView(sidebar=sidebar, min_sidebar_width=220)
        sidebar_toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text="Chats")
        self.split.bind_property("show-sidebar", sidebar_toggle, "active",
                                 GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.BIDIRECTIONAL)
        header = Adw.HeaderBar(title_widget=self.model_picker)
        header.pack_start(sidebar_toggle)
        content = Adw.ToolbarView(content=self.scroller)
        content.add_top_bar(header)
        content.add_bottom_bar(bottom)
        self.split.set_content(content)

        # Narrow windows show one side at a time.
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 560sp"))
        narrow.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(narrow)

        self.toasts.set_child(self.split)
        self.set_content(self.toasts)
        # Models come and go while the window lives; look again whenever it gets focus.
        self.connect("notify::is-active", lambda *_: self.is_active() and self.refresh_models())
        self.refresh_models()
        self.reload_chats()
        self.entry.grab_focus()

    # --- Models

    def refresh_models(self):
        try:
            models = client.running_models()
        except OSError:
            models = []
        current = self.selected_model()
        self.model_picker.set_model(Gtk.StringList.new(models or ["no models running"]))
        self.model_picker.set_sensitive(bool(models))
        if current in models:
            self.model_picker.set_selected(models.index(current))

    def selected_model(self):
        item = self.model_picker.get_selected_item()
        return item.get_string() if item and self.model_picker.get_sensitive() else None

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))

    # --- Chat history

    def reload_chats(self):
        self.chat_list.remove_all()
        for chat in chats.load_all():
            row = Adw.ActionRow(title=chat.title, subtitle=chat.created[:16].replace("T", " "),
                                use_markup=False, activatable=True, title_lines=1)
            delete = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete",
                                valign=Gtk.Align.CENTER, css_classes=["flat"])
            delete.connect("clicked", lambda _, chat=chat: self.delete_chat(chat))
            row.add_suffix(delete)
            row.chat = chat
            self.chat_list.append(row)
            if chat.id == self.chat.id:
                self.chat_list.select_row(row)

    def open_chat(self, chat):
        self.chat = chat
        while child := self.messages.get_first_child():
            self.messages.remove(child)
        for message in chat.messages:
            label = self.add_message(message["content"], from_user=message["role"] == "user")
            label.remove_css_class("dim-label")
        self.reload_chats()
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)
        self.entry.grab_focus()

    def delete_chat(self, chat):
        chat.delete()
        if chat.id == self.chat.id:
            self.open_chat(chats.Chat())
        else:
            self.reload_chats()

    # --- Messages

    def add_message(self, content, from_user):
        text = client.plain_text(content)
        label = Gtk.Label(label=text, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                          selectable=True, xalign=0)
        if not from_user:
            label.set_halign(Gtk.Align.FILL)
            self.messages.append(label)
            GLib.idle_add(self.scroll_to_end)
            return label

        # A card sets your own words apart, with any pictures above them; replies stay plain.
        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.END,
                         margin_start=48, css_classes=["card"])
        media = media_of(content)
        if media:
            row = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                              halign=Gtk.Align.START, margin_start=8, margin_end=8, margin_top=8)
            for kind, data in media:
                row.append(preview(kind, data, 120))
            bubble.append(row)
        if text:
            for side in ("start", "end"):
                getattr(label, f"set_margin_{side}")(12)
            for side in ("top", "bottom"):
                getattr(label, f"set_margin_{side}")(8)
            bubble.append(label)
        self.messages.append(bubble)
        GLib.idle_add(self.scroll_to_end)
        return label

    def scroll_to_end(self):
        adjustment = self.scroller.get_vadjustment()
        adjustment.set_value(adjustment.get_upper())

    def start_turn(self, text):
        """Adds your message to the chat; returns what a worker needs to fetch the reply."""
        model = self.selected_model()
        if not model or not (text or self.pending):
            return None
        if self.replying:
            self.toast("Wait for the answer first")
            return None
        if self.pending and model not in client.VISION_MODELS:
            self.toast(f"{model} cannot see attachments; pick {', '.join(sorted(client.VISION_MODELS))}")
            return None
        content = text
        if self.pending:
            content = [{"type": "text", "text": text}] if text else []
            content += [attachment.part() for attachment in self.pending]
        self.pending = []
        self.show_pending()
        self.chat.messages.append({"role": "user", "content": content})
        self.chat.save()
        self.reload_chats()
        self.add_message(content, from_user=True)
        label = self.add_message("…", from_user=False)
        label.add_css_class("dim-label")
        self.replying = True
        return self.chat, model, list(self.chat.messages), label

    def send(self):
        turn = self.start_turn(self.entry.get_text().strip())
        if turn:
            self.entry.set_text("")
            threading.Thread(target=self.reply, args=turn, daemon=True).start()

    def reply(self, chat, model, messages, label):
        """Worker thread: streams the answer into label and saves it."""
        try:
            answer = client.stream_reply(
                model, messages,
                on_text=lambda text: GLib.idle_add(self.show_reply, label, text),
                on_thinking=lambda: GLib.idle_add(label.set_label, "thinking…"))
        except OSError as error:
            GLib.idle_add(self.finish_turn, chat, label, None, f"Could not reach {model}: {error}")
            return
        GLib.idle_add(self.finish_turn, chat, label, answer, None)

    def finish_turn(self, chat, label, answer, error):
        self.replying = False
        if error:
            # Models expect you and them to take turns, so an unanswered message leaves.
            chat.messages.pop()
            self.show_reply(label, error)
            label.add_css_class("error")
        else:
            chat.messages.append({"role": "assistant", "content": answer})
        if chat.messages:
            chat.save()

    def show_reply(self, label, text):
        label.remove_css_class("dim-label")
        label.set_label(text)
        self.scroll_to_end()

    # --- Attachments

    def on_attach(self, _button):
        model = self.selected_model()
        if model not in client.VISION_MODELS:
            self.toast(f"Only {', '.join(sorted(client.VISION_MODELS))} can look at pictures and videos")
            return
        media = Gtk.FileFilter(name="Pictures and videos")
        for mime in MEDIA_TYPES:
            media.add_mime_type(mime)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(media)
        dialog = Gtk.FileDialog(title="Attach", filters=filters, default_filter=media)
        dialog.open_multiple(self, None, self.on_files_chosen)

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
        if mime not in MEDIA_TYPES:
            self.toast(f"{name}: only PNG, JPEG, WebP, GIF, MP4 or WebM")
            return
        if info.get_size() > LARGEST_ATTACHMENT:
            self.toast(f"{name} is larger than {LARGEST_ATTACHMENT // 1024 ** 2} MB")
            return
        data = file.load_bytes(None)[0].get_data()
        self.pending.append(Attachment(MEDIA_TYPES[mime], mime, name, data))
        self.show_pending()

    def show_pending(self):
        while child := self.attachments.get_first_child():
            self.attachments.remove(child)
        for attachment in self.pending:
            chip = Gtk.Overlay(child=preview(attachment.kind, attachment.data, 56),
                               tooltip_text=attachment.name)
            remove = Gtk.Button(icon_name="window-close-symbolic", halign=Gtk.Align.END,
                                valign=Gtk.Align.START, css_classes=["circular", "osd"])
            remove.connect("clicked", lambda _, a=attachment: self.remove_attachment(a))
            chip.add_overlay(remove)
            self.attachments.append(chip)
        self.attachments.set_visible(bool(self.pending))

    def remove_attachment(self, attachment):
        self.pending.remove(attachment)
        self.show_pending()


def present(app):
    window = app.props.active_window or ChatWindow(app)
    window.present()


def main():
    app = Adw.Application(application_id="io.github.nicolasmahn.LocalLlmChat")
    app.connect("activate", present)
    app.run()


if __name__ == "__main__":
    main()
