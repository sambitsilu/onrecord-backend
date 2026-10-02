-- Run once as the database owner, AFTER migrations.
-- The app connects as onrecord_app, which can read and write rows but can't
-- change tables, drop the audit-log protection or disable triggers.
-- Replace the password, or better, use IAM database authentication on Cloud SQL.

CREATE ROLE onrecord_app LOGIN PASSWORD 'change-me';
GRANT CONNECT ON DATABASE onrecord TO onrecord_app;
GRANT USAGE ON SCHEMA public TO onrecord_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO onrecord_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO onrecord_app;
-- The audit log only ever needs SELECT and INSERT from the app.
REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM onrecord_app;
GRANT UPDATE (note, before, after, redacted) ON audit_events TO onrecord_app;  -- erasure redaction only
GRANT DELETE ON audit_events TO onrecord_app;  -- only takes effect inside an erasure transaction (trigger)
REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM onrecord_app;
