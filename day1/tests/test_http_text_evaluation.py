import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('text_http_probe',ROOT/'day1/4-validation/probe_text_http.py')
probe=importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def transport(tmp_path,monkeypatch,respond):
    calls=[]
    def handler(request):
        if request.method=='GET':return httpx.Response(200,json={'data':[{'id':'base'},{'id':'fine'}]})
        calls.append(json.loads(request.content))
        return respond(request)
    original=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    args=SimpleNamespace(base_url='http://127.0.0.1:5111/v1',output=tmp_path,
                         seeds=[3407],sampler_profile='greedy')
    rows=[{'id':'case-1','messages':[{'role':'user','content':'question only'}],
           'expected':'reference must never enter request'}]
    return args,rows,calls


def test_unknown_timeout_is_not_retried_and_reference_is_not_transmitted(tmp_path,monkeypatch):
    def fail(request):raise httpx.ReadTimeout('unknown',request=request)
    args,rows,calls=transport(tmp_path,monkeypatch,fail)
    with pytest.raises(httpx.ReadTimeout):probe.run(args,rows,{'base':'base'})
    assert len(calls)==1 and 'reference' not in json.dumps(calls)
    assert (tmp_path/'case-1-base-3407-request.json').is_file()
    assert not (tmp_path/'case-1-base-3407-http.json').exists()


def test_wrong_served_identity_is_preserved_and_rejected(tmp_path,monkeypatch):
    args,rows,calls=transport(tmp_path,monkeypatch,lambda request:httpx.Response(200,json={
        'model':'base','choices':[{'message':{'content':'answer'},'finish_reason':'stop'}]}))
    with pytest.raises(ValueError,match='identity'):probe.run(args,rows,{'finetuned':'fine'})
    assert len(calls)==1
    assert json.loads((tmp_path/'case-1-finetuned-3407-http.json').read_text())['model']=='base'


def test_length_finish_is_not_claimed_normal_or_observed_eos(tmp_path,monkeypatch):
    args,rows,_=transport(tmp_path,monkeypatch,lambda request:httpx.Response(200,json={
        'model':'base','choices':[{'message':{'content':'partial answer'},'finish_reason':'length'}]}))
    result=probe.run(args,rows,{'base':'base'})
    row=json.loads((tmp_path/'responses.jsonl').read_text())
    assert result['truncated']==1 and result['normal_stops']==0 and row['ended_with_eos'] is None
