# 源码定义与入口索引

由本次公开副本AST生成。详细业务约束见AGENTS.md；索引不替代函数实现。

## assistant_app/backend/app.py

- L28: `RequestGuard`
- L29: `__init__`
- L33: `__call__`
- L41: `secured_send`
- L51: `reject`
- L94: `bounded_receive`
- L104: `public_job`
- L112: `create_app`
- L116: `lifespan`
- L134: `maintenance`
- L164: `validation_error`
- L168: `unexpected_error`
- L173: `owner`
- L176: `status`
- L200: `live`
- L204: `ready`
- L209: `get_status`
- L213: `model_choices`
- L219: `session`
- L223: `conversations`
- L228: `new_conversation`
- L234: `conversation`
- L241: `clear`
- L250: `upload`
- L272: `image`
- L280: `delete_image`
- L291: `message`
- L302: `job`
- L306: `cancel`
- L312: `events`
- L320: `stream`
- L342: `feedback`
- L351: `feishu`
- L359: `no_frontend`

## assistant_app/backend/channels/__init__.py

- L4: `ChannelAdapter`
- L5: `receive`
- L6: `tick`
- L7: `close`

## assistant_app/backend/channels/feishu.py

- L19: `allowlist`
- L23: `FeishuChannel`
- L24: `__init__`
- L29: `close`
- L32: `eligible`
- L54: `receive`
- L124: `token`
- L136: `image`
- L148: `content`
- L173: `tick`

## assistant_app/backend/image_processing.py

- L11: `normalize_image`
- L37: `save_image`
- L44: `_save_image`

## assistant_app/backend/model_catalog.py

- L18: `read_json`
- L24: `ProfileFile`
- L45: `Route`
- L51: `ModelCatalog`
- L52: `__init__`
- L60: `category`
- L64: `entry`
- L78: `local_settings`
- L107: `paths`
- L113: `children`
- L126: `entries`
- L182: `resolve`
- L200: `artifact`
- L213: `listing`
- L218: `inspect`
- L233: `close`

## assistant_app/backend/model_clients.py

- L8: `ModelError`
- L9: `__init__`
- L14: `ModelClients`
- L15: `__init__`
- L21: `config`
- L26: `close`
- L29: `call`
- L88: `available`
- L100: `text_artifact_state`
- L119: `vision_as_text`
- L130: `vision_artifact_state`

## assistant_app/backend/model_inventory.py

- L7: `digest`
- L15: `inventory`
- L48: `adapter_inventory`
- L80: `verify_receipt`

## assistant_app/backend/orchestration.py

- L15: `Jobs`
- L16: `__init__`
- L22: `start`
- L27: `phase`
- L31: `authorize`
- L39: `run`
- L128: `history`
- L149: `cancel`
- L158: `close`

## assistant_app/backend/prompts.py

- L85: `injection_signals`
- L87: `strings`
- L102: `policy_metadata`

## assistant_app/backend/quantity_review.py

- L26: `material_label`
- L32: `quantity_notices`

## assistant_app/backend/schemas.py

- L5: `Strict`
- L9: `MessageInput`
- L16: `nonempty`
- L24: `ExtractedField`
- L33: `VisionEvidence`
- L43: `bounded`
- L53: `FeedbackInput`

## assistant_app/backend/security.py

- L14: `gateway_identity`
- L27: `Security`
- L28: `__init__`
- L38: `digest`
- L41: `csrf`
- L44: `rate`
- L56: `origin`
- L66: `owner`
- L85: `session`

## assistant_app/backend/sessions.py

- L16: `fail`
- L20: `uid`
- L24: `Store`
- L25: `__init__`
- L58: `tx`
- L73: `one`
- L78: `all`
- L82: `execute`
- L86: `own`
- L93: `conversation`
- L102: `submit`
- L146: `event`
- L151: `clear`
- L163: `cleanup`

## assistant_app/backend/settings.py

- L11: `Settings`
- L73: `boundaries`
- L110: `feishu_enabled`

## assistant_app/scripts/check_models.py

- L15: `main`
- L58: `probe`

## assistant_app/scripts/configure_qwen_light.py

- L13: `main`

## assistant_app/scripts/launch_model.py

- L17: `main`

