"""
Centralized AI Service for all OpenAI interactions.
Provides consistent model selection with fallback chain across all features.

Model Hierarchy (GPT-5.6 family, Responses API preferred):
1. Primary: gpt-5.6-sol (flagship)
2. Fallback: gpt-5.6-terra
3. Legacy: gpt-5.4

Pro mode (reasoning.mode=pro) is intentionally OFF unless a caller opts in later.
Structured outputs use Responses API ``text.format`` (json_schema), not Chat Completions.

Usage:
    from services.ai_service import generate_ai_response
    
    response = generate_ai_response(
        system_prompt="You are a helpful assistant...",
        user_prompt="Help me with...",
        temperature=0.7,
        json_mode=False  # Set True for JSON responses
    )
"""

import base64
import json
import os
import openai
import logging
from config import Config

# Set up logging
logger = logging.getLogger(__name__)

# Model configuration - change these to update all AI features at once
PRIMARY_MODEL = "gpt-5.6-sol"
FALLBACK_MODEL = "gpt-5.6-terra"
LEGACY_MODEL = "gpt-5.4"
MODEL_CHAIN = (PRIMARY_MODEL, FALLBACK_MODEL, LEGACY_MODEL)

# Errors that should trigger fallback (model not available, rate limited, etc.)
FALLBACK_ERROR_CODES = [401, 403, 404, 429]


def _should_fallback(error):
    """Check if error should trigger fallback to next model."""
    if hasattr(error, 'status_code'):
        return error.status_code in FALLBACK_ERROR_CODES
    return False


def _supports_text_verbosity(model: str) -> bool:
    """``text.verbosity`` is a GPT-5.6 family control."""
    return model.startswith("gpt-5.6")


def _call_responses_api(
    client,
    model,
    system_prompt,
    user_prompt,
    reasoning_effort="medium",
    text_format=None,
    verbosity=None,
):
    """
    Call the OpenAI Responses API (preferred path for GPT-5.6 / GPT-5.4).

    Args:
        text_format: Optional structured-output format dict for ``text.format``
            e.g. {"type": "json_schema", "name": "...", "strict": True, "schema": {...}}
        verbosity: Optional ``text.verbosity`` — "low" | "medium" | "high"
            (only sent for gpt-5.6* models)
    """
    kwargs = {
        "model": model,
        "instructions": system_prompt,
        "input": user_prompt,
        # Standard mode only — do not set reasoning.mode = "pro"
        "reasoning": {"effort": reasoning_effort},
    }
    text_opts = {}
    if text_format:
        text_opts["format"] = text_format
    if verbosity and _supports_text_verbosity(model):
        text_opts["verbosity"] = verbosity
    if text_opts:
        kwargs["text"] = text_opts

    response = client.responses.create(**kwargs)
    return response.output_text


def _call_chat_completions_api(client, model, system_prompt, user_prompt, temperature=0.7, json_mode=False):
    """Call the OpenAI Chat Completions API (for GPT-4.x and legacy models)."""
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": temperature
    }
    
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    
    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content


def _call_chat_completions_with_history(client, model, messages, temperature=0.7, max_tokens=2000):
    """Call Chat Completions API with full message history (for chat features)."""
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens
    )
    return response.choices[0].message.content


def generate_ai_response(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    json_mode: bool = False,
    reasoning_effort: str = "medium",
    api_key: str = None
) -> str:
    """
    Generate an AI response using the model fallback chain.
    
    Args:
        system_prompt: The system instructions for the AI
        user_prompt: The user's input/question
        temperature: Creativity level (0.0-1.0), used for legacy model
        json_mode: If True, request JSON response format (legacy model only)
        reasoning_effort: Reasoning effort for GPT-5.x models ("low", "medium", "high")
        api_key: Optional API key override (uses Config.OPENAI_API_KEY if not provided)
    
    Returns:
        The AI-generated response text
    
    Raises:
        ValueError: If API key is not configured
        Exception: If all models fail
    """
    # Get API key
    key = api_key or Config.OPENAI_API_KEY
    if not key:
        logger.error("OpenAI API key is not configured!")
        raise ValueError("OpenAI API key is not configured")
    
    # Initialize client
    client = openai.OpenAI(api_key=key)
    
    # Log masked API key for debugging
    masked_key = f"{key[:8]}...{key[-4:]}" if len(key) > 12 else "***"
    logger.info(f"AI Service using API key: {masked_key}")
    
    last_error = None
    for i, model in enumerate(MODEL_CHAIN):
        try:
            logger.info(f"[{i+1}/{len(MODEL_CHAIN)}] Attempting model: {model}")
            result = _call_responses_api(
                client, model, system_prompt, user_prompt, reasoning_effort
            )
            logger.info(f"SUCCESS: Generated response with {model}")
            return result

        except (openai.NotFoundError, openai.AuthenticationError,
                openai.PermissionDeniedError, openai.RateLimitError) as e:
            last_error = e
            logger.warning(
                f"FALLBACK TRIGGERED: {model} failed with {type(e).__name__}. Error: {e}"
            )
            continue

        except openai.APIError as e:
            last_error = e
            if _should_fallback(e):
                logger.warning(
                    f"FALLBACK TRIGGERED: {model} failed with status {e.status_code}. Error: {e}"
                )
                continue
            logger.error(f"FATAL: {model} failed with unrecoverable error: {e}")
            raise

        except Exception as e:
            last_error = e
            logger.warning(f"FALLBACK TRIGGERED: {model} unexpected error: {e}")
            continue

    # Last-ditch Chat Completions on LEGACY_MODEL (e.g. json_mode callers)
    try:
        logger.info(f"Chat Completions fallback: {LEGACY_MODEL}")
        result = _call_chat_completions_api(
            client, LEGACY_MODEL, system_prompt, user_prompt, temperature, json_mode
        )
        logger.info(f"SUCCESS: Generated response with {LEGACY_MODEL} (Chat Completions)")
        return result
    except Exception as legacy_error:
        logger.error(f"FATAL: All models failed. Last error: {last_error}; Chat Completions: {legacy_error}")
        raise


