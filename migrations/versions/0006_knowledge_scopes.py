"""Knowledge multi-line applicability and vector parent authorization."""

from alembic import op
from sqlalchemy import text

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    base = """tenant_id=current_setting('app.tenant',true) AND
       (site_id='' OR site_id=ANY(string_to_array(current_setting('app.sites',true),',')) OR '*'=ANY(string_to_array(current_setting('app.sites',true),','))) AND
       (scope_line_id='' OR scope_line_id=ANY(string_to_array(current_setting('app.lines',true),',')) OR '*'=ANY(string_to_array(current_setting('app.lines',true),',')))"""
    bind.execute(
        text(f"""ALTER POLICY scope_isolation ON knowledge USING ({base} AND
      (coalesce(jsonb_array_length(scope::jsonb->'factory_ids'),0)=0 OR '*'=ANY(string_to_array(current_setting('app.sites',true),',')) OR
       EXISTS (SELECT 1 FROM jsonb_array_elements_text(scope::jsonb->'factory_ids') AS factories(value) WHERE factories.value=ANY(string_to_array(current_setting('app.sites',true),',')))) AND
      (coalesce(jsonb_array_length(scope::jsonb->'line_ids'),0)=0 OR '*'=ANY(string_to_array(current_setting('app.lines',true),',')) OR
       EXISTS (SELECT 1 FROM jsonb_array_elements_text(scope::jsonb->'line_ids') AS allowed(value) WHERE allowed.value=ANY(string_to_array(current_setting('app.lines',true),',')))))""")
    )
    bind.execute(
        text(f"""ALTER POLICY scope_isolation ON knowledge_vectors USING ({base} AND
      EXISTS (SELECT 1 FROM knowledge WHERE knowledge.id=knowledge_vectors.knowledge_id))""")
    )


def downgrade():
    raise RuntimeError("禁止移除知识权限，回退须独立恢复验证")
