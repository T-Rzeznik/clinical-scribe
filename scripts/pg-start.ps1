# Start the local portable PostgreSQL used for Clinical Scribe dev.
# Portable install lives at %USERPROFILE%\scribe-pg (not a Windows service).
$bin  = Join-Path $env:USERPROFILE 'scribe-pg\pgsql\bin'
$data = Join-Path $env:USERPROFILE 'scribe-pg\data'
$log  = Join-Path $env:USERPROFILE 'scribe-pg\pg.log'
& (Join-Path $bin 'pg_ctl.exe') -D $data -l $log -o "-p 5432" start
& (Join-Path $bin 'pg_isready.exe') -h localhost -p 5432