def generate_structured_response(
    system_prompt: str,
    user_prompt: str,
    schema: dict,
    schema_name: str = "structured_output",
    temperature: float = 0.4,
    reasoning_effort: str = "medium",
    api_key: str = None,
    model_chain: tuple | list | None = None,
) -> tuple:
    """
    Generate a validated JSON object via Responses API + strict json_schema.

    GPT-5.6 prefers Responses ``text.format`` over Chat Completions
    ``response_format``. Falls back through ``model_chain`` (default MODEL_CHAIN);
    if Responses structured output fails for a model, retries that model once via
    Chat Completions.

    Args:
        model_chain: Optional override of models to try, in order. Use this to
            pin a feature (e.g. Daily Briefing) to a faster model without
            changing chat / Telegram / other callers.

    Returns:
        (parsed_dict, model_used)

    Raises:
        ValueError: If API key is missing
        Exception: If all models fail or the response is not valid JSON
    """
    key = api_key or Config.OPENAI_API_KEY
    if not key:
        logger.error("OpenAI API key is not configured!")
        raise ValueError("OpenAI API key is not configured")

    chain = tuple(model_chain) if model_chain else MODEL_CHAIN
    client = openai.OpenAI(api_key=key)
    last_error = None
    text_format = {
        "type": "json_schema",
        "name": schema_name,
        "strict": True,
        "schema": schema,
    }

    for i, model in enumerate(chain):
        # --- Preferred: Responses API text.format ---
        try:
            logger.info(f"[{i+1}/{len(chain)}] Structured (Responses): {model}")
            raw = _call_responses_api(
                client,
                model,
                system_prompt,
                user_prompt,
                reasoning_effort=reasoning_effort,
                text_format=text_format,
            )
            parsed = json.loads(raw or "{}")
            logger.info(f"SUCCESS: Structured response with {model} (Responses)")
            return parsed, model

        except (openai.NotFoundError, openai.AuthenticationError,
                openai.PermissionDeniedError, openai.RateLimitError) as e:
            last_error = e
            logger.warning(
                f"Structured fallback: {model} Responses failed with {type(e).__name__}"
            )
            # Fall through to Chat Completions attempt, then next model
        except openai.APIError as e:
            last_error = e
            if _should_fallback(e):
                logger.warning(
                    f"Structured fallback: {model} Responses status {e.status_code}"
                )
            else:
                logger.warning(f"Structured: {model} Responses error, trying Chat Completions: {e}")
        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(f"Structured JSON parse failed on {model} (Responses): {e}")
        except Exception as e:
            last_error = e
            logger.warning(f"Structured Responses error with {model}: {e}")

        # --- Compatibility: Chat Completions response_format ---
        try:
            logger.info(f"[{i+1}/{len(chain)}] Structured (Chat Completions): {model}")
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    },
                },
                "reasoning_effort": reasoning_effort,
            }
            response = client.chat.completions.create(**kwargs)
            raw = response.choices[0].message.content or "{}"
            parsed = json.loads(raw)
            logger.info(f"SUCCESS: Structured response with {model} (Chat Completions)")
            return parsed, model

        except (openai.NotFoundError, openai.AuthenticationError,
                openai.PermissionDeniedError, openai.RateLimitError) as e:
            last_error = e
            logger.warning(
                f"Structured fallback: {model} Chat Completions failed with {type(e).__name__}"
            )
            continue
        except openai.APIError as e:
            last_error = e
            if _should_fallback(e):
                logger.warning(
                    f"Structured fallback: {model} Chat Completions status {e.status_code}"
                )
                continue
            logger.error(f"Structured fatal: {model} unrecoverable: {e}")
            raise
        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(f"Structured JSON parse failed on {model} (Chat Completions): {e}")
            continue
        except Exception as e:
            last_error = e
            logger.error(f"Structured error with {model}: {e}")
            if i < len(chain) - 1:
                continue
            raise

    raise Exception(f"All models failed for structured response: {last_error}")


