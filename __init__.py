"""
Anki AI Reviewer Plugin
Adds an AI writing exercise + feedback panel to card reviews
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
    # Guard against re-creation on profile switches: AIReviewer registers
    # gui_hooks in its constructor, and a second instance would double them.
    if ai_reviewer is None:
        ai_reviewer = AIReviewer()

def show_config():
    """Show configuration dialog"""
    show_config_dialog()

def setup_menu():
    """Add menu item to Anki"""
    action = QAction("AI Reviewer Settings", mw)
    action.triggered.connect(show_config)
    mw.form.menuTools.addAction(action)

# mw is None when the module is imported outside a running Anki (e.g. by
# the test suite); everything below only makes sense inside the app.
if mw is not None:
    # Serve the panel's CSS/JS to Anki's webviews at /_addons/<package>/web/…
    mw.addonManager.setWebExports(__name__, r"web/.*(css|js)")

    # Initialize on profile load
    gui_hooks.profile_did_open.append(init_ai_reviewer)

    # Setup menu
    setup_menu()