from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_ROOT = Path(__file__).resolve().parents[1]
ROOT = APP_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='ASSISTANT_', extra='ignore', hide_input_in_errors=True)
    data_dir: Path = APP_ROOT / '.data'
    host: str = '127.0.0.1'
    port: int = Field(5001, ge=1024, le=65535)
    public_origin: str = 'http://127.0.0.1:5001'
    secure_cookie: bool = False
    gateway_key: SecretStr = SecretStr('')
    text_model_base_url: str = 'http://127.0.0.1:5101/v1'
    vision_model_base_url: str = 'http://127.0.0.1:5102/v1'
    text_served_model: str = 'cosmetics-qwen35-9b'
    vision_served_model: str = 'cosmetics-qwen3-vl-8b'
    text_api_key: SecretStr = SecretStr('')
    vision_api_key: SecretStr = SecretStr('')
    model_hosts: str = '127.0.0.1,localhost,::1'
    text_mode: str = 'finetuned_lora'
    vision_mode: str = 'vision_base'
    allow_base_preview: bool = False
    text_model_path: Path = ROOT / 'models/base/Qwen3.5-9B'
    vision_model_path: Path = ROOT / 'models/base/Qwen3-VL-8B-Instruct'
    vision_adapter_path: Path | None = None
    vision_merged_model_path: Path | None = None
    vision_verification_file: Path | None = None
    text_adapter_path: Path | None = None
    text_merged_model_path: Path | None = None
    # Operator-generated evidence, not inferred from a served alias.
    model_verification_file: Path | None = None
    model_directory: Path = ROOT / 'models'
    model_profiles_dir: Path = APP_ROOT / 'configs/model_profiles'
    local_text_model_base_url: str = 'http://127.0.0.1:5101/v1'
    local_vision_model_base_url: str = 'http://127.0.0.1:5102/v1'
    local_text_served_model: str = 'cosmetics-qwen35-9b'
    local_vision_served_model: str = 'cosmetics-qwen3-vl-8b'
    local_text_api_key: SecretStr = SecretStr('')
    local_vision_api_key: SecretStr = SecretStr('')
    max_images: int = Field(3, ge=1, le=3)
    image_bytes: int = Field(6 * 1024 * 1024, ge=1024, le=8 * 1024 * 1024)
    image_pixels: int = Field(12_000_000, ge=1024, le=24_000_000)
    storage_bytes: int = Field(512 * 1024 * 1024, ge=1024)
    ttl_seconds: int = Field(86400, ge=60, le=604800)
    queue_limit: int = Field(8, ge=1, le=32)
    body_concurrency: int = Field(8, ge=1, le=32)
    concurrency: int = Field(2, ge=1, le=4)
    request_timeout: float = Field(180, ge=1, le=600)
    model_timeout: float = Field(60, ge=1, le=180)
    max_output_tokens: int = Field(1024, ge=64, le=2048)
    vision_max_output_tokens: int = Field(4096, ge=512, le=8192)
    history_bytes: int = Field(12000, ge=1000, le=24000)
    visitor_budget: int = Field(80, ge=1, le=500)
    daily_model_calls: int = Field(500, ge=1, le=10000)
    feishu_transport: str = 'disabled'
    feishu_app_id: str = ''
    feishu_app_secret: SecretStr = SecretStr('')
    feishu_encrypt_key: SecretStr = SecretStr('')
    feishu_verification_token: SecretStr = SecretStr('')
    feishu_bridge_secret: SecretStr = SecretStr('')
    feishu_bot_open_id: str = ''
    feishu_tenants: str = ''
    feishu_users: str = ''
    feishu_chats: str = ''

    @model_validator(mode='after')
    def boundaries(self):
        self.data_dir = self.data_dir.expanduser().resolve()
        if any(self.data_dir.is_relative_to(ROOT / name) for name in ('var', 'models', 'frontend')):
            raise ValueError('助手数据必须使用独立目录')
        if self.text_mode not in ('finetuned_lora', 'finetuned_merged', 'base_preview', 'approved_cloud'):
            raise ValueError('Invalid text mode')
        if self.text_mode == 'base_preview' and not self.allow_base_preview:
            raise ValueError('base_preview requires explicit allow_base_preview')
        if self.vision_mode not in ('vision_base', 'approved_cloud', 'finetuned_lora', 'finetuned_merged'):
            raise ValueError('Invalid vision mode')
        if self.feishu_transport not in ('disabled', 'ws', 'http'):
            raise ValueError('Invalid Feishu transport')
        for endpoint in (self.text_model_base_url, self.vision_model_base_url,
                         self.local_text_model_base_url, self.local_vision_model_base_url):
            u = urlsplit(endpoint)
            if (u.hostname not in set(self.model_hosts.split(',')) | {'127.0.0.1', 'localhost', '::1'}
                    or u.username or u.password
                    or u.query or u.fragment or u.path.rstrip('/') not in ('/v1', '/compatible-mode/v1')
                    or u.scheme not in ('http', 'https')):
                raise ValueError('模型端点须显式列入允许主机，使用 /v1 且不能包含凭据')
            if u.scheme != 'https' and u.hostname not in ('127.0.0.1', 'localhost', '::1'):
                raise ValueError('跨主机模型连接须 HTTPS 或 SSH 本地转发')
        origin = urlsplit(self.public_origin)
        if origin.path or origin.query or origin.fragment or not origin.hostname:
            raise ValueError('public_origin must be an origin')
        if origin.hostname not in ('127.0.0.1', 'localhost', '::1', 'testserver'):
            if origin.scheme != 'https' or not self.secure_cookie:
                raise ValueError('公网入口须 HTTPS 和 Secure Cookie')
            if len(self.gateway_key.get_secret_value()) < 32:
                raise ValueError('远程入口须配置已认证账号网关及至少32字符的独立网关密钥')
        if self.host not in ('127.0.0.1', 'localhost', '::1') and len(self.gateway_key.get_secret_value()) < 32:
            raise ValueError('非回环监听须配置账号网关，不允许直接公开访客助手')
        if self.gateway_key.get_secret_value() and len(self.gateway_key.get_secret_value()) < 32:
            raise ValueError('网关密钥至少32字符')
        return self

    @property
    def feishu_enabled(self):
        values = (self.feishu_app_id, self.feishu_app_secret.get_secret_value(), self.feishu_tenants,
                  self.feishu_users, self.feishu_bot_open_id)
        transport_key = (self.feishu_bridge_secret.get_secret_value() if self.feishu_transport == 'ws'
                         else self.feishu_encrypt_key.get_secret_value()
                         and self.feishu_verification_token.get_secret_value())
        return self.feishu_transport != 'disabled' and all(values) and bool(transport_key)
