"""
Configuration dialog for AI Language Tutor
"""

import json
import os

from aqt import mw
from aqt.qt import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                    QLineEdit, QPushButton, QListWidget, QGroupBox,
                    QFormLayout, QComboBox, QFileDialog, QFont, QTabWidget,
                    QStackedWidget, QWidget, Qt)
from aqt.utils import showInfo, tooltip

from . import providers
from . import provider_models

# Dropdown order for the provider combo box; index <-> config name.
_PROVIDERS = provider_models.PROVIDERS
_PROVIDER_LABELS = [provider_models.PROVIDER_LABELS[p] for p in _PROVIDERS]

# Last entry of every model combo: selecting it clears the field so the user
# can type any model name by hand. Never saved as a model (see save_and_close).
_TYPE_YOUR_OWN = "Insert model name…"

# Cloud providers that share one panel shape (model combo + API key) and one
# persistence path. Value is the model used to prefill an unset config; order
# matches config.json. Ollama (no key) and custom (base URL) are handled apart.
_CLOUD_DEFAULT_MODELS = {
    "gemini": "gemini-3.5-flash",
    "nvidia": "deepseek-ai/deepseek-v4-flash",
    "cerebras": "gpt-oss-120b",
    "openai": "gpt-5.1",
    "xai": "grok-4",
    "anthropic": "claude-haiku-4-5",
}


def get_config():
    """Load the add-on config.

    Anki merges the user's saved settings over the defaults in config.json and
    returns that. If it hands back None (e.g. config.json wasn't readable in the
    install folder), fall back to the shipped defaults so the UI never crashes.
    """
    config = mw.addonManager.getConfig(__name__)
    if config is None:
        config = _default_config()
    return config


