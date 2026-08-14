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

- Configured official Gemini API base URL: `https://generativelanguage.googleapis.com/v1beta`
- The configured request uses `POST <base_url>/models/<model>:generateContent` with `contents[].parts[].inline_data`. Google also recommends `POST <base_url>/interactions` for agentic and multi-turn workflows; set `endpoint_style: "interactions"` when that request shape is preferred.
- Authentication uses `x-goog-api-key: <GEMINI_API_KEY>`. The key is read from the project's `.env`.
- Native `generateContent` requests must set `contents[0].role` to `user`. The bridge sends an explicit `User-Agent` for traceability and compatibility with gateways that enforce client-header policies.
- Gemini requests from this skill do not include any thinking parameter.
- Interactions responses are read from `output_text` (with a fallback traversal of `outputs`).

For a compatible third-party Gemini gateway, override `base_url` and, if necessary, set `endpoint_style` to `generate-content`, `interactions`, or `openai`.

## Response Extraction

Qwen-compatible responses are read from `choices[0].message.content`. Gemini Interactions responses are read from `output_text`; the legacy `generateContent` fallback reads text parts under `candidates[0].content.parts`. A response with no text is treated as an error so DeepSeek never receives an apparently valid but empty visual result.
