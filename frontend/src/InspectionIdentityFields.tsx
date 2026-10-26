import {Alert,Form,Input,InputNumber,Select} from 'antd';
import {RecordSelect} from './DataTable';

export function InspectionIdentityFields(){
 const form=Form.useFormInstance(),line=Form.useWatch('line_id',form);
 return <details open><summary>原始身份、来源覆盖与追加更正</summary><Alert type="info" showIcon message="按实际来源填写身份和覆盖范围。未知关联或仅NG记录不会变成完整生产分母；更正保留旧结果。"/>
 <div className="learning-grid">{Object.entries({source_session_id:'来源会话／设备重启批次',product_id:'实际产品标识',production_batch_id:'生产批次',hanger_id:'挂具',load_id:'挂次',equipment_run_id:'设备运行批次',furnace_run_id:'炉次',rack_position:'挂位',station_id:'实际检验工位'}).map(([name,label])=><Form.Item key={name} name={name} label={label}><Input/></Form.Item>)}</div>
 <Form.Item name="material_batch_ids" label="实际物料批次标识"><Select mode="tags"/></Form.Item>
 <Form.Item name="passage_id" label="关联实际经过"><RecordSelect resource="passages" filters={line?{line_id:line}:{}} labelField="object_id"/></Form.Item>
 <Form.Item name="collection_scope" label="本来源结果覆盖口径"><Select options={[{value:'unknown',label:'覆盖尚未核实'},{value:'all_results',label:'已核实采集全部OK与NG结果'},{value:'ng_only',label:'仅采集NG结果'}]}/></Form.Item>
 <Form.Item name="detection_confidence" label="原始视觉置信度（来源提供时填写）"><InputNumber min={0} max={1} step={0.01}/></Form.Item>
 <Form.Item name="applicable" label="来源适用性"><Select options={[{value:true,label:'适用'},{value:false,label:'不适用，保留原始结果'}]}/></Form.Item>
 <Form.Item name="error" label="原始失败或超范围原因"><Input.TextArea/></Form.Item>
 <Form.Item name="correction_of" label="本次明确追加更正的原检测"><RecordSelect resource="inspections" filters={line?{line_id:line}:{}} labelField="record_id"/></Form.Item>
 <Form.Item name="correction_reason" label="追加更正原因"><Input.TextArea/></Form.Item>
 </details>;
}