## assistant_app/scripts/launch_qwen_light.py

- L15: `main`
- L29: `stop`

## assistant_app/scripts/preflight.py

- L16: `main`

## assistant_app/scripts/run_assistant.py

- L15: `main`

## assistant_app/scripts/run_feishu_ws.py

- L14: `main`
- L29: `on_message`

## assistant_app/scripts/status_assistant.py

- L10: `owned_process`
- L27: `settings_from_args`

## assistant_app/scripts/verify_prompt_policy.py

- L27: `fixture`
- L44: `verify`
- L84: `main`

## day1/0-download-model/1-download.py

- L14: `main`
- L27: `validate_manifest`
- L44: `download_model`

## day1/2-prepare-data/1-prepare_dataset.py

- L11: `main`

## day1/2-prepare-data/2-tokenizer_preflight.py

- L13: `main`

## day1/3-train/1-train.py

- L18: `main`

## day1/4-validation/2-chat.py

- L9: `build_body`
- L22: `main`

## day1/4-validation/3-test-adapter.py

- L20: `read_json`
- L24: `file_digest`
- L32: `load_cases`
- L48: `inspect_adapter`
- L81: `infer`
- L149: `main`

## day1/5-evaluation/build_cases.py

- L18: `add`
- L35: `numeric`
- L55: `evidence`
- L76: `safety`
- L99: `workflow`
- L123: `regression`
- L147: `write_frozen`
- L153: `main`

## day1/multimodal/dataset.py

- L7: `load_records`
- L52: `conversation`

## day1/multimodal/prepare_images.py

- L38: `main`
- L91: `meta`

## day1/multimodal/train_vl.py

- L21: `main`
- L80: `VisionRows`
- L81: `__len__`
- L82: `__getitem__`

## day1/multimodal/verify_vl.py

- L12: `main`

## day1/pipeline.py

- L11: `native_tokenizer`
- L25: `sha256`
- L29: `read_messages`
- L53: `verified_rows`
- L64: `tokenize_example`
- L71: `render`
- L95: `_tokenize_full_template`
- L106: `render`
- L147: `AssistantCollator`
- L148: `__init__`
- L151: `__call__`

## day2/5-service/service-v2.py

- L26: `Message`
- L32: `ResponseFormat`
- L37: `ChatRequest`
- L47: `validate_turns`
- L59: `lifespan`
- L94: `health`
- L99: `models`
- L104: `chat`

## migrations/versions/0001_initial.py

- L16: `upgrade`
- L52: `downgrade`

## migrations/versions/0002_sync_ack.py

- L12: `upgrade`
- L20: `downgrade`

## migrations/versions/0003_knowledge_and_lab.py

- L13: `upgrade`
- L31: `downgrade`

## migrations/versions/0004_line_scope.py

- L13: `upgrade`
- L50: `downgrade`

## migrations/versions/0005_object_replication.py

- L12: `upgrade`
- L21: `downgrade`

## migrations/versions/0006_knowledge_scopes.py

- L12: `upgrade`
- L32: `downgrade`

## migrations/versions/0007_dispositions.py

- L13: `upgrade`
- L28: `downgrade`

## migrations/versions/0008_identity_leases.py

- L12: `upgrade`
- L25: `downgrade`

## migrations/versions/0009_distribution.py

- L13: `upgrade`
- L28: `downgrade`

## migrations/versions/0010_delivery_integrity.py

- L13: `upgrade`
- L17: `redact`
- L51: `downgrade`

## migrations/versions/0011_expert_learning.py

- L13: `upgrade`
- L40: `downgrade`

## migrations/versions/0012_enterprise_coordination.py

- L29: `upgrade`
- L79: `downgrade`

## quality/actual_passages.py

- L18: `normalize`
- L136: `capture`
- L179: `recover_envelope`

## quality/adapters.py

- L19: `AdapterConfig`
- L47: `secret`
- L54: `mapped`
- L66: `normalize`
- L108: `http_pull`
- L134: `mqtt_read`
- L148: `pending`
- L177: `on_connect`
- L184: `on_message`
- L231: `opc_read`
- L273: `read_adapter`

## quality/agent.py

