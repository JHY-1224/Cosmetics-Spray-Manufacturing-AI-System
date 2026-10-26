from collections import deque

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.resources import Resource


class RecentSpans(SpanExporter):
    def __init__(self):
        self.items = deque(maxlen=300)

    def export(self, spans):
        for span in spans:
            self.items.append(
                {
                    "trace_id": f"{span.context.trace_id:032x}",
                    "name": span.name,
                    "duration_ms": (span.end_time - span.start_time) / 1e6,
                    "attributes": dict(span.attributes),
                }
            )
        return SpanExportResult.SUCCESS


def setup(settings):
    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": "quality-" + settings.node_role, "service.instance.id": settings.node_id}
        )
    )
    recent = RecentSpans()
    provider.add_span_processor(SimpleSpanProcessor(recent))
    if settings.otlp_endpoint:
        from .security import endpoint

        endpoint(settings.otlp_endpoint, settings)
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from .sdk_control import telemetry_session

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_endpoint, timeout=5,
                                               session=telemetry_session(settings)))
        )
    return provider, provider.get_tracer("quality.workflow"), recent
