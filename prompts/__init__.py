import os
import re

_template_dir = os.path.dirname(os.path.abspath(__file__))

# The prompts only use two constructs, so we render them ourselves instead of
# pulling in jinja2 (Anki does not bundle jinja2 — importing it would crash the
# add-on on a stock install).
_IF_RE = re.compile(r"{%\s*if\s+(\w+)\s*%}(.*?){%\s*endif\s*%}", re.DOTALL)
_VAR_RE = re.compile(r"{{\s*(\w+)\s*}}")


class _Template:
    """Minimal stand-in for a jinja2 template.

    Supports exactly what our .j2 prompts use: `{{ var }}` substitution and a
    plain `{% if var %}...{% endif %}` block (no else/elif, no loops). A missing
    or None value renders as an empty string.
    """

    def __init__(self, filename):
        with open(os.path.join(_template_dir, filename), encoding="utf-8") as f:
            self._text = f.read()

    def render(self, **kwargs):
        def _if(match):
            return match.group(2) if kwargs.get(match.group(1)) else ""

        def _var(match):
            value = kwargs.get(match.group(1))
            return "" if value is None else str(value)

        # Resolve conditionals first so `{{ var }}` inside a kept block is then
        # substituted, and one inside a dropped block simply disappears.
        text = _IF_RE.sub(_if, self._text)
        return _VAR_RE.sub(_var, text)


system_prompt = _Template("system_prompt.j2")
language_card_prompt = _Template("language_card.j2")
quick_card_prompt = _Template("quick_card.j2")
conversation_prompt = _Template("conversation.j2")
