"""Dependency-injection composition root for technical platform services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import structlog

from vinami.audit.adapters.writer import SQLAlchemyAuditWriter
from vinami.audit.application.history import (
    CaseHistoryApplicationService,
    CaseHistoryQueryService,
)
from vinami.audit.application.history import (
    UnitOfWorkFactory as CaseHistoryUnitOfWorkFactory,
)
from vinami.clinical_case.application.case_workspace import (
    CompleteCaseWorkspaceQueryService,
)
from vinami.clinical_case.application.case_workspace import (
    UnitOfWorkFactory as CaseWorkspaceUnitOfWorkFactory,
)
from vinami.clinical_case.application.command_center import (
    CommandCenterQueryService,
)
from vinami.clinical_case.application.command_center import (
    UnitOfWorkFactory as CommandCenterUnitOfWorkFactory,
)
from vinami.ecg.adapters.artifact_store import LocalECGArtifactStore
from vinami.ecg.adapters.ingestion import (
    CanonicalWaveformAdapter,
    CanonicalXMLAdapter,
    DICOMWaveformAdapter,
    MockDICOMSourceConnector,
    PDFECGAdapter,
)
from vinami.ecg.adapters.mock_provider import MockECGAIProvider
from vinami.ecg.adapters.precomputed_showcase_provider import PTBXLShowcaseProvider
from vinami.ecg.adapters.preprocessing import CanonicalECGPreprocessor, TechnicalECGQualityEngine
from vinami.ecg.adapters.torch_provider import TorchConvNeXtV2Provider
from vinami.ecg.adapters.torch_stemi_provider import TorchTCNStemiProvider
from vinami.ecg.application.service import (
    ECGApplicationService,
    ECGQueryService,
)
from vinami.ecg.application.service import (
    UnitOfWorkFactory as ECGUnitOfWorkFactory,
)
from vinami.ecg.ports.ingestion import ECGSourceAdapter
from vinami.ecg.ports.provider import ECGAIProvider
from vinami.identity.adapters.binding import SQLAlchemyIdentityBinding
from vinami.identity.adapters.development import DevelopmentIdentityProvider
from vinami.identity.adapters.oidc import OIDCIdentityProvider
from vinami.identity.application.authorization import ExplicitAuthorizationPolicy
from vinami.identity.ports.authorization import AuthorizationPolicy
from vinami.identity.ports.provider import IdentityBinding, IdentityProvider
from vinami.integration.adapters.realtime import SQLAlchemyRealtimeEventRepository
from vinami.integration.application.realtime import RealtimeQueryService
from vinami.integration.application.service import IntegrationApplicationService
from vinami.reasoning.application.service import (
    ClinicalReasoningApplicationService,
    ClinicalReasoningQueryService,
)
from vinami.reasoning.application.service import (
    UnitOfWorkFactory as ReasoningUnitOfWorkFactory,
)
from vinami.shared.config import Settings
from vinami.shared.database import Database
from vinami.shared.events.dispatcher import InternalEventDispatcher
from vinami.shared.events.handlers import build_default_handler_registry
from vinami.shared.events.registry import EventRegistry
from vinami.shared.events.serializer import EventSerializer
from vinami.shared.logging import configure_logging
from vinami.shared.metrics import Metrics
from vinami.shared.orm_registry import initialize_mappings
from vinami.shared.unit_of_work import SQLAlchemyUnitOfWork
from vinami.troponin.adapters.laboratory import MockLaboratoryConnector
from vinami.troponin.application.service import (
    TroponinApplicationService,
    TroponinQueryService,
)
from vinami.workflow.application.operations import (
    WorkflowOperationsQueryService,
    WorkflowOperationsService,
)


@dataclass(slots=True)
class AppContainer:
    """Own dependencies for a single application instance without global state."""

    settings: Settings
    database: Database
    metrics: Metrics
    logger: structlog.stdlib.BoundLogger
    identity_provider: IdentityProvider
    identity_binding: IdentityBinding
    authorization_policy: AuthorizationPolicy

    @classmethod
    def create(cls, settings: Settings | None = None) -> AppContainer:
        """Compose validated settings and infrastructure adapters."""
        configured_settings = settings or Settings()
        if configured_settings.identity_mode is None:
            raise RuntimeError("VINAMI_IDENTITY_MODE must explicitly select development or oidc")
        initialize_mappings()
        database = Database.from_settings(configured_settings)
        return cls(
            settings=configured_settings,
            database=database,
            metrics=Metrics.create(),
            logger=configure_logging(configured_settings),
            identity_provider=_identity_provider(configured_settings),
            identity_binding=SQLAlchemyIdentityBinding(database.session_factory),
            authorization_policy=ExplicitAuthorizationPolicy(),
        )

    def unit_of_work(self) -> SQLAlchemyUnitOfWork:
        """Create an explicit persistence boundary for an application command."""
        return SQLAlchemyUnitOfWork(self.database.session_factory)

    def event_dispatcher(self) -> InternalEventDispatcher:
        """Compose the replaceable PostgreSQL outbox dispatcher and its initial handlers."""
        registry = EventRegistry.default()
        return InternalEventDispatcher(
            session_factory=self.database.session_factory,
            serializer=EventSerializer(registry),
            handlers=build_default_handler_registry(),
            logger=self.logger,
            max_retries=self.settings.event_max_retries,
            retry_delay_seconds=self.settings.event_retry_delay_seconds,
            claim_ttl_seconds=self.settings.event_claim_ttl_seconds,
        )

    def audit_writer(self) -> SQLAlchemyAuditWriter:
        """Create the append-only command audit adapter used by consequential handlers."""
        return SQLAlchemyAuditWriter(self.database.session_factory)

    def ecg_artifact_store(self) -> LocalECGArtifactStore:
        return LocalECGArtifactStore(
            self.settings.ecg_artifact_root,
            self.settings.ecg_artifact_max_bytes,
        )

    def ecg_providers(self) -> dict[str, ECGAIProvider]:
        providers: dict[str, ECGAIProvider] = {}
        if self.settings.ecg_allow_mock_provider:
            providers["mock"] = MockECGAIProvider()
        if self.settings.ecg_checkpoint_path and self.settings.ecg_checkpoint_sha256:
            providers["torch"] = TorchConvNeXtV2Provider(
                self.settings.ecg_checkpoint_path,
                self.settings.ecg_checkpoint_sha256,
                self.settings.ecg_device,
                self.settings.ecg_provider_timeout_seconds,
            )
        if (
            self.settings.ecg_showcase_manifest_path
            and self.settings.ecg_showcase_manifest_path.is_file()
        ):
            providers["ptbxl-showcase"] = PTBXLShowcaseProvider(
                self.settings.ecg_showcase_manifest_path,
                self.settings.ecg_showcase_artifact_root,
            )
        if self.settings.ecg_stemi_checkpoint_path and self.settings.ecg_stemi_checkpoint_sha256:
            providers["torch-acs-stemi"] = TorchTCNStemiProvider(
                self.settings.ecg_stemi_checkpoint_path,
                self.settings.ecg_stemi_checkpoint_sha256,
                self.settings.ecg_device,
                self.settings.ecg_provider_timeout_seconds,
            )
        return providers

    def ecg_application(self) -> ECGApplicationService:
        sources = _ecg_sources()
        return ECGApplicationService(
            cast(ECGUnitOfWorkFactory, self.unit_of_work),
            self.ecg_artifact_store(),
            sources,
            CanonicalECGPreprocessor(),
            TechnicalECGQualityEngine(),
            self.ecg_providers(),
            MockDICOMSourceConnector(),
            fallback_provider_name=self.settings.ecg_fallback_provider
            if self.settings.ecg_allow_provider_fallback
            else None,
        )

    def ecg_queries(self) -> ECGQueryService:
        return ECGQueryService(
            cast(ECGUnitOfWorkFactory, self.unit_of_work),
            self.ecg_artifact_store(),
            _ecg_sources(),
            self.ecg_providers(),
            MockDICOMSourceConnector(),
        )

    def laboratory_connectors(self) -> dict[str, MockLaboratoryConnector]:
        """Expose only the deterministic local/mock connector in R4."""
        connector = MockLaboratoryConnector()
        return {connector.connector_id: connector}

    def troponin_application(self) -> TroponinApplicationService:
        """Compose the R4 troponin command service behind application ports."""
        return TroponinApplicationService(self.unit_of_work, self.laboratory_connectors())

    def troponin_queries(self) -> TroponinQueryService:
        """Compose the R4 troponin read service."""
        return TroponinQueryService(self.unit_of_work, self.laboratory_connectors())

    def clinical_reasoning_application(self) -> ClinicalReasoningApplicationService:
        """Compose R5 commands behind the reasoning persistence port."""
        return ClinicalReasoningApplicationService(
            cast(ReasoningUnitOfWorkFactory, self.unit_of_work)
        )

    def clinical_reasoning_queries(self) -> ClinicalReasoningQueryService:
        """Compose the R5 evidence workspace query service."""
        return ClinicalReasoningQueryService(cast(ReasoningUnitOfWorkFactory, self.unit_of_work))

    def workflow_operations(self) -> WorkflowOperationsService:
        """Compose R6 operational workflow commands."""
        return WorkflowOperationsService(self.unit_of_work)

    def workflow_operation_queries(self) -> WorkflowOperationsQueryService:
        """Compose the authoritative R6 workflow workspace query."""
        return WorkflowOperationsQueryService(self.unit_of_work)

    def command_center_queries(self) -> CommandCenterQueryService:
        """Compose the R7 operational queue projection."""
        return CommandCenterQueryService(cast(CommandCenterUnitOfWorkFactory, self.unit_of_work))

    def complete_case_workspace_queries(self) -> CompleteCaseWorkspaceQueryService:
        """Compose the R8 integrated case projection."""
        return CompleteCaseWorkspaceQueryService(
            cast(CaseWorkspaceUnitOfWorkFactory, self.unit_of_work)
        )

    def case_history_queries(self) -> CaseHistoryQueryService:
        """Compose R9 timeline, audit, outcome and provenance queries."""
        return CaseHistoryQueryService(cast(CaseHistoryUnitOfWorkFactory, self.unit_of_work))

    def case_history_application(self) -> CaseHistoryApplicationService:
        """Compose R9 outcome and research export commands."""
        return CaseHistoryApplicationService(cast(CaseHistoryUnitOfWorkFactory, self.unit_of_work))

    def integration_application(self) -> IntegrationApplicationService:
        """Compose R10 simulated hospital integration commands."""
        return IntegrationApplicationService(
            self.unit_of_work,
            self.troponin_application(),
            self.ecg_application(),
        )

    def realtime_queries(self) -> RealtimeQueryService:
        """Compose R10 server-sent notification queries."""
        return RealtimeQueryService(
            SQLAlchemyRealtimeEventRepository(self.database.session_factory)
        )


def _identity_provider(settings: Settings) -> IdentityProvider:
    """Build exactly one provider from validated environment-driven configuration."""
    if settings.identity_mode == "development":
        return DevelopmentIdentityProvider()
    assert settings.oidc_issuer is not None
    assert settings.oidc_audience is not None
    assert settings.oidc_jwks_url is not None
    return OIDCIdentityProvider(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        jwks_url=settings.oidc_jwks_url,
        allowed_algorithms=settings.oidc_algorithms,
        role_claim=settings.oidc_role_claim,
        permission_claim=settings.oidc_permission_claim,
    )


def _ecg_sources() -> dict[str, ECGSourceAdapter]:
    adapters = (
        CanonicalWaveformAdapter(),
        DICOMWaveformAdapter(),
        CanonicalXMLAdapter(),
        PDFECGAdapter(),
    )
    return {adapter.source_type: adapter for adapter in adapters}
