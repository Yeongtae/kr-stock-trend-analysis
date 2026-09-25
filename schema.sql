PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    end_date TEXT NOT NULL,
    week_start TEXT NOT NULL,
    week_end_trade TEXT,
    recent2_start TEXT,
    recent2_end TEXT,
    source_note TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS universes (
    universe TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    rank INTEGER NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (universe, snapshot_date, code)
);

CREATE TABLE IF NOT EXISTS daily_prices (
    code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    name TEXT,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume INTEGER,
    source TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    PRIMARY KEY (code, trade_date)
);

CREATE TABLE IF NOT EXISTS return_rankings (
    run_id TEXT NOT NULL,
    period_type TEXT NOT NULL,
    universe TEXT NOT NULL,
    rank INTEGER NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    start_open REAL NOT NULL,
    end_close REAL NOT NULL,
    return_pct REAL NOT NULL,
    direction TEXT NOT NULL,
    PRIMARY KEY (run_id, period_type, universe, direction, rank),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS investor_flows (
    run_id TEXT NOT NULL,
    period_type TEXT NOT NULL,
    universe TEXT NOT NULL,
    investor TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    sell_volume INTEGER,
    buy_volume INTEGER,
    net_volume INTEGER,
    sell_value INTEGER,
    buy_value INTEGER,
    net_value INTEGER,
    source TEXT NOT NULL,
    PRIMARY KEY (run_id, period_type, universe, investor, code),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS flow_rankings (
    run_id TEXT NOT NULL,
    period_type TEXT NOT NULL,
    universe TEXT NOT NULL,
    investor TEXT NOT NULL,
    direction TEXT NOT NULL,
    rank INTEGER NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    net_value INTEGER NOT NULL,
    PRIMARY KEY (run_id, period_type, universe, investor, direction, rank),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS ma60_candidates (
    run_id TEXT NOT NULL,
    universe TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    score REAL NOT NULL,
    stage TEXT NOT NULL,
    pullback_quality TEXT,
    current_close REAL NOT NULL,
    ma60 REAL NOT NULL,
    slope20_pct REAL,
    distance_pct REAL NOT NULL,
    distance_change_3d REAL,
    pullback_from_high_pct REAL,
    cross_date TEXT,
    last_cross_date TEXT,
    days_since_cross INTEGER,
    held3 TEXT NOT NULL,
    turnover_ratio REAL,
    weekly_return_pct REAL,
    today_return_pct REAL,
    foreign_week_value INTEGER NOT NULL,
    institution_week_value INTEGER NOT NULL,
    foreign_recent2_value INTEGER NOT NULL,
    institution_recent2_value INTEGER NOT NULL,
    PRIMARY KEY (run_id, universe, code),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS ma60_candidate_sets (
    run_id TEXT NOT NULL,
    flow_period TEXT NOT NULL,
    flow_start_date TEXT NOT NULL,
    universe TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    score REAL NOT NULL,
    stage TEXT NOT NULL,
    pullback_quality TEXT,
    current_close REAL NOT NULL,
    ma60 REAL NOT NULL,
    distance_pct REAL NOT NULL,
    weekly_return_pct REAL,
    signal_return_pct REAL,
    today_return_pct REAL,
    foreign_flow_value INTEGER NOT NULL,
    institution_flow_value INTEGER NOT NULL,
    PRIMARY KEY (run_id, flow_period, universe, code),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_prices_date ON daily_prices(trade_date);
CREATE INDEX IF NOT EXISTS idx_returns_period ON return_rankings(period_type, universe, end_date);
CREATE INDEX IF NOT EXISTS idx_flows_period ON investor_flows(period_type, universe, investor);
CREATE INDEX IF NOT EXISTS idx_ma60_stage ON ma60_candidates(run_id, stage, score);
CREATE INDEX IF NOT EXISTS idx_ma60_candidate_sets ON ma60_candidate_sets(run_id, flow_period, stage, score);
