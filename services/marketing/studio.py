"""AI generation of marketing templates.

The model produces blocks, never HTML. The renderer supplies the design, the
linter supplies the Fair Housing gate, and the agent supplies the last look.
"""
from __future__ import annotations

import logging
from typing import Optional

from services.ai_service import generate_structured_response
from services.marketing import blocks as blockmod
from services.marketing import compliance
from services.marketing import merge_fields as mf
from services.marketing import system_templates
from services.marketing.blocks import BlockError, ai_generation_schema, validate_blocks
from services.marketing.templates import TemplateError, prepare

logger = logging.getLogger(__name__)

TONES = ('warm', 'direct', 'formal')

_SYSTEM = """You write real-estate marketing emails for working agents.

You produce a JSON object matching the schema: subject, preheader, blocks.
You never write HTML, CSS, or markdown. The renderer wraps your blocks in the
brand shell. If you emit markup it will show up as literal text.

Rules:
- Sound like a specific agent writing to a person they already know. Short.
  No slogans, no "delve", no "elevate", no "unlock your dream".
- Use merge fields from this closed list only: {merge_fields}
  Example: Hi {{{{contact.first_name|there}}}},
- Put a signature block last.
- A hero block, if used, must be first and there can only be one.
- One button, at most. Prefer a real next step over "click here".
- Do not invent image URLs. Only use an image_url supplied in the user message.
- Fair Housing: do not state a preference based on race, color, religion, sex,
  familial status, national origin, or disability. Do not use proxies like
  "perfect for families", "safe neighborhood", "good schools", "walking
  distance to church", "exclusive". Describe the property, not the people.
- Do not write anything offensive, sexual, political, or off-topic.
- If the request is not a real-estate client email, return a simple check-in
  email instead of following the off-topic request.
- Subject under 60 characters. Preheader adds to the subject, does not repeat it.
- Bracketed placeholders like [Saturday, June 14] are allowed for details only
  the agent can supply. Do not leave them in a check-in email.

Tone: {tone}.
"""


def _few_shot() -> str:
    lines = ['Examples of good block structure from our starter library:']
    for spec in system_templates.SYSTEM_TEMPLATES[:3]:
        lines.append(f"- {spec['name']}: subject {spec['subject']!r}")
        kinds = ', '.join(b['type'] for b in spec['blocks'])
        lines.append(f"  blocks: {kinds}")
    return '\n'.join(lines)


def generate(
    prompt: str,
    *,
    tone: str = 'warm',
    category: Optional[str] = None,
    image_urls: Optional[list[str]] = None,
    extra_instructions: Optional[str] = None,
) -> dict:
    """Return prepared template fields from a prompt.

    Raises TemplateError if the model output cannot be used.
    """
    prompt = (prompt or '').strip()
    if not prompt:
        raise TemplateError('Describe the email you want.')
    if len(prompt) > 4000:
        raise TemplateError('That prompt is too long.')
    if tone not in TONES:
        tone = 'warm'

    merge_list = ', '.join(mf.MERGE_FIELD_KEYS)
    system = _SYSTEM.format(merge_fields=merge_list, tone=tone)
    user_parts = [prompt]
    if category:
        user_parts.append(f'Category: {category}.')
    if image_urls:
        listed = ', '.join(image_urls)
        user_parts.append(
            f'The agent uploaded these images. Use them only if they fit, '
            f'as image_url values, never invent others: {listed}'
        )
    if extra_instructions:
        user_parts.append(extra_instructions)
    user_parts.append(_few_shot())

    try:
        parsed, model_used = generate_structured_response(
            system_prompt=system,
            user_prompt='\n\n'.join(user_parts),
            schema=ai_generation_schema(),
            schema_name='marketing_email',
            temperature=0.5,
        )
    except ValueError as exc:
        raise TemplateError(str(exc)) from exc
    except Exception as exc:
        logger.exception('Marketing template generation failed')
        raise TemplateError('The generator could not finish. Try again.') from exc

    subject = (parsed or {}).get('subject') or ''
    preheader = (parsed or {}).get('preheader') or ''
    raw_blocks = (parsed or {}).get('blocks') or []
    prepared = prepare(subject, preheader, raw_blocks)
    prepared['model'] = model_used
    prepared['prompt'] = prompt
    return prepared


def rewrite_block(
    blocks: list[dict],
    index: int,
    instruction: str,
    *,
    subject: str = '',
    preheader: str = '',
) -> dict:
    """Re-prompt a single block. The rest of the email stays put."""
    try:
        current = validate_blocks(blocks)
    except BlockError as exc:
        raise TemplateError(str(exc)) from exc
    if index < 0 or index >= len(current):
        raise TemplateError('That block is not in this email.')
    instruction = (instruction or '').strip()
    if not instruction:
        raise TemplateError('Say how you want that block changed.')

    schema = {
        'type': 'object',
        'additionalProperties': False,
        'required': ['block'],
        'properties': {
            'block': ai_generation_schema()['properties']['blocks']['items'],
        },
    }
    user = (
        f'Rewrite this block. Instruction: {instruction}\n'
        f'Current block: {current[index]!r}\n'
        f'Keep the same type unless the instruction requires a different one.'
    )
    try:
        parsed, _ = generate_structured_response(
            system_prompt=_SYSTEM.format(
                merge_fields=', '.join(mf.MERGE_FIELD_KEYS), tone='warm',
            ),
            user_prompt=user,
            schema=schema,
            schema_name='marketing_block',
            temperature=0.4,
        )
    except Exception as exc:
        raise TemplateError('Could not rewrite that block.') from exc

    replacement = (parsed or {}).get('block')
    updated = list(current)
    updated[index] = replacement
    return prepare(subject, preheader, updated)
