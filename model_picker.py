"""The model menu in the header: the current model's name, and all models to search through."""

from gi.repository import GObject, Gtk, Pango

PROVIDERS = {"local": "On this computer", "openai-codex": "Codex", "openrouter": "OpenRouter"}


def model_key(model):
    return f"{model['provider']}/{model['id']}"


class ModelPicker(Gtk.MenuButton):
    """Emits "chosen" with the model dict someone picked."""

    __gsignals__ = {"chosen": (GObject.SignalFlags.RUN_FIRST, None, (object,))}

    def __init__(self):
        super().__init__(always_show_arrow=True, css_classes=["flat"], tooltip_text="Model")
        self.label = Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=26,
                               css_classes=["heading"])
        self.set_child(self.label)

        # Hosted providers list hundreds of models; typing finds one.
        self.search = Gtk.SearchEntry(placeholder_text="Search models", margin_start=6,
                                      margin_end=6, margin_top=6, margin_bottom=6)
        self.search.connect("search-changed", self.on_search)
        self.search.connect("activate", self.on_search_activate)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["navigation-sidebar"])
        self.list.set_filter_func(self.matches)
        self.list.set_header_func(self.add_provider_header)
        self.list.connect("row-activated", lambda _, row: self.choose(row.model))
        scroller = Gtk.ScrolledWindow(child=self.list, propagate_natural_height=True,
                                      max_content_height=420,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=320)
        box.append(self.search)
        box.append(scroller)
        popover = Gtk.Popover(child=box, css_classes=["menu"])
        # The search field takes Escape for itself; it should still close the menu.
        self.search.connect("stop-search", lambda _: popover.popdown())
        popover.connect("show", lambda _: (self.search.set_text(""), self.search.grab_focus()))
        self.set_popover(popover)

    def show_models(self, models, current):
        """Lists models (in the order given) and shows which is current, by key."""
        self.list.remove_all()
        for model in models:
            row = Gtk.ListBoxRow()
            row.model = model
            box = Gtk.Box(spacing=12)
            box.append(Gtk.Label(label=model["name"], hexpand=True, xalign=0, wrap=True,
                                 wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=30))
            box.append(Gtk.Image(icon_name="object-select-symbolic",
                                 opacity=1 if model_key(model) == current else 0))
            row.set_child(box)
            self.list.append(row)
        chosen = next((model for model in models if model_key(model) == current), None)
        self.label.set_label(chosen["name"] if chosen else "No model" if models else "No local model")
        self.set_sensitive(bool(models))

    def choose(self, model):
        self.get_popover().popdown()
        self.emit("chosen", model)

    def matches(self, row):
        words = self.search.get_text().casefold().split()
        haystack = f"{row.model['name']} {model_key(row.model)}".casefold()
        return all(word in haystack for word in words)

    def on_search(self, _entry):
        self.list.invalidate_filter()
        self.list.invalidate_headers()

    def on_search_activate(self, _entry):
        index = 0
        while row := self.list.get_row_at_index(index):
            if self.matches(row):
                self.choose(row.model)
                return
            index += 1

    def add_provider_header(self, row, before):
        provider = row.model["provider"]
        if before and before.model["provider"] == provider:
            row.set_header(None)
            return
        row.set_header(Gtk.Label(label=PROVIDERS.get(provider, provider), xalign=0,
                                 css_classes=["heading", "dim-label"], margin_start=12,
                                 margin_top=12, margin_bottom=6))
