"""FastAPI + in-process asynchronous vLLM, following the FFmpeg service layout.

Generic text chat only. Does not operate devices, send notifications, or replace
the existing quality application's model connection automatically.
"""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys
import time
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from day1.pipeline import native_tokenizer

MODEL_NAME = os.environ.get('COSMETICS_SERVED_MODEL_NAME', 'cosmetics-qwen35-9b')


class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['system', 'user', 'assistant']
    content: str = Field(min_length=1, max_length=100000)


class ResponseFormat(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['text', 'json_object'] = 'text'


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str = MODEL_NAME
    messages: list[Message] = Field(min_length=1, max_length=32)
    max_tokens: int = Field(default=1024, ge=1, le=4096)
    temperature: float = Field(default=0, ge=0, le=2)
    stream: Literal[False] = False
    response_format: ResponseFormat | None = None

    @model_validator(mode='after')
    def validate_turns(self):
        roles = [message.role for message in self.messages]
        if roles and roles[0] == 'system':
            roles = roles[1:]
        if not roles or roles[-1] != 'user' or any(
                role != ('user' if i % 2 == 0 else 'assistant')
                for i, role in enumerate(roles)):
            raise ValueError('Expected optional system then user/assistant history ending with user')
        return self


@asynccontextmanager
async def lifespan(app):
    configured = os.environ.get('COSMETICS_MODEL_DIR')
    if not configured:
        raise RuntimeError('Set COSMETICS_MODEL_DIR to an explicitly selected base or merged model')
    model = Path(configured).expanduser().resolve()
    index = model / 'model.safetensors.index.json'
    if index.exists():
        shards = set(json.loads(index.read_text())['weight_map'].values())
        if not all((model / x).is_file() for x in shards):
            raise RuntimeError('Model shards are incomplete')
    elif not (model / 'model.safetensors').is_file():
        raise RuntimeError('Expected full model weights; an adapter alone cannot be served here')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    from vllm.engine.arg_utils import AsyncEngineArgs
    from vllm.engine.async_llm_engine import AsyncLLMEngine
    app.state.tokenizer = native_tokenizer(model)
    app.state.context_length = int(os.environ.get('COSMETICS_MAX_MODEL_LEN', '8192'))
    app.state.engine = AsyncLLMEngine.from_engine_args(AsyncEngineArgs(
        model=str(model), max_model_len=app.state.context_length,
        tensor_parallel_size=int(os.environ.get('COSMETICS_TENSOR_PARALLEL_SIZE', '1')),
        gpu_memory_utilization=0.9, trust_remote_code=False, language_model_only=True))
    app.state.slots = asyncio.Semaphore(2)
    try:
        yield
    finally:
        shutdown = getattr(app.state.engine, 'shutdown_background_loop', None)
        if shutdown:
            shutdown()


app = FastAPI(title='化妆品专业模型试验服务', lifespan=lifespan)


@app.get('/health')
def health():
    return {'status': 'ready', 'model': MODEL_NAME}


@app.get('/v1/models')
def models():
    return {'object': 'list', 'data': [{'id': MODEL_NAME, 'object': 'model', 'owned_by': 'local'}]}


@app.post('/v1/chat/completions')
async def chat(request: ChatRequest):
    if request.model != MODEL_NAME:
        raise HTTPException(404, 'Unknown served model')
    messages = [x.model_dump() for x in request.messages]
    try:
        prompt = app.state.tokenizer.apply_chat_template(
            messages, tokenize=True, return_dict=False, add_generation_prompt=True,
            enable_thinking=False, preserve_thinking=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if len(prompt) + request.max_tokens > app.state.context_length:
        raise HTTPException(422, 'Prompt and output limit exceed model context')
    from vllm import SamplingParams
    from vllm.sampling_params import StructuredOutputsParams
    json_mode = request.response_format and request.response_format.type == 'json_object'
    params = SamplingParams(
        temperature=request.temperature, max_tokens=request.max_tokens,
        structured_outputs=StructuredOutputsParams(json_object=True) if json_mode else None)
    request_id = 'chatcmpl-' + uuid4().hex
    async with app.state.slots:
        final = None
        try:
            async for output in app.state.engine.generate({'prompt_token_ids': prompt}, params, request_id):
                final = output
        except asyncio.CancelledError:
            await app.state.engine.abort(request_id)
            raise
    if final is None or not final.outputs:
        raise HTTPException(503, 'No model output')
    answer = final.outputs[0]
    completion = len(answer.token_ids)
    return {'id': request_id, 'object': 'chat.completion', 'created': int(time.time()),
            'model': MODEL_NAME,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': answer.text},
                         'finish_reason': answer.finish_reason}],
            'usage': {'prompt_tokens': len(prompt), 'completion_tokens': completion,
                      'total_tokens': len(prompt) + completion}}
