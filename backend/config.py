from pathlib import Path
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='ZHIXING_', env_file=ROOT / '.env', extra='ignore',env_ignore_empty=True)
    mode: Literal['demo','live'] = 'demo'
    data_dir: str = str(ROOT / 'data')
    model_base_url: str = 'https://api.deepseek.com/v1'
    model_name: str = ''
    model_api_key: str = ''
    model_timeout: int = Field(default=60,ge=1,le=600)
    model_daily_calls: int = Field(default=100,ge=1,le=10000)
    model_input_price: float | None = Field(default=None,ge=0,le=1000000000,allow_inf_nan=False)
    model_output_price: float | None = Field(default=None,ge=0,le=1000000000,allow_inf_nan=False)
    jev_mode: Literal['off','shadow'] = 'off'
    jev_api_key: str = ''
    jev_model: str = 'jev-1.13.0'
    jev_timeout: int = Field(default=5,ge=1,le=60)
    jev_daily_calls: int = Field(default=30,ge=1,le=10000)
    jev_input_price: float | None = Field(default=None,ge=0,le=1000000000,allow_inf_nan=False)
    feishu_app_id: str = ''
    feishu_app_secret: str = ''
    feishu_owner: str = ''
    feishu_groups: list[str] = []
    feishu_calendar_id: str = ''
    mail_address: str = ''
    mail_password: str = ''
    notifications: bool = False

settings = Settings()