- L23: `ParameterWindow`
- L29: `finite`
- L35: `RuleInput`
- L50: `State`
- L62: `checkpointer`
- L77: `validate_rules`
- L113: `score_candidates`
- L117: `analyze`
- L124: `_analyze`
- L220: `evidence_check`
- L232: `rules_node`
- L239: `model_node`
- L400: `review_node`
- L502: `resume_review`
- L505: `review`

## quality/anomalies.py

- L11: `AnomalyPolicy`
- L23: `trigger`

## quality/app.py

- L36: `create_app`
- L45: `lifespan`
- L58: `maintenance_busy`
- L62: `request_context`
- L117: `duplicate`
- L124: `concurrent`
- L131: `invalid`
- L143: `actor`
- L173: `service`
- L179: `command`
- L198: `site_scope`
- L203: `health`
- L227: `live`
- L231: `metrics`
- L236: `login`
- L304: `logout`
- L318: `me`
- L339: `users`
- L348: `create_user`
- L351: `apply`
- L372: `revoke`
- L375: `apply`
- L388: `masters`
- L397: `master`
- L400: `apply`
- L406: `routes`
- L413: `route`
- L416: `apply`
- L432: `route_review`
- L433: `apply`
- L476: `bind`
- L479: `apply`
- L491: `upload`
- L496: `apply`
- L524: `download`
- L535: `files`
- L546: `manual_inspection`
- L556: `visual`
- L558: `accept`
- L583: `apply`
- L603: `visual_image`
- L636: `apply`
- L663: `inspections`
- L672: `passage`
- L675: `apply`
- L707: `trace`
- L731: `lab_result`
- L734: `apply`
- L740: `lab_results`
- L749: `cases`
- L763: `create_case`
- L767: `case_detail`
- L796: `diagnose`
- L799: `apply`
- L811: `add_evidence`
- L814: `apply`
- L867: `plan`
- L871: `save_draft`
- L875: `submit_draft`
- L879: `approve`
- L880: `apply`
- L895: `execute`
- L899: `retest`
- L903: `close`
- L907: `cancel`
- L910: `apply`
- L921: `release`
- L924: `apply`
- L948: `reopen`
- L950: `apply`
- L968: `command_result`
- L975: `knowledge`
- L983: `selected`
- L991: `knowledge_search`
- L1001: `index_knowledge`
- L1004: `apply`
- L1032: `knowledge_create`
- L1035: `apply`
- L1073: `knowledge_parse`
- L1076: `apply`
- L1088: `knowledge_review`
- L1089: `apply`
- L1153: `diff`
- L1167: `adapters`
- L1176: `adapter_create`
- L1179: `apply`
- L1222: `adapter_poll`
- L1225: `apply`
- L1233: `adapter_schemas`
- L1240: `import_preview`
- L1266: `import_commit`
- L1271: `apply`
- L1279: `statistics`
- L1298: `operations`
- L1302: `status_file`
- L1341: `traces`
- L1346: `replay`
- L1349: `apply`

## quality/backup.py

- L17: `pg_tool`
- L26: `pg_env`
- L41: `backup`
- L48: `_backup`
- L133: `verify`
- L143: `restore`
- L150: `_restore`

## quality/business_gateway.py

- L24: `safe`
- L28: `credential`
- L37: `connector`
- L71: `intent_key`
- L88: `intent_hash`
- L98: `prepare_payload`
- L279: `create`
- L363: `runtime`
- L374: `unfreeze`
- L409: `control`
- L476: `frozen`
- L486: `epoch`
- L498: `connection_for`
- L516: `claim`
- L597: `receive`
- L700: `run_once`
- L786: `serve`

## quality/business_protocol.py

- L17: `SourceIdentity`
- L28: `BusinessRequest`
- L42: `BusinessReceiptBody`
- L67: `SignedBusinessReceipt`

## quality/business_reference.py

- L24: `ReferenceStore`
- L25: `__init__`
- L60: `transaction`
- L68: `stock_key`
- L71: `receipt`
- L104: `apply`
- L196: `handle`
- L267: `server`
- L268: `Handler`
- L269: `log_message`
- L272: `do_POST`
- L295: `main`

## quality/cli.py

- L18: `migrate`
- L25: `_migrate`
- L48: `initialize`
- L75: `seed_demo`
- L128: `main`

## quality/config.py

