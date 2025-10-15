import os
import jinja2

template_dir = os.path.dirname(os.path.abspath(__file__))

template_loader = jinja2.FileSystemLoader(searchpath=template_dir)
prompt_loader = jinja2.Environment(loader=template_loader)
system_prompt = prompt_loader.get_template('system_prompt.j2')
language_card_prompt = prompt_loader.get_template('language_card.j2')