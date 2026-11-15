# UI spec: Reddit Narrative Map

What the UI shows, which file each piece comes from, and how it behaves. The backend writes two JSON files to `data/`; the UI reads only these.

## The idea in one sentence
**Position = what communities say (narratives). Lines = who connects them (shared commenters).** Where the two disagree is the insight. Examples: close together but no line means they reached the same story independently; far apart but a thick line means the same people tell it differently in different places.

---

## Data inputs

### `/data/clusters.json`
```jsonc
{
  "params": {"method": "hdbscan", "min_cluster_size": 54, "embed_backend": "openai", "n_clustered": 1080},
  "narratives": [            // added after labeling; one per narrative
    {"id": 3, "label": "Insiders win, retail loses",
     "summary": "Early investors captured the gains; public buyers are exit liquidity.",
     "size": 155, "weighted_share": 0.14,
     "mean_stance": 0.31,      // 0..1 or null
     "on_axis_share": 0.82,    // share of comments that address the stance question
     "mean_controversy": 0.21,
     "n_threads": 24, "n_subreddits": 6}
  ],
  "comments": [              // one per clustered comment (~1–5k)
    {"id": "c1", "post_id": "abc", "parent_id": "c0", "subreddit": "stocks", "author": "u130",
     "body": "original comment text", "claim": "neutral one-sentence rewrite",
     "score": 42, "weight": 1.3,             // weight → dot size
     "n_replies": 4, "controversy": 1.27,    // many replies + low score = contested
     "stance": 0.2,                          // 0..1, or null = off-axis
     "emotion": "resentment",
     "cluster": 3, "narrative": 3,           // use `narrative` (after merges)
     "is_noise": false,                      // true → draw fainter
     "x": 4.32, "y": 8.79}                   // 2D UMAP coords (raw units, rescale)
  ]
}
```

### `data/connectivity.json`
```jsonc
{
  "stance_axis": {"question": "Is the IPO good for ordinary investors?", "0": "bad for retail", "1": "good for retail"},
  "communities": [{"subreddit": "stocks", "n_comments": 600, "group": 0, "x": -0.12, "y": -0.27, "mean_stance": 0.42}],
  "shares": {"stocks": {"0": 0.30, "3": 0.30}},            // narrative mix per subreddit (sums to 1)
  "stance": {"stocks": {"0": 0.21, "3": 0.18}},            // mean stance per subreddit × narrative (null ok)
  "shared_commenters": [{"a": "stocks", "b": "investing", "shared": 168, "jaccard": 0.22}],
  "community_groups": {
    "groups": [{"id": 0, "label": "Retail investors", "members": ["wallstreetbets", "stocks", "investing"],
                "n_comments": 2020, "x": 0.1, "y": -0.3, "mean_stance": 0.55,
                "shares": {"0": 0.27, "1": 0.06}}],
    "shared_commenters": [{"a": 0, "b": 1, "jaccard": 0.04}],
    "js_distance": {"0|1": 0.41}
  },
  "reply_matrix_pct": {"3": {"2": 34.0, "3": 30.0}},       // row narrative replies → column narrative, % of row
  "echo_score": {"3": 30.0}                                // % of a narrative's replies that stay inside it
}
```

**Joins:** `comment.narrative` ↔ `narratives[].id`; `comment.subreddit` ↔ `communities[].subreddit`; `communities[].group` ↔ `community_groups.groups[].id`.
**JSON keys for narratives are strings** (`"3"`), but `narrative` on comments is an int.

---

## Main view: community map

### Level 1: groups (default)
- One **circle per community group**, placed at `groups[].x, y`. The coordinates are an MDS layout of narrative distance, in raw units, so rescale them to the canvas. Add collision padding so circles don't overlap.
- **Circle area ∝ `n_comments`**, so radius ∝ √n.
- **Inside each circle:** a mini narrative map. Plot that group's comments at their `x, y`, which come from one shared global UMAP, so a position means the same narrative in every circle. Clip the dots to the circle.
  - Faint gray background dots: a sample of *all* comments, for reference.
  - Colored dots: this group's comments. Dot size from `weight`; `is_noise` dots at about 40% opacity.
- **Lines between circles:** `community_groups.shared_commenters`, with thickness ∝ `jaccard`. Show "N% shared" on hover, or as a label when it's large.
- **Labels under each circle:** group label, plus "3 subreddits · 2,020 comments".

### Level 2: subreddits inside a group
- Click a group once to select it and show it in the panel. Click it again to **expand** into its member subreddits, drawn the same way, using `communities` filtered by `group`.
- A **"All groups"** back button returns to level 1.
- Optional: lines between the member subreddits from `shared_commenters`.

### Color toggle: Narrative | Stance
- **Narrative:** dot color = narrative (fixed palette, see below).
- **Stance:** dot color on a diverging scale: `0` (red) → `0.5` (gray) → `1` (blue). Dots with `stance = null` are gray at low opacity (off-axis, not neutral).
- Show the stance question from `stance_axis.question` above the map at all times, and use the axis labels `"0"` / `"1"` on the legend. **Never** show "negative/positive".

### Narrative chips (legend)
- One chip per narrative (color square + label). Click to **highlight** that narrative everywhere: other dots fade to about 10% opacity, and circle sublabels switch to "X% <narrative>". Click again to clear.

---

## Side / bottom panel (click-driven)

| Selected | Show |
|---|---|
| Nothing | Hint: "Click a group or a narrative." |
| **Group or subreddit** | Name and members · **stance bar** (% below 0.4 / 0.4–0.6 / above 0.6, using the axis labels; note that gray = off-axis is excluded) · **narrative mix** (horizontal bars from `shares`) · **most contested comment** here |
| **Narrative chip** | Label + `summary` · three stat tiles: **mean stance**, **echo score**, **controversy** · **most contested comments** (top 3 by `controversy` with `n_replies ≥ 2`) |
| **A single dot** | The original `body`, plus subreddit · score · replies · narrative · stance · emotion. Optional: link to the Reddit permalink |

When a narrative is selected *and* a group is clicked, the panel shows that group's stance **within that narrative** (filter comments by both).

---

## Encodings and style
- **Narrative palette** (fixed order, never cycled; fold anything past 8 into "Other"):
  `#2a78d6, #eb6834, #1baf7a, #eda100, #e87ba4, #008300, #6250d6, #e34948`
- **Stance diverging scale:** red `#e34948` ← gray `#B4B2A9` → blue `#2a78d6`.
- Neutral lines and edges in gray. Text in neutral ink colors, never the series color.
- Must work in light and dark mode.

## Performance
- 1–5k dots per view: **draw dots on canvas**, not SVG (SVG lags on hover/click past ~2k). Circles, lines and labels can be SVG on top.
- Precompute, for each circle, the rescaled dot coordinates once per layout, not on every redraw.
- Load cached demo events instantly; for a live run, show the map as soon as `clusters.json` exists and fill in labels when `narratives` appears.

## Edge cases
- `narratives` missing → use `cluster` ids with placeholder labels "Narrative 1…".
- `stance_axis` null or all stances null → hide the stance toggle.
- Only 1–2 groups → skip level 1 and start at subreddits.
- A narrative with `on_axis_share < 0.3` → show "mostly off-axis" instead of its mean stance.

## Optional extras (only if time)
- **Reply heatmap:** narrative × narrative from `reply_matrix_pct`, with the diagonal outlined (echo).
- **Polymarket chip:** current market odds next to each group's mean stance, if a matching market exists.
