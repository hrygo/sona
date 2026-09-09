import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";
import type { VoiceQualityReport } from "../contracts/voiceContract";
import { VoiceQualityCard } from "./VoiceQualityCard";

let root: Root;
let container: HTMLDivElement;

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const baseReport: VoiceQualityReport = {
  policy_version: "voice_quality_v1",
  status: "pass",
  run_id: "vqr-1",
  tested_at: "2026-09-09T10:00:00Z",
  synthesis: { probe_count: 3, successful_probe_count: 3, deterministic: true },
  failure_codes: [],
};

it("renders a passing report with probe evidence", () => {
  act(() => root.render(createElement(VoiceQualityCard, { report: baseReport, title: "音色验收" })));

  expect(container.textContent).toContain("质量良好");
  expect(container.textContent).toContain("3 / 3");
  expect(container.textContent).toContain("个测试通过");
  expect(container.textContent).toContain("确定性输出");
  expect(container.querySelector("[aria-live='polite']")).not.toBeNull();
});

it("renders warning and actionable failure codes", () => {
  act(() => root.render(createElement(VoiceQualityCard, {
      title: "参考录音检查",
      report: {
        ...baseReport,
        status: "warn",
        failure_codes: ["low_snr", "high_noise_floor"],
      },
      onRerecord: () => undefined,
    })));

  expect(container.textContent).toContain("存在风险");
  expect(container.textContent).toContain("环境噪声偏高");
  expect(container.textContent).toContain("信噪比偏低");
  expect(container.querySelector("button")).not.toBeNull();
});

it("does not claim quality when the server report is unavailable", () => {
  act(() => root.render(createElement(VoiceQualityCard, { title: "服务端质量检查" })));

  expect(container.textContent).toContain("尚未评估");
  expect(container.textContent).not.toContain("质量良好");
  expect(container.textContent).toContain("服务端尚未返回质量报告");
});

it("does not render zero probe counts for a reference-only report", () => {
  act(() => root.render(createElement(VoiceQualityCard, {
    title: "参考音频验收",
    evidence: "reference",
    report: {
      ...baseReport,
      synthesis: {
        probe_count: 0,
        successful_probe_count: 0,
        peak_dbfs: 0,
        deterministic: false,
      },
      reference: {
        duration_seconds: 8,
        estimated_snr_db: 28,
        speech_active_ratio: 0.8,
      },
    },
  })));

  expect(container.textContent).toContain("参考音频");
  expect(container.textContent).toContain("8.0 s");
  expect(container.textContent).not.toContain("0 / 0");
});

it("does not mark a zero-probe report as passed synthesis quality", () => {
  act(() => root.render(createElement(VoiceQualityCard, {
    title: "服务端质量验收",
    evidence: "synthesis",
    report: {
      ...baseReport,
      synthesis: { probe_count: 0, successful_probe_count: 0 },
    },
  })));

  expect(container.textContent).toContain("尚未评估");
  expect(container.textContent).not.toContain("质量良好");
  expect(container.textContent).not.toContain("0 / 0");
});
