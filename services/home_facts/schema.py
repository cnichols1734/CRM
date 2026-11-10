"""One pinned form definition for the web, native apps and PDF renderer."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'documents' / 'home_facts'


@lru_cache(maxsize=4)
def manifest(version=1):
    if version != 1:
        raise ValueError('This questionnaire version is not supported.')
    value = json.loads((ROOT / 'v1' / 'manifest.json').read_text())
    template = ROOT / 'v1' / 'questionnaire.pdf'
    if hashlib.sha256(template.read_bytes()).hexdigest() != value['template_sha256']:
        raise RuntimeError('Home Facts template does not match its field manifest.')
    return value


def fields(version=1):
    return {f['id']: f for s in manifest(version)['sections'] for g in s['groups'] for f in g['fields']}


def validate_answers(changes, version=1):
    if not isinstance(changes, dict) or len(changes) > 600:
        raise ValueError('Answers must be a field map.')
    definitions = fields(version)
    clean = {}
    for key, value in changes.items():
        definition = definitions.get(key)
        if definition is None:
            raise ValueError('An answer does not belong to this questionnaire.')
        if value is None:
            clean[key] = None
        elif definition['kind'] == 'text':
            if not isinstance(value, str) or len(value) > definition['max_length'] or '\x00' in value:
                raise ValueError(f"Check {definition['label']}. Use at most {definition['max_length']} characters.")
            clean[key] = value
        elif definition['kind'] == 'choice':
            if not isinstance(value, str) or value not in definition['options']:
                raise ValueError(f"Choose one answer for {definition['label']}.")
            clean[key] = value
        else:
            if (not isinstance(value, list) or any(not isinstance(v, str) for v in value)
                    or len(value) != len(set(value)) or any(v not in definition['options'] for v in value)):
                raise ValueError(f"Check the choices for {definition['label']}.")
            clean[key] = [v for v in definition['options'] if v in value]
    if len(json.dumps(clean).encode()) > 512_000:
        raise ValueError('This save is too large. Save one section at a time.')
    return clean


def display_value(value):
    return ', '.join(value) if isinstance(value, list) else str(value or '')
