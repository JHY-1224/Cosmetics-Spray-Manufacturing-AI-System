from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, hide_input_in_errors=True)


class MessageInput(Strict):
    text: str = Field('', max_length=6000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=3)
    request_key: str = Field(min_length=8, max_length=120, pattern=r'^[a-zA-Z0-9_-]+$')
    model_id: str | None = Field(None, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')

    @model_validator(mode='after')
    def nonempty(self):
        if not self.text and not self.attachment_ids:
            raise ValueError('请输入文字或图片')
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError('重复图片')
        return self


class ExtractedField(Strict):
    label: str = Field(min_length=1, max_length=160)
    raw_text: str | None = Field(max_length=1000)
    value_text: str | None = Field(max_length=160)
    unit_text: str | None = Field(max_length=80)
    location: str = Field(max_length=200)
    status: Literal['readable', 'uncertain', 'unknown']


class VisionEvidence(Strict):
    image_id: str = Field(min_length=1, max_length=80)
    image_kind: str = Field(min_length=1, max_length=100)
    visible_observations: list[str] = Field(min_length=1, max_length=20)
    extracted_fields: list[ExtractedField] = Field(max_length=30)
    uncertain_items: list[str] = Field(max_length=20)
    missing_context: list[str] = Field(max_length=20)
    warnings: list[str] = Field(max_length=20)

    @model_validator(mode='after')
    def bounded(self):
        if any(not x.strip() or len(x) > 1000 for x in self.visible_observations
               + self.uncertain_items + self.missing_context + self.warnings):
            raise ValueError('视觉字段为空或超长')
        for f in self.extracted_fields:
            if f.status != 'readable' and (f.value_text is not None or f.unit_text is not None):
                raise ValueError('不确定字段的数值和单位必须为空')
        return self


class FeedbackInput(Strict):
    request_id: str
    value: Literal['helpful', 'problem']
