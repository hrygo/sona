import type { ControlCommand } from "../protocol";

type SetMicMutedCommand = Extract<ControlCommand, { cmd: "set_mic_muted" }>;

export type VoiceRecordingMicCommandSender = (command: SetMicMutedCommand) => Promise<unknown>;

/**
 * 记录需要临时静音助手麦克风的语音工作流租约，并保证恢复操作可重试且只发送一次。
 */
export class VoiceRecordingMicLease {
  private mutedBefore: boolean | null = null;
  private restorePending = false;
  private restoreInFlight: Promise<void> | null = null;

  get needsRestore(): boolean {
    return this.restorePending;
  }

  begin(mutedBefore: boolean): void {
    if (this.restorePending || this.restoreInFlight !== null) {
      throw new Error("上一段录音的麦克风恢复仍未完成");
    }
    this.mutedBefore = mutedBefore;
  }

  cancel(): void {
    if (this.restoreInFlight !== null) return;
    this.mutedBefore = null;
    this.restorePending = false;
  }

  restore(ready: boolean, sendCommand: VoiceRecordingMicCommandSender): Promise<void> {
    if (this.mutedBefore === null) return Promise.resolve();
    if (this.mutedBefore !== false) {
      this.clear();
      return Promise.resolve();
    }

    this.restorePending = true;
    if (this.restoreInFlight !== null) return this.restoreInFlight;
    if (!ready) {
      return Promise.reject(new Error("控制端连接中，无法恢复语音助手麦克风"));
    }

    const operation = Promise.resolve()
      .then(() => sendCommand({ cmd: "set_mic_muted", muted: false }))
      .then(() => {
        this.clear();
      })
      .finally(() => {
        this.restoreInFlight = null;
      });
    this.restoreInFlight = operation;
    return operation;
  }

  private clear(): void {
    this.mutedBefore = null;
    this.restorePending = false;
  }
}
