# Stop the local portable PostgreSQL used for Clinical Scribe dev.
$bin  = Join-Path $env:USERPROFILE 'scribe-pg\pgsql\bin'
$data = Join-Path $env:USERPROFILE 'scribe-pg\data'
& (Join-Path $bin 'pg_ctl.exe') -D $data stop
