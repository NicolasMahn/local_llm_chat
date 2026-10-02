"""Replies written in Markdown, shown as GTK widgets."""

from gi.repository import Adw, GLib, Gtk, Pango
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

PARSER = MarkdownIt("commonmark").enable(["table", "strikethrough"])
HEADINGS = {"h1": "title-2", "h2": "title-3", "h3": "title-4"}  # smaller ones: "heading"
# While a reply streams in, text arrives token by token; rebuilding more often than
# this only costs time.
RENDER_INTERVAL_MS = 100


class MarkdownView(Gtk.Box):
    def __init__(self, markdown=""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.markdown = markdown
        self.cooling_down = False
        self.stale = False
        self.render()

    def set_markdown(self, markdown):
        """Shows markdown; fine to call for every streamed token."""
        self.markdown = markdown
        if self.cooling_down:
            self.stale = True
            return
        self.render()
        self.cooling_down = True
        GLib.timeout_add(RENDER_INTERVAL_MS, self.cool_down)

    def cool_down(self):
        self.cooling_down = False
        if self.stale:
            self.stale = False
            self.set_markdown(self.markdown)
        return GLib.SOURCE_REMOVE

    def show_note(self, text, css_class="dim-label"):
        """Plain text in place of the reply, such as "thinking…" or an error."""
        self.stale = False  # a render still waiting must not cover the note
        self.clear()
        self.append(label(GLib.markup_escape_text(text), css_class))

    def show_waiting(self):
        self.stale = False
        self.clear()
        self.append(Adw.Spinner(halign=Gtk.Align.START, width_request=16, height_request=16))

    def render(self):
        self.clear()
        for node in SyntaxTreeNode(PARSER.parse(self.markdown)).children:
            self.append(block(node))

    def clear(self):
        while child := self.get_first_child():
            self.remove(child)


def label(markup, *css_classes):
    return Gtk.Label(label=markup, use_markup=True, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                     selectable=True, xalign=0, css_classes=list(css_classes))


def block(node):
    """The widget for one block of the document."""
    match node.type:
        case "paragraph":
            return label(inline(node.children[0]))
        case "heading":
            return label(inline(node.children[0]), HEADINGS.get(node.tag, "heading"))
        case "bullet_list" | "ordered_list":
            return list_of(node)
        case "blockquote":
            quote = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["quote", "dim-label"])
            for child in node.children:
                quote.append(block(child))
            return quote
        case "fence" | "code_block":
            return code(node.content.removesuffix("\n"))
        case "table":
            return table(node)
        case "hr":
            return Gtk.Separator()
        case _:  # raw HTML, shown as written
            return label(GLib.markup_escape_text(node.content))


def inline(node):
    """Pango markup for a run of text with emphasis, code and links."""
    parts = []
    for child in node.children:
        match child.type:
            case "text" | "html_inline":
                parts.append(GLib.markup_escape_text(child.content))
            case "code_inline":
                parts.append(f"<tt>{GLib.markup_escape_text(child.content)}</tt>")
            case "strong":
                parts.append(f"<b>{inline(child)}</b>")
            case "em":
                parts.append(f"<i>{inline(child)}</i>")
            case "s":
                parts.append(f"<s>{inline(child)}</s>")
            case "link":
                href = GLib.markup_escape_text(child.attrs["href"])
                parts.append(f'<a href="{href}">{inline(child)}</a>')
            case "softbreak":
                parts.append(" ")
            case "hardbreak":
                parts.append("\n")
            case _:  # images: their alt text
                parts.append(inline(child))
    return "".join(parts)


def list_of(node):
    items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    start = int(node.attrs.get("start", 1))
    for number, item in enumerate(node.children, start):
        marker = f"{number}." if node.type == "ordered_list" else "•"
        row = Gtk.Box(spacing=8)
        row.append(Gtk.Label(label=marker, valign=Gtk.Align.START))
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        for child in item.children:
            content.append(block(child))
        row.append(content)
        items.append(row)
    return items


def code(text):
    # Code keeps its lines; long ones scroll sideways rather than wrap.
    text_label = Gtk.Label(label=text, selectable=True, xalign=0, css_classes=["monospace"],
                           margin_start=12, margin_end=12, margin_top=10, margin_bottom=10)
    scroller = Gtk.ScrolledWindow(child=text_label, vscrollbar_policy=Gtk.PolicyType.NEVER,
                                  propagate_natural_height=True)
    copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy", halign=Gtk.Align.END,
                      valign=Gtk.Align.START, margin_top=4, margin_end=4, css_classes=["osd", "circular", "copy"])
    copy.connect("clicked", lambda button: button.get_clipboard().set(text))
    overlay = Gtk.Overlay(child=scroller, css_classes=["code"], overflow=Gtk.Overflow.HIDDEN)
    overlay.add_overlay(copy)
    return overlay


def table(node):
    grid = Gtk.Grid(column_spacing=16, row_spacing=6)
    rows = [row for section in node.children for row in section.children]  # thead, then tbody
    for y, row in enumerate(rows):
        for x, cell in enumerate(row.children):
            markup = inline(cell.children[0]) if cell.children else ""
            cell_label = label(f"<b>{markup}</b>" if cell.type == "th" else markup)
            # Inside a sideways scroll, wrapping text would size itself for the narrowest width.
            cell_label.set_wrap(False)
            grid.attach(cell_label, x, y, 1, 1)
    grid.add_css_class("table")
    return Gtk.ScrolledWindow(child=grid, vscrollbar_policy=Gtk.PolicyType.NEVER,
                              propagate_natural_height=True, css_classes=["code"])
