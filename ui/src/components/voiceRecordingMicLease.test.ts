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

it("waits for pending mute before restoring on close and coalesces acquisition", async () => {
  const lease = new VoiceRecordingMicLease();
  let complete!: () => void;
  const events: boolean[] = [];
  const send = vi.fn((command: { muted: boolean }) => {
    events.push(command.muted);
    return command.muted ? new Promise<void>((resolve) => { complete = resolve; }) : Promise.resolve();
  });
  const first = lease.acquire(true, false, send);
  expect(lease.acquire(true, false, send)).toBe(first);
  const restored = lease.restore(true, send);
  await Promise.resolve(); expect(events).toEqual([true]);
  complete(); await restored;
  expect(events).toEqual([true, false]); expect(lease.needsRestore).toBe(false);
});
it("retries failed acquisition without losing the original mute state", async () => {
  const lease = new VoiceRecordingMicLease();
  const send = vi.fn().mockRejectedValueOnce(new Error("disconnected")).mockResolvedValue(undefined);
  await expect(lease.acquire(false, false, send)).rejects.toThrow("控制连接未就绪");
  expect(send).not.toHaveBeenCalled();
  await expect(lease.acquire(true, false, send)).rejects.toThrow("disconnected");
  await lease.acquire(true, true, send); await lease.restore(true, send);
  expect(send.mock.calls.map(([command]) => command.muted)).toEqual([true, true, false]);
});
it("preserves an initially muted user through acquire and restore", async () => {
  const lease = new VoiceRecordingMicLease(); const send = vi.fn();
  await lease.acquire(true, true, send); await lease.restore(true, send);
  expect(send).not.toHaveBeenCalled();
});
