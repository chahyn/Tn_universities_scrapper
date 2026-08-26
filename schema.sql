-- Tunisian Universities Directory -- schema
--
-- Deviates from the PDF's literal wording in one place, deliberately:
-- the brief says "University -> Program (1)", "University -> Club (1)",
-- "University -> Event (1)" as if each university had exactly one of
-- each. That can't be right for a directory app (every real university
-- has many programs/clubs/events) -- so those are modeled as one-to-many
-- (FK on the child table) instead of one-to-one. Location genuinely is
-- 1:1 per the brief and is modeled that way. Tag is N:M via a join table,
-- matching the brief's own "University <-> Tag (N)" notation.

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- for gen_random_uuid()

-- Generic trigger to keep updated_at current on every UPDATE, used by
-- every table below that has the column.
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- University
-- ============================================================
CREATE TABLE universities (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name             TEXT NOT NULL,
    type             TEXT CHECK (type IN ('PUBLIC', 'PRIVATE')),
    description      TEXT,
    logo_url         TEXT,
    cover_image_url  TEXT,
    website          TEXT,
    email            TEXT,
    phone            TEXT,
    -- Not in the PDF's schema, but etl/export.py already produces this
    -- (short forms/acronyms like "ENIS", "INSAT" the site itself uses) --
    -- kept as a Postgres array rather than dropped, since the spec's
    -- keyword search needs it (users type the acronym, not the full name).
    aliases          TEXT[] NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Names are how the loader matches an incoming scraped record to an
    -- existing DB row on re-runs (upsert key) -- see load_db.py. Without
    -- this, re-running the scraper would duplicate every university on
    -- every run instead of updating it in place.
    CONSTRAINT universities_name_unique UNIQUE (name)
);

CREATE TRIGGER trg_universities_updated_at
    BEFORE UPDATE ON universities
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- Location (1:1 with University)
-- ============================================================
CREATE TABLE locations (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    university_id UUID NOT NULL REFERENCES universities(id) ON DELETE CASCADE,
    address       TEXT,
    city          TEXT,
    latitude      DECIMAL(9, 6),
    longitude     DECIMAL(9, 6),

    -- Not in the PDF's schema, but present in the app's export shape
    -- (etl/export.py) -- lets the app distinguish a rooftop-accurate pin
    -- from a city-centroid fallback rather than treating every pin as
    -- equally precise. Nullable: older/partial records just won't have it.
    geocode_precision TEXT CHECK (geocode_precision IN ('address', 'institution', 'city')),

    -- Enforces the 1:1 relationship at the database level, not just in
    -- application code.
    CONSTRAINT locations_university_unique UNIQUE (university_id)
);

-- ============================================================
-- Program (1:N -- one university has many programs)
-- ============================================================
CREATE TABLE programs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    university_id   UUID NOT NULL REFERENCES universities(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    degree_type     TEXT CHECK (degree_type IN ('LICENCE', 'MASTER', 'ENGINEERING', 'PHD')),
    duration_years  INTEGER,
    description     TEXT
);

CREATE INDEX idx_programs_university_id ON programs(university_id);

-- ============================================================
-- Club (1:N -- one university has many clubs)
-- ============================================================
CREATE TABLE clubs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    university_id UUID NOT NULL REFERENCES universities(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    description   TEXT,
    logo_url      TEXT,
    contact_info  TEXT
);

CREATE INDEX idx_clubs_university_id ON clubs(university_id);

-- ============================================================
-- Event (1:N -- one university has many events)
-- ============================================================
CREATE TABLE events (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    university_id UUID NOT NULL REFERENCES universities(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    description   TEXT,
    start_date    TIMESTAMPTZ,
    end_date      TIMESTAMPTZ
);

CREATE INDEX idx_events_university_id ON events(university_id);

-- ============================================================
-- Tag (N:M with University via join table)
-- ============================================================
CREATE TABLE tags (
    id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE university_tags (
    university_id UUID NOT NULL REFERENCES universities(id) ON DELETE CASCADE,
    tag_id        UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (university_id, tag_id)
);

CREATE INDEX idx_university_tags_tag_id ON university_tags(tag_id);