- L8: `Settings`
- L82: `__init__`
- L124: `validate_mode`
- L206: `sites`

## quality/costs.py

- L15: `document`
- L90: `reconcile`

## quality/db.py

- L9: `Database`
- L10: `__init__`
- L32: `sqlite_constraints`
- L37: `session`
- L68: `check_runtime_role`
- L87: `create_schema`

## quality/diagnostics.py

- L15: `timestamp`
- L19: `MetricCondition`
- L32: `bounds`
- L45: `evaluate_condition`
- L62: `usable`
- L63: `numeric`
- L113: `rank_candidates`
- L187: `evidence_features`

## quality/digital_tasks.py

- L41: `can_read`
- L52: `validate_policy`
- L69: `create_policy`
- L77: `offline`
- L93: `prepare`
- L218: `decision`
- L358: `enqueue`
- L415: `safe`
- L421: `fingerprint`
- L435: `approve`
- L467: `actor_for`
- L483: `revalidate`
- L523: `control`
- L570: `claim`
- L614: `run_claimed`
- L672: `mark_failed`
- L686: `run_once`
- L703: `serve`

## quality/dispositions.py

- L10: `request_disposition`
- L37: `review_disposition`
- L85: `validate_reentry`
- L120: `register_routes`
- L125: `dispositions`
- L134: `disposition`
- L138: `review`

## quality/distribution.py

- L24: `RolloutInput`
- L34: `DecisionInput`
- L39: `content_hash`
- L44: `check_scope`
- L59: `build_package`
- L86: `preview`
- L156: `stage`
- L184: `receive`
- L209: `outbound`
- L222: `acknowledge`
- L251: `apply_record`
- L303: `register_distribution`
- L305: `list_distributions`
- L325: `create_rollout`
- L330: `apply`
- L404: `import_preview`
- L408: `import_package`
- L414: `row_preview`
- L419: `decide`
- L423: `apply`

## quality/enterprise.py

- L20: `json_value`
- L32: `lock`
- L41: `line_scope`
- L51: `evidence`
- L65: `fits`
- L76: `effective`
- L94: `governed_values`
- L128: `definition`
- L138: `validate_definition`
- L181: `review`
- L293: `detail`

## quality/enterprise_api.py

- L60: `register_enterprise`
- L67: `dependency_readiness`
- L75: `business_readiness`
- L83: `status`
- L102: `create_definition`
- L106: `review_definition`
- L112: `assess`
- L118: `assess_plan`
- L124: `cohort`
- L130: `process_decision`
- L136: `actual_passage`
- L142: `actual_passage_import`
- L146: `apply`
- L164: `material`
- L170: `inventory`
- L176: `formulation`
- L182: `calculate`
- L188: `mix`
- L194: `reserve`
- L200: `bom`
- L206: `order`
- L212: `order_revision`
- L218: `supply_plan`
- L224: `usage`
- L230: `document`
- L236: `cost_report`
- L242: `policy`
- L248: `task`
- L254: `batch`
- L258: `apply`
- L286: `review_task`
- L292: `control_task`
- L298: `connector`
- L304: `revise_connector`
- L312: `business_command`
- L318: `business_control`
- L324: `business_runtime`
- L330: `reservation_control`
- L336: `business_recovery`
- L342: `business_callback`
- L378: `tools`
- L386: `invoke_tool`
- L392: `record`

## quality/enterprise_models.py

- L22: `Governed`
- L37: `DecisionDefinition`
- L46: `QualityAssessment`
- L72: `ProcessDecision`
- L85: `ProductionCohort`
- L99: `MaterialItem`
- L110: `MaterialFormulation`
- L123: `FormulationComponent`
- L133: `FormulationCalculation`
- L146: `MixingBatch`
- L158: `AutomationPolicy`
- L177: `PolicyDecision`
- L190: `DigitalTask`
- L212: `DomainApproval`
- L224: `BusinessConnector`
- L239: `BusinessCommand`
- L266: `BusinessCommandAttempt`
- L277: `BusinessReceipt`
- L288: `BillOfMaterials`
- L298: `BomComponent`
- L314: `BusinessOrder`
- L327: `BusinessOrderLine`
- L340: `InventorySnapshot`
- L370: `SupplyPlan`
- L380: `MaterialDemand`
- L392: `InventoryReservation`
- L404: `ActualMaterialUsage`
- L421: `BusinessDocument`
- L436: `BusinessDocumentLine`
- L456: `CostReport`
- L469: `ToolInvocation`

