-- Deletes arrise_api.request_logs rows older than the 90-day retention
-- window. Document records (table `arrise_api.documents`) are never
-- touched by this script.
--
-- Run manually, e.g.:
--   psql -d arrise_vm_db -f scripts/purge_old_request_logs.sql
--
-- Scheduling this on a recurring basis (cron / systemd timer / Task
-- Scheduler) is a deployment concern and is not wired up in this increment.

DELETE FROM arrise_api.request_logs
WHERE created_at < NOW() - INTERVAL '90 days';
