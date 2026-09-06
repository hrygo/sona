import { describe, expect, it, vi } from "vitest";

import { VoiceRecordingMicLease } from "./voiceRecordingMicLease";

describe("VoiceRecordingMicLease", () => {
  it("keeps a restore request pending while control is unavailable", async () => {
    const lease = new VoiceRecordingMicLease();
    const sendCommand = vi.fn().mockResolvedValue(undefined);

    lease.begin(false);

    await expect(lease.restore(false, sendCommand)).rejects.toThrow("控制端连接中");
    expect(lease.needsRestore).toBe(true);
    expect(sendCommand).not.toHaveBeenCalled();

    await lease.restore(true, sendCommand);

    expect(sendCommand).toHaveBeenCalledWith({ cmd: "set_mic_muted", muted: false });
    expect(lease.needsRestore).toBe(false);
  });

  it("coalesces concurrent restore attempts into one command", async () => {
    const lease = new VoiceRecordingMicLease();
    let resolveCommand!: () => void;
    const sendCommand = vi.fn(() => new Promise<void>((resolve) => {
      resolveCommand = resolve;
    }));

    lease.begin(false);
    const first = lease.restore(true, sendCommand);
    const second = lease.restore(true, sendCommand);
    await Promise.resolve();
    resolveCommand();

    await Promise.all([first, second]);

    expect(sendCommand).toHaveBeenCalledTimes(1);
    expect(lease.needsRestore).toBe(false);
  });

  it("clears without sending when recording started with an already muted assistant", async () => {
    const lease = new VoiceRecordingMicLease();
    const sendCommand = vi.fn().mockResolvedValue(undefined);

    lease.begin(true);
    await lease.restore(false, sendCommand);

    expect(sendCommand).not.toHaveBeenCalled();
    expect(lease.needsRestore).toBe(false);
  });
});