## quality/enterprise_schemas.py

- L12: `exact`
- L26: `zoned`
- L51: `IndustrialScope`
- L56: `GovernedInput`
- L66: `validity`
- L72: `MethodSpec`
- L85: `QualificationSpec`
- L93: `StandardSpec`
- L109: `bounds`
- L119: `QualityPlanSpec`
- L130: `acceptance`
- L138: `ProcessModeSpec`
- L158: `DefinitionInput`
- L162: `GovernanceAction`
- L167: `QualityObservation`
- L202: `observation_times`
- L214: `PlanAssessmentInput`
- L222: `ProcessDecisionInput`
- L238: `ActualPassageInput`
- L259: `ActualPassageBatch`
- L263: `CohortInput`
- L281: `MaterialItemInput`
- L291: `ComponentInput`
- L298: `FormulationInput`
- L310: `components_valid`
- L320: `CalculationInput`
- L335: `ActualWeighing`
- L344: `MixingInput`
- L354: `PolicyScope`
- L358: `PolicyInput`
- L378: `DigitalTaskInput`
- L392: `DigitalBatchInput`
- L397: `TaskAction`
- L402: `ConnectorInput`
- L416: `BusinessActionPayload`
- L433: `action_contract`
- L447: `BusinessCommandInput`
- L464: `ApprovalInput`
- L470: `CommandAction`
- L475: `ConnectorRevision`
- L479: `BusinessRecoveryInput`
- L485: `BomLineInput`
- L496: `blank_reference`
- L500: `one_target`
- L506: `BomInput`
- L515: `OrderLineInput`
- L524: `OrderInput`
- L537: `OrderRevisionInput`
- L541: `InventoryInput`
- L565: `quantities`
- L579: `SupplyPlanInput`
- L590: `ReservationInput`
- L596: `UsageInput`
- L610: `DocumentLineInput`
- L621: `DocumentInput`
- L635: `CostInput`

## quality/evidence_tasks.py

- L12: `TaskDecision`
- L19: `register_evidence_tasks`
- L21: `decide`
- L24: `apply`

## quality/exports.py

- L15: `register_exports`
- L17: `export`
- L36: `safe`
- L43: `content`

## quality/formulations.py

- L12: `components`
- L20: `validate_formulation`
- L42: `create`
- L64: `calculate`
- L221: `mix`

## quality/http_control.py

- L14: `address`
- L19: `resolved`
- L30: `PinnedBackend`
- L31: `__init__`
- L35: `connect_tcp`
- L38: `checked_rows`
- L49: `check_peer`
- L54: `connect_socket`
- L80: `client`

## quality/identity.py

- L10: `local_lease`
- L18: `renew_local_lease`
- L61: `validate_identity`
- L72: `authorization_snapshot`
- L104: `apply_authorizations`

## quality/imports.py

- L13: `ReceiptRow`
- L20: `register_imports`
- L22: `preview`
- L64: `commit`
- L68: `apply`

## quality/industrial_distribution.py

- L29: `state`
- L38: `references`
- L70: `extension`
- L86: `validate_package`
- L144: `dependency_errors`
- L156: `values`
- L167: `validate_local`
- L196: `install`

## quality/industrial_tools.py

- L14: `RecordInput`
- L19: `KnowledgeInput`
- L25: `InvokeInput`
- L30: `InvokeResult`
- L40: `record`
- L51: `knowledge`
- L63: `resolve`
- L132: `registry`
- L158: `invoke`
- L221: `record_execution`

## quality/ingestion.py

- L10: `error_code`
- L21: `committed_marker`
- L27: `stage_raw`
- L88: `recover_raw`
- L129: `apply_raw`
- L223: `collect_adapter`

## quality/learning.py

