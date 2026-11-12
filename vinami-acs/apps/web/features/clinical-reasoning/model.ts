import { z } from "zod";

const sourceSchema = z.object({
  source_type: z.string(),
  source_id: z.string().uuid(),
  source_version: z.number().int().positive(),
  relevant_at: z.string(),
});

const snapshotSchema = z.object({
  snapshot_id: z.string().uuid(),
  clinical_case_id: z.string().uuid(),
  case_version: z.number().int().positive(),
  record_version: z.number().int().positive(),
  created_at: z.string(),
  sources: z.array(sourceSchema),
  summaries: z.record(z.string(), z.unknown()),
  freshness: z.record(z.string(), z.string()),
  missing_evidence: z.array(z.string()),
  technical_conflicts: z.array(z.string()),
});

const assessmentSchema = z.object({
  assessment_id: z.string().uuid(),
  snapshot_id: z.string().uuid(),
  revision_number: z.number().int().positive(),
  record_version: z.number().int().positive(),
  summary: z.string(),
  supporting_evidence: z.array(z.string()),
  contradictory_evidence: z.array(z.string()),
  uncertainty: z.string(),
  additional_information_required: z.string().nullable(),
  actor_id: z.string().uuid(),
  recorded_at: z.string(),
  corrects_assessment_id: z.string().uuid().nullable(),
  correction_reason: z.string().nullable(),
});

const decisionSchema = z.object({
  decision_id: z.string().uuid(),
  snapshot_id: z.string().uuid(),
  assessment_id: z.string().uuid(),
  decision_type: z.string(),
  status: z.string(),
  revision_number: z.number().int().positive(),
  record_version: z.number().int().positive(),
  rationale: z.string(),
  uncertainty: z.string(),
  evidence_references: z.array(z.string()),
  recorded_at: z.string(),
  corrects_decision_id: z.string().uuid().nullable(),
  correction_reason: z.string().nullable(),
  workflow_authorization_id: z.string().uuid().nullable(),
  effective: z.boolean(),
});

export const clinicalReasoningWorkspaceSchema = z.object({
  case_id: z.string().uuid(),
  case_version: z.number().int().positive(),
  snapshot: snapshotSchema.nullable(),
  assessments: z.array(assessmentSchema),
  decisions: z.array(decisionSchema),
  safety_notice: z.string(),
});

export type ClinicalReasoningWorkspaceView = z.infer<
  typeof clinicalReasoningWorkspaceSchema
>;
export type AssessmentView = z.infer<typeof assessmentSchema>;
export type DecisionView = z.infer<typeof decisionSchema>;

// TODO: replace with generated types from packages/contracts once `pnpm api:generate`
// is rerun against a running API that includes the risk-scores endpoints.
export const riskScoreSchema = z.object({
  risk_score_id: z.string().uuid().nullable(),
  name: z.string(),
  value: z.number(),
  category: z.string(),
  algorithm_version: z.string(),
  calculated_at: z.string(),
});
export const riskScoreListSchema = z.array(riskScoreSchema);
export type RiskScoreView = z.infer<typeof riskScoreSchema>;
