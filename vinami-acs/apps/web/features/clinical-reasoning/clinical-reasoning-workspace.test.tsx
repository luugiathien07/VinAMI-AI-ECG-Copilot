import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { ClinicalReasoningWorkspaceView } from "./model";
import { ClinicalReasoningWorkspace } from "./clinical-reasoning-workspace";

const fixture: ClinicalReasoningWorkspaceView = {
  case_id: "30000000-0000-4000-8000-000000000001",
  case_version: 1,
  snapshot: {
    snapshot_id: "40000000-0000-4000-8000-000000000001",
    clinical_case_id: "30000000-0000-4000-8000-000000000001",
    case_version: 1,
    record_version: 1,
    created_at: "2026-07-15T08:00:00Z",
    sources: [
      {
        source_type: "clinical_case",
        source_id: "30000000-0000-4000-8000-000000000001",
        source_version: 1,
        relevant_at: "2026-07-15T08:00:00Z",
      },
    ],
    summaries: { triage: { chief_complaint: "Synthetic evidence" } },
    freshness: { clinical_case: "current" },
    missing_evidence: ["troponin"],
    technical_conflicts: [],
  },
  assessments: [
    {
      assessment_id: "50000000-0000-4000-8000-000000000001",
      snapshot_id: "40000000-0000-4000-8000-000000000001",
      revision_number: 1,
      record_version: 1,
      summary: "Physician-authored evidence assessment.",
      supporting_evidence: ["clinical_case:v1"],
      contradictory_evidence: [],
      uncertainty: "Additional evidence required.",
      additional_information_required: null,
      actor_id: "60000000-0000-4000-8000-000000000001",
      recorded_at: "2026-07-15T08:01:00Z",
      corrects_assessment_id: null,
      correction_reason: null,
    },
  ],
  decisions: [],
  safety_notice:
    "Research-use prototype. Evidence synthesis does not diagnose ACS or AMI.",
};

vi.mock("@web/shared/api/browser", () => ({
  createBrowserApiClient: () => ({
    getClinicalReasoningWorkspace: vi.fn().mockResolvedValue(fixture),
    createEvidenceSnapshot: vi.fn(),
    recordClinicalAssessment: vi.fn(),
    correctClinicalAssessment: vi.fn(),
    recordClinicalDecision: vi.fn(),
    correctClinicalDecision: vi.fn(),
    revokeClinicalDecision: vi.fn(),
    getRiskScores: vi.fn().mockResolvedValue([]),
    recordHeartScore: vi.fn(),
    recordGraceScore: vi.fn(),
  }),
}));

function Wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {children}
    </QueryClientProvider>
  );
}

describe("ClinicalReasoningWorkspace", () => {
  it("presents evidence limits and requires a physician confirmation dialog", async () => {
    render(
      <Wrapper>
        <ClinicalReasoningWorkspace
          caseId={fixture.case_id}
          initialValue={fixture}
        />
      </Wrapper>,
    );
    expect(
      screen.getByRole("heading", { name: "Tổng hợp bằng chứng lâm sàng" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Thiếu hoặc chưa rõ")).toBeInTheDocument();
    expect(screen.getByText("Troponin")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Kế hoạch đã ghi nhận"), {
      target: { value: "request_cath_lab_review" },
    });
    fireEvent.change(screen.getByLabelText("Lý do"), {
      target: { value: "Physician specialist review request." },
    });
    fireEvent.change(screen.getAllByLabelText("Điểm chưa chắc chắn")[1]!, {
      target: { value: "Specialist review remains necessary." },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Rà soát và xác nhận" }),
    );
    expect(
      await screen.findByRole("dialog", {
        name: "Xác nhận quyết định của bác sĩ",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Xác nhận yêu cầu bác sĩ xem xét can thiệp",
      }),
    ).toBeInTheDocument();
  });
});