- L18: `get_record`
- L27: `touch`
- L36: `scope_check`
- L57: `applicability`
- L69: `case_groups`
- L128: `file_groups`
- L138: `provenance_guard`
- L154: `site_policy`
- L169: `held_groups`
- L185: `require_not_holdout`
- L190: `reject_heldout_sources`
- L212: `source_used_for_learning`
- L215: `used`
- L268: `definition_rows`
- L290: `condition_snapshot`
- L341: `condition_measurements`
- L364: `freeze_case`
- L380: `freeze_experience`
- L389: `check_references`
- L412: `experience_validation`
- L449: `validate_rule`
- L511: `conflicts`
- L563: `ensure_capacity`
- L601: `enqueue`
- L620: `ancestors`
- L635: `root_id`
- L640: `deactivate_record`
- L648: `new_record`
- L680: `validate_handoff`
- L688: `ready_for_publication`
- L730: `review`
- L793: `create_dataset`
- L931: `record_conditions`
- L1008: `matches`
- L1077: `public_task`
- L1081: `register_learning`
- L1083: `status`
- L1097: `detail`
- L1109: `case_conditions`
- L1113: `add_conditions`
- L1122: `handoff`
- L1125: `apply`
- L1134: `condition`
- L1137: `apply`
- L1146: `experience`
- L1149: `apply`
- L1170: `material`
- L1173: `apply`
- L1192: `check_material`
- L1195: `apply`
- L1215: `dataset`
- L1219: `rule`
- L1222: `apply`
- L1241: `review_record`
- L1242: `apply`
- L1273: `revision`
- L1276: `apply`
- L1308: `save`
- L1312: `apply`
- L1363: `replay`
- L1366: `apply`
- L1396: `task_detail`
- L1402: `task_action`
- L1403: `apply`

## quality/learning_schemas.py

- L14: `Applicability`
- L25: `HandoffStage`
- L40: `proof`
- L48: `HandoffInput`
- L55: `distinct`
- L61: `ConditionInput`
- L81: `limits`
- L93: `ConditionValue`
- L107: `times`
- L119: `ConditionBatch`
- L123: `Opinion`
- L136: `ExpertLabel`
- L148: `time`
- L158: `ExperienceInput`
- L174: `MaterialInput`
- L180: `MaterialCheck`
- L188: `ranges`
- L194: `LearningDecision`
- L201: `RevisionInput`
- L205: `DatasetItem`
- L215: `times`
- L221: `DatasetInput`
- L231: `RuleProposal`
- L242: `ReplayInput`
- L246: `TaskAction`
- L251: `DraftSave`

## quality/learning_tasks.py

- L25: `task_actor`
- L48: `extract_material`
- L98: `replay_rule`
- L115: `candidate`
- L128: `fits`
- L173: `counts`
- L208: `activate`
- L356: `run_claimed`
- L421: `claim`
- L474: `mark_failed`
- L496: `launch_claimed`
- L537: `run_once`
- L544: `serve`

## quality/maintenance.py

- L15: `MaintenanceBusy`
- L24: `_Guard`
- L25: `__init__`
- L32: `guard`
- L40: `gate`

## quality/material_inventory.py

- L20: `quantity`
- L35: `material`
- L62: `inventory`
- L123: `reconciled_usage_ids`
- L128: `available`
- L219: `reserve`
- L263: `cancel_reservation`

## quality/model_assistance.py

- L11: `assistance`

## quality/model_base.py

- L9: `uid`
- L13: `now`
- L17: `Base`
- L21: `Scoped`

## quality/model_journal.py

- L12: `analysis_slot`
- L28: `model_slot`
- L52: `locked_case`
- L61: `write`
- L76: `reserve`
- L107: `finish`

## quality/models.py

- L9: `User`
- L25: `Session`
- L33: `Master`
- L44: `Evidence`
- L56: `Inspection`
- L71: `Passage`
- L84: `Measurement`
- L99: `Case`
- L118: `Plan`
- L131: `Approval`
- L141: `Receipt`
- L150: `Retest`
- L159: `Knowledge`
- L175: `KnowledgeVector`
- L183: `LabResult`
- L196: `Route`
- L209: `Adapter`
- L220: `Run`
- L227: `Audit`
- L236: `Command`
- L246: `Outbox`
- L258: `Inbox`
- L267: `Site`
- L279: `Snapshot`
- L288: `RemoteRequest`
- L299: `Disposition`
- L316: `Distribution`
- L331: `RawRecord`
- L347: `LearningRecord`
- L368: `LearningTask`
- L384: `StudyGroup`

