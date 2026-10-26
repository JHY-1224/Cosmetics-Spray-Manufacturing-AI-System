from datetime import UTC, datetime
import pytest

from .conftest import post
from .test_workflow import file, plan


@pytest.mark.parametrize('already_released',[False,True])
def test_correction_requires_review_and_preserves_frozen_approvals_and_release(core, already_released):
    _, _, clients, lines=core
    engineer, inspector, approver, executor, quality=[clients[r] for r in ['engineer','inspector','approver','executor','quality']]
    line=lines[0]
    evidence=file(engineer)
    raw={'record_id':'original-ng','line_id':line['id'],'object_id':'SYNTHETIC-OBJECT','granularity':'bottle',
         'association':'manual_confirmed','occurred_at':datetime.now(UTC).isoformat(),'status':'NG',
         'defects':[{'category':'颗粒','count':1}],'inspected_count':1,'defective_count':1,
         'model_version':'manual','config_version':'test-v1','process':line['data']['process']}
    original=post(inspector,'/inspections',raw)
    c=post(engineer,'/cases',{'title':'冻结证据更正审核夹具','line_id':line['id'],'process':line['data']['process'],
                            'defect':'颗粒','inspection_ids':[original['id']],'evidence_ids':[evidence]})
    c=post(engineer,f"/cases/{c['id']}/diagnose",{'version':c['version']})
    saved=plan(engineer,c,samples=1)
    c,p=saved['case'],saved['plan']
    c=post(approver,f"/cases/{c['id']}/approve",{'version':c['version'],'plan_id':p['id'],
                                            'approved':True,'reason':'独立核对当前版本证据'})
    if already_released:
        c=post(executor,f"/cases/{c['id']}/execute",{'version':c['version'],'plan_id':p['id'],
            'executed_at':datetime.now(UTC).isoformat(),'object_ids':['SYNTHETIC-OBJECT'],
            'actual_actions':'完成合成夹具现场核查','deviations':'','evidence_ids':[evidence],'success':True})
        stamp=datetime.now(UTC).isoformat()
        c=post(quality,f"/cases/{c['id']}/retest",{'version':c['version'],'plan_id':p['id'],
            'started_at':stamp,'finished_at':stamp,'sample_count':1,'defective_count':0,'sample_unit':'bottle',
            'conditions_met':True,'other_constraints_met':True,'notes':'合成复核','evidence_ids':[evidence]})
        c=post(quality,f"/cases/{c['id']}/close",{'version':c['version'],'root_cause_confirmation':'unverified',
                 'quality_disposition':'await_release','reason':'合成复测完成，由质量人员归档'})
        c=post(quality,f"/cases/{c['id']}/release",{'version':c['version'],'reason':'独立核对合成质量放行依据'})
    frozen=c['context']
    outcome=c['outcome']
    corrected=post(inspector,'/inspections',{**raw,'record_id':'corrected-ok','status':'OK','defective_count':0,
                  'defects':[],'correction_of':original['id'],'correction_reason':'人工复核原始登记错误并追加'})
    detail=engineer.get('/api/cases/'+c['id']).json()
    assert detail['context']==frozen and detail['outcome']==outcome
    assert detail['context']['inspection_snapshots'][0]['status']=='NG'
    assert detail['review_flags'][0]['correction_id']==corrected['id'] and not detail['review_flags'][0]['resolved']
    assert len(detail['approvals'])==1 and detail['approvals'][0]['approved']
    if already_released:
        assert detail['status']=='closed' and detail['outcome']['released']
        assert len(detail['receipts'])==1 and len(detail['retests'])==1
        detail=post(quality,f"/cases/{c['id']}/reopen",{'version':detail['version'],'reason':'新更正触发质量人员受控重开',
                                                    'evidence_ids':[evidence]})
        assert detail['context']['closed_history'][-1]['outcome']['released']
    else:
        assert detail['status']=='needs_evidence' and detail['plans'][0]['status']=='superseded'
    revised=post(engineer,f"/cases/{c['id']}/evidence",{'version':detail['version'],'inspection_ids':[corrected['id']],
          'reason':'已人工核对更正，重新纳入关键证据并重新审核','purpose':'critical'})
    assert revised['review_flags'][0]['resolved'] and revised['status']=='needs_evidence'
    assert len(revised['context']['inspection_snapshots'])==2
    assert engineer.get('/api/statistics').json()['defective_count']==0
