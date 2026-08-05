-- OneTake 数据层 schema（P0 一次到位，见 prd.md 2.3 表 2-2）
-- 所有时间字段统一 TEXT，存 ISO8601 本地时间（datetime('now','localtime') 默认）

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,           -- pid，如 p20260805-001
    topic       TEXT NOT NULL,
    skill_id    TEXT,                       -- P6 起使用，P0 为 NULL
    status      TEXT NOT NULL DEFAULT 'created',
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS shots (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id),
    idx           INTEGER NOT NULL,          -- 分镜序号，从 1 开始
    duration      REAL,                      -- 目标时长（秒）；TTS 后回写真实音频时长
    visual_prompt TEXT,
    narration     TEXT,                      -- 台词
    status        TEXT NOT NULL DEFAULT 'created',
    UNIQUE (project_id, idx)
);

CREATE TABLE IF NOT EXISTS generations (
    id          TEXT PRIMARY KEY,
    idem_key    TEXT UNIQUE,                 -- sha256(model+prompt+params+tier)，P3 缓存用
    project_id  TEXT,                        -- 归属项目（单点验证时为 NULL）
    task_type   TEXT NOT NULL,               -- llm / image / video / tts / vl
    model       TEXT NOT NULL,
    tier        TEXT NOT NULL DEFAULT 'draft',
    prompt      TEXT,
    params      TEXT,                        -- JSON
    usage_json  TEXT,                        -- 各厂商原始用量字段
    unit_price  REAL,                        -- 单价（人民币，见 gateway/pricing.py）
    cost        REAL NOT NULL DEFAULT 0,     -- 本次调用成本（人民币元）
    latency_ms  INTEGER,
    status      TEXT NOT NULL DEFAULT 'succeeded',  -- succeeded / failed
    error       TEXT,
    file_path   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_generations_created ON generations (created_at);

CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL,              -- video_gen / batch_render / aggregate ...
    payload_json TEXT NOT NULL,
    priority     INTEGER NOT NULL DEFAULT 100,  -- 数值越小越先调度
    status       TEXT NOT NULL DEFAULT 'pending', -- pending/running/succeeded/failed/dead
    retry_count  INTEGER NOT NULL DEFAULT 0,
    max_retries  INTEGER NOT NULL DEFAULT 3,
    run_at       TEXT,
    worker_id    TEXT,
    idem_key     TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    finished_at  TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id        TEXT PRIMARY KEY,
    ts        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    trace_id  TEXT NOT NULL,
    kind      TEXT NOT NULL,                 -- generation / eval / publish / job
    ref_id    TEXT,                          -- 关联的 generation/job/shot id
    data_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_trace ON events (trace_id);

CREATE TABLE IF NOT EXISTS model_perf_daily (
    date         TEXT NOT NULL,
    model        TEXT NOT NULL,
    tier         TEXT NOT NULL,
    calls        INTEGER NOT NULL DEFAULT 0,
    success_rate REAL,
    avg_latency  REAL,
    total_cost   REAL NOT NULL DEFAULT 0,
    avg_quality  REAL,
    PRIMARY KEY (date, model, tier)
);

CREATE TABLE IF NOT EXISTS skills (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    version      TEXT NOT NULL,
    yaml_path    TEXT NOT NULL,
    metrics_json TEXT
);

CREATE TABLE IF NOT EXISTS memories (
    id         TEXT PRIMARY KEY,
    type       TEXT NOT NULL,                -- profile / episode
    content    TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
