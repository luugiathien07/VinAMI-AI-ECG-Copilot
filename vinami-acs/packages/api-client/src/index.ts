import createClient from "openapi-fetch";
import type { components, paths } from "@vinami/contracts";

export type ProblemDetail = components["schemas"]["ProblemDetail"];
export type ClinicalCasePage =
  components["schemas"]["PaginatedClinicalCasesResponse"];
export type ClinicalCase = components["schemas"]["ClinicalCaseResponse"];
export type Patient = components["schemas"]["PatientResponse"];
export type Encounter = components["schemas"]["EncounterResponse"];
export type TimelineEvent = components["schemas"]["TimelineEventResponse"];
export type AuditEntry = components["schemas"]["AuditEntryResponse"];
export type IdentitySession = components["schemas"]["IdentitySessionResponse"];
export type TriageAssessment =
  components["schemas"]["TriageAssessmentResponse"];
export type TriageHistory = components["schemas"]["TriageHistoryResponse"];
export type TriageVitalsSummary =
  components["schemas"]["TriageVitalsSummaryResponse"];
export type VitalObservationSet =
  components["schemas"]["VitalObservationSetResponse"];
export type VitalObservationHistory =
  components["schemas"]["VitalObservationHistoryResponse"];
export type RecordTriageInput =
  components["schemas"]["RecordTriageAssessmentRequest"];
export type CorrectTriageInput =
  components["schemas"]["CorrectTriageAssessmentRequest"];
export type RecordVitalsInput =
  components["schemas"]["RecordVitalObservationSetRequest"];
export type CorrectVitalsInput =
  components["schemas"]["CorrectVitalObservationSetRequest"];
export type ECGWorkspace = components["schemas"]["ECGWorkspaceResponse"];
export type ECGWaveform = components["schemas"]["WaveformResponse"];
export type ECGIngestionJob = components["schemas"]["IngestionJobResponse"];
export type ECGAnalysis = components["schemas"]["AnalysisResponse"];
export type ECGInterpretation = components["schemas"]["InterpretationResponse"];
export type ECGAnalysisInput = components["schemas"]["RequestAnalysisBody"];
export type ECGInterpretationInput =
  components["schemas"]["InterpretationBody"];
export type ECGConnector = components["schemas"]["ConnectorResponse"];
export type ECGImportInput = components["schemas"]["ImportBody"];
export type ECGRetryInput = components["schemas"]["RetryIngestionBody"];
export type ECGComparison = components["schemas"]["ComparisonResponse"];
export type ECGComparisonInput = components["schemas"]["ComparisonBody"];
export type ECGComparisonReview =
  components["schemas"]["ComparisonReviewResponse"];
export type ECGComparisonReviewInput =
  components["schemas"]["ComparisonReviewBody"];
export type ECGReminder = components["schemas"]["ReminderResponse"];
export type ECGReminderInput = components["schemas"]["ReminderBody"];
export type ECGReminderTransitionInput =
  components["schemas"]["ReminderTransitionBody"];
export type ClinicalReasoningWorkspace =
  components["schemas"]["ClinicalReasoningWorkspaceResponse"];
export type EvidenceSnapshotInput = components["schemas"]["SnapshotBody"];
export type ClinicalAssessmentInput = components["schemas"]["AssessmentBody"];
export type ClinicalAssessmentCorrectionInput =
  components["schemas"]["AssessmentCorrectionBody"];
export type ClinicalDecisionInput = components["schemas"]["DecisionBody"];
export type ClinicalDecisionCorrectionInput =
  components["schemas"]["DecisionCorrectionBody"];
export type ClinicalDecisionRevocationInput =
  components["schemas"]["DecisionRevocationBody"];
export type WorkflowOperationsWorkspace =
  components["schemas"]["WorkflowWorkspaceResponse"];
export type WorkflowTask = components["schemas"]["TaskResponse"];
export type WorkflowAlert = components["schemas"]["AlertResponse"];
export type GenerateWorkflowTaskInput =
  components["schemas"]["GenerateTaskBody"];
export type WorkflowTaskActionInput = components["schemas"]["TaskActionBody"];
export type WorkflowAlertActionInput = components["schemas"]["AlertActionBody"];
export type CommandCenterPage =
  components["schemas"]["CommandCenterPageResponse"];
export type CompleteCaseWorkspace =
  components["schemas"]["CompleteCaseWorkspaceResponse"];
