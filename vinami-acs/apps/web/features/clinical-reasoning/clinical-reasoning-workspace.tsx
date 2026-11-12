"use client";

import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { AlertTriangle, FilePenLine } from "lucide-react";
import { createBrowserApiClient } from "@web/shared/api/browser";
import { formatTimestamp, shortReference } from "@web/shared/formatting";
import {
  viDecisionType,
  viEvidence,
  viSafetyNotice,
  viStatus,
} from "@web/shared/locale/vi";
import { EvidenceStatusSummary } from "@web/features/evidence/evidence-status-summary";
import { RiskScoresPanel } from "./risk-scores-panel";
import {
  clinicalReasoningWorkspaceSchema,
  type AssessmentView,
  type ClinicalReasoningWorkspaceView,
  type DecisionView,
} from "./model";

const decisionLabels = {
  continue_observation: "Tiếp tục theo dõi",
  request_repeat_ecg: "Yêu cầu lặp lại ECG",
  request_repeat_troponin: "Yêu cầu lặp lại Troponin",
  request_additional_information: "Yêu cầu bổ sung thông tin",
  request_cardiology_review: "Yêu cầu hội chẩn Tim mạch",
  request_cath_lab_review: "Bác sĩ yêu cầu xem xét can thiệp",
  maintain_current_plan: "Duy trì kế hoạch hiện tại",
  other_documented_plan: "Kế hoạch khác đã ghi nhận",
} as const;
type DecisionCode = keyof typeof decisionLabels;

type AssessmentForm = {
  summary: string;
  uncertainty: string;
  additionalInformation: string;
  correctionReason: string;
};
type DecisionForm = {
  decisionType: DecisionCode;
  rationale: string;
  uncertainty: string;
  correctionReason: string;
};

