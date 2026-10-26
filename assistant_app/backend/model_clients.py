import asyncio
import json
import time

import httpx


class ModelError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


class ModelClients:
    def __init__(self, settings):
        self.settings = settings
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(settings.model_timeout, connect=5),
                                      follow_redirects=False, trust_env=False,
                                      limits=httpx.Limits(max_connections=8))

    def config(self, kind):
        s = self.settings
        return ((s.text_model_base_url, s.text_served_model, s.text_api_key) if kind == 'text'
                else (s.vision_model_base_url, s.vision_served_model, s.vision_api_key))

    async def close(self):
        await self.http.aclose()

    async def call(self, kind, messages, json_mode=False):
        url, model, key = self.config(kind)
        cloud = (self.settings.text_mode == 'approved_cloud' if kind == 'text'
                 else self.settings.vision_mode == 'approved_cloud')
        payload = {'model': model, 'messages': messages, 'temperature': 0,
                   'max_tokens': self.settings.max_output_tokens if kind == 'text' else self.settings.vision_max_output_tokens,
                   'stream': False}
        if cloud:
            payload['enable_thinking'] = False
        else:
            payload['chat_template_kwargs'] = {'enable_thinking': False}
        if json_mode:
            payload['response_format'] = {'type': 'json_object'}
        headers = {'Authorization': 'Bearer ' + key.get_secret_value()} if key.get_secret_value() else {}
        start = time.monotonic()
        try:
            async with self.http.stream('POST', url.rstrip('/') + '/chat/completions',
                                        json=payload, headers=headers) as response:
                if response.status_code != 200:
                    raise ModelError('model_http_error', '模型服务暂不可用，请检查受控推理端点')
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 256 * 1024:
                        raise ModelError('model_response_large', '模型响应超过安全上限')
            result = json.loads(raw)
            choice = result['choices'][0]
            message = choice['message']
            text = message.get('content')
            if result.get('model') != model:
                raise ModelError('model_identity_mismatch', '模型响应身份与配置不一致')
            if choice.get('finish_reason') != 'stop' or message.get('tool_calls') or message.get('function_call'):
                raise ModelError('model_incomplete', '模型输出截断或包含不支持的工具请求')
            if not isinstance(text, str) or not text.strip():
                raise ModelError('model_empty', '模型未返回有效内容')
            # Never display internal reasoning, even if accidentally emitted inside content.
            if '<think>' in text or '</think>' in text:
                raise ModelError('model_reasoning_exposed', '模型返回了不适合展示的内部推理，请检查模板配置')
            # Keys never belong in model messages; also prevent accidental echo from
            # a faulty upstream endpoint. Error text must not repeat the secret.
            for secret in (self.settings.gateway_key, self.settings.text_api_key, self.settings.vision_api_key,
                           self.settings.local_text_api_key, self.settings.local_vision_api_key,
                           self.settings.feishu_app_secret, self.settings.feishu_bridge_secret,
                           self.settings.feishu_encrypt_key, self.settings.feishu_verification_token):
                value = secret.get_secret_value()
                if len(value) >= 8 and value in text:
                    raise ModelError('model_secret_exposed', '模型响应包含受保护配置，已阻止展示')
            raw_usage = result.get('usage')
            usage = ({k: raw_usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                      if type(raw_usage.get(k)) is int and raw_usage[k] >= 0}
                     if isinstance(raw_usage, dict) else None)
            usage = usage or None
            return text.strip(), {'model': model, 'finish_reason': 'stop', 'usage': usage,
                                  'elapsed_ms': round((time.monotonic() - start) * 1000)}
        except (httpx.HTTPError, asyncio.TimeoutError):
            raise ModelError('model_unavailable', '模型连接失败或超时，请稍后重试') from None
        except (KeyError, IndexError, TypeError, ValueError):
            raise ModelError('model_response_invalid', '模型响应格式无效') from None

    async def available(self, kind):
        url, model, key = self.config(kind)
        headers = {'Authorization': 'Bearer ' + key.get_secret_value()} if key.get_secret_value() else {}
        try:
            response = await self.http.get(url.rstrip('/') + '/models', headers=headers,
                                           timeout=httpx.Timeout(10, connect=5))
            return response.status_code == 200 and any(x.get('id') == model
                for x in response.json().get('data', []))
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return False


def text_artifact_state(settings):
    if settings.text_mode == 'approved_cloud':
        return 'approved_cloud'
    if settings.text_mode == 'base_preview':
        return 'base_preview'
    if settings.text_mode == 'finetuned_lora' and not settings.text_adapter_path:
        return 'text_adapter_missing'
    if settings.text_mode == 'finetuned_merged' and not settings.text_merged_model_path:
        return 'text_merged_missing'
    if not settings.model_verification_file:
        return 'artifact_verification_required'
    try:
        from .model_inventory import verify_receipt
        verify_receipt(settings)
        return settings.text_mode
    except (ValueError, OSError, KeyError, TypeError):
        return 'artifact_verification_invalid'


def vision_as_text(settings):
    """Reuse artifact verification for the independently trained vision component."""
    return settings.model_copy(update={
        'text_mode': settings.vision_mode, 'text_model_path': settings.vision_model_path,
        'text_adapter_path': settings.vision_adapter_path,
        'text_merged_model_path': settings.vision_merged_model_path,
        'model_verification_file': settings.vision_verification_file,
        'text_model_base_url': settings.vision_model_base_url,
        'text_served_model': settings.vision_served_model, 'text_api_key': settings.vision_api_key})


def vision_artifact_state(settings):
    if settings.vision_mode in ('vision_base', 'approved_cloud'):
        return settings.vision_mode
    return text_artifact_state(vision_as_text(settings))