def generate_chat_response(
    messages: list,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    api_key: str = None
) -> str:
    """
    Generate a chat response with full conversation history.
    Uses the same model fallback chain but supports multi-turn conversations.
    
    Args:
        messages: List of message dicts with 'role' and 'content' keys
        temperature: Creativity level (0.0-1.0)
        max_tokens: Maximum response length
        api_key: Optional API key override
    
    Returns:
        The AI-generated response text
    """
    # Get API key
    key = api_key or Config.OPENAI_API_KEY
    if not key:
        logger.error("OpenAI API key is not configured!")
        raise ValueError("OpenAI API key is not configured")
    
    # Initialize client
    client = openai.OpenAI(api_key=key)
    
    # Extract system prompt and build user context for Responses API
    system_prompt = ""
    conversation_context = ""
    
    for msg in messages:
        if msg['role'] == 'system':
            system_prompt = msg['content']
        else:
            conversation_context += f"\n{msg['role'].upper()}: {msg['content']}\n"
    
    last_error = None
    for i, model in enumerate(MODEL_CHAIN):
        try:
            logger.info(f"[{i+1}/{len(MODEL_CHAIN)}] Chat: Attempting {model}")
            result = _call_responses_api(
                client,
                model,
                system_prompt,
                conversation_context,
                reasoning_effort="medium",
                verbosity="medium",
            )
            logger.info(f"SUCCESS: Chat response generated with {model}")
            return result

        except (openai.NotFoundError, openai.AuthenticationError,
                openai.PermissionDeniedError, openai.RateLimitError) as e:
            last_error = e
            logger.warning(f"FALLBACK TRIGGERED: {model} failed with {type(e).__name__}")
            continue

        except openai.APIError as e:
            last_error = e
            if _should_fallback(e):
                logger.warning(f"FALLBACK TRIGGERED: {model} failed with status {e.status_code}")
                continue
            raise

        except Exception as e:
            last_error = e
            logger.warning(f"FALLBACK TRIGGERED: {model} unexpected error: {e}")
            continue

    try:
        logger.info(f"Chat Completions fallback: {LEGACY_MODEL}")
        result = _call_chat_completions_with_history(
            client, LEGACY_MODEL, messages, temperature, max_tokens
        )
        logger.info(f"SUCCESS: Chat response generated with {LEGACY_MODEL} (Chat Completions)")
        return result
    except Exception as legacy_error:
        logger.error(f"FATAL: All chat models failed. Last: {last_error}; Chat Completions: {legacy_error}")
        raise


def stream_chat_response(
    system_prompt: str,
    user_prompt: str,
    image_data: str = None,
    api_key: str = None,
    reasoning_effort: str = "medium",
):
    """
    Stream a chat response with the same model fallback chain as non-streaming.

    Yields text chunks. Falls back through gpt-5.6-sol -> gpt-5.6-terra -> gpt-5.4.
    Uses Responses API (standard reasoning mode, no pro) when there is no image.
    If all fail, yields an error message.

    Args:
        system_prompt: System instructions
        user_prompt: Full user prompt (including conversation context)
        image_data: Optional base64-encoded image for vision
        api_key: Optional API key override
        reasoning_effort: GPT-5.6 reasoning effort (default medium; pro mode OFF)

    Yields:
        str: Text chunks of the response
    """
    key = api_key or Config.OPENAI_API_KEY
    if not key:
        yield "Sorry, the AI service is not configured. Please try again later."
        return

    client = openai.OpenAI(api_key=key)

    def _build_user_content(prompt, img):
        if img:
            return [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}", "detail": "auto"}}
            ]
        return prompt

    user_content = _build_user_content(user_prompt, image_data)

    for i, model in enumerate(MODEL_CHAIN):
        try:
            logger.info(f"[{i+1}/{len(MODEL_CHAIN)}] Streaming: attempting {model}")

            if not image_data:
                # Responses API is the GPT-5.6-recommended path for chat
                stream_kwargs = {
                    "model": model,
                    "instructions": system_prompt,
                    "input": user_prompt,
                    "reasoning": {"effort": reasoning_effort},
                    "stream": True,
                }
                if _supports_text_verbosity(model):
                    stream_kwargs["text"] = {"verbosity": "medium"}
                stream = client.responses.create(**stream_kwargs)
                for event in stream:
                    if hasattr(event, 'type') and event.type == "response.output_text.delta":
                        yield event.delta
                    elif hasattr(event, 'delta') and event.delta:
                        yield event.delta
            else:
                # Vision attachments: Chat Completions multimodal path
                stream = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    stream=True,
                )
                for chunk in stream:
                    if chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content

            logger.info(f"SUCCESS: Streaming completed with {model}")
            return

        except (openai.NotFoundError, openai.AuthenticationError,
                openai.PermissionDeniedError, openai.RateLimitError) as e:
            logger.warning(f"Stream fallback: {model} failed with {type(e).__name__}")
            continue

        except openai.APIError as e:
            if _should_fallback(e):
                logger.warning(f"Stream fallback: {model} failed with status {e.status_code}")
                continue
            logger.error(f"Stream fatal: {model} unrecoverable error: {e}")
            break

        except Exception as e:
            logger.error(f"Stream error with {model}: {e}")
            if i < len(MODEL_CHAIN) - 1:
                continue
            break

    yield "Sorry, I encountered an error. Please try again."