export function ClinicalReasoningWorkspace({
  caseId,
  initialValue,
}: {
  caseId: string;
  initialValue: ClinicalReasoningWorkspaceView;
}) {
  const api = useMemo(() => createBrowserApiClient(), []);
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["clinical-reasoning", caseId],
    initialData: initialValue,
    queryFn: async () =>
      clinicalReasoningWorkspaceSchema.parse(
        await api.getClinicalReasoningWorkspace(caseId),
      ),
  });
  const value = query.data;
  const snapshot = value.snapshot;
  const [supporting, setSupporting] = useState<string[]>([]);
  const [contradictory, setContradictory] = useState<string[]>([]);
  const [assessmentCorrection, setAssessmentCorrection] =
    useState<AssessmentView | null>(null);
  const [decisionCorrection, setDecisionCorrection] =
    useState<DecisionView | null>(null);
  const [pendingDecision, setPendingDecision] = useState<DecisionForm | null>(
    null,
  );
  const [revokeDecision, setRevokeDecision] = useState<DecisionView | null>(
    null,
  );
  const [revocationReason, setRevocationReason] = useState("");
  const assessmentForm = useForm<AssessmentForm>();
  const decisionForm = useForm<DecisionForm>({
    defaultValues: { decisionType: "continue_observation" },
  });
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ["clinical-reasoning", caseId] });

  const snapshotMutation = useMutation({
    mutationFn: () =>
      api.createEvidenceSnapshot(
        caseId,
        { expected_case_version: value.case_version },
        crypto.randomUUID(),
      ),
    onSuccess: refresh,
  });
  const assessmentMutation = useMutation({
    mutationFn: async (form: AssessmentForm) => {
      if (!snapshot)
        throw new Error("Cần tạo snapshot bằng chứng hiện hành trước.");
      const base = {
        snapshot_id: snapshot.snapshot_id,
        expected_snapshot_version: snapshot.record_version,
        summary: form.summary,
        supporting_evidence: supporting,
        contradictory_evidence: contradictory,
        uncertainty: form.uncertainty,
        additional_information_required: form.additionalInformation || null,
      };
      if (assessmentCorrection) {
        return api.correctClinicalAssessment(
          caseId,
          assessmentCorrection.assessment_id,
          {
            ...base,
            expected_assessment_version: assessmentCorrection.record_version,
            correction_reason: form.correctionReason,
          },
          crypto.randomUUID(),
        );
      }
      return api.recordClinicalAssessment(caseId, base, crypto.randomUUID());
    },
    onSuccess: () => {
      assessmentForm.reset();
      setAssessmentCorrection(null);
      refresh();
    },
  });
  const decisionMutation = useMutation({
    mutationFn: async (form: DecisionForm) => {
      if (!snapshot || !value.assessments[0])
        throw new Error("Cần có snapshot hiện hành và đánh giá của bác sĩ.");
      const assessment = value.assessments[0];
      const base = {
        snapshot_id: snapshot.snapshot_id,
        assessment_id: assessment.assessment_id,
        expected_snapshot_version: snapshot.record_version,
        expected_assessment_version: assessment.record_version,
        decision_type: form.decisionType,
        rationale: form.rationale,
        uncertainty: form.uncertainty,
        evidence_references: [...supporting, ...contradictory],
      };
      if (decisionCorrection) {
        return api.correctClinicalDecision(
          caseId,
          decisionCorrection.decision_id,
          {
            ...base,
            expected_decision_version: decisionCorrection.record_version,
            correction_reason: form.correctionReason,
          },
          crypto.randomUUID(),
        );
      }
      return api.recordClinicalDecision(caseId, base, crypto.randomUUID());
    },
    onSuccess: () => {
      setPendingDecision(null);
      setDecisionCorrection(null);
      decisionForm.reset({ decisionType: "continue_observation" });
      refresh();
    },
  });
  const revokeMutation = useMutation({
    mutationFn: async () => {
      if (!snapshot || !revokeDecision)
        throw new Error("Cần có quyết định hiện hành và snapshot bằng chứng.");
      const assessment = value.assessments.find(
        (item) => item.assessment_id === revokeDecision.assessment_id,
      );
      if (!assessment)
        throw new Error("Không tìm thấy đánh giá bác sĩ được liên kết.");
      return api.revokeClinicalDecision(
        caseId,
        revokeDecision.decision_id,
        {
          snapshot_id: snapshot.snapshot_id,
          assessment_id: assessment.assessment_id,
          expected_snapshot_version: snapshot.record_version,
          expected_assessment_version: assessment.record_version,
          expected_decision_version: revokeDecision.record_version,
          decision_type: revokeDecision.decision_type as DecisionCode,
          rationale: revokeDecision.rationale,
          uncertainty: revokeDecision.uncertainty,
          evidence_references: revokeDecision.evidence_references,
          revocation_reason: revocationReason,
          operation: "revoke",
        },
        crypto.randomUUID(),
      );
    },
    onSuccess: () => {
      setRevokeDecision(null);
      setRevocationReason("");
      refresh();
    },
  });

  const sourceOptions = snapshot?.sources ?? [];
  return (
    <main className="vm-reasoning-workspace">
      <section
        className="vm-card vm-reasoning-hero"
        aria-labelledby="reasoning-title"
      >
        <div className="vm-card__body">
          <div>
            <p className="vm-eyebrow">
              Không gian bác sĩ - nguyên mẫu nghiên cứu
            </p>
            <h2 id="reasoning-title">Tổng hợp bằng chứng lâm sàng</h2>
            <p>{viSafetyNotice(value.safety_notice)}</p>
          </div>
          <button
            className="vm-button vm-button--primary"
            disabled={snapshotMutation.isPending}
            onClick={() => snapshotMutation.mutate()}
          >
            {snapshotMutation.isPending
              ? "Đang tạo snapshot..."
              : "Tạo snapshot hiện hành"}
          </button>
        </div>
      </section>

      <div className="vm-reasoning-grid">
        <section className="vm-card">
          <div className="vm-card__body">
            <h3>Định danh ca</h3>
            <dl className="vm-reasoning-facts">
              <dt>Ca</dt>
              <dd>
                <strong>{shortReference(caseId, "CASE")}</strong>
                <code className="vm-raw-identifier">{caseId}</code>
              </dd>
              <dt>Phiên bản ca</dt>
              <dd>v{value.case_version}</dd>
              <dt>Snapshot</dt>
              <dd>
                {snapshot
                  ? `v${snapshot.record_version} - ${formatTimestamp(snapshot.created_at)}`
                  : "Chưa tạo"}
              </dd>
            </dl>
          </div>
        </section>
        <section className="vm-card">
          <div className="vm-card__body">
            {!snapshot && <p>Tạo snapshot để xem bằng chứng đã lưu.</p>}
            {snapshot && (
              <EvidenceStatusSummary
                title="Giới hạn bằng chứng"
                status={
                  snapshot.missing_evidence.length ||
                  snapshot.technical_conflicts.length
                    ? "incomplete"
                    : "current"
                }
                freshness={snapshot.freshness}
                missingEvidence={snapshot.missing_evidence}
                technicalLimits={snapshot.technical_conflicts}
              />
            )}
          </div>
        </section>
      </div>

      <div className="vm-reasoning-grid vm-reasoning-grid--three">
        <EvidencePanel
          title="Tiếp nhận và sinh hiệu"
          value={{
            triage: snapshot?.summaries.triage,
            vitals: snapshot?.summaries.vitals,
          }}
          empty="Snapshot hiện hành chưa có nội dung tóm tắt."
        />
        <EvidencePanel
          title="Tóm tắt ECG"
          value={snapshot?.summaries.ecg}
          empty="Snapshot hiện hành chưa có nội dung tóm tắt."
        />
        <EvidencePanel
          title="Tóm tắt Troponin"
          value={snapshot?.summaries.troponin}
          empty="Snapshot hiện hành chưa có nội dung tóm tắt."
        />
      </div>

      <section className="vm-card">
        <div className="vm-card__body">
          <h3>Chọn bằng chứng để bác sĩ ghi nhận</h3>
          <p>
            Các lựa chọn chỉ là tham chiếu tới bản ghi đã lưu. Chúng không tạo
            kết luận lâm sàng.
          </p>
          <div className="vm-evidence-picker">
            {sourceOptions.map((source) => {
              const key = `${source.source_type}:${source.source_id}:v${source.source_version}`;
              return (
                <label key={key}>
                  <input
                    type="checkbox"
                    checked={supporting.includes(key)}
                    onChange={(event) =>
                      setSupporting(
                        toggle(supporting, key, event.target.checked),
                      )
                    }
                  />{" "}
                  Ủng hộ - {viEvidence(source.source_type)} v
                  {source.source_version}
                </label>
              );
            })}
            {sourceOptions.map((source) => {
              const key = `${source.source_type}:${source.source_id}:v${source.source_version}`;
              return (
                <label key={`contra-${key}`}>
                  <input
                    type="checkbox"
                    checked={contradictory.includes(key)}
                    onChange={(event) =>
                      setContradictory(
                        toggle(contradictory, key, event.target.checked),
                      )
                    }
                  />{" "}
                  Mâu thuẫn - {viEvidence(source.source_type)} v
                  {source.source_version}
                </label>
              );
            })}
          </div>
        </div>
      </section>

      <div className="vm-reasoning-grid">
        <section className="vm-card">
          <div className="vm-card__body">
            <h3>
              <FilePenLine size={18} /> Đánh giá của bác sĩ
            </h3>
            <form
              onSubmit={assessmentForm.handleSubmit((form) =>
                assessmentMutation.mutate(form),
              )}
              className="vm-form-stack"
            >
              <label>
                Tóm tắt đánh giá
                <textarea
                  {...assessmentForm.register("summary", { required: true })}
                />
              </label>
              <label>
                Điểm chưa chắc chắn
                <textarea
                  {...assessmentForm.register("uncertainty", {
                    required: true,
                  })}
                />
              </label>
              <label>
                Thông tin bổ sung cần có
                <textarea
                  {...assessmentForm.register("additionalInformation")}
                />
              </label>
              {assessmentCorrection && (
                <label>
                  Lý do chỉnh sửa
                  <textarea
                    {...assessmentForm.register("correctionReason", {
                      required: true,
                    })}
                  />
                </label>
              )}
              <button
                className="vm-button vm-button--primary"
                disabled={!snapshot || assessmentMutation.isPending}
              >
                {assessmentCorrection
                  ? "Ghi nối tiếp chỉnh sửa đánh giá"
                  : "Ghi nhận đánh giá bác sĩ"}
              </button>
            </form>
            <MutationError error={assessmentMutation.error} />
          </div>
        </section>
        <History
          title="Lịch sử phiên bản đánh giá"
          empty="Chưa ghi nhận đánh giá của bác sĩ."
          items={value.assessments.map((item) => ({
            id: item.assessment_id,
            heading: `Phiên bản ${item.revision_number}`,
            body: item.summary,
            meta: formatTimestamp(item.recorded_at),
            action: (
              <button
                className="vm-button"
                onClick={() => {
                  setAssessmentCorrection(item);
                  assessmentForm.reset({
                    summary: item.summary,
                    uncertainty: item.uncertainty,
                    additionalInformation:
                      item.additional_information_required ?? "",
                    correctionReason: "",
                  });
                }}
              >
                Tạo chỉnh sửa
              </button>
            ),
          }))}
        />
      </div>

      <div className="vm-reasoning-grid">
        <section className="vm-card">
          <div className="vm-card__body">
            <h3>Quyết định của bác sĩ</h3>
            <form
              onSubmit={decisionForm.handleSubmit(setPendingDecision)}
              className="vm-form-stack"
            >
              <label>
                Kế hoạch đã ghi nhận
                <select
                  {...decisionForm.register("decisionType", { required: true })}
                >
                  {Object.entries(decisionLabels).map(([code, label]) => (
                    <option key={code} value={code}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Lý do
                <textarea
                  {...decisionForm.register("rationale", { required: true })}
                />
              </label>
              <label>
                Điểm chưa chắc chắn
                <textarea
                  {...decisionForm.register("uncertainty", { required: true })}
                />
              </label>
              {decisionCorrection && (
                <label>
                  Lý do chỉnh sửa
                  <textarea
                    {...decisionForm.register("correctionReason", {
                      required: true,
                    })}
                  />
                </label>
              )}
              <button
                className="vm-button vm-button--primary"
                disabled={!snapshot || !value.assessments.length}
              >
                Rà soát và xác nhận
              </button>
            </form>
            <MutationError error={decisionMutation.error} />
          </div>
        </section>
        <History
          title="Lịch sử chỉnh sửa và thu hồi quyết định"
          empty="Chưa ghi nhận quyết định của bác sĩ."
          items={value.decisions.map((item) => ({
            id: item.decision_id,
            heading: `${decisionLabel(item.decision_type)} - ${viStatus(item.status)}`,
            body: item.rationale,
            meta: `Phiên bản ${item.revision_number} - ${item.effective ? "hiệu lực" : "không hiệu lực"}`,
            action: item.effective ? (
              <div className="vm-inline-actions">
                <button
                  className="vm-button"
                  onClick={() => {
                    setDecisionCorrection(item);
                    decisionForm.reset({
                      decisionType: item.decision_type as DecisionCode,
                      rationale: item.rationale,
                      uncertainty: item.uncertainty,
                      correctionReason: "",
                    });
                  }}
                >
                  Tạo chỉnh sửa
                </button>
                <button
                  className="vm-button vm-button--danger"
                  onClick={() => setRevokeDecision(item)}
                >
                  Thu hồi
                </button>
              </div>
            ) : undefined,
          }))}
        />
      </div>

      <RiskScoresPanel caseId={caseId} />

      <nav
        className="vm-card vm-reasoning-links"
        aria-label="Không gian liên quan của ca"
      >
        <Link href={`/cases/${caseId}/ecg`}>Không gian ECG</Link>
        <Link href={`/cases/${caseId}/troponin`}>Không gian Troponin</Link>
        <Link href={`/cases/${caseId}/timeline`}>Dòng thời gian</Link>
        <Link href={`/cases/${caseId}/audit`}>Nhật ký kiểm chứng</Link>
      </nav>

      {pendingDecision && (
        <ConfirmationDialog
          title="Xác nhận quyết định của bác sĩ"
          confirmLabel={
            pendingDecision.decisionType === "request_cath_lab_review"
              ? "Xác nhận yêu cầu bác sĩ xem xét can thiệp"
              : "Ghi nhận quyết định của bác sĩ"
          }
          busy={decisionMutation.isPending}
          onCancel={() => setPendingDecision(null)}
          onConfirm={() => decisionMutation.mutate(pendingDecision)}
        >
          <p>
            Bản ghi bất biến này được liên kết với snapshot bằng chứng hiện hành
            và đánh giá của bác sĩ.
          </p>
          <strong>{decisionLabels[pendingDecision.decisionType]}</strong>
          <p>
            Hộp thoại này không thực hiện chuyển trạng thái quy trình hoặc hành
            động điều trị.
          </p>
        </ConfirmationDialog>
      )}
      {revokeDecision && (
        <ConfirmationDialog
          title="Thu hồi quyết định của bác sĩ"
          confirmLabel="Ghi nối tiếp thu hồi"
          busy={revokeMutation.isPending}
          onCancel={() => setRevokeDecision(null)}
          onConfirm={() => revokeMutation.mutate()}
        >
          <label>
            Lý do thu hồi
            <textarea
              value={revocationReason}
              onChange={(event) => setRevocationReason(event.target.value)}
            />
          </label>
          <p>
            Bản ghi trước vẫn kiểm chứng được và sẽ không còn ủy quyền cho
            chuyển trạng thái quy trình về sau.
          </p>
        </ConfirmationDialog>
      )}
    </main>
  );
}

function EvidencePanel({
  title,
  value,
  empty,
}: {
  title: string;
  value: unknown;
  empty: string;
}) {
  return (
    <section className="vm-card">
      <div className="vm-card__body">
        <h3>{title}</h3>
        {value ? (
          <pre className="vm-clinical-summary">
            {JSON.stringify(value, null, 2)}
          </pre>
        ) : (
          <p>{empty}</p>
        )}
      </div>
    </section>
  );
}

function History({
  title,
  empty,
  items,
}: {
  title: string;
  empty: string;
  items: Array<{
    id: string;
    heading: string;
    body: string;
    meta: string;
    action?: React.ReactNode;
  }>;
}) {
  return (
    <section className="vm-card">
      <div className="vm-card__body">
        <h3>{title}</h3>
        {!items.length && <p>{empty}</p>}
        {items.map((item) => (
          <article className="vm-history-row" key={item.id}>
            <div>
              <strong>{item.heading}</strong>
              <p>{item.body}</p>
              <small>{item.meta}</small>
            </div>
            {item.action}
          </article>
        ))}
      </div>
    </section>
  );
}

function ConfirmationDialog({
  title,
  confirmLabel,
  busy,
  onCancel,
  onConfirm,
  children,
}: {
  title: string;
  confirmLabel: string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="vm-dialog-backdrop" role="presentation">
      <div
        className="vm-confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirmation-title"
      >
        <h3 id="confirmation-title">{title}</h3>
        {children}
        <div className="vm-inline-actions">
          <button className="vm-button" onClick={onCancel}>
            Hủy
          </button>
          <button
            className="vm-button vm-button--primary"
            disabled={busy}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

function MutationError({ error }: { error: Error | null }) {
  return error ? (
    <p className="vm-warning" role="alert">
      <AlertTriangle size={16} /> {error.message}
    </p>
  ) : null;
}

function toggle(values: string[], value: string, checked: boolean) {
  return checked
    ? [...new Set([...values, value])]
    : values.filter((item) => item !== value);
}

function decisionLabel(code: string) {
  return code in decisionLabels
    ? decisionLabels[code as DecisionCode]
    : viDecisionType(code);
}
