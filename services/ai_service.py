"""
Centralized AI Service for all OpenAI interactions.
Provides consistent model selection with fallback chain across all features.

Model Hierarchy:
1. Primary: GPT-5.1 (Responses API with reasoning)
2. Fallback: GPT-5-mini (Responses API with reasoning)
3. Legacy: GPT-4o (Chat Completions API)

Usage:
    from services.ai_service import generate_ai_response
    
    response = generate_ai_response(
        system_prompt="You are a helpful assistant...",
        user_prompt="Help me with...",
        temperature=0.7,
        json_mode=False  # Set True for JSON responses
    )
"""

import openai
import logging
from config import Config

# Set up logging
logger = logging.getLogger(__name__)

# Model configuration - change these to update all AI features at once
PRIMARY_MODEL = "gpt-5.1"
FALLBACK_MODEL = "gpt-5-mini"
LEGACY_MODEL = "gpt-4.1-mini"  # Updated from gpt-4o for better vision support and speed

# Errors that should trigger fallback (model not available, rate limited, etc.)
FALLBACK_ERROR_CODES = [401, 403, 404, 429]


def _should_fallback(error):
    """Check if error should trigger fallback to next model."""
    if hasattr(error, 'status_code'):
        return error.status_code in FALLBACK_ERROR_CODES
    return False


def _call_responses_api(client, model, system_prompt, user_prompt, reasoning_effort="medium"):
    """Call the OpenAI Responses API (for GPT-5.x models)."""
    response = client.responses.create(
        model=model,
        instructions=system_prompt,
        input=user_prompt,
        reasoning={"effort": reasoning_effort}
    )
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
    
    # ===== TRY PRIMARY MODEL (GPT-5.1) =====
    try:
        logger.info(f"[1/3] Attempting primary model: {PRIMARY_MODEL}")
        result = _call_responses_api(client, PRIMARY_MODEL, system_prompt, user_prompt, reasoning_effort)
        logger.info(f"SUCCESS: Generated response with {PRIMARY_MODEL}")
        return result
        
    except (openai.NotFoundError, openai.AuthenticationError, openai.PermissionDeniedError, openai.RateLimitError) as e:
        logger.warning(f"FALLBACK TRIGGERED: {PRIMARY_MODEL} failed with {type(e).__name__}. Error: {str(e)}")
        
    except openai.APIError as e:
        if _should_fallback(e):
            logger.warning(f"FALLBACK TRIGGERED: {PRIMARY_MODEL} failed with status {e.status_code}. Error: {str(e)}")
        else:
            logger.error(f"FATAL: {PRIMARY_MODEL} failed with unrecoverable error: {str(e)}")
            raise
    
    # ===== TRY FALLBACK MODEL (GPT-5-mini) =====
    try:
        logger.info(f"[2/3] Attempting fallback model: {FALLBACK_MODEL}")
        result = _call_responses_api(client, FALLBACK_MODEL, system_prompt, user_prompt, reasoning_effort)
        logger.info(f"SUCCESS: Generated response with {FALLBACK_MODEL}")
        return result
        
    except (openai.NotFoundError, openai.AuthenticationError, openai.PermissionDeniedError, openai.RateLimitError) as e:
        logger.warning(f"FALLBACK TRIGGERED: {FALLBACK_MODEL} failed with {type(e).__name__}. Error: {str(e)}")
        
    except openai.APIError as e:
        if _should_fallback(e):
            logger.warning(f"FALLBACK TRIGGERED: {FALLBACK_MODEL} failed with status {e.status_code}. Error: {str(e)}")
        else:
            logger.error(f"FATAL: {FALLBACK_MODEL} failed with unrecoverable error: {str(e)}")
            raise
    
    # ===== TRY LEGACY MODEL (GPT-4o) =====
    try:
        logger.info(f"[3/3] Attempting legacy model: {LEGACY_MODEL} (using Chat Completions API)")
        result = _call_chat_completions_api(client, LEGACY_MODEL, system_prompt, user_prompt, temperature, json_mode)
        logger.info(f"SUCCESS: Generated response with legacy model {LEGACY_MODEL}")
        return result
        
    except Exception as legacy_error:
        logger.error(f"FATAL: All models failed. Legacy model {LEGACY_MODEL} error: {str(legacy_error)}")
        raise


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
    
    # ===== TRY PRIMARY MODEL (GPT-5.1) =====
    try:
        logger.info(f"[1/3] Chat: Attempting primary model: {PRIMARY_MODEL}")
        result = _call_responses_api(client, PRIMARY_MODEL, system_prompt, conversation_context, "medium")
        logger.info(f"SUCCESS: Chat response generated with {PRIMARY_MODEL}")
        return result
        
    except (openai.NotFoundError, openai.AuthenticationError, openai.PermissionDeniedError, openai.RateLimitError) as e:
        logger.warning(f"FALLBACK TRIGGERED: {PRIMARY_MODEL} failed with {type(e).__name__}")
        
    except openai.APIError as e:
        if _should_fallback(e):
            logger.warning(f"FALLBACK TRIGGERED: {PRIMARY_MODEL} failed with status {e.status_code}")
        else:
            raise
    
    # ===== TRY FALLBACK MODEL (GPT-5-mini) =====
    try:
        logger.info(f"[2/3] Chat: Attempting fallback model: {FALLBACK_MODEL}")
        result = _call_responses_api(client, FALLBACK_MODEL, system_prompt, conversation_context, "medium")
        logger.info(f"SUCCESS: Chat response generated with {FALLBACK_MODEL}")
        return result
        
    except (openai.NotFoundError, openai.AuthenticationError, openai.PermissionDeniedError, openai.RateLimitError) as e:
        logger.warning(f"FALLBACK TRIGGERED: {FALLBACK_MODEL} failed with {type(e).__name__}")
        
    except openai.APIError as e:
        if _should_fallback(e):
            logger.warning(f"FALLBACK TRIGGERED: {FALLBACK_MODEL} failed with status {e.status_code}")
        else:
            raise
    
    # ===== TRY LEGACY MODEL (GPT-4o) =====
    try:
        logger.info(f"[3/3] Chat: Attempting legacy model: {LEGACY_MODEL}")
        result = _call_chat_completions_with_history(client, LEGACY_MODEL, messages, temperature, max_tokens)
        logger.info(f"SUCCESS: Chat response generated with legacy model {LEGACY_MODEL}")
        return result
        
    except Exception as legacy_error:
        logger.error(f"FATAL: All chat models failed. Error: {str(legacy_error)}")
        raise


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