export type OutcomeInput = Record<string, unknown>;

export type ApiErrorKind =
  | "authentication"
  | "authorization"
  | "not_found"
  | "conflict"
  | "validation"
  | "timeout"
  | "offline"
  | "server"
  | "malformed"
  | "unknown";

export class VinamiApiError extends Error {
  readonly occurredAt = new Date().toISOString();

  constructor(
    readonly kind: ApiErrorKind,
    message: string,
    readonly status: number | null,
    readonly correlationId: string | null,
    readonly retryable: boolean,
  ) {
    super(message);
    this.name = "VinamiApiError";
  }
}

export interface ApiClientOptions {
  baseUrl: string;
  developmentIdentity?: string;
  requestSource?: string;
  timeoutMs?: number;
  onResponse?: (observation: {
    status: number;
    correlationId: string | null;
  }) => void;
}

export interface CaseListQuery {
  page?: number;
  page_size?: number;
  status?: "open" | "under_review" | "closed";
  priority?: "routine" | "urgent" | "critical";
  location?: string;
}

export interface CommandCenterQuery {
  page?: number;
  page_size?: number;
  priority?: "routine" | "urgent" | "critical";
  workflow_state?:
    | "NEW"
    | "TRIAGE_IN_PROGRESS"
    | "AWAITING_ECG"
    | "ECG_UNDER_REVIEW"
    | "AWAITING_TROPONIN"
    | "OBSERVATION"
    | "PHYSICIAN_REVIEW_REQUIRED"
    | "PATHWAY_SELECTED"
    | "TRANSFER_IN_PROGRESS"
    | "CATH_LAB_REVIEW"
    | "DISCHARGE_REVIEW"
    | "CLOSED"
    | "CANCELLED";
  overdue?: boolean;
  alert_status?: "active" | "acknowledged" | "unresolved" | "none";
  evidence_status?: "current" | "missing" | "stale" | "technical_limit";
  owner_id?: string;
  sort?: "priority" | "opened_at" | "next_due_at" | "latest_activity";
  direction?: "asc" | "desc";
}

export class VinamiApiClient {
  private readonly client: ReturnType<typeof createClient<paths>>;
  private readonly baseUrl: string;
  private readonly defaultHeaders: Record<string, string>;
  private readonly safeFetch: typeof fetch;

  constructor(options: ApiClientOptions) {
    const headers: Record<string, string> = {
      Accept: "application/json",
      "X-Request-Source": options.requestSource ?? "vinami-web",
    };
    if (options.developmentIdentity) {
      headers["X-Development-Identity"] = options.developmentIdentity;
    }
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.defaultHeaders = headers;
    this.safeFetch = createSafeFetch(
      options.timeoutMs ?? 8_000,
      options.onResponse,
    );
    this.client = createClient<paths>({
      baseUrl: this.baseUrl,
      headers,
      fetch: this.safeFetch,
    });
  }

  async listClinicalCases(
    query: CaseListQuery = {},
  ): Promise<ClinicalCasePage> {
    const result = await this.client.GET("/api/v1/clinical-cases", {
      params: { query },
    });
    return unwrap(result);
  }

  async getCommandCenter(
    query: CommandCenterQuery = {},
  ): Promise<CommandCenterPage> {
    return unwrap(
      await this.client.GET("/api/v1/command-center/cases", {
        params: { query },
      }),
    );
  }

  async getCompleteCaseWorkspace(
    caseId: string,
  ): Promise<CompleteCaseWorkspace> {
    return unwrap(
      await this.client.GET(
        "/api/v1/clinical-cases/{case_id}/complete-workspace",
        { params: { path: { case_id: caseId } } },
      ),
    );
  }

  async getTroponinWorkspace(caseId: string): Promise<unknown> {
    return this.rawJson(`/api/v1/clinical-cases/${caseId}/troponin-workspace`);
  }

  async getTroponinAssays(): Promise<unknown> {
    return this.rawJson("/api/v1/troponin-assays");
  }

  async getClinicalReasoningWorkspace(
    caseId: string,
  ): Promise<ClinicalReasoningWorkspace> {
    return unwrap(
      await this.client.GET(
        "/api/v1/clinical-cases/{case_id}/clinical-reasoning",
        { params: { path: { case_id: caseId } } },
      ),
    );
  }