# Ordinary tool loops keep the existing Chat Completions transcript format.
# Turns with native file inputs use the Responses API because it can ingest
# PDFs directly while preserving function-call output items between rounds.
MAX_TOOL_ROUNDS = 5

# Chat Completions will not accept function tools from a GPT-5.6 model with any
# reasoning effort above 'none'. This is the cost of staying on Chat Completions
# for the tool loop; see _tool_round_with_fallback.
TOOL_REASONING_EFFORT = "none"


def run_tool_conversation(
    system_prompt: str,
    messages: list,
    tools: list,
    execute_tool,
    max_rounds: int = MAX_TOOL_ROUNDS,
    api_key: str = None,
    temperature: float = 0.3,
    input_files: list[dict] | None = None,
):
    """Run a tool-calling conversation, yielding (event, payload) tuples.

    Events, in the order a caller will see them:

        ('tool_start', {'name', 'arguments'})   a tool is about to run
        ('tool_result', {'name', 'result'})     that tool returned
        ('text', str)                           a chunk of the final answer
        ('messages', list)                      full transcript incl. tool turns
        ('error', str)                          fatal; no answer was produced

    ``execute_tool(name, arguments)`` must return a ``(model_payload, meta)``
    tuple: the JSON the model sees, and anything the caller wants forwarded on
    the 'tool_result' event. It must not raise; a failure should come back as a
    payload the model can read and recover from.

    Args:
        messages: Prior turns as OpenAI message dicts (no system message).
        tools: OpenAI function-tool schemas.
        max_rounds: Cap on tool rounds before the loop is cut off, so a model
            that keeps calling tools cannot spin indefinitely.
        input_files: Optional trusted file payloads. When present, the turn
            uses the Responses API so PDFs are read as complete documents.
    """
    key = api_key or Config.OPENAI_API_KEY
    if not key:
        yield ('error', 'Sorry, the AI service is not configured. Please try again later.')
        return

    client = openai.OpenAI(api_key=key)
    if input_files:
        yield from _run_responses_tool_conversation(
            client=client,
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            execute_tool=execute_tool,
            input_files=input_files,
            max_rounds=max_rounds,
        )
        return

    convo = [{"role": "system", "content": system_prompt}] + list(messages)

    for round_index in range(max_rounds):
        is_final_round = round_index == max_rounds - 1

        completion = _tool_round_with_fallback(
            client=client,
            messages=convo,
            tools=tools,
            temperature=temperature,
            # On the last permitted round, drop the tools so the model is
            # forced to answer instead of requesting another call we would
            # have to refuse.
            allow_tools=not is_final_round,
        )
        if completion is None:
            yield ('error', 'Sorry, I encountered an error. Please try again.')
            return

        message = completion.choices[0].message
        tool_calls = getattr(message, 'tool_calls', None)

        if not tool_calls:
            text = message.content or ''
            convo.append({"role": "assistant", "content": text})
            for chunk in _chunk_text(text):
                yield ('text', chunk)
            yield ('messages', convo[1:])
            return

        convo.append({
            "role": "assistant",
            "content": message.content or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments or '{}',
                    },
                }
                for call in tool_calls
            ],
        })

        for call in tool_calls:
            name = call.function.name
            arguments = _parse_tool_arguments(call.function.arguments)
            yield ('tool_start', {'name': name, 'arguments': arguments})

            payload, meta = execute_tool(name, arguments)
            yield ('tool_result', {'name': name, 'result': meta})

            convo.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(payload, default=str),
            })

    # Ran out of rounds without a text answer. Say so rather than leaving the
    # agent staring at tool chips with no reply.
    yield ('text', (
        'I got partway through that but ran out of steps before I could wrap up. '
        'Anything already confirmed above did go through. Ask me again and I '
        'will pick it up from here.\n\n--BOB'
    ))
    yield ('messages', convo[1:])


def _run_responses_tool_conversation(
    *,
    client,
    system_prompt: str,
    messages: list,
    tools: list,
    execute_tool,
    input_files: list[dict],
    max_rounds: int,
):
    """Responses API tool loop for turns containing native file inputs.

    The Responses API can read PDFs as files, including their selectable text
    and rendered pages. Chat Completions cannot accept PDF content directly.
    """
    input_items = _responses_input_items(messages, input_files)
    response_tools = _responses_tool_schemas(tools)

    for round_index in range(max_rounds):
        is_final_round = round_index == max_rounds - 1
        response = _responses_tool_round_with_fallback(
            client=client,
            system_prompt=system_prompt,
            input_items=input_items,
            tools=response_tools,
            allow_tools=not is_final_round,
        )
        if response is None:
            yield ('error', 'Sorry, I encountered an error. Please try again.')
            return

        output_items = list(getattr(response, 'output', None) or [])
        input_items.extend(output_items)
        tool_calls = [
            item for item in output_items
            if getattr(item, 'type', None) == 'function_call'
        ]

        if not tool_calls:
            text = getattr(response, 'output_text', None) or ''
            transcript = list(messages)
            transcript.append({'role': 'assistant', 'content': text})
            for chunk in _chunk_text(text):
                yield ('text', chunk)
            yield ('messages', transcript)
            return

        for call in tool_calls:
            name = call.name
            arguments = _parse_tool_arguments(call.arguments)
            yield ('tool_start', {'name': name, 'arguments': arguments})

            payload, meta = execute_tool(name, arguments)
            yield ('tool_result', {'name': name, 'result': meta})
            input_items.append({
                'type': 'function_call_output',
                'call_id': call.call_id,
                'output': json.dumps(payload, default=str),
            })

    yield ('text', (
        'I got partway through that but ran out of steps before I could wrap up. '
        'Anything already confirmed above did go through. Ask me again and I '
        'will pick it up from here.\n\n--BOB'
    ))
    yield ('messages', list(messages))