## quality/observability.py

- L13: `RecentSpans`
- L14: `__init__`
- L17: `export`
- L30: `setup`

## quality/ontology.py

- L5: `normalize_terms`

## quality/operations.py

- L21: `PolicyUpdate`
- L26: `industrial_metrics`
- L52: `alerts`
- L55: `add`
- L66: `query`
- L140: `archive`
- L192: `register_operations`
- L194: `get_alerts`
- L199: `update_policy`
- L202: `apply`

## quality/process_decisions.py

- L12: `sop_basis`
- L35: `select_mode`

## quality/quality_decisions.py

- L12: `source_observation`
- L18: `_source_observation`
- L135: `assess`
- L141: `_assess`
- L278: `inside`
- L368: `current_assessment`
- L407: `assess_plan`
- L520: `cohort`

## quality/querying.py

- L12: `QueryScope`
- L21: `window`
- L29: `get_scope`
- L37: `readonly_line_scope`
- L50: `apply_scope`
- L90: `page_result`

## quality/readiness.py

- L14: `dependencies`
- L100: `business`

## quality/recipes.py

- L10: `RecipeInput`
- L20: `validate_recipe`
- L55: `register_recipes`
- L57: `listing`
- L66: `create`
- L69: `apply`
- L103: `review`
- L104: `apply`
- L167: `bind`
- L170: `apply`

## quality/records.py

- L12: `json_members`
- L35: `knowledge_scope`
- L86: `task_collection`
- L233: `collection`
- L412: `register_records`
- L414: `records`

## quality/regression.py

- L11: `register_regression`
- L13: `evaluate`
- L23: `apply`

## quality/retrieval.py

- L11: `embed`
- L31: `search`

## quality/schemas.py

- L7: `Strict`
- L11: `Login`
- L16: `UserInput`
- L24: `MasterInput`
- L48: `Defect`
- L58: `coordinates`
- L73: `InspectionInput`
- L112: `time_zone`
- L118: `counts`
- L138: `CaseInput`
- L155: `VersionInput`
- L159: `RetestPlan`
- L171: `Action`
- L181: `PlanInput`
- L188: `ApprovalInput`
- L194: `ReceiptInput`
- L208: `ActionResult`
- L215: `RetestInput`
- L228: `CloseInput`
- L234: `EvidenceUpdate`
- L241: `LabInput`
- L254: `KnowledgeInput`
- L267: `scope_schema`
- L279: `ReviewInput`
- L284: `RouteInput`
- L292: `PassageInput`
- L304: `Candidate`
- L317: `ModelOutput`
- L323: `DispositionInput`
- L333: `DispositionReview`
- L338: `ReleaseInput`
- L344: `ReopenInput`
- L349: `DraftPlanInput`

## quality/scopes.py

- L1: `applicable`
- L19: `knowledge_write_scope`

## quality/sdk_control.py

- L12: `pins`
- L20: `controlled_pools`
- L24: `connect`
- L34: `pin_s3`
- L41: `pin_mqtt`
- L44: `connect`
- L52: `pin_opc`
- L57: `connect`
- L86: `telemetry_session`
- L93: `DirectSession`
- L94: `request`

## quality/security.py

- L58: `digest`
- L68: `aware`
- L72: `fail`
- L76: `check_password`
- L86: `Actor`
- L93: `require`
- L102: `token`
- L106: `sign`
- L114: `endpoint`
- L136: `current_time`

## quality/service.py

- L10: `serialize`
- L24: `critical_context_hash`
- L28: `command_response`
- L44: `Service`
- L45: `__init__`
- L48: `command_authorization`
- L60: `cached_command_result`
- L69: `can_read_command_result`
- L72: `scoped`
- L80: `ids_filter`
- L106: `get`
- L115: `add`
- L170: `audit`
- L212: `event`
- L223: `evidence`
- L236: `version`
- L243: `advance`
- L291: `create_master`
- L314: `record_lab`
- L336: `create_case`
- L408: `valid_actions`
- L452: `require_reviewed_corrections`
- L460: `applicable_knowledge`
- L473: `plan_assistance`
- L485: `save_plan`
- L528: `active_plan`
- L565: `approve`
- L585: `execute`
- L656: `save_draft`
- L692: `identified_actions`
- L713: `submit_draft`
- L756: `retest`
- L810: `close`

