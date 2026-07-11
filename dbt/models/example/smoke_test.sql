-- Disposable starter model: proves the dbt -> thriftserver -> hive-metastore
-- -> spark-warehouse pipeline end to end. Delete once real models exist.
select 1 as id, 'ok' as status
