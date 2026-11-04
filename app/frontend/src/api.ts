export type Ok<T> = { ok: true; data: T; trace_id: string };
export type Err = {
  ok: false;
  error: { code: string; message: string; detail?: unknown; retryable?: boolean };
  trace_id: string;
};

export class ApiFailure extends Error {
  code: string;
  detail: unknown;
  traceId: string;
  retryable: boolean;

  constructor(body: Err) {
    super(body.error.message);
    this.name = "ApiFailure";
    this.code = body.error.code;
    this.detail = body.error.detail;
    this.traceId = body.trace_id;
    this.retryable = Boolean(body.error.retryable);
  }
}

const TIMEOUT_MS = 35000;

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(path, {
      ...init,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) }
    });
    const body = (await response.json()) as Ok<T> | Err;
    if (!body.ok) {
      throw new ApiFailure(body);
    }
    return body.data;
  } catch (error) {
    if (error instanceof ApiFailure) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("请求超时（35s），请检查后端是否在运行");
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

export type Health = {
  /** 语料总量（官方标注 + 同源层） */
  corpus_total?: number;
  app: string;
  db: string;
  model: string;
  knowledge: string;
  works: number;
  version: string;
  contract: string;
  snapshot_at?: string | null;
  snapshot_path?: string;
  platform?: string;
  dist?: string;
};

export type Meta = {
  works: number;
  periods: Record<string, number>;
  poets: number;
  tunes: number;
  source: string;
  snapshot: string;
  version: string;
  contract: string;
  /** 语料真实规模：Tier 1 官方基准 + Tier 2a 同源清词 + Tier 2b 同源宋词 */
  corpus_total?: number;
  tier2?: {
    works: number;
    poets: number;
    tunes: number;
    available: boolean;
    label: string;
    layers?: { tier: string; label: string; works: number }[];
  };
  tiers?: {
    tier: string;
    label: string;
    works: number;
    official: boolean;
    poets?: number;
    tunes?: number;
  }[];
};

export type WorkSummary = {
  /** Tier 1 是整数；Tier 2a 是 `t2_<hash>` 字符串 */
  id: number | string;
  title: string;
  tune: string;
  period: string;
  poet: string;
  lines: number;
  tier_label: string;
  source: string;
};

export type WorkList = {
  items: WorkSummary[];
  total: number;
  page: number;
  page_size: number;
  tier?: string;
  tier_counts?: { works: number; poets: number; tunes: number; available: boolean };
};

/** 单篇详情：/api/works/{id} 不带 lines，逐句统计在 /chars 与 /lines */
export type WorkDetail = {
  id: number | string;
  title: string;
  tune: string;
  period: string;
  poet: string;
  text: string;
  split_after_line: number;
  tier_label: string;
  source: string;
};

export type CharItem = {
  char: string;
  pinyin: string;
  tone: number;
  tone_name: string;
  ping_ze: "平" | "仄" | "未知";
  source: string;
  tier_label: string;
  candidates: string[];
  unknown_reason: string | null;
};

export type CharLine = {
  line_no: number;
  text: string;
  character_count: number;
  ping_count: number;
  ze_count: number;
  chars: CharItem[];
};

export type CharDoc = {
  work_id: number;
  title: string;
  tune: string;
  split_after_line: number;
  source_note: string;
  lines: CharLine[];
};

export type WorkLine = {
  line_no: number;
  text: string;
  character_count: number;
  ping_count: number;
  ze_count: number;
};

export type WorkLineDoc = {
  work_id: number;
  split_after_line: number;
  lines: WorkLine[];
};

export type TraceStep = {
  step: string;
  input: string;
  formula: string;
  output: Record<string, unknown>;
  evidence_ids: string[];
};

export type EvidenceSegment = {
  segment: string;
  quote: string;
  quote_line_no: number;
  candidates?: { line_no: number; text: string }[];
};

export type Evidence = {
  work_id: number;
  title: string;
  line_ids: number[];
  source: string;
  tier_label: string;
  quote?: string;
  quote_line_no?: number;
  candidates?: { line_no: number; text: string }[];
  segments?: EvidenceSegment[];
};

export type FieldMeta = {
  label: string;
  desc: string;
};

export type Narrative = {
  status: "ok" | "degraded" | "blocked";
  candidate: { draft: string; basis: string[] | string; verification: string | null; boundary: string | null } | null;
  checks: { structure: boolean; facts: boolean; boundary: boolean; label: boolean } | null;
  violations: Record<string, unknown> | null;
  model: string | null;
  latency_ms: number | null;
  fallback_reason: string | null;
};

export type AgentResult = {
  status: string;
  /** 确定性求解 / 确定性求解 + 模型解读 / 模型兜底（未验证） */
  mode: string;
  category: string;
  answer: string;
  fields: Record<string, unknown>;
  field_meta: Record<string, FieldMeta>;
  trace: TraceStep[];
  evidence: Evidence[];
  narrative: Narrative | null;
  source: string;
  fallback: { reason: string; rule_error: string; note: string } | null;
  boundary: string;
  solve_id?: string | null;
};

export type SolveResult = {
  status: "ok" | "insufficient_data" | "out_of_scope" | "degraded";
  category: string;
  answer: string;
  fields: Record<string, unknown>;
  /** 字段中文名与释义；与 fields 同键。缺字段时前端仍按原键显示 */
  field_meta?: Record<string, FieldMeta>;
  /** 作答来源：本地官方快照 / 官方 API 实时查询 */
  source?: string;
  /** 智能体模式下附带的模型解读 */
  narrative?: Narrative | null;
  /** 作答方式：确定性求解 / 确定性求解 + 模型解读 / 模型兜底（未验证） */
  mode?: string;
  /** 兜底信息：仅模型兜底时有 */
  fallback?: { reason: string; rule_error: string; note: string } | null;
  /** 边界声明 */
  boundary?: string;
  trace: TraceStep[];
  evidence: Evidence[];
  solve_id?: string;
};

/** 单独取回一次求解的推理链（/api/solve/{id}/trace）。 */
export type SolveTrace = {
  solve_id: string;
  trace: TraceStep[];
  fields: Record<string, unknown>;
  evidence: Evidence[];
  answer: string;
  category: string | null;
};

// —— 同调统计（检索板块）——
export type TuneStats = {
  tune: string;
  works: number;
  poets: number;
  sentence_counts: { sentences: number; works: number }[];
  ze_ratio_median: number | null;
  periods: Record<string, number>;
  tier_label: string;
  boundary?: string;
};

// —— 校订导出记录 ——
export type ExportRecord = {
  id: number;
  export_key: string;
  created_at: string;
  row_count: number;
  accepted_total: number;
  pending_total: number;
  filename: string;
};

export type ExportHistory = {
  items: ExportRecord[];
  total: number;
  closure_level: string;
};

export type AgentCapabilities = {
  deterministic_categories: { id: string; name: string }[];
  routing: string[];
  discipline: string[];
  narrative_available: boolean;
  model: string;
  second_model: string;
};

export type DemoQuestion = { category: string; question: string };

// —— 自由文本解析（板块 A，非官方口径）——
export type TuneCandidate = {
  form_id: string;
  tune: string;
  pattern: number[];
  score: number;
  confidence: string;
  reasons: string[];
  sample_work_ids: number[];
  sample_count: number;
  tier_label: string;
};

export type TuneResult = {
  candidates: TuneCandidate[];
  ambiguous: boolean;
  hint_form_count: number | null;
  target_pattern: number[];
  boundary: string;
  tier_label: string;
};

export type ParseResult = {
  parse_id: string;
  lines: CharLine[];
  pattern: number[];
  split_after: number;
  unknown: { line_no: number; char: string; reason: string }[];
  unknown_count: number;
  located: {
    tier: string;
    tier_label: string;
    work_id: number;
    title: string;
    tune: string;
    poet: string;
    official: boolean;
    hint: string;
    warning: string | null;
  } | null;
  tier_label: string;
  source_note: string;
  boundary: string;
  tune_hint: string;
  tunes: TuneResult;
};

export type DemoCase = {
  id: string;
  title: string;
  caption: string;
  target: string;
  mode: "parse" | "revision";
  text?: string;
  tune?: string;
  cached: Record<string, unknown>;
};

export type DemoCaseSet = {
  version: string;
  generated_at: string;
  source_sha256: string;
  note: string;
  cases: DemoCase[];
};

export type FactCard = {
  work: { id: number; title: string; tune: string; poet: string; period: string };
  facts: Record<string, string | number | number[]>;
  derived: Record<string, string | number>;
  quote_candidates: { line_no: number; text: string }[];
  source: string;
  tier_label: string;
};

export type SentimentBenchmarkItem = {
  id: string;
  text: string;
  work_id: number;
  poet: string;
  tune: string;
  period: string;
  line_no: number;
  layer: string;
  votes: string[];
  label: string;
  strength: number | null;
  label_source: string;
  inference_violation: boolean;
};

export type SentimentBenchmark = {
  version: string;
  spec_version: string;
  seed: number;
  sample_hash: string;
  built_at: string;
  labels: string[];
  rounds: { key: string; model: string; variant: string; order: string }[];
  arbiter: { key: string; model: string; variant: string; order: string };
  metrics: {
    benchmark_size: number;
    blind_size: number;
    agreement_rate: number;
    disputed: number;
    disputed_ratio: number;
    kappa_pairs: Record<string, number>;
    kappa_vs_majority: number;
    consistency_with_benchmark: number;
    blind_consistency: number;
    label_distribution: Record<string, number>;
    inference_violations: number;
  };
  disclosure: string;
  items: SentimentBenchmarkItem[];
};

export type SentimentCandidateResult = {
  status: "ok" | "blocked" | "degraded";
  candidate: { draft: string; basis: string[]; verification: string | null; boundary: string | null } | null;
  checks: Record<string, boolean> | null;
  violations: Record<string, unknown> | null;
  direction: string | null;
  strength: number | null;
  fact_card: {
    sentence: { id: string; text: string; line_no?: number };
    work: Record<string, unknown>;
    facts: Record<string, unknown>;
    source: string;
    tier_label: string;
  };
  labels: string[];
  model: string;
  latency_ms: number;
  fallback_reason: string | null;
};

export type TuneForm = {
  form_id: string;
  business_key: string;
  tune_id: string;
  pattern: number[];
  total_chars: number;
  work_ids: number[];
  source: string;
  tier_label: string;
  display: string;
};

export type TuneEntry = {
  tune_id: string;
  name: string;
  works: number;
  forms: TuneForm[];
  periods: Record<string, number>;
  poets: string[];
};

export type TuneOverview = {
  tunes: TuneEntry[];
  stats: {
    works: number;
    tunes: number;
    forms: number;
    multi_form_tunes: number;
    aliases: number;
    unmerged_aliases: number;
    missing_tune: number[];
    unique_tune_rate: number;
  };
  /** 同源层摘要：规模统计 + 热门词牌榜 + 全部词牌名（供筛选下拉） */
  corpus?: {
    available: boolean;
    works: number;
    tunes: number;
    forms: number;
    layers: { period: string; works: number }[];
    top: TuneEntry[];
    names?: string[];
    all?: { name: string; works: number }[];
    boundary: string;
  };
  boundary: string;
};

export type TuneReview = {
  size: number;
  seed: number;
  merged: number;
  unmerged: { alias: string; tune: string; rule: string }[];
  items: { alias: string; tune: string; rule: string; merged: boolean }[];
  note: string;
};

export type ProsodyCandidate = {
  form_id: string;
  author: string | null;
  sketch: string | null;
  total: number;
  sentences: number[];
  split_after: number | null;
  penalty: number;
  scores: Record<string, number>;
  confidence: string;
};

export type ProsodyResolve = {
  work_id: number;
  tune: string;
  title?: string;
  sentences: number[];
  candidates: ProsodyCandidate[];
  unique: boolean;
  allowed_for_alignment: boolean;
  form: ProsodyCandidate | null;
  per_line: string[];
  available: boolean;
  boundary: string;
};

export const api = {
  health: () => call<Health>("/api/health"),
  meta: () => call<Meta>("/api/meta"),
  works: (params: {
    page?: number;
    page_size?: number;
    q?: string;
    period?: string;
    tier?: "all" | "1" | "2a" | "2b";
  }) => {
    const search = new URLSearchParams();
    if (params.page) search.set("page", String(params.page));
    if (params.page_size) search.set("page_size", String(params.page_size));
    if (params.q) search.set("q", params.q);
    if (params.period) search.set("period", params.period);
    if (params.tier) search.set("tier", params.tier);
    return call<WorkList>("/api/works?" + search.toString());
  },
  chars: (id: number | string) => call<CharDoc>(`/api/works/${id}/chars`),
  lines: (id: number | string) => call<WorkLineDoc>(`/api/works/${id}/lines`),
  work: (id: number | string) => call<WorkDetail>(`/api/works/${id}`),
  solve: (question: string) =>
    call<SolveResult>("/api/solve", { method: "POST", body: JSON.stringify({ question }) }),
  agentAnswer: (question: string, narrative: boolean) =>
    call<AgentResult>("/api/agent/answer", {
      method: "POST",
      body: JSON.stringify({ question, narrative })
    }),
  agentCapabilities: () => call<AgentCapabilities>("/api/agent/capabilities"),
  demo: (category?: string) =>
    call<DemoQuestion>("/api/demo" + (category ? "?category=" + encodeURIComponent(category) : "")),
  demoCases: () => call<DemoCaseSet>("/api/demo/cases"),
  parse: (text: string, tune?: string) =>
    call<ParseResult>("/api/parse", {
      method: "POST",
      body: JSON.stringify({ text, tune: tune || "" })
    }),
  parseTunes: (parseId: string) => call<TuneResult>(`/api/parse/${parseId}/tunes`),
  factCard: (workId: number) => call<FactCard>(`/api/factcard/${workId}`),
  sentimentBenchmark: () => call<SentimentBenchmark>("/api/sentiment/benchmark"),
  tunes: () => call<TuneOverview>("/api/tunes"),
  tuneDetail: (tuneId: string, tier?: "official" | "corpus") =>
    call<TuneEntry>(`/api/tunes/${encodeURIComponent(tuneId)}${tier ? `?tier=${tier}` : ""}`),
  prosodyResolve: (workId: number) => call<ProsodyResolve>(`/api/prosody/resolve?work_id=${workId}`),
  tuneReview: () => call<TuneReview>("/api/tunes/review"),
  sentimentCandidate: (payload: { work_id?: number; line_no?: number; text?: string; model?: string }) =>
    call<SentimentCandidateResult>("/api/sentiment/candidate", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  solveTrace: (solveId: string) => call<SolveTrace>(`/api/solve/${solveId}/trace`),
  searchStats: (tune: string, tier: "1" | "2a" | "2b" = "1") =>
    call<TuneStats>(`/api/search/stats?tune=${encodeURIComponent(tune)}&tier=${tier}`),
  exportHistory: (limit = 50) => call<ExportHistory>(`/api/revision/exports?limit=${limit}`)
};
