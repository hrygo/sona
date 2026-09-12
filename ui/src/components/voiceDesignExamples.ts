/** Editable text starters, not audio samples or guaranteed speaker identities. */
export interface VoiceDesignExample {
  readonly id: string;
  readonly title: string;
  readonly name: string;
  readonly use: string;
  readonly traits: string;
  readonly instruction: string;
}

export const DESIGN_REFERENCE_TEXT = "你好，欢迎收听今天的分享。我们先把复杂的问题讲清楚，再用一个简单的例子说明。无论是工作中的新想法，还是生活里的小发现，都值得认真倾听。";

export const VOICE_DESIGN_EXAMPLES: readonly VoiceDesignExample[] = [
  { id: "warm", title: "温柔知性", name: "知性女声", use: "日常助手 · 轻松交流", traits: "成年女声 / 温暖清晰 / 自然",
    instruction: "成年女性普通话声音，音色温暖清澈，中音区自然舒展，吐字清楚。语速适中，短句之间自然停顿，语气亲切平和，如同面对面交谈。情绪克制，不使用夸张的气声或刻意卖萌的语调。" },
  { id: "technical", title: "技术讲解", name: "技术讲解男声", use: "技术分享 · 教程旁白", traits: "成年男声 / 清晰稳健 / 条理分明",
    instruction: "成年男性普通话声音，中低音区清晰结实，音色温润，发音准确。语速适中略慢，术语和数字读清楚，按语义自然停顿，重点词轻微强调。语气耐心、客观，适合技术讲解，不用演讲式高声调。" },
  { id: "news", title: "简报播报", name: "清晰简报女声", use: "每日简报 · 会议摘要", traits: "成熟女声 / 干净明亮 / 简洁",
    instruction: "成熟女性普通话声音，音色干净明亮，咬字清楚，句尾收束自然。语速中等、节奏稳定，信息之间留短暂停顿，数字和专有名词清晰。整体语气客观从容，不拖腔，不刻意煽情。" },
  { id: "reading", title: "有声阅读", name: "温润阅读男声", use: "文章朗读 · 故事旁白", traits: "成熟男声 / 温润醇厚 / 从容",
    instruction: "成熟男性普通话声音，音色温润醇厚，低音自然、不压喉。语速舒缓但连贯，依照标点和句意停顿，叙述平稳，情感起伏轻微。适合散文和故事旁白，不模仿人物对白，不使用戏剧化哭腔。" },
  { id: "bright", title: "明快陪伴", name: "明快青年女声", use: "生活提醒 · 趣味解说", traits: "青年女声 / 明亮轻盈 / 友好",
    instruction: "成年年轻女性普通话声音，音色明亮轻盈，咬字清晰。语速轻快但不赶句，停顿自然，语气积极友好，重音适度。保持真实日常交流的感觉，不尖叫、不夸张升调，不使用儿童化声音。" },
  { id: "story", title: "京味叙事", name: "京味说书男声", use: "文化趣谈 · 叙事表达", traits: "成熟男声 / 轻微京腔 / 有节奏",
    instruction: "成熟男性声音，以清晰普通话为基础，带轻微自然的北京口音。音色厚实有颗粒感，语速适中，叙事有节奏，重点字稍有顿挫。语气幽默但克制，儿化音适量，不夸张模仿特定艺人，不使用舞台喊腔。" },
];

/** Runtime IDs are opaque; retain one per attempt for safe timeout/retry recovery. */
export function newDesignVoiceId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return `design_${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`;
}

export function validateDesignText(instruction: string, reference: string, seed: number): string | null {
  const size = Array.from(reference.trim()).length;
  if (!instruction.trim()) return "请先选择一个示例，或填写声音描述";
  if (Array.from(instruction.trim()).length > 1000) return "声音描述请控制在 1000 字以内";
  if (size < 20 || size > 240) return "参考朗读文本需要 20–240 字；它是要读出的内容，不是声音描述";
  if (!/[\p{L}\p{N}]/u.test(reference)) return "参考朗读文本不能只有标点或符号";
  if (!Number.isInteger(seed) || seed < 0 || seed > 2 ** 32 - 1) return "随机种子需要是 0–4294967295 的整数";
  return null;
}
