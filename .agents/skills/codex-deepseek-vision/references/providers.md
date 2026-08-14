# Vision Provider Reference

## Qwen / DashScope

- Recommended OpenAI-compatible base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- Global-region alternative: `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`
- Official model code: `qwen3.8-max` (lowercase). The product page display name is not the API identifier.
- The bridge posts to `<base_url>/chat/completions` with a multimodal `messages` array containing `image_url` followed by text. Local files are sent as `data:<mime>;base64,...` Data URLs; this is supported by the Qwen vision documentation for the OpenAI-compatible API.
- Authentication uses `Authorization: Bearer <DASHSCOPE_API_KEY>`.
- When enabled, thinking is sent as `enable_thinking: true`; a positive `thinking_budget` is also sent as `thinking_budget`. With the OpenAI-compatible SDK, these non-standard fields belong in the request body.

The Qwen documentation notes that `qwen3.8-max` requires the multimodal route when an image is supplied. Do not send a plain string-only message for an image task.

## Gemini

- Configured compatible-gateway base URL: `https://zeusapi.huggi.dev/v1beta`
- Optional direct-Google REST base URL (only when deliberately using Google's own credential): `https://generativelanguage.googleapis.com/v1beta`
- Official model code: `gemini-3.6-flash` (lowercase).
- The configured gateway uses the legacy native `POST <base_url>/models/<model>:generateContent` shape with `contents[].parts[].inline_data`. The official current image-understanding examples use `POST <base_url>/interactions` with `input` entries `{type:"text", text:...}` and `{type:"image", data:<base64>, mime_type:<mime>}`; select `endpoint_style: "interactions"` when using the official Google endpoint.
- The configured relay accepts the same `x-goog-api-key: <GEMINI_API_KEY>` header as the native Gemini shape. The key is read from the project's `.env`; it is not assumed to be a Google-issued key.
- Native `generateContent` requests must set `contents[0].role` to `user`. The configured relay is fronted by Cloudflare; the bridge sends an explicit `User-Agent` because Python's default `Python-urllib/...` can be rejected before the API returns a JSON error.
- Gemini requests from this skill do not include any thinking parameter.
- Interactions responses are read from `output_text` (with a fallback traversal of `outputs`).

For an older or third-party Gemini gateway, set `endpoint_style` to `generate-content` or `openai` and configure its endpoint explicitly. A WAF/403 response from a third-party gateway is not evidence that the official Gemini request shape is invalid.

## Response Extraction

Qwen-compatible responses are read from `choices[0].message.content`. Gemini Interactions responses are read from `output_text`; the legacy `generateContent` fallback reads text parts under `candidates[0].content.parts`. A response with no text is treated as an error so DeepSeek never receives an apparently valid but empty visual result.