def _responses_input_items(messages: list, input_files: list[dict]) -> list:
    """Convert chat messages and append native files to the current user turn."""
    items = []
    for message in messages:
        role = message.get('role')
        content = message.get('content')
        if role not in {'user', 'assistant', 'developer', 'system'}:
            continue
        items.append({'role': role, 'content': content})

    file_parts = []
    for item in input_files:
        data = item.get('data')
        if not isinstance(data, (bytes, bytearray)) or not data:
            continue
        mime = item.get('mime') or 'application/octet-stream'
        encoded = base64.b64encode(bytes(data)).decode('ascii')
        file_parts.append({
            'type': 'input_file',
            'filename': item.get('filename') or 'attachment',
            'file_data': f'data:{mime};base64,{encoded}',
            'detail': item.get('detail') or 'high',
        })

    if not file_parts:
        return items

    if items and items[-1]['role'] == 'user':
        current = items[-1].get('content')
        if isinstance(current, str):
            items[-1]['content'] = [
                {'type': 'input_text', 'text': current},
                *file_parts,
            ]
        elif isinstance(current, list):
            items[-1]['content'] = list(current) + file_parts
        else:
            items[-1]['content'] = file_parts
    else:
        items.append({'role': 'user', 'content': file_parts})
    return items


def _responses_tool_schemas(tools: list) -> list[dict]:
    """Translate Chat Completions function tools to Responses API tools."""
    translated = []
    for tool in tools or []:
        function = tool.get('function') if isinstance(tool, dict) else None
        if not isinstance(function, dict):
            translated.append(tool)
            continue
        translated.append({
            'type': 'function',
            'name': function.get('name'),
            'description': function.get('description'),
            'parameters': function.get('parameters') or {
                'type': 'object',
                'properties': {},
            },
        })
    return translated


def _responses_tool_round_with_fallback(
    *,
    client,
    system_prompt: str,
    input_items: list,
    tools: list,
    allow_tools: bool,
):
    """One Responses API round, walking the standard model fallback chain."""
    for i, model in enumerate(MODEL_CHAIN):
        try:
            kwargs = {
                'model': model,
                'instructions': system_prompt,
                'input': input_items,
                'reasoning': {'effort': 'medium'},
            }
            if allow_tools and tools:
                kwargs['tools'] = tools
                kwargs['tool_choice'] = 'auto'

            logger.info(
                f"[{i+1}/{len(MODEL_CHAIN)}] Responses tool round: "
                f"attempting {model} "
                f"(tools={'on' if allow_tools and tools else 'off'})"
            )
            return client.responses.create(**kwargs)

        except (openai.NotFoundError, openai.AuthenticationError,
                openai.PermissionDeniedError, openai.RateLimitError) as e:
            logger.warning(
                f"Responses tool round fallback: {model} failed with "
                f"{type(e).__name__}"
            )
            continue

        except openai.APIError as e:
            if _should_fallback(e):
                logger.warning(
                    f"Responses tool round fallback: {model} failed with "
                    f"status {e.status_code}"
                )
                continue
            logger.error(
                f"Responses tool round fatal: {model} unrecoverable error: {e}"
            )
            return None

        except Exception as e:
            logger.error(f"Responses tool round error with {model}: {e}")
            if i < len(MODEL_CHAIN) - 1:
                continue
            return None

    logger.error("FATAL: All models failed during Responses tool round")
    return None


def _tool_round_with_fallback(*, client, messages, tools, temperature,
                              allow_tools: bool):
    """One Chat Completions call, walking MODEL_CHAIN on recoverable errors."""
    for i, model in enumerate(MODEL_CHAIN):
        try:
            kwargs = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                # The GPT-5.6 family refuses function tools on
                # /v1/chat/completions unless reasoning is off entirely:
                # "Function tools with reasoning_effort are not supported ...
                # use /v1/responses or set reasoning_effort to 'none'".
                # Kept uniform across the loop so every round behaves the same.
                # Restoring reasoning here means moving to the Responses API,
                # which does support tools and reasoning together.
                "reasoning_effort": TOOL_REASONING_EFFORT,
            }
            if allow_tools and tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            logger.info(
                f"[{i+1}/{len(MODEL_CHAIN)}] Tool round: attempting {model} "
                f"(tools={'on' if allow_tools and tools else 'off'})"
            )
            return client.chat.completions.create(**kwargs)

        except (openai.NotFoundError, openai.AuthenticationError,
                openai.PermissionDeniedError, openai.RateLimitError) as e:
            logger.warning(f"Tool round fallback: {model} failed with {type(e).__name__}")
            continue

        except openai.APIError as e:
            if _should_fallback(e):
                logger.warning(
                    f"Tool round fallback: {model} failed with status {e.status_code}"
                )
                continue
            logger.error(f"Tool round fatal: {model} unrecoverable error: {e}")
            return None

        except Exception as e:
            logger.error(f"Tool round error with {model}: {e}")
            if i < len(MODEL_CHAIN) - 1:
                continue
            return None

    logger.error("FATAL: All models failed during tool round")
    return None


