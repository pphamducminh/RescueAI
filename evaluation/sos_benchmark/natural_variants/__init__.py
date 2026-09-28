"""Provider-neutral render specs and offline variant import for SOS Task 6."""

from .models import ExternalContext, RenderClaim, RenderFact, RenderSpec, RenderStyle
from .pipeline import (
    ImportSummary,
    SemanticReviewDecision,
    SemanticValidator,
    SemanticVerdict,
    VariantBatch,
    VariantInput,
    VariantResult,
    VariantStatus,
    import_variants,
)
from .specs import build_render_spec, export_render_specs, load_benchmark_cases
from .validation import DeterministicResult, validate_variant

__all__ = [
    "DeterministicResult",
    "ExternalContext",
    "ImportSummary",
    "RenderClaim",
    "RenderFact",
    "RenderSpec",
    "RenderStyle",
    "SemanticReviewDecision",
    "SemanticValidator",
    "SemanticVerdict",
    "VariantBatch",
    "VariantInput",
    "VariantResult",
    "VariantStatus",
    "build_render_spec",
    "export_render_specs",
    "import_variants",
    "load_benchmark_cases",
    "validate_variant",
]
