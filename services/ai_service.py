"""
Centralized AI Service for all OpenAI interactions.
Provides consistent model selection with fallback chain across all features.

Model Hierarchy:
1. Primary: GPT-4o (most capable, widely available)
2. Fallback: GPT-4o-mini (faster, cost-effective)
3. Legacy: GPT-3.5-turbo (broad compatibility)

All models use the Chat Completions API for maximum compatibility.

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

# Model configuration - using Chat Completions API compatible models
PRIMARY_MODEL = "gpt-4o"
FALLBACK_MODEL = "gpt-4o-mini"
LEGACY_MODEL = "gpt-3.5-turbo"

# Errors that should trigger fallback (model not available, rate limited, etc.)
FALLBACK_ERROR_CODES = [401, 403, 404, 429]


def _should_fallback(error):
    """Check if error should trigger fallback to next model."""
    if hasattr(error, 'status_code'):
        return error.status_code in FALLBACK_ERROR_CODES
    return False


def _call_chat_completions_api(client, model, system_prompt, user_prompt, temperature=0.7, json_mode=False):
    """Call the OpenAI Chat Completions API."""
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
        temperature: Creativity level (0.0-1.0)
        json_mode: If True, request JSON response format
        reasoning_effort: (unused, kept for backwards compatibility)
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
    
    # ===== TRY PRIMARY MODEL (GPT-4o) =====
    try:
        logger.info(f"[1/3] Attempting primary model: {PRIMARY_MODEL}")
        result = _call_chat_completions_api(client, PRIMARY_MODEL, system_prompt, user_prompt, temperature, json_mode)
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
    
    # ===== TRY FALLBACK MODEL (GPT-4o-mini) =====
    try:
        logger.info(f"[2/3] Attempting fallback model: {FALLBACK_MODEL}")
        result = _call_chat_completions_api(client, FALLBACK_MODEL, system_prompt, user_prompt, temperature, json_mode)
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
    
    # ===== TRY LEGACY MODEL (GPT-3.5-turbo) =====
    try:
        logger.info(f"[3/3] Attempting legacy model: {LEGACY_MODEL}")
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
    
    # ===== TRY PRIMARY MODEL (GPT-4o) =====
    try:
        logger.info(f"[1/3] Chat: Attempting primary model: {PRIMARY_MODEL}")
        result = _call_chat_completions_with_history(client, PRIMARY_MODEL, messages, temperature, max_tokens)
        logger.info(f"SUCCESS: Chat response generated with {PRIMARY_MODEL}")
        return result
        
    except (openai.NotFoundError, openai.AuthenticationError, openai.PermissionDeniedError, openai.RateLimitError) as e:
        logger.warning(f"FALLBACK TRIGGERED: {PRIMARY_MODEL} failed with {type(e).__name__}")
        
    except openai.APIError as e:
        if _should_fallback(e):
            logger.warning(f"FALLBACK TRIGGERED: {PRIMARY_MODEL} failed with status {e.status_code}")
        else:
            raise
    
    # ===== TRY FALLBACK MODEL (GPT-4o-mini) =====
    try:
        logger.info(f"[2/3] Chat: Attempting fallback model: {FALLBACK_MODEL}")
        result = _call_chat_completions_with_history(client, FALLBACK_MODEL, messages, temperature, max_tokens)
        logger.info(f"SUCCESS: Chat response generated with {FALLBACK_MODEL}")
        return result
        
    except (openai.NotFoundError, openai.AuthenticationError, openai.PermissionDeniedError, openai.RateLimitError) as e:
        logger.warning(f"FALLBACK TRIGGERED: {FALLBACK_MODEL} failed with {type(e).__name__}")
        
    except openai.APIError as e:
        if _should_fallback(e):
            logger.warning(f"FALLBACK TRIGGERED: {FALLBACK_MODEL} failed with status {e.status_code}")
        else:
            raise
    
    # ===== TRY LEGACY MODEL (GPT-3.5-turbo) =====
    try:
        logger.info(f"[3/3] Chat: Attempting legacy model: {LEGACY_MODEL}")
        result = _call_chat_completions_with_history(client, LEGACY_MODEL, messages, temperature, max_tokens)
        logger.info(f"SUCCESS: Chat response generated with legacy model {LEGACY_MODEL}")
        return result
        
    except Exception as legacy_error:
        logger.error(f"FATAL: All chat models failed. Error: {str(legacy_error)}")
        raise

