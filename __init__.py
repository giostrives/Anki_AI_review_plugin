"""
Anki AI Reviewer Plugin
Replaces card review with AI-generated exercises using Ollama
"""

from aqt import mw, gui_hooks
from aqt.qt import QAction
from aqt.utils import showInfo, tooltip
from .reviewer import AIReviewer
from .config_dialog import show_config_dialog

# Initialize the AI reviewer
ai_reviewer = None

def init_ai_reviewer():
    global ai_reviewer
    ai_reviewer = AIReviewer()

def show_config():
    """Show configuration dialog"""
    show_config_dialog()

def setup_menu():
    """Add menu item to Anki"""
    action = QAction("AI Reviewer Settings", mw)
    action.triggered.connect(show_config)
    mw.form.menuTools.addAction(action)

# Initialize on profile load
gui_hooks.profile_did_open.append(init_ai_reviewer)

# Setup menu
setup_menu()