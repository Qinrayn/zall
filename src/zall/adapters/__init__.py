"""zall.adapters — model SDK isolation layer.

This subpackage MAY import model SDKs (zall.core MUST NOT). Each Adapter
implements core.ModelAdapter interface, so core is oblivious to specific model details,
satisfying PR-3 / IPR-3.

Available adapters:
  - OpenAICompatAdapter    — OpenAI-compatible APIs (OpenAI, DeepSeek, GLM, etc.)
  - AnthropicAdapter       — Anthropic Claude
  - GeminiAdapter          — Google Gemini
  - OllamaAdapter          — Ollama local models (llama, qwen, etc.)
"""

from zall.adapters.base import BaseAdapter
from zall.adapters.openai_compat import OpenAICompatAdapter
from zall.adapters.anthropic import AnthropicAdapter
from zall.adapters.gemini import GeminiAdapter
from zall.adapters.ollama import OllamaAdapter

__all__ = [
    "BaseAdapter",
    "OpenAICompatAdapter",
    "AnthropicAdapter",
    "GeminiAdapter",
    "OllamaAdapter",
]
