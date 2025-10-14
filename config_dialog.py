"""
Configuration dialog for AI Reviewer
"""

from aqt import mw
from aqt.qt import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                    QLineEdit, QPushButton, QListWidget, QGroupBox,
                    QFormLayout, QComboBox)
from aqt.utils import showInfo, tooltip


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
        self.setMinimumHeight(400)

        layout = QVBoxLayout()

        # Ollama settings
        ollama_group = QGroupBox("Ollama Settings")
        ollama_layout = QFormLayout()

        self.endpoint_input = QLineEdit(self.config.get("ollama_endpoint", "http://localhost:11434"))
        ollama_layout.addRow("Endpoint:", self.endpoint_input)

        self.model_input = QLineEdit(self.config.get("model", "gemma3"))
        ollama_layout.addRow("Model:", self.model_input)

        ollama_group.setLayout(ollama_layout)
        layout.addWidget(ollama_group)

        # Deck configuration
        deck_group = QGroupBox("Deck Configuration")
        deck_layout = QVBoxLayout()

        self.deck_list = QListWidget()
        self.deck_list.itemClicked.connect(self.on_deck_selected)
        deck_layout.addWidget(QLabel("Select a deck to configure:"))
        deck_layout.addWidget(self.deck_list)

        # Language pair settings
        lang_layout = QFormLayout()
        self.source_lang = QLineEdit()
        self.target_lang = QLineEdit()
        self.enabled_checkbox = QComboBox()
        self.enabled_checkbox.addItems(["Disabled", "Enabled"])

        lang_layout.addRow("Source Language:", self.source_lang)
        lang_layout.addRow("Target Language:", self.target_lang)
        lang_layout.addRow("AI Review:", self.enabled_checkbox)

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
        """Load all decks into the list"""
        self.deck_list.clear()
        for deck in mw.col.decks.all_names_and_ids():
            self.deck_list.addItem(deck.name)

    def on_deck_selected(self, item):
        """Load configuration for selected deck"""
        deck_name = item.text()
        deck_configs = self.config.get("deck_configs", {})

        if deck_name in deck_configs:
            cfg = deck_configs[deck_name]
            self.source_lang.setText(cfg.get("source_language", ""))
            self.target_lang.setText(cfg.get("target_language", ""))
            self.enabled_checkbox.setCurrentIndex(1 if cfg.get("enabled", False) else 0)
        else:
            self.source_lang.setText("")
            self.target_lang.setText("")
            self.enabled_checkbox.setCurrentIndex(0)

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
            "enabled": self.enabled_checkbox.currentIndex() == 1
        }

        tooltip(f"Configuration saved for {deck_name}")

    def save_and_close(self):
        """Save all settings and close"""
        self.config["ollama_endpoint"] = self.endpoint_input.text()
        self.config["model"] = self.model_input.text()

        save_config(self.config)
        tooltip("Configuration saved!")
        self.accept()


def show_config_dialog():
    dialog = ConfigDialog(mw)
    dialog.exec()