  async getRiskScores(caseId: string): Promise<unknown> {
    return this.rawJson(
      `/api/v1/clinical-cases/${caseId}/clinical-reasoning/risk-scores`,
    );
  }

  async recordHeartScore(
    caseId: string,
    body: unknown,
    idempotencyKey: string,
  ): Promise<unknown> {
    return this.postJson(
      `/api/v1/clinical-cases/${caseId}/clinical-reasoning/risk-scores/heart`,
      body,
      idempotencyKey,
    );
  }

  async recordGraceScore(
    caseId: string,
    body: unknown,
    idempotencyKey: string,
  ): Promise<unknown> {
    return this.postJson(
      `/api/v1/clinical-cases/${caseId}/clinical-reasoning/risk-scores/grace`,
      body,
      idempotencyKey,
    );
  }

  async getWorkflowOperationsWorkspace(
    caseId: string,
  ): Promise<WorkflowOperationsWorkspace> {
    return unwrap(
      await this.client.GET(
        "/api/v1/clinical-cases/{case_id}/workflow-operations",
        { params: { path: { case_id: caseId } } },
      ),
    );
  }

  async generateWorkflowTask(
    caseId: string,
    body: GenerateWorkflowTaskInput,
    idempotencyKey: string,
  ): Promise<WorkflowTask> {
    return unwrap(
      await this.client.POST(
        "/api/v1/clinical-cases/{case_id}/workflow-operations/tasks/from-decisions",
        {
          params: {
            path: { case_id: caseId },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body,
        },
      ),
    );
  }

  async changeWorkflowTask(
    caseId: string,
    taskId: string,
    action: "assign" | "acknowledge" | "start" | "complete" | "cancel",
    body: WorkflowTaskActionInput,
    idempotencyKey: string,
  ): Promise<WorkflowTask> {
    return unwrap(
      await this.client.POST(
        "/api/v1/clinical-cases/{case_id}/workflow-operations/tasks/{task_id}/{action}",
        {
          params: {
            path: { case_id: caseId, task_id: taskId, action },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body,
        },
      ),
    );
  }

  async evaluateWorkflowOverdue(caseId: string, idempotencyKey: string) {
    return unwrap(
      await this.client.POST(
        "/api/v1/clinical-cases/{case_id}/workflow-operations/overdue-evaluations",
        {
          params: {
            path: { case_id: caseId },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body: {},
        },
      ),
    );
  }

  async changeWorkflowAlert(
    caseId: string,
    alertId: string,
    action: "acknowledge" | "resolve",
    body: WorkflowAlertActionInput,
    idempotencyKey: string,
  ): Promise<WorkflowAlert> {
    return unwrap(
      await this.client.POST(
        "/api/v1/clinical-cases/{case_id}/workflow-operations/alerts/{alert_id}/{action}",
        {
          params: {
            path: { case_id: caseId, alert_id: alertId, action },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body,
        },
      ),
    );
  }

  async createEvidenceSnapshot(
    caseId: string,
    body: EvidenceSnapshotInput,
    idempotencyKey: string,
  ): Promise<unknown> {
    return unwrap(
      await this.client.POST(
        "/api/v1/clinical-cases/{case_id}/clinical-reasoning/snapshots",
        {
          params: {
            path: { case_id: caseId },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body,
        },
      ),
    );
  }

  async recordClinicalAssessment(
    caseId: string,
    body: ClinicalAssessmentInput,
    idempotencyKey: string,
  ): Promise<unknown> {
    return unwrap(
      await this.client.POST(
        "/api/v1/clinical-cases/{case_id}/clinical-reasoning/assessments",
        {
          params: {
            path: { case_id: caseId },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body,
        },
      ),
    );
  }

  async recordClinicalDecision(
    caseId: string,
    body: ClinicalDecisionInput,
    idempotencyKey: string,
  ): Promise<unknown> {
    return unwrap(
      await this.client.POST(
        "/api/v1/clinical-cases/{case_id}/clinical-reasoning/decisions",
        {
          params: {
            path: { case_id: caseId },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body,
        },
      ),
    );
  }

  async correctClinicalAssessment(
    caseId: string,
    assessmentId: string,
    body: ClinicalAssessmentCorrectionInput,
    idempotencyKey: string,
  ): Promise<unknown> {
    return unwrap(
      await this.client.POST(
        "/api/v1/clinical-cases/{case_id}/clinical-reasoning/assessments/{assessment_id}/corrections",
        {
          params: {
            path: { case_id: caseId, assessment_id: assessmentId },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body,
        },
      ),
    );
  }

  async correctClinicalDecision(
    caseId: string,
    decisionId: string,
    body: ClinicalDecisionCorrectionInput,
    idempotencyKey: string,
  ): Promise<unknown> {
    return unwrap(
      await this.client.POST(
        "/api/v1/clinical-cases/{case_id}/clinical-reasoning/decisions/{decision_id}/corrections",
        {
          params: {
            path: { case_id: caseId, decision_id: decisionId },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body,
        },
      ),
    );
  }

  async revokeClinicalDecision(
    caseId: string,
    decisionId: string,
    body: ClinicalDecisionRevocationInput,
    idempotencyKey: string,
  ): Promise<unknown> {
    return unwrap(
      await this.client.POST(
        "/api/v1/clinical-cases/{case_id}/clinical-reasoning/decisions/{decision_id}/revocations",
        {
          params: {
            path: { case_id: caseId, decision_id: decisionId },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body,
        },
      ),
    );
  }

  private async rawJson(path: string): Promise<unknown> {
    const response = await this.safeFetch(`${this.baseUrl}${path}`, {
      headers: this.defaultHeaders,
    });
    const payload: unknown = await response.json();
    if (response.ok) return payload;
    throw mapApiError(
      response.status,
      payload,
      response.headers.get("x-correlation-id"),
    );
  }

  private async postJson(
    path: string,
    body: unknown,
    idempotencyKey: string,
  ): Promise<unknown> {
    const response = await this.safeFetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: {
        ...this.defaultHeaders,
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify(body),
    });
    const payload: unknown = await response.json().catch(() => undefined);
    if (response.ok) return payload;
    throw mapApiError(
      response.status,
      payload,
      response.headers.get("x-correlation-id"),
    );
  }

  async getClinicalCase(caseId: string): Promise<ClinicalCase> {
    return unwrap(
      await this.client.GET("/api/v1/clinical-cases/{case_id}", {
        params: { path: { case_id: caseId } },
      }),
    );
  }

  async getPatient(patientId: string): Promise<Patient> {
    return unwrap(
      await this.client.GET("/api/v1/patients/{patient_id}", {
        params: { path: { patient_id: patientId } },
      }),
    );
  }

  async getEncounter(encounterId: string): Promise<Encounter> {
    return unwrap(
      await this.client.GET("/api/v1/encounters/{encounter_id}", {
        params: { path: { encounter_id: encounterId } },
      }),
    );
  }

  async getTimeline(caseId: string): Promise<TimelineEvent[]> {
    return unwrap(
      await this.client.GET("/api/v1/clinical-cases/{case_id}/timeline", {
        params: { path: { case_id: caseId } },
      }),
    );
  }

  async getAudit(caseId: string): Promise<AuditEntry[]> {
    return unwrap(
      await this.client.GET("/api/v1/clinical-cases/{case_id}/audit", {
        params: { path: { case_id: caseId } },
      }),
    );
  }

  async getTimelineHistory(
    caseId: string,
    query: Record<string, unknown> = {},
  ): Promise<unknown> {
    const suffix = queryString(query);
    return this.rawJson(
      `/api/v1/clinical-cases/${encodeURIComponent(caseId)}/history/timeline${suffix}`,
    );
  }

  async getAuditHistory(
    caseId: string,
    query: Record<string, unknown> = {},
  ): Promise<unknown> {
    const suffix = queryString(query);
    return this.rawJson(
      `/api/v1/clinical-cases/${encodeURIComponent(caseId)}/history/audit${suffix}`,
    );
  }

  async getOutcomeHistory(caseId: string): Promise<unknown> {
    return this.rawJson(
      `/api/v1/clinical-cases/${encodeURIComponent(caseId)}/outcomes`,
    );
  }

  async recordOutcome(
    caseId: string,
    body: OutcomeInput,
    idempotencyKey: string,
  ): Promise<unknown> {
    return this.postJson(
      `/api/v1/clinical-cases/${encodeURIComponent(caseId)}/outcomes`,
      body,
      idempotencyKey,
    );
  }

  async correctOutcome(
    caseId: string,
    outcomeId: string,
    body: OutcomeInput,
    idempotencyKey: string,
  ): Promise<unknown> {
    return this.postJson(
      `/api/v1/clinical-cases/${encodeURIComponent(caseId)}/outcomes/${encodeURIComponent(outcomeId)}/corrections`,
      body,
      idempotencyKey,
    );
  }

  async getModelProvenance(caseId: string): Promise<unknown> {
    return this.rawJson(
      `/api/v1/clinical-cases/${encodeURIComponent(caseId)}/model-provenance`,
    );
  }

  async getIntegrationMessages(
    query: Record<string, unknown> = {},
  ): Promise<unknown> {
    return this.rawJson(`/api/v1/integration/messages${queryString(query)}`);
  }

  async getIntegrationConnectorStatus(): Promise<unknown> {
    return this.rawJson("/api/v1/integration/connectors/status");
  }

  async simulateLaboratoryResult(
    body: Record<string, unknown>,
    idempotencyKey: string,
  ): Promise<unknown> {
    return this.postJson(
      "/api/v1/integration/simulated/laboratory-results",
      body,
      idempotencyKey,
    );
  }

  async simulateECGStudy(
    body: Record<string, unknown>,
    idempotencyKey: string,
  ): Promise<unknown> {
    return this.postJson(
      "/api/v1/integration/simulated/ecg-studies",
      body,
      idempotencyKey,
    );
  }

  async requestResearchExport(
    caseIds: string[],
    idempotencyKey: string,
  ): Promise<unknown> {
    return this.postJson(
      "/api/v1/research-exports",
      { case_ids: caseIds },
      idempotencyKey,
    );
  }

  async getCurrentTriage(caseId: string): Promise<TriageAssessment | null> {
    return unwrap(
      await this.client.GET("/api/v1/clinical-cases/{case_id}/triage", {
        params: { path: { case_id: caseId } },
      }),
    );
  }

  async getTriageHistory(caseId: string): Promise<TriageHistory> {
    return unwrap(
      await this.client.GET("/api/v1/clinical-cases/{case_id}/triage/history", {
        params: { path: { case_id: caseId } },
      }),
    );
  }

  async getVitalHistory(caseId: string): Promise<VitalObservationHistory> {
    return unwrap(
      await this.client.GET("/api/v1/clinical-cases/{case_id}/vitals", {
        params: { path: { case_id: caseId } },
      }),
    );
  }

  async getLatestVitals(caseId: string): Promise<VitalObservationSet | null> {
    return unwrap(
      await this.client.GET("/api/v1/clinical-cases/{case_id}/vitals/latest", {
        params: { path: { case_id: caseId } },
      }),
    );
  }

  async getTriageVitalsSummary(caseId: string): Promise<TriageVitalsSummary> {
    return unwrap(
      await this.client.GET(
        "/api/v1/clinical-cases/{case_id}/triage-vitals-summary",
        { params: { path: { case_id: caseId } } },
      ),
    );
  }

  async recordTriage(
    caseId: string,
    body: RecordTriageInput,
    idempotencyKey: string,
  ): Promise<TriageAssessment> {
    return unwrap(
      await this.client.POST(
        "/api/v1/clinical-cases/{case_id}/triage-assessments",
        {
          params: {
            path: { case_id: caseId },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body,
        },
      ),
    );
  }

  async correctTriage(
    caseId: string,
    assessmentId: string,
    body: CorrectTriageInput,
    idempotencyKey: string,
  ): Promise<TriageAssessment> {
    return unwrap(
      await this.client.POST(
        "/api/v1/clinical-cases/{case_id}/triage-assessments/{assessment_id}/corrections",
        {
          params: {
            path: { case_id: caseId, assessment_id: assessmentId },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body,
        },
      ),
    );
  }

  async recordVitals(
    caseId: string,
    body: RecordVitalsInput,
    idempotencyKey: string,
  ): Promise<VitalObservationSet> {
    return unwrap(
      await this.client.POST(
        "/api/v1/clinical-cases/{case_id}/vital-observation-sets",
        {
          params: {
            path: { case_id: caseId },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body,
        },
      ),
    );
  }

  async correctVitals(
    caseId: string,
    vitalSetId: string,
    body: CorrectVitalsInput,
    idempotencyKey: string,
  ): Promise<VitalObservationSet> {
    return unwrap(
      await this.client.POST(
        "/api/v1/clinical-cases/{case_id}/vital-observation-sets/{vital_set_id}/corrections",
        {
          params: {
            path: { case_id: caseId, vital_set_id: vitalSetId },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body,
        },
      ),
    );
  }

  async getIdentitySession(): Promise<IdentitySession> {
    return unwrap(await this.client.GET("/api/v1/identity/session"));
  }

  async getECGWorkspace(caseId: string): Promise<ECGWorkspace> {
    return unwrap(
      await this.client.GET("/api/v1/clinical-cases/{case_id}/ecg-workspace", {
        params: { path: { case_id: caseId } },
      }),
    );
  }

  async getECGWaveform(studyId: string): Promise<ECGWaveform> {
    return unwrap(
      await this.client.GET("/api/v1/ecg-studies/{study_id}/waveform", {
        params: { path: { study_id: studyId } },
      }),
    );
  }

  async requestECGAnalysis(
    studyId: string,
    body: ECGAnalysisInput,
    idempotencyKey: string,
  ): Promise<ECGAnalysis> {
    return unwrap(
      await this.client.POST("/api/v1/ecg-studies/{study_id}/analyses", {
        params: {
          path: { study_id: studyId },
          header: { "Idempotency-Key": idempotencyKey },
        },
        body,
      }),
    );
  }

  async recordECGInterpretation(
    studyId: string,
    body: ECGInterpretationInput,
    idempotencyKey: string,
  ): Promise<ECGInterpretation> {
    return unwrap(
      await this.client.POST(
        "/api/v1/ecg-studies/{study_id}/physician-interpretations",
        {
          params: {
            path: { study_id: studyId },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body,
        },
      ),
    );
  }

  async uploadECGStudy(
    caseId: string,
    form: FormData,
    idempotencyKey: string,
  ): Promise<ECGIngestionJob> {
    const headers = new Headers(this.defaultHeaders);
    headers.set("Idempotency-Key", idempotencyKey);
    const response = await this.safeFetch(
      `${this.baseUrl}/api/v1/clinical-cases/${encodeURIComponent(caseId)}/ecg-studies`,
      { method: "POST", headers, body: form },
    );
    const payload: unknown = await response.json().catch(() => undefined);
    if (!response.ok) {
      throw mapApiError(
        response.status,
        payload,
        response.headers.get("x-correlation-id"),
      );
    }
    return payload as ECGIngestionJob;
  }

  async getECGConnectors(): Promise<ECGConnector[]> {
    return unwrap(await this.client.GET("/api/v1/ecg-source-connectors"));
  }

  async importECGStudy(
    caseId: string,
    body: ECGImportInput,
    idempotencyKey: string,
  ): Promise<ECGIngestionJob> {
    return unwrap(
      await this.client.POST("/api/v1/clinical-cases/{case_id}/ecg-imports", {
        params: {
          path: { case_id: caseId },
          header: { "Idempotency-Key": idempotencyKey },
        },
        body,
      }),
    );
  }

  async retryECGIngestion(
    jobId: string,
    body: ECGRetryInput,
    idempotencyKey: string,
  ): Promise<ECGIngestionJob> {
    return unwrap(
      await this.client.POST("/api/v1/ecg-ingestion-jobs/{job_id}/retry", {
        params: {
          path: { job_id: jobId },
          header: { "Idempotency-Key": idempotencyKey },
        },
        body,
      }),
    );
  }

  async createECGComparison(
    caseId: string,
    body: ECGComparisonInput,
    idempotencyKey: string,
  ): Promise<ECGComparison> {
    return unwrap(
      await this.client.POST(
        "/api/v1/clinical-cases/{case_id}/ecg-comparisons",
        {
          params: {
            path: { case_id: caseId },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body,
        },
      ),
    );
  }

  async recordECGComparisonReview(
    comparisonId: string,
    body: ECGComparisonReviewInput,
    idempotencyKey: string,
  ): Promise<ECGComparisonReview> {
    return unwrap(
      await this.client.POST(
        "/api/v1/ecg-comparisons/{comparison_id}/physician-reviews",
        {
          params: {
            path: { comparison_id: comparisonId },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body,
        },
      ),
    );
  }

  async createECGReminder(
    caseId: string,
    body: ECGReminderInput,
    idempotencyKey: string,
  ): Promise<ECGReminder> {
    return unwrap(
      await this.client.POST(
        "/api/v1/clinical-cases/{case_id}/repeat-ecg-reminders",
        {
          params: {
            path: { case_id: caseId },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body,
        },
      ),
    );
  }

  async transitionECGReminder(
    reminderId: string,
    action: "acknowledge" | "complete" | "cancel",
    body: ECGReminderTransitionInput,
    idempotencyKey: string,
  ): Promise<ECGReminder> {
    const paths = {
      acknowledge:
        "/api/v1/repeat-ecg-reminders/{reminder_id}/acknowledge" as const,
      complete: "/api/v1/repeat-ecg-reminders/{reminder_id}/complete" as const,
      cancel: "/api/v1/repeat-ecg-reminders/{reminder_id}/cancel" as const,
    };
    return unwrap(
      await this.client.POST(paths[action], {
        params: {
          path: { reminder_id: reminderId },
          header: { "Idempotency-Key": idempotencyKey },
        },
        body,
      }),
    );
  }

  async getHealth(): Promise<Record<string, unknown>> {
    return recordResponse(unwrap(await this.client.GET("/health")));
  }

  async getReadiness(): Promise<Record<string, unknown>> {
    return recordResponse(unwrap(await this.client.GET("/ready")));
  }
}

function recordResponse(value: unknown): Record<string, unknown> {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  throw new VinamiApiError(
    "malformed",
    "The platform status response is malformed.",
    null,
    null,
    false,
  );
}

function queryString(query: Record<string, unknown>): string {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  });
  const value = params.toString();
  return value ? `?${value}` : "";
}

interface OpenApiResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

function unwrap<T>(result: OpenApiResult<T>): T {
  const correlationId = result.response.headers.get("x-correlation-id");
  if (result.data !== undefined) return result.data;
  throw mapApiError(result.response.status, result.error, correlationId);
}

export function mapApiError(
  status: number,
  payload: unknown,
  headerCorrelationId: string | null = null,
): VinamiApiError {
  const problem = isProblem(payload) ? payload : null;
  const correlationId = problem?.correlation_id ?? headerCorrelationId;
  const details: Record<number, [ApiErrorKind, string, boolean]> = {
    401: [
      "authentication",
      "Your verified session is unavailable or has expired.",
      false,
    ],
    403: [
      "authorization",
      "Your verified identity does not have permission to view this information.",
      false,
    ],
    404: [
      "not_found",
      "The requested clinical record is no longer available.",
      false,
    ],
    409: [
      "conflict",
      "The record changed while it was being read. Refresh before continuing.",
      true,
    ],
    422: [
      "validation",
      "The request could not be validated against the current API contract.",
      false,
    ],
  };
  const [kind, message, retryable] =
    details[status] ??
    (status >= 500
      ? ([
          "server",
          "The clinical service is temporarily unavailable.",
          true,
        ] as const)
      : (["unknown", "The request could not be completed.", false] as const));
  return new VinamiApiError(
    kind,
    problem?.detail ?? message,
    status,
    correlationId,
    retryable,
  );
}

function isProblem(value: unknown): value is ProblemDetail {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.title === "string" &&
    typeof candidate.status === "number" &&
    typeof candidate.detail === "string" &&
    typeof candidate.correlation_id === "string"
  );
}

function createSafeFetch(
  timeoutMs: number,
  onResponse?: ApiClientOptions["onResponse"],
): typeof fetch {
  return async (input, init) => {
    const method = (init?.method ?? "GET").toUpperCase();
    const attempts = method === "GET" ? 2 : 1;
    let lastError: unknown;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await fetch(input, {
          ...init,
          signal: controller.signal,
        });
        onResponse?.({
          status: response.status,
          correlationId: response.headers.get("x-correlation-id"),
        });
        if (
          method === "GET" &&
          attempt === 0 &&
          [502, 503, 504].includes(response.status)
        ) {
          continue;
        }
        return response;
      } catch (error) {
        lastError = error;
        if (attempt + 1 === attempts) {
          const aborted =
            error instanceof DOMException && error.name === "AbortError";
          throw new VinamiApiError(
            aborted ? "timeout" : "offline",
            aborted
              ? "The clinical service did not respond in time."
              : "The clinical service could not be reached.",
            null,
            null,
            true,
          );
        }
      } finally {
        clearTimeout(timeout);
      }
    }
    throw lastError;
  };
}
