-- Run once in the Supabase SQL editor. Stores each pipeline run's outputs so raw comments can be deleted.
create table if not exists public.narrative_runs (
  run_id      bigint generated always as identity primary key,
  event_id    bigint references public.events(event_id),
  created_at  timestamptz default now(),
  n_comments  int,
  clusters    jsonb,        -- = /data/clusters.json   (comments with x,y,narrative,stance + narratives)
  connectivity jsonb        -- = /data/connectivity.json
);
alter table public.narrative_runs enable row level security;
-- frontend reads with the publishable key; writes only with the secret key (server side)
drop policy if exists "narrative_runs read" on public.narrative_runs;
create policy "narrative_runs read" on public.narrative_runs for select using (true);