## quality/sources.py

- L17: `PointMapping`
- L31: `SourceRegistry`
- L44: `MappingDraft`
- L50: `MappingReview`
- L56: `SourceReason`
- L60: `SourceDecision`
- L64: `validate_connection`
- L78: `redacted`
- L87: `register_sources`
- L89: `rotate_token`
- L91: `apply`
- L103: `measurements`
- L118: `registry`
- L120: `apply`
- L146: `draft`
- L148: `apply`
- L169: `review`
- L171: `apply`
- L199: `enabled`
- L201: `apply`
- L212: `sample`
- L230: `raw_records`
- L245: `raw_sample`
- L258: `reprocess`
- L260: `apply`

## quality/statistics.py

- L8: `verified_cycle_facts`
- L26: `normalized_observations`
- L63: `quality_statistics`
- L67: `stamp`
- L112: `summarize`

## quality/storage.py

- L28: `validate_file`
- L63: `Storage`
- L64: `__init__`
- L90: `path`
- L96: `put`
- L116: `get`
- L129: `reconcile`
- L151: `parse_document`

## quality/supply.py

- L12: `bom_components`
- L18: `validate_bom`
- L52: `create`
- L68: `order_detail`
- L90: `validate_order_lines`
- L110: `order`
- L170: `explode`
- L214: `current_plan`
- L250: `plan_validation`
- L262: `plan`
- L393: `usage`

## quality/sync.py

- L22: `private_key`
- L28: `public_key`
- L36: `register_sync`
- L37: `site_identity`
- L53: `sites`
- L62: `register`
- L67: `apply`
- L99: `site_endpoint`
- L102: `apply`
- L117: `revoke`
- L120: `apply`
- L129: `rotate_site_token`
- L138: `apply`
- L149: `push`
- L257: `snapshots`
- L264: `remote_list`
- L275: `center_file`
- L317: `site_evidence`
- L356: `remote`
- L359: `apply`
- L395: `pull`
- L420: `ack`
- L449: `package`
- L476: `preview`
- L481: `apply_package`
- L484: `apply`
- L512: `verify_package`
- L588: `sync_once`

## quality/templates.py

- L73: `candidate_points`
- L87: `reference_routes`
- L119: `effective_steps`
- L123: `validate_route`

## quality/visual.py

- L10: `flag_corrected_evidence`
- L27: `ingest_inspection`

## quality/worker.py

- L17: `dispatch`
- L32: `publish_once`
- L142: `local_dispatch_once`
- L193: `run`
- L203: `stop`

## quality/workspace.py

- L18: `AssistantInput`
- L25: `ToolReference`
- L31: `ToolResult`
- L43: `assistant_result`
- L145: `register_workspace`
- L147: `evidence_record`
- L166: `scope_options`
- L179: `assistant`
- L180: `apply`
- L196: `tasks`
- L201: `process_context`

## scripts/advanced_browser_check.py

- L145: `worker`

## scripts/audit_public_release.py

- L9: `main`

## scripts/benchmark.py

- L15: `main`
- L65: `request`

## scripts/bundle_checksums.py

- L9: `sha256`
- L14: `inventory`
- L26: `verify`

## scripts/customer_package.py

- L18: `rabbit_hash`
- L23: `generate`

## scripts/environment_preflight.py

- L16: `command`

## scripts/fetch_storage_reference.py

- L15: `main`

## scripts/industrial_browser_check.py

- L18: `main`

## scripts/install_customer.py

- L15: `main`
- L37: `run`

## scripts/multisite_check.py

- L25: `free_port`
- L31: `launch`
- L77: `post`
- L88: `check_customer`
- L351: `main`

## scripts/node_package.py

- L18: `generate`

## scripts/prepare_local_offline.py

- L19: `prepare`

## scripts/prepare_public_release.py

- L19: `digest`
- L23: `main`
- L30: `copy`

## scripts/run_local.py

- L23: `build_id`
- L33: `health`
- L43: `launch`

## scripts/verify_local.py

- L18: `main`

## scripts/verify_model_workflows.py

- L17: `main`
