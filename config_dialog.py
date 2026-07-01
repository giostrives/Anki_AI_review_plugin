"""
Configuration dialog for AI Reviewer
"""

import os

from aqt import mw
from aqt.qt import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                    QLineEdit, QPushButton, QListWidget, QGroupBox,
                    QFormLayout, QComboBox, QFileDialog, Qt)
from aqt.utils import showInfo, tooltip

from . import providers


def get_config():
    """Load configuration"""
    return mw.addonManager.getConfig(__name__)


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
        self.setWindowTitle("AI Reviewer Configuration")
        self.setMinimumWidth(600)
        self.setMinimumHeight(560)

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

        # Provider selection
        provider_group = QGroupBox("AI Provider")
        provider_layout = QFormLayout()
        self.provider_select = QComboBox()
        self.provider_select.addItems(["Ollama", "Gemini"])
        provider = self.config.get("provider", "ollama")
        self.provider_select.setCurrentIndex(1 if provider == "gemini" else 0)
        provider_layout.addRow("Use:", self.provider_select)
        provider_group.setLayout(provider_layout)
        layout.addWidget(provider_group)

        # Ollama settings
        ollama_cfg = self.config.get("ollama", {})
        ollama_group = QGroupBox("Ollama Settings")
        ollama_layout = QFormLayout()
        self.endpoint_input = QLineEdit(
            ollama_cfg.get("endpoint") or self.config.get("ollama_endpoint", "http://localhost:11434"))
        ollama_layout.addRow("Endpoint:", self.endpoint_input)
        self.model_input = QLineEdit(
            ollama_cfg.get("model") or self.config.get("model", "gemma3"))
        ollama_layout.addRow("Model:", self.model_input)
        ollama_group.setLayout(ollama_layout)
        layout.addWidget(ollama_group)

        # Gemini settings
        gemini_cfg = self.config.get("gemini", {})
        gemini_group = QGroupBox("Gemini Settings")
        gemini_layout = QFormLayout()
        self.gemini_model_input = QLineEdit(gemini_cfg.get("model", "gemini-3.5-flash"))
        gemini_layout.addRow("Model:", self.gemini_model_input)

        self.gemini_key_input = QLineEdit(providers.gemini_api_key())
        self.gemini_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_key_input.setPlaceholderText("Paste your Gemini API key")
        gemini_layout.addRow("API Key:", self.gemini_key_input)

        delete_key_btn = QPushButton("Delete API Key")
        delete_key_btn.clicked.connect(self.delete_gemini_key)
        gemini_layout.addRow("", delete_key_btn)

        gemini_layout.addRow(QLabel("Stored in the add-on's .env; removed when you uninstall the add-on."))
        gemini_group.setLayout(gemini_layout)
        layout.addWidget(gemini_group)

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
            "Word being learned (target language)",
            "Meaning / translation (source language)",
        ])

        # User level dropdown
        self.user_level = QComboBox()
        self.user_level.addItems(["Beginner", "Intermediate", "Advanced"])

        # Default review mode dropdown
        self.review_mode = QComboBox()
        self.review_mode.addItems(["Full", "Quick"])

        lang_layout.addRow("Source Language:", self.source_lang)
        lang_layout.addRow("Target Language:", self.target_lang)
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

    def delete_gemini_key(self):
        """Clear the stored Gemini API key from .env and the input field"""
        providers.delete_gemini_api_key()
        self.gemini_key_input.setText("")
        tooltip("Gemini API key deleted")

    def save_deck_config(self):
        """Save configuration for currently selected deck"""
        current_item = self.deck_list.currentItem()
        if not current_item:
            showInfo("Please select a deck first")
            return

        deck_name = current_item.text()

        if not self.source_lang.text() or not self.target_lang.text():
            showInfo("Please enter both source and target languages")
            return

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

        tooltip(f"Configuration saved for {deck_name}")
        # Reflect "Apply to subdecks" immediately: collapse newly covered subdecks.
        self.load_decks()
        match = self.deck_list.findItems(deck_name, Qt.MatchFlag.MatchExactly)
        if match:
            self.deck_list.setCurrentItem(match[0])

    def save_and_close(self):
        """Save all settings and close"""
        self.config["theme"] = "polished" if self.theme_select.currentIndex() == 1 else "native"
        self.config["provider"] = "gemini" if self.provider_select.currentIndex() == 1 else "ollama"
        self.config["ollama"] = {
            "endpoint": self.endpoint_input.text(),
            "model": self.model_input.text(),
        }
        self.config["gemini"] = {
            "model": self.gemini_model_input.text(),
        }
        self.config["logging_enabled"] = self.logging_enabled.currentIndex() == 1
        self.config["conversations_dir"] = self.log_dir_input.text().strip()
        # The API key is stored in .env (removed on uninstall), never in the config.
        providers.set_gemini_api_key(self.gemini_key_input.text().strip())
        # Drop the obsolete flat keys if present.
        self.config.pop("ollama_endpoint", None)
        self.config.pop("model", None)

        save_config(self.config)
        tooltip("Configuration saved!")
        self.accept()


def show_config_dialog():
    dialog = ConfigDialog(mw)
    dialog.exec()
