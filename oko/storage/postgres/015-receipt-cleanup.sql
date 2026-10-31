-- Derive supersession from the authoritative receipt history in bounded batches.
DO $body$
BEGIN
    IF EXISTS (
        WITH expected AS (
            SELECT r.sequence,r.completed FROM reader_action_results r
            WHERE EXISTS (SELECT 1 FROM reader_action_results newer
                          WHERE newer.identity=r.identity AND newer.sequence>r.sequence)
        ), differences AS (
            (SELECT sequence,completed FROM expected EXCEPT SELECT sequence,completed FROM reader_action_superseded)
            UNION ALL
            (SELECT sequence,completed FROM reader_action_superseded EXCEPT SELECT sequence,completed FROM expected)
        ) SELECT 1 FROM differences
    ) THEN
        RAISE EXCEPTION 'inconsistent superseded receipts';
    END IF;
END
$body$;
DROP TRIGGER journal_receipt_added ON reader_action_results;
DROP TRIGGER journal_receipt_completed ON reader_action_results;
DROP TRIGGER journal_receipt_removed ON reader_action_results;
DROP FUNCTION oko_trigger_journal_receipt_added();
DROP FUNCTION oko_trigger_journal_receipt_completed();
DROP FUNCTION oko_trigger_journal_receipt_removed();
DROP TABLE reader_action_superseded;
DROP INDEX reader_action_expiry;
CREATE INDEX reader_action_expiry ON reader_action_results(completed,sequence) WHERE completed IS NOT NULL;
