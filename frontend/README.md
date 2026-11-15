# r/eaction — frontend

Search a news event and see how Reddit is reading it: which subreddits tell which story, how they lean on the event's central question, and which communities share commenters.

The frontend is plain HTML/CSS/JS: no build step, no dependencies, no external requests. It reads the pipeline's JSON files from `data/`.

## Run it

```bash
cd r-eaction
python3 -m http.server 8000
# open http://localhost:8000
```

Serve it over HTTP. If you double-click `index.html`, the browser blocks it from reading `data/`.

## Folder layout

```
r-eaction/
  index.html          page markup (home page + event page)
  styles.css          all styling, light + dark
  app.js              everything else (loading, layout, drawing, search)
  fonts/              Source Serif 4 (used for headings)
  data/
    events.json       the list of searchable topics
    anthropic-ipo/    FICTIONAL sample data in the pipeline's format
      clusters.json
      connectivity.json
    <slug>/           one folder per topic, same two files
```

## Adding a topic

1. Put the pipeline output in `data/<slug>/clusters.json` and `data/<slug>/connectivity.json`.
2. Add (or edit) its entry in `data/events.json`:

```jsonc
{
  "slug": "faa-closure",            // folder name under data/, also the page link: /#faa-closure
  "title": "FAA closure",           // shown on the page and as a "Try" chip
  "query": "FAA closure",           // also counts as a search phrase
  "keywords": ["faa", "federal aviation", "air traffic", "airspace"],
  "sample": false,                  // true shows a small "SAMPLE DATA" tag
  "path": "custom/folder/"          // optional: load the two files from somewhere else
}
```

**How search works (demo mode):** a search opens a topic if it contains any of that topic's keywords, its `query` or its `title` as whole words. For example, "implications of nvidia" and "NVDA stock" both open `jensen-huang`. Anything that matches nothing shows "Oops! We don't have data on … yet." Add keywords generously.

A topic listed in `events.json` with no data folder still opens. It shows a "We don't have data for this topic yet" message instead of a map.

The four non-sample topics in `events.json` (Jensen Huang / Nvidia, FAA closure, FIFA election, You Can See Everything) have no data yet. Drop in their files and they'll work.

## The sample data

`data/anthropic-ipo/` is **made up**. It's 2,970 fictional comments generated to show the format and the UI. Before the demo, replace it with the real pipeline output (or delete the folder) and set `"sample": false`.

## Data contract (fields the UI reads)

### `clusters.json`

| Field | Used for | Required? |
|---|---|---|
| `comments[].subreddit` | which blob the comment is in | **yes** |
| `comments[].id` | stable dot placement; Reddit link | recommended |
| `comments[].narrative` (or `cluster`) | color + direction inside the blob. `-1`/`null` = off-topic | **yes** |
| `comments[].is_noise` | treated as off-topic (gray) | no |
| `comments[].stance` | 0..1 or −1..1 (auto-detected), `null` = no clear stance | for Stance view |
| `comments[].score`, `n_replies` | dot size (engagement = score + 4 × replies) | recommended |
| `comments[].weight` | dot size, only if score/replies are missing | no |
| `comments[].body`, `claim`, `author`, `emotion` | tooltip, side panel | recommended |
| `comments[].post_id` | Reddit link (only shown when ids look like real Reddit ids) | no |
| `narratives[].id`, `label`, `summary` | names + one-line summaries | **yes** |
| `narratives[].short_label` | short name used around a zoomed-in blob (falls back to `label`) | no |

`comments[].x` / `y` (UMAP) are **not** used. Inside each blob, every narrative sits in a fixed direction (the same in every blob), so blobs can be compared at a glance.

Up to 8 narratives get colors (the 8 largest, in the order the file lists them). Any beyond that show as gray "off-topic".

### `connectivity.json`

| Field | Used for | Required? |
|---|---|---|
| `stance_axis.question`, `"0"`, `"1"` | the question under the title; the two ends of the stance scale | for Stance view |
| `shared_commenters[]`: `a`, `b`, `jaccard`, `shared` | **line thickness** between subreddits; "N% shared · ~X people" | for lines |
| `js_distance` | **distance between blobs** (similar story mix = closer). Accepts `{a:{b:d}}` or `[{a,b,d}]`. Computed from the narrative mix if missing | no |
| `communities[].subreddit`, `group` | group name shown above a subreddit | no |
| `community_groups.groups[]`: `id`, `label` | turns numeric group ids into names | no |

Lines are drawn for pairs whose overlap is at least 15% of the event's largest overlap (`LINK_MIN_FRACTION` at the top of `app.js`).

## What's on screen

- **Home page:** wordmark, search, "Try" chips for every topic in `events.json`.
- **Event page, left:** one blob per subreddit, one dot per comment.
  - Bigger dots got more engagement.
  - Blobs that tell similar stories sit closer.
  - Thicker straight lines mean more shared commenters. Lines never pass over another blob.
  - **Narrative / Stance** switches the dot colors. Stance runs brown (`"0"`) → gray → teal (`"1"`). In Stance view, all other colors on the page also switch to stance.
  - Hover a blob to highlight it and see "% shared" on its lines. Click it to zoom in, then hover or click dots to read comments.
  - Scroll to zoom, drag to pan.
- **Event page, right:** search for other topics, the light/dark toggle, then details for whatever's selected: the event, a subreddit, or a comment.
- The layout reshapes itself to the window, so the map fills the space on any screen size.

## Settings (top of `app.js`)

- `DATA`: data folder, default `data/` (or `?data=folder/` in the URL)
- `PALETTE`: narrative colors, in order
- `LINK_MIN_FRACTION`: how strong an overlap needs to be to get a line

## Not built yet

- Live analysis for topics that aren't in `events.json`.
- Reply network / "who argues with whom" (`reply_matrix_pct`, `echo_score`) isn't shown.
- Groups view (collapsing subreddits into `community_groups`) isn't shown. Group names only appear as a label.
