# audioforma-stems
making stems for audioforma

A FastAPI service that splits songs into stems with [Demucs](https://github.com/facebookresearch/demucs),
either all at once (`/api/audio/...`) or **streamed**, chunk by chunk, as the
separation runs (`/api/stream/...`).

## Streaming stems

Demucs separates long audio in overlapping segments (7.8 s for `htdemucs`)
and blends neighbours with a triangular weight. As soon as a segment is done,
everything before the next segment's start is final, so the service sends it
right away. The streamed stems are identical to a whole-file run with
`--shifts 0`, and the first chunk arrives after one segment's worth of work.

On 2 CPU cores, a full-size HTDemucs streams at about 1.8x real time: the first
5.85 s of stems after ~3 s, then a chunk every ~2.8 s. A GPU is much faster.

### Start a job

```
POST /api/stream              multipart: file, [model], [artist], [track]
POST /api/stream/from-url     JSON: {"url": ..., "model"?: ..., "artist"?: ..., "track"?: ...}
```

Both return `{"job_id", "status", "events_url", "status_url"}`.

`model` is one of `htdemucs_6s` (default: drums, bass, other, vocals,
guitar, piano), `htdemucs` (4 stems), `htdemucs_ft`, `mdx_extra`,
`mdx_extra_q`. Horns aren't a Demucs stem: a trumpet usually lands in `other`
or `vocals`, which is why the 6-stem model is the default (piano gets its own
stem). Demucs notes that its piano stem is still rough.

If Supabase is configured and `artist` and `track` are given, the finished
stems are also uploaded and added to `stem_lookup`, like the original flow.

### Follow it

`GET /api/stream/{job_id}/events` is a Server-Sent Events stream. Reconnect
with `Last-Event-ID` (EventSource does this for you) to resume without gaps.

| event      | data |
|------------|------|
| `status`   | `{state}`: `queued`, `loading_model`, `decoding` |
| `meta`     | `{sample_rate, channels, stems, frames, duration, chunk_seconds, chunk_count, model}` |
| `chunk`    | `{index, start, start_frame, frames, duration, urls: {stem: url}}` |
| `progress` | `{fraction, seconds_done, elapsed, speed}` (speed > 1 is faster than real time) |
| `done`     | `{files: {stem: url}}` full-length stems |
| `error`    | `{message}` |

Each chunk URL is a 16-bit PCM WAV for one stem. Chunks tile the song exactly
(`start_frame` + `frames`), so a player can schedule them back to back.

```js
const job = await (await fetch(`${API}/api/stream`, { method: "POST", body: form })).json();
const events = new EventSource(API + job.events_url);
events.addEventListener("chunk", async (e) => {
  const chunk = JSON.parse(e.data);
  for (const [stem, url] of Object.entries(chunk.urls)) {
    const audio = await ctx.decodeAudioData(await (await fetch(API + url)).arrayBuffer());
    // schedule `audio` at chunk.start seconds on a shared clock
  }
});
events.addEventListener("done", () => events.close());
```

`GET /api/stream/{job_id}` returns the same information as one JSON snapshot
for clients that would rather poll. Jobs and their files are kept for two hours.

A command-line client: `python stream_client.py song.mp3 --model htdemucs_6s`.

## Running

```
docker compose up --build
```

or, with Demucs installed locally, `uvicorn src.app:app --reload`.

| variable | |
|---|---|
| `SUPABASE_SERVICE_ROLE_KEY` | optional; without it nothing is uploaded and job status lives in memory |
| `ENVIRONMENT` | `production` restricts CORS to `ALLOWED_ORIGINS`; otherwise any localhost port is allowed too |
| `ALLOWED_ORIGINS` | comma-separated extra origins |
| `STEMS_BACKEND` | `demucs` (default) or `filterbank`, a fast stand-in that splits frequency bands, for UI work without a model |
| `STREAM_DIR` | where streamed chunks are written (default `temp/stream`) |
| `DEMUCS_MODELS` | models the Docker build pre-downloads |

## Tests

```
pip install pytest requests
pytest
```

The Demucs test checks, with a small randomly initialised HTDemucs, that
streaming gives the same stems as a whole-file separation.