def _parse_tool_arguments(raw):
    """Tool arguments arrive as a JSON string and can be malformed."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning(f"Unparseable tool arguments: {raw!r}")
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _chunk_text(text: str, size: int = 240):
    """Emit the final answer in chunks so the UI can render progressively.

    The tool loop is not streamed token-by-token: the model has to be able to
    change course after a tool result, which means waiting for each full turn.
    """
    if not text:
        return
    for start in range(0, len(text), size):
        yield text[start:start + size]


def transcribe_audio(audio_data: bytes, filename: str = "audio.webm", api_key: str = None) -> str:
    """
    Transcribe audio using OpenAI Whisper API.
    
    Args:
        audio_data: Raw audio bytes (supports webm, mp3, mp4, mpeg, mpga, m4a, wav, or webm)
        filename: Filename with extension (used to determine format)
        api_key: Optional API key override
    
    Returns:
        Transcribed text string
    
    Raises:
        ValueError: If API key is not configured or audio data is empty
        Exception: If transcription fails
    """
    import io
    
    # Validate audio data
    if not audio_data or len(audio_data) == 0:
        logger.error("Empty audio data provided to transcribe_audio")
        raise ValueError("Audio data is empty")
    
    # Get API key
    key = api_key or Config.OPENAI_API_KEY
    if not key:
        logger.error("OpenAI API key is not configured!")
        raise ValueError("OpenAI API key is not configured")
    
    # Initialize client
    client = openai.OpenAI(api_key=key)
    
    try:
        logger.info(f"Transcribing audio file: {filename} ({len(audio_data)} bytes)")
        
        # Create a file-like object from the audio data
        audio_file = io.BytesIO(audio_data)
        audio_file.name = filename  # Whisper needs the filename for format detection
        
        # Call Whisper API
        # Use "json" format instead of "text" to avoid New Relic instrumentation
        # issues with plain string responses
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="json"
        )
        
        # Extract text from JSON response
        transcription_text = response.text.strip()
        
        logger.info(f"SUCCESS: Transcribed {len(transcription_text)} characters")
        return transcription_text
        
    except Exception as e:
        logger.error(f"Whisper transcription failed: {str(e)}")
        raise


def generate_vision_response(
    system_prompt: str,
    user_prompt: str,
    images: list = None,
    temperature: float = 0.7,
    api_key: str = None
) -> str:
    """
    Generate an AI response with vision/image analysis capability.
    Uses Chat Completions API with image input support.
    
    Args:
        system_prompt: The system instructions for the AI
        user_prompt: The user's input/question
        images: List of base64-encoded image strings
        temperature: Creativity level (0.0-1.0)
        api_key: Optional API key override
    
    Returns:
        The AI-generated response text
    """
    # Get API key
    key = api_key or Config.OPENAI_API_KEY
    if not key:
        logger.error("OpenAI API key is not configured!")
        raise ValueError("OpenAI API key is not configured")
    
    # Initialize client
    client = openai.OpenAI(api_key=key)
    
    # Build content array with text and images
    user_content = [{"type": "text", "text": user_prompt}]
    
    # Add images if provided
    for img_base64 in images or []:
        user_content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{img_base64}",
                "detail": "auto"
            }
        })
    
    # ===== TRY PRIMARY MODEL (GPT-5.1) =====
    try:
        logger.info(f"[1/2] Vision: Attempting primary model: {PRIMARY_MODEL}")
        response = client.chat.completions.create(
            model=PRIMARY_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=temperature
        )
        logger.info(f"SUCCESS: Vision response generated with {PRIMARY_MODEL}")
        return response.choices[0].message.content
        
    except (openai.NotFoundError, openai.AuthenticationError, openai.PermissionDeniedError, openai.RateLimitError) as e:
        logger.warning(f"FALLBACK TRIGGERED: {PRIMARY_MODEL} vision failed with {type(e).__name__}")
        
    except openai.APIError as e:
        if _should_fallback(e):
            logger.warning(f"FALLBACK TRIGGERED: {PRIMARY_MODEL} vision failed with status {e.status_code}")
        else:
            raise
    
    # ===== TRY LEGACY MODEL (GPT-4.1-mini) =====
    try:
        logger.info(f"[2/2] Vision: Attempting legacy model: {LEGACY_MODEL}")
        response = client.chat.completions.create(
            model=LEGACY_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=temperature
        )
        logger.info(f"SUCCESS: Vision response generated with {LEGACY_MODEL}")
        return response.choices[0].message.content
        
    except Exception as legacy_error:
        logger.error(f"FATAL: All vision models failed. Error: {str(legacy_error)}")
        raise


# =============================================================================
# MAGIC INBOX CONTACT EXTRACTION (vCard / CSV / business card photo / signature)
# =============================================================================

# Single primary model for everything sent to the magic inbox: vCards, CSVs,
# images, plain text. Vision-capable + cheap, with one safety-net fallback.
# Override at runtime via INBOX_EXTRACTION_MODEL if the canonical model name
# changes (the plan allows for the live name to be e.g. ``gpt-5-nano-2026-…``).
INBOX_PRIMARY_MODEL = os.getenv('INBOX_EXTRACTION_MODEL', 'gpt-5.4-nano')
INBOX_FALLBACK_MODEL = os.getenv('INBOX_EXTRACTION_FALLBACK_MODEL', 'gpt-5-mini')

# Strict JSON schema we hold the model to. The orchestrator validates against
# this shape before creating any contacts.
CONTACT_EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["contacts"],
    "properties": {
        "contacts": {
            "type": "array",
            "description": (
                "Every distinct person referenced in the message. Empty if "
                "no real person can be confidently identified."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "first_name", "last_name", "email", "phone",
                    "street_address", "city", "state", "zip_code",
                    "notes", "group_name", "confidence",
                ],
                "properties": {
                    "first_name": {"type": ["string", "null"]},
                    "last_name": {"type": ["string", "null"]},
                    "email": {"type": ["string", "null"]},
                    "phone": {"type": ["string", "null"]},
                    "street_address": {"type": ["string", "null"]},
                    "city": {"type": ["string", "null"]},
                    "state": {"type": ["string", "null"]},
                    "zip_code": {"type": ["string", "null"]},
                    "notes": {
                        "type": ["string", "null"],
                        "description": (
                            "Brief context: company, title, where the contact "
                            "came from. Keep under 280 chars."
                        ),
                    },
                    "group_name": {
                        "type": ["string", "null"],
                        "description": (
                            "Only set when the sender explicitly says which "
                            "CRM group/tag/list these contacts should go into, "
                            "for example 'add to Buyers', 'group: Sphere', or "
                            "'put these in Past Clients'. Otherwise null."
                        ),
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                },
            },
        }
    },
}

CONTACT_EXTRACTION_SYSTEM_PROMPT = (
    "You extract real-estate-CRM contacts from messy inbound material: vCards, "
    "CSV rows, business-card photos, screenshots of LinkedIn profiles, email "
    "signatures, and forwarded messages. "
    "Return ONLY people the message clearly identifies as real human contacts. "
    "Do NOT invent fields. If a value is missing, return null. "
    "Use null (not empty string) for unknown fields. "
    "Normalize phone numbers to digits only (no formatting); the caller will "
    "format. Lowercase email addresses. Title-case names. "
    "Set confidence='high' only when you have at least a full name plus one of "
    "email or phone. Use 'medium' for partial business-card style data, 'low' "
    "for ambiguous mentions. "
    "If the sender clearly asks to put the contact(s) in a group, tag, bucket, "
    "or list, copy that requested group name into group_name for each affected "
    "contact. Examples: 'add these to Buyers', 'group: Sphere', 'tag as Open "
    "House Leads'. Do not infer a group from the person's job title, company, "
    "or email content unless the sender explicitly asks for it. "
    "Skip generic mailing-list footers, automated 'do-not-reply' senders, and "
    "the recipient themselves."
)


def _build_inbox_user_content(text: str, image_blocks):
    """Build the user message content array for the extraction call."""
    user_content = [{
        "type": "text",
        "text": (
            "Extract every real contact from the material below.\n\n"
            "------ MESSAGE ------\n"
            f"{text or '(no text body)'}\n"
            "------ END MESSAGE ------"
        ),
    }]
    for img_b64 in image_blocks or []:
        user_content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{img_b64}",
                "detail": "auto",
            },
        })
    return user_content


def _call_inbox_extraction(client, model, text, image_blocks):
    """One Chat Completions call with strict json_schema.

    Returns ``(parsed_dict, usage)`` where ``usage`` exposes token counts so
    the orchestrator can record observability data.
    """
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": CONTACT_EXTRACTION_SYSTEM_PROMPT},
            {"role": "user",
             "content": _build_inbox_user_content(text, image_blocks)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "contact_extraction",
                "schema": CONTACT_EXTRACTION_SCHEMA,
                "strict": True,
            },
        },
    )
    raw = response.choices[0].message.content or '{}'
    parsed = json.loads(raw)
    return parsed, response.usage


def generate_contact_extraction(
    text: str,
    image_blocks: list | None = None,
    api_key: str = None,
) -> dict:
    """Extract structured contacts from a normalized inbound payload.

    Single AI path — one model handles vCards, CSVs, photos, signatures.
    Reliability comes from the strict json_schema, not from branching by
    source kind.

    Args:
        text: Cleaned text body plus any text-attachment passthrough
              (vcf/csv/txt). Already capped + HTML-stripped by the caller.
        image_blocks: Optional list of base64-encoded JPEG images
              (already downscaled and capped by the caller).
        api_key: Optional API key override.

    Returns:
        Dict shaped like ``{"contacts": [...], "_meta": {...}}``. ``_meta``
        carries the model used and token usage so the orchestrator can write
        cost and audit info onto the InboundMessage row.
    """
    key = api_key or Config.OPENAI_API_KEY
    if not key:
        raise ValueError("OpenAI API key is not configured")

    client = openai.OpenAI(api_key=key)

    img_count = len(image_blocks or [])
    text_len = len(text or '')

    # ---- Primary: gpt-5.4-nano ------------------------------------------
    try:
        logger.info(
            'Inbox extraction [1/2]: model=%s text_chars=%d images=%d',
            INBOX_PRIMARY_MODEL, text_len, img_count,
        )
        parsed, usage = _call_inbox_extraction(
            client, INBOX_PRIMARY_MODEL, text, image_blocks,
        )
        parsed['_meta'] = _usage_meta(INBOX_PRIMARY_MODEL, usage)
        logger.info(
            'Inbox extraction SUCCESS: model=%s contacts=%d',
            INBOX_PRIMARY_MODEL, len(parsed.get('contacts') or []),
        )
        return parsed

    except (openai.NotFoundError, openai.AuthenticationError,
            openai.PermissionDeniedError, openai.RateLimitError,
            json.JSONDecodeError) as e:
        logger.warning(
            'Inbox extraction primary failed (%s) — falling back. err=%s',
            type(e).__name__, str(e),
        )
    except openai.APIError as e:
        if not _should_fallback(e):
            logger.error(
                'Inbox extraction primary failed unrecoverably status=%s err=%s',
                getattr(e, 'status_code', None), str(e),
            )
            raise
        logger.warning(
            'Inbox extraction primary APIError status=%s — falling back.',
            getattr(e, 'status_code', None),
        )

    # ---- Fallback: gpt-5-mini -------------------------------------------
    logger.info('Inbox extraction [2/2]: model=%s', INBOX_FALLBACK_MODEL)
    parsed, usage = _call_inbox_extraction(
        client, INBOX_FALLBACK_MODEL, text, image_blocks,
    )
    parsed['_meta'] = _usage_meta(INBOX_FALLBACK_MODEL, usage)
    logger.info(
        'Inbox extraction SUCCESS (fallback): model=%s contacts=%d',
        INBOX_FALLBACK_MODEL, len(parsed.get('contacts') or []),
    )
    return parsed


def _usage_meta(model: str, usage) -> dict:
    """Pull token counts off an OpenAI usage object in a defensive way."""
    tokens_in = getattr(usage, 'prompt_tokens', None) if usage else None
    tokens_out = getattr(usage, 'completion_tokens', None) if usage else None
    return {
        'model': model,
        'tokens_in': tokens_in,
        'tokens_out': tokens_out,
    }


# =============================================================================
# DOCUMENT EXTRACTION (structured data from document images)
# =============================================================================

EXTRACTION_MODEL = os.getenv("DOCUMENT_EXTRACTION_MODEL", "gpt-5.1")
EXTRACTION_FALLBACK_MODEL = os.getenv("DOCUMENT_EXTRACTION_FALLBACK_MODEL", "gpt-5-mini")
EXTRACTION_LEGACY_MODEL = os.getenv("DOCUMENT_EXTRACTION_LEGACY_MODEL", "gpt-4.1-mini")


def generate_document_extraction(
    system_prompt: str,
    user_prompt: str,
    images: list = None,
    api_key: str = None
) -> dict:
    """
    Extract structured data from document images using GPT-4.1-mini.
    Returns parsed JSON dict. Uses json_object response format for
    guaranteed valid JSON output.

    Args:
        system_prompt: Instructions for the extraction task
        user_prompt: Field definitions and format instructions
        images: List of base64-encoded PNG image strings (one per page)
        api_key: Optional API key override

    Returns:
        dict with extracted field values
    """
    key = api_key or Config.OPENAI_API_KEY
    if not key:
        raise ValueError("OpenAI API key is not configured")

    client = openai.OpenAI(api_key=key)

    user_content = [{"type": "text", "text": user_prompt}]
    for img_b64 in images or []:
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{img_b64}", "detail": "high"}
        })

    models = [EXTRACTION_MODEL, EXTRACTION_FALLBACK_MODEL, EXTRACTION_LEGACY_MODEL]
    last_error = None
    logger.info(f"Document extraction: sending {len(images or [])} page images to {models[0]}")

    for index, model in enumerate(dict.fromkeys(models), start=1):
        try:
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                "response_format": {"type": "json_object"},
            }
            if not model.startswith("gpt-5"):
                kwargs["temperature"] = 0.1

            response = client.chat.completions.create(**kwargs)
            result = json.loads(response.choices[0].message.content)
            logger.info(f"Document extraction: received {len(result)} fields from {model}")
            return result
        except Exception as error:
            last_error = error
            logger.warning(
                "Document extraction model %s/%s failed for %s: %s",
                index,
                len(models),
                model,
                error,
                exc_info=True,
            )

    raise last_error
