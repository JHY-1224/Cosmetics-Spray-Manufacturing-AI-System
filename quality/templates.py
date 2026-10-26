from .models import Route, uid

PLASTIC = [
    "来料检验（塑料瓶基材）",
    "除油脱脂清洗",
    "火焰／电晕处理",
    "静电除尘",
    "喷涂底漆",
    "流平",
    "底漆低温烘烤",
    "喷涂面漆（实色／珠光／磨砂）",
    "流平",
    "面漆低温烘烤",
    "喷罩光漆",
    "流平与终烘",
    "冷却",
    "成品全检",
]
GLASS = [
    "来料检验（玻璃瓶基材）",
    "超声波清洗及除油脱脂",
    "脱水烘干",
    "静电除尘",
    "喷涂底漆",
    "流平",
    "底漆烘烤固化",
    "真空机上料",
    "抽高真空",
    "真空蒸镀铝层",
    "破真空下料",
    "喷涂保护面漆",
    "流平",
    "面漆烘烤固化",
    "自然冷却",
    "成品全检",
]

# Candidates are implementation survey prompts, not measured points or production limits.
POINT_CATALOG_VERSION = 'coating-points-v1'
POINT_GROUPS = [
    (('来料',), 'MES/WMS只读视图、检验或受控人工记录', [
        ('product_id', '产品与基材', 'string', '', 'material'),
        ('material_batch_ids', '材料批次与使用时间', 'array', '', 'material')]),
    (('清洗','除油','火焰','电晕','除尘'), '前处理设备步骤与维护/检查记录', [
        ('treatment_record', '处理完成记录及检查依据', 'string', '', 'management'),
        ('maintenance_record', '清洁保养及有效状态', 'string', '', 'equipment')]),
    (('喷','流平'), '喷涂/配料控制器、输送系统及受控配料记录', [
        ('pressure', '喷涂压力，区分设定与实测', 'number', 'Pa', 'equipment'),
        ('flow', '实际可用流量，单位待现场确认', 'number', '', 'equipment'),
        ('mixture_ratio', '配比与混合使用时长，口径待确认', 'string', '', 'material'),
        ('line_speed', '输送速度，单位待现场确认', 'number', '', 'process')]),
    (('烘','固化'), '烘箱温控/输送记录及独立瓶体测温（如安装）', [
        ('air_temperature', '烘箱区段空气温度，不代表瓶体温度', 'number', 'degC', 'process'),
        ('body_temperature', '瓶体温度；未安装测温设备时缺失', 'number', 'degC', 'process'),
        ('residence_time', '实际经过/停留时长', 'number', 's', 'process')]),
    (('上料','下料'), '装卸载记录/MES只读数据/人工确认', [
        ('load_identity', '炉次、运行、挂架、位置与装载批次', 'object', '', 'management')]),
    (('抽高真空','蒸镀'), '真空计/控制器曲线、蒸发电源、材料及膜厚仪（如安装）', [
        ('vacuum_pressure', '实测真空曲线与原始质量状态', 'number', 'Pa', 'equipment'),
        ('step_duration', '实际工步时间', 'number', 's', 'process'),
        ('evaporation_record', '电源、铝材料及报警/维护记录', 'object', '', 'equipment'),
        ('film_thickness', '实测膜厚；无膜厚仪/接口时缺失', 'number', '', 'process'),
        ('deposition_rate', '实测速率；缺少仪器时缺失', 'number', '', 'process')]),
    (('冷却',), '实际安装的温湿度传感器/冷却记录', [
        ('ambient_temperature', '环境温度与校准有效期', 'number', 'degC', 'environment'),
        ('relative_humidity', '环境湿度与校准有效期', 'number', '%RH', 'environment')]),
    (('全检',), '外部视觉、人工检验和实验室；返工/退镀经批准记录', [
        ('inspection_event', '事件/视角/检验轮次、图像和检测版本', 'object', '', 'management'),
        ('quality_disposition', '包装、返工/退镀或报废的批准决定与轮次', 'object', '', 'management')]),
]


def candidate_points(process, step_name):
    identity = ['line_id','product_id','production_batch_id','object_id','step_id']
    if process == 'glass_vacuum':
        identity += ['furnace_run_id','equipment_run_id','hanger_id','rack_position','load_id']
    return [{'name': name, 'meaning': meaning, 'datatype': datatype, 'unit_candidate': unit,
             'candidate_source': source, 'diagnostic_dimension': dimension,
             'identity_fields': identity, 'value_type': 'uncollected',
             'allowed_value_types': ['measured','setpoint','manual','derived'],
             'mapping_version': POINT_CATALOG_VERSION, 'production_limits': None,
             'confirmation': '客户确认来源、单位、质量标识、采样/心跳及批准SOP后才可启用'}
            for keywords, source, points in POINT_GROUPS if any(k in step_name for k in keywords)
            for name, meaning, datatype, unit, dimension in points]


def reference_routes(tenant_id, author_id):
    for process, name, names in [
        ("plastic_spray", "塑料瓶喷涂参考路线", PLASTIC),
        ("glass_vacuum", "玻璃瓶真空蒸镀铝层参考路线", GLASS),
    ]:
        yield Route(
            tenant_id=tenant_id,
            site_id="",
            name=name,
            process=process,
            status="reference",
            author_id=author_id,
            steps=[
                {
                    "id": uid(),
                    "name": n,
                    "optional": process == "plastic_spray" and i == 10,
                    "enabled": not (process == "plastic_spray" and i == 10),
                    "quality_gate": i in (0, len(names) - 1),
                }
                for i, n in enumerate(names)
            ],
            disposition={
                "ok": "pack",
                "ng": "rework" if process == "plastic_spray" else "strip",
                "approved": False,
                "return_step_id": None,
                "max_cycles": 0,
            },
        )


def effective_steps(steps):
    return [s for s in steps if not s.get("optional") or s.get("enabled")]


def validate_route(steps, disposition):
    from .security import fail

    ids = [s.get("id") for s in steps]
    if len(set(ids)) != len(ids) or not all(ids) or any(not s.get("name") for s in steps):
        fail(422, "route_steps", "每个工序实例必须有独立 ID 与名称")
    if len(effective_steps(steps)) < 2:
        fail(422, "route_empty", "有效路线至少两个工序")
    if disposition.get("approved") and (
        disposition.get("return_step_id") not in ids or not 1 <= disposition.get("max_cycles", 0) <= 10
    ):
        fail(422, "disposition_invalid", "返工/退镀需要有效返回工序与次数上限")
