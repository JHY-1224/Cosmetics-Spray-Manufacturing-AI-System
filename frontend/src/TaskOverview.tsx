import {DataTable} from './DataTable';
import {Alert,Button,Card,Empty,Space,Tag} from 'antd';

const labels:Record<string,string>={accepted:'待核对与诊断',needs_evidence:'待补证',awaiting_approval:'待审批',approved:'待现场回执',awaiting_retest:'待复测',inconclusive:'复测证据不足',retest_passed:'待质量结案',closed:'待独立放行',execution_failed:'执行失败待处理',partially_executed:'部分执行待处理',retest_failed:'复测失败待处理'};
export function TaskOverview({report,error,onCase,alerts,onSources}:{report:any;error?:string;onCase:(id:string,snapshotId?:string)=>void;alerts:any[];onSources:()=>void}){
 return <Card title="我的待办" className="section-bottom" extra={<Tag>依据岗位与授权范围</Tag>}>{error?<Alert type="error" message="待办暂不可用" description={error}/>:!report?<Empty description="正在读取待办"/>:<>
 <Space wrap className="section-bottom">{alerts.length>0&&<Button danger onClick={onSources}>采集与运行异常 · {alerts.length}</Button>}{Object.entries(report.counts_by_state??{}).map(([key,value])=><Tag key={key}>{labels[key]??key} · {String(value)}</Tag>)}</Space>
 <DataTable<any> tableId="taskoverview-1" resource="tasks" size="middle" rowKey="case_id"   scroll={{x:700}} locale={{emptyText:'当前范围没有本岗位待处理案件'}} columns={[{title:'待处理案件',dataIndex:'title',render:(v,r)=><Button type="link" onClick={()=>onCase(r.case_id,r.snapshot_id)}>{v}</Button>},{title:'下一步',dataIndex:'status',render:v=><Tag>{labels[v]??v}</Tag>},{title:'生产范围',render:(_,r)=>r.site_id+' / '+r.line_id.slice(0,8)},{title:'版本',dataIndex:'version'},{title:'有效性',render:(_,r)=>r.overdue?<Tag color="warning">方案过期，需重新核验</Tag>:<span>按当前版本办理</span>}]}/>
 <p className="muted">状态计数为授权范围内可办理案件数；作者自审和已放行对象在计数、分页前排除。不会自动审批或现场执行。</p></>}</Card>;
}