def _default_config():
    """The shipped defaults, read straight from the bundled config.json."""
    path = os.path.join(os.path.dirname(__file__), "config.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(config):
    """Save configuration"""
    mw.addonManager.writeConfig(__name__, config)


class ConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = get_config()
        self.setup_ui()
        self.load_decks()

    def setup_ui(self):
        self.setWindowTitle("AI Language Tutor Settings")
        self.setMinimumWidth(600)
        self.setMinimumHeight(460)

        layout = QVBoxLayout()

        # Two screens: everything about models/APIs on one, decks and
        # general add-on settings on the other. The same provider (and
        # fallback chain) is used everywhere, so it is configured once.
        tabs = QTabWidget()
        tabs.addTab(self._build_provider_tab(), "AI Providers")
        tabs.addTab(self._build_general_tab(), "Decks && General")
        layout.addWidget(tabs)

        # Save and close
        btn_layout = QHBoxLayout()
        save_btn = QPushButton("Save All")
        save_btn.clicked.connect(self.save_and_close)
        close_btn = QPushButton("Cancel")
        close_btn.clicked.connect(self.reject)

        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def _build_provider_tab(self):
        """Provider choice, fallback, and per-provider connection settings."""
        tab = QWidget()
        layout = QVBoxLayout()

        # Provider selection
        provider_group = QGroupBox("AI Provider")
        provider_layout = QFormLayout()
        self.provider_select = QComboBox()
        self.provider_select.addItems(_PROVIDER_LABELS)
        provider = self.config.get("provider", "ollama")
        current = _PROVIDERS.index(provider) if provider in _PROVIDERS else 0
        self.provider_select.setCurrentIndex(current)
        provider_layout.addRow("Use:", self.provider_select)

        # Fallback: when the primary provider errors, try the others (ones
        # without an API key are skipped automatically).
        self.fallback_select = QComboBox()
        self.fallback_select.addItems(["Nothing (no fallback)", "All other providers"])
        self.fallback_select.setCurrentIndex(
            1 if self.config.get("fallback_providers") else 0)
        provider_layout.addRow("If it fails, try:", self.fallback_select)

        provider_group.setLayout(provider_layout)
        layout.addWidget(provider_group)

        # One settings page per provider, only the selected one visible. Every
        # page's widgets stay alive though, so save_and_close persists them all
        # — including a key typed while briefly switched to another provider.
        self.model_inputs = {}
        self.key_inputs = {}
        self.provider_stack = QStackedWidget()
        for name in _PROVIDERS:
            if name == "ollama":
                page = self._build_ollama_panel()
            elif name == "custom":
                page = self._build_custom_panel()
            else:
                page = self._build_cloud_panel(name)
            self.provider_stack.addWidget(page)
        self.provider_stack.setCurrentIndex(current)
        self.provider_select.currentIndexChanged.connect(
            self.provider_stack.setCurrentIndex)
        layout.addWidget(self.provider_stack)

        layout.addStretch()
        tab.setLayout(layout)
        return tab

    def _build_ollama_panel(self):
        """Local Ollama: server URL + model, no API key."""
        ollama_cfg = self.config.get("ollama", {})
        group = QGroupBox("Local LLM Settings")
        form = QFormLayout()
        endpoint = (ollama_cfg.get("endpoint")
                    or self.config.get("ollama_endpoint", provider_models.OLLAMA_DEFAULT_ENDPOINT))
        # Editable combo of known local server URLs; the saved value is added if
        # it isn't a preset. Item text is the bare URL: picking it sets the field
        # verbatim.
        self.endpoint_input = QComboBox()
        self.endpoint_input.setEditable(True)
        self.endpoint_input.addItems(provider_models.LOCAL_ENDPOINT_PRESETS)
        if self.endpoint_input.findText(endpoint) < 0:
            self.endpoint_input.addItem(endpoint)
        self.endpoint_input.setCurrentText(endpoint)
        form.addRow("Server URL:", self.endpoint_input)
        self.model_inputs["ollama"] = self._make_model_combo(
            "ollama", ollama_cfg.get("model") or self.config.get("model", "gemma4"))
        form.addRow("Model:", self.model_inputs["ollama"])

        note = QLabel(
            "Any server exposing Ollama's API works here. For OpenAI-compatible "
            "local servers (LM Studio, vLLM, llama.cpp) use the 'Custom' "
            "provider instead.")
        note.setWordWrap(True)
        form.addRow(note)
        group.setLayout(form)
        return group

    def _build_cloud_panel(self, name):
        """A key-based cloud provider (Gemini/NVIDIA/Cerebras/OpenAI/xAI/
        Anthropic): model combo + API key + delete button. All identical bar
        names."""
        label = provider_models.PROVIDER_LABELS[name]
        cfg = self.config.get(name, {})
        group = QGroupBox(f"{label} Settings")
        form = QFormLayout()

        self.model_inputs[name] = self._make_model_combo(
            name, cfg.get("model", _CLOUD_DEFAULT_MODELS[name]))
        form.addRow("Model:", self.model_inputs[name])

        key_input = QLineEdit(getattr(providers, f"{name}_api_key")())
        key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_input.setPlaceholderText(f"Paste your {label} API key")
        self.key_inputs[name] = key_input
        form.addRow("API Key:", key_input)

        delete_btn = QPushButton("Delete API Key")
        delete_btn.clicked.connect(lambda _=False, n=name: self._delete_key(n))
        form.addRow("", delete_btn)

        form.addRow(QLabel("Stored in the add-on's .env; removed when you uninstall the add-on."))
        group.setLayout(form)
        return group

    def _build_custom_panel(self):
        """User-configured OpenAI-compatible endpoint: base URL, model, and an
        optional API key (a self-hosted server may need no auth)."""
        cfg = self.config.get("custom", {})
        group = QGroupBox("Custom (OpenAI-compatible) Settings")
        form = QFormLayout()

        self.custom_endpoint_input = QLineEdit(cfg.get("endpoint", ""))
        self.custom_endpoint_input.setPlaceholderText("https://api.example.com/v1")
        form.addRow("Base URL:", self.custom_endpoint_input)

        # Plain field, not a combo: there is no known model list for custom.
        self.custom_model_input = QLineEdit(cfg.get("model", ""))
        self.custom_model_input.setPlaceholderText("Insert model name")
        form.addRow("Model:", self.custom_model_input)

        key_input = QLineEdit(providers.custom_api_key())
        key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_input.setPlaceholderText("Paste your API key (optional)")
        self.key_inputs["custom"] = key_input
        form.addRow("API Key (optional):", key_input)

        delete_btn = QPushButton("Delete API Key")
        delete_btn.clicked.connect(lambda _=False: self._delete_key("custom"))
        form.addRow("", delete_btn)

        note = QLabel(
            "Works with any API that speaks the OpenAI chat/completions format "
            "(e.g. LM Studio, vLLM, OpenRouter). Compatibility is not guaranteed. "
            "The API key is stored in the add-on's .env; removed when you "
            "uninstall the add-on.")
        note.setWordWrap(True)
        form.addRow(note)
        group.setLayout(form)
        return group

    @staticmethod
    def _make_model_combo(provider, current_value):
        """Editable combo box seeded with known models for `provider`, prefilled
        with `current_value` (which may be a custom string not in the list).

        The list ends with an italic "Insert model name…" entry: picking it
        empties the field and focuses it, making the type-anything ability of
        the editable combo discoverable."""
        combo = QComboBox()
        combo.addItems(provider_models.MODEL_OPTIONS[provider])
        combo.addItem(_TYPE_YOUR_OWN)
        italic = QFont()
        italic.setItalic(True)
        combo.setItemData(combo.count() - 1, italic, Qt.ItemDataRole.FontRole)
        combo.setEditable(True)
        # Hint shown when the model field is emptied out.
        combo.lineEdit().setPlaceholderText("Insert model name")
        idx = combo.findText(current_value)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setEditText(current_value)

        def on_activated(index):
            if combo.itemText(index) == _TYPE_YOUR_OWN:
                combo.clearEditText()
                combo.lineEdit().setFocus()

        combo.activated.connect(on_activated)
        return combo

    def _model_text(self, name, default):
        """A model combo's text, falling back to `default` when the user left
        it empty or on the "Insert model name…" prompt entry."""
        text = self.model_inputs[name].currentText().strip()
        if not text or text == _TYPE_YOUR_OWN:
            return default
        return text

    def _build_general_tab(self):
        """Appearance, logging, and per-deck configuration."""
        tab = QWidget()
        layout = QVBoxLayout()

        # Appearance: which theme the review panel uses (applies on the
        # next card; no restart needed).
        appearance_group = QGroupBox("Appearance")
        appearance_layout = QFormLayout()
        self.theme_select = QComboBox()
        self.theme_select.addItems(["Native (match Anki)", "Polished"])
        self.theme_select.setCurrentIndex(
            1 if self.config.get("theme") == "polished" else 0)
        appearance_layout.addRow("Theme:", self.theme_select)
        appearance_group.setLayout(appearance_layout)
        layout.addWidget(appearance_group)

        # Logging settings (off by default; saves conversations + errors to a folder)
        logging_group = QGroupBox("Logging")
        logging_layout = QFormLayout()
        self.logging_enabled = QComboBox()
        self.logging_enabled.addItems(["Disabled", "Enabled"])
        self.logging_enabled.setCurrentIndex(1 if self.config.get("logging_enabled") else 0)
        logging_layout.addRow("Save logs:", self.logging_enabled)

        log_dir_row = QHBoxLayout()
        self.log_dir_input = QLineEdit(self.config.get("conversations_dir", ""))
        self.log_dir_input.setPlaceholderText("Default folder")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self.choose_log_dir)
        log_dir_row.addWidget(self.log_dir_input)
        log_dir_row.addWidget(browse_btn)
        logging_layout.addRow("Folder:", log_dir_row)

        logging_hint = QLabel(
            "Leave the folder blank to use the add-on's user_files folder. "
            "Stores per-session conversations and an errors.log.")
        logging_hint.setWordWrap(True)
        logging_layout.addRow(logging_hint)
        logging_group.setLayout(logging_layout)
        layout.addWidget(logging_group)

        # Deck configuration
        deck_group = QGroupBox("Deck Configuration")
        deck_layout = QVBoxLayout()

        self.deck_list = QListWidget()
        self.deck_list.itemClicked.connect(self.on_deck_selected)
        deck_layout.addWidget(QLabel("Select a deck to configure:"))
        deck_layout.addWidget(self.deck_list)

        # Per-deck settings
        lang_layout = QFormLayout()
        self.source_lang = QLineEdit()
        self.target_lang = QLineEdit()
        self.enabled_checkbox = QComboBox()
        self.enabled_checkbox.addItems(["Disabled", "Enabled"])

        # Apply this deck's config to all of its subdecks
        self.subdeck_checkbox = QComboBox()
        self.subdeck_checkbox.addItems(["Disabled", "Enabled"])

        # Which language the note's FIRST field holds. Decks differ ("dog"
        # first vs "perro" first) and the plugin can't guess: it decides
        # what the instruction may show and how the LLM prompt is filled.
        self.front_field = QComboBox()
        self.front_field.addItems([
            "Word being learned (language you're learning)",
            "Meaning / translation (language you speak)",
        ])

        # User level dropdown
        self.user_level = QComboBox()
        self.user_level.addItems(["Beginner", "Intermediate", "Advanced"])

        # Default review mode dropdown
        self.review_mode = QComboBox()
        self.review_mode.addItems(["Full", "Quick"])

        lang_layout.addRow("You speak:", self.source_lang)
        lang_layout.addRow("You're learning:", self.target_lang)
        lang_layout.addRow("First field holds:", self.front_field)
        lang_layout.addRow("User Level:", self.user_level)
        lang_layout.addRow("Default Review:", self.review_mode)
        lang_layout.addRow("AI Review:", self.enabled_checkbox)
        lang_layout.addRow("Apply to subdecks:", self.subdeck_checkbox)

        deck_layout.addLayout(lang_layout)

        save_deck_btn = QPushButton("Save Deck Config")
        save_deck_btn.clicked.connect(self.save_deck_config)
        deck_layout.addWidget(save_deck_btn)

        deck_group.setLayout(deck_layout)
        layout.addWidget(deck_group)

        tab.setLayout(layout)
        return tab

    def load_decks(self):
        """Load decks into the list, collapsing subdecks already covered by a parent.

        When a deck is enabled with "Apply to subdecks", its descendants inherit
        that config (see reviewer._match_deck_config), so listing them just adds
        clutter. Hide such descendants — unless they have their own explicit
        config, which would override the parent.
        """
        self.deck_list.clear()
        deck_configs = self.config.get("deck_configs", {})
        covered_parents = [
            name for name, cfg in deck_configs.items()
            if cfg.get("enabled") and cfg.get("include_subdecks")
        ]
        for deck in mw.col.decks.all_names_and_ids():
            name = deck.name
            if name not in deck_configs and any(
                name.startswith(parent + "::") for parent in covered_parents
            ):
                continue
            self.deck_list.addItem(name)

    def on_deck_selected(self, item):
        """Load configuration for selected deck"""
        deck_name = item.text()
        deck_configs = self.config.get("deck_configs", {})

        if deck_name in deck_configs:
            cfg = deck_configs[deck_name]
            self.source_lang.setText(cfg.get("source_language", ""))
            self.target_lang.setText(cfg.get("target_language", ""))
            self.enabled_checkbox.setCurrentIndex(1 if cfg.get("enabled", False) else 0)
            self.subdeck_checkbox.setCurrentIndex(1 if cfg.get("include_subdecks", False) else 0)
            self.front_field.setCurrentIndex(
                1 if cfg.get("front_field", "target") == "source" else 0)

            # Load user level
            level = cfg.get("user_level", "Beginner")
            level_index = self.user_level.findText(level)
            if level_index >= 0:
                self.user_level.setCurrentIndex(level_index)

            # Load review mode
            mode = cfg.get("review_mode", "full").capitalize()
            mode_index = self.review_mode.findText(mode)
            if mode_index >= 0:
                self.review_mode.setCurrentIndex(mode_index)
        else:
            self.source_lang.setText("")
            self.target_lang.setText("")
            self.enabled_checkbox.setCurrentIndex(0)
            self.subdeck_checkbox.setCurrentIndex(0)
            self.front_field.setCurrentIndex(0)
            self.user_level.setCurrentIndex(0)
            self.review_mode.setCurrentIndex(0)

    def choose_log_dir(self):
        """Open a folder picker for the log/conversations directory."""
        start = self.log_dir_input.text() or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "Select Log Folder", start)
        if path:
            self.log_dir_input.setText(path)

    def _delete_key(self, name):
        """Clear a provider's stored API key from .env and its input field."""
        getattr(providers, f"delete_{name}_api_key")()
        self.key_inputs[name].setText("")
        tooltip(f"{provider_models.PROVIDER_LABELS[name]} API key deleted")

    def save_deck_config(self):
        """Save configuration for currently selected deck"""
        current_item = self.deck_list.currentItem()
        if not current_item:
            showInfo("Please select a deck first")
            return

        deck_name = current_item.text()

        if not self.source_lang.text() or not self.target_lang.text():
            showInfo("Please fill in both language fields")
            return

        self._store_deck_config(deck_name)
        save_config(self.config)

        tooltip(f"Configuration saved for {deck_name}")
        # Reflect "Apply to subdecks" immediately: collapse newly covered subdecks.
        self.load_decks()
        match = self.deck_list.findItems(deck_name, Qt.MatchFlag.MatchExactly)
        if match:
            self.deck_list.setCurrentItem(match[0])

    def _store_deck_config(self, deck_name):
        """Write the deck form's current values into self.config (in memory)."""
        if "deck_configs" not in self.config:
            self.config["deck_configs"] = {}

        self.config["deck_configs"][deck_name] = {
            "source_language": self.source_lang.text(),
            "target_language": self.target_lang.text(),
            "front_field": "source" if self.front_field.currentIndex() == 1 else "target",
            "user_level": self.user_level.currentText(),
            "review_mode": self.review_mode.currentText().lower(),
            "enabled": self.enabled_checkbox.currentIndex() == 1,
            "include_subdecks": self.subdeck_checkbox.currentIndex() == 1
        }

    def save_and_close(self):
        """Save all settings and close"""
        # "Save All" also captures the deck currently being edited, so a
        # separate "Save Deck Config" click isn't required. Skipped when the
        # languages are incomplete: an untouched selection stays untouched.
        current_item = self.deck_list.currentItem()
        if current_item and self.source_lang.text() and self.target_lang.text():
            self._store_deck_config(current_item.text())

        self.config["theme"] = "polished" if self.theme_select.currentIndex() == 1 else "native"
        primary = _PROVIDERS[self.provider_select.currentIndex()]
        self.config["provider"] = primary
        # "All other providers" stores the concrete names so the runtime chain
        # (and a hand-edited order) stays a plain list in the config.
        self.config["fallback_providers"] = (
            [p for p in _PROVIDERS if p != primary]
            if self.fallback_select.currentIndex() == 1 else [])
        self.config["ollama"] = {
            "endpoint": self.endpoint_input.currentText().strip(),
            "model": self._model_text("ollama", "gemma4"),
        }
        # Cloud key-based providers: model goes in the config, the API key in
        # .env (removed on uninstall, never in the config). Every provider is
        # saved regardless of which one is currently selected.
        for name in _CLOUD_DEFAULT_MODELS:
            self.config[name] = {
                "model": self._model_text(name, _CLOUD_DEFAULT_MODELS[name])}
            getattr(providers, f"set_{name}_api_key")(
                self.key_inputs[name].text().strip())
        self.config["custom"] = {
            "endpoint": self.custom_endpoint_input.text().strip(),
            "model": self.custom_model_input.text().strip(),
        }
        providers.set_custom_api_key(self.key_inputs["custom"].text().strip())
        self.config["logging_enabled"] = self.logging_enabled.currentIndex() == 1
        self.config["conversations_dir"] = self.log_dir_input.text().strip()
        # Drop the obsolete flat keys if present.
        self.config.pop("ollama_endpoint", None)
        self.config.pop("model", None)

        save_config(self.config)
        tooltip("Configuration saved!")
        self.accept()


def show_config_dialog():
    dialog = ConfigDialog(mw)
    dialog.exec()
