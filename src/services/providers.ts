export type Provider = { id: string; name: string; endpoint: string; model: string; keys: string; note?: string }
export const PROVIDERS: Provider[] = [
  { id: 'openrouter', name: 'OpenRouter', endpoint: 'https://openrouter.ai/api/v1', model: 'openrouter/free', keys: 'https://openrouter.ai/settings/keys', note: 'Free router; provider limits apply.' },
  { id: 'groq', name: 'Groq', endpoint: 'https://api.groq.com/openai/v1', model: 'llama-3.3-70b-versatile', keys: 'https://console.groq.com/keys' },
  { id: 'google', name: 'Google Gemini', endpoint: 'https://generativelanguage.googleapis.com/v1beta/openai', model: 'gemini-2.5-flash', keys: 'https://aistudio.google.com/app/apikey' },
  { id: 'cerebras', name: 'Cerebras', endpoint: 'https://api.cerebras.ai/v1', model: 'llama3.1-8b', keys: 'https://cloud.cerebras.ai/' },
  { id: 'mistral', name: 'Mistral', endpoint: 'https://api.mistral.ai/v1', model: 'mistral-small-latest', keys: 'https://console.mistral.ai/' },
  { id: 'nvidia', name: 'NVIDIA NIM', endpoint: 'https://integrate.api.nvidia.com/v1', model: 'meta/llama-3.1-8b-instruct', keys: 'https://build.nvidia.com/' },
  { id: 'huggingface', name: 'Hugging Face', endpoint: 'https://router.huggingface.co/v1', model: 'meta-llama/Llama-3.1-8B-Instruct', keys: 'https://huggingface.co/settings/tokens' },
  { id: 'cohere', name: 'Cohere', endpoint: 'https://api.cohere.ai/compatibility/v1', model: 'command-r-plus', keys: 'https://dashboard.cohere.com/api-keys' },
  { id: 'freellmapi', name: 'FreeLLMAPI router', endpoint: 'http://localhost:3001/v1', model: 'auto', keys: 'https://github.com/tashfeenahmed/freellmapi', note: 'Requires a locally running router accessible to this browser.' },
  { id: 'openai', name: 'OpenAI', endpoint: 'https://api.openai.com/v1', model: 'gpt-4.1-mini', keys: 'https://platform.openai.com/api-keys', note: 'API usage is billed separately from ChatGPT Plus.' },
  { id: 'custom', name: 'Other OpenAI compatible', endpoint: '', model: '', keys: '' },
]
export function providerForEndpoint(endpoint: string): Provider { return PROVIDERS.find(item => item.id !== 'custom' && item.endpoint === endpoint) ?? PROVIDERS[PROVIDERS.length - 1] }
