"""Shared model policy for BOB chat, tools, and supporting AI requests."""

import json
import logging

import openai
from config import Config

MODEL = "gpt-6.1-sol"
REASONING_EFFORT = "medium"
SERVICE_TIER = "fast"
logger = logging.getLogger(__name__)


def create_response(*, client=None, **kwargs):
    """Use the same BOB model policy on every request, without model fallback."""
    if client is None:
        if not Config.OPENAI_API_KEY:
            raise ValueError("OpenAI API key is not configured")
        client = openai.OpenAI(api_key=Config.OPENAI_API_KEY)
    response = client.responses.create(
        **kwargs,
        model=MODEL,
        reasoning={"effort": REASONING_EFFORT},
        service_tier=SERVICE_TIER,
    )
    logger.info("BOB response model=%s requested_tier=%s actual_tier=%s effort=%s",
                getattr(response, "model", MODEL), SERVICE_TIER,
                getattr(response, "service_tier", None), REASONING_EFFORT)
    return response


def generate_response(system_prompt, user_prompt, *, web_search=False,
                      user_location=None, json_mode=False):
    kwargs = {}
    if web_search:
        from services.ai_service import _web_search_tools
        kwargs["tools"] = _web_search_tools(user_location)
    if json_mode:
        kwargs["text"] = {"format": {"type": "json_object"}}
        user_prompt = "Return JSON.\n" + user_prompt
    return create_response(instructions=system_prompt, input=user_prompt,
                           **kwargs).output_text


def generate_chat_response(messages):
    return create_response(input=messages).output_text


def stream_chat_response(system_prompt, user_prompt):
    with create_response(instructions=system_prompt, input=user_prompt, stream=True) as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                yield event.delta
            elif event.type in {"response.failed", "response.incomplete", "error"}:
                raise RuntimeError("BOB stream did not complete")
            elif event.type == "response.completed":
                logger.info("BOB stream model=%s actual_tier=%s",
                            event.response.model, event.response.service_tier)


def generate_structured_response(system_prompt, user_prompt, schema,
                                 schema_name="structured_output", temperature=None):
    """Return parsed structured output for BOB briefings and templates.

    Temperature is accepted for the shared marketing generator interface but
    omitted from the API request because BOB uses reasoning.
    """
    response = create_response(
        instructions=system_prompt, input=user_prompt,
        text={"format": {"type": "json_schema", "name": schema_name,
                         "strict": True, "schema": schema}},
    )
    return json.loads(response.output_text), response.model


def generate_contact_extraction(text, image_blocks=None):
    """Reuse contact extraction rules and schema with BOB's model policy."""
    from services.ai_service import (
        CONTACT_EXTRACTION_SCHEMA, CONTACT_EXTRACTION_SYSTEM_PROMPT,
    )
    content = [{"type": "input_text", "text": text or "Extract contacts from this image."}]
    content.extend({"type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{image}",
                    "detail": "high"} for image in image_blocks or [])
    response = create_response(
        instructions=CONTACT_EXTRACTION_SYSTEM_PROMPT,
        input=[{"role": "user", "content": content}],
        text={"format": {"type": "json_schema", "name": "contact_extraction",
                         "strict": True, "schema": CONTACT_EXTRACTION_SCHEMA}},
    )
    parsed = json.loads(response.output_text)
    usage = getattr(response, "usage", None)
    parsed["_meta"] = {
        "model": getattr(response, "model", MODEL),
        "tokens_in": getattr(usage, "input_tokens", None),
        "tokens_out": getattr(usage, "output_tokens", None),
    }
    return parsed
