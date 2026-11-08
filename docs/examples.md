# Examples

Runnable scripts in the repository's `examples/` folder. Each one is a
complete program with `--help`. Set `MUSICBRAINZ_CONTACT` to your e-mail
address or URL so MusicBrainz can reach you if a script misbehaves.

## Discography

Search for an artist and list their studio albums (or EPs, singles, …),
oldest first. Shows search, paging with `iter_browse`, and filtering by
release type.

```sh
uv run python examples/discography.py "Nirvana"
uv run python examples/discography.py "Björk" --type ep
```

```text
Nirvana (1980s–1990s US grunge band)

  1989  Bleach
  1991  Nevermind
  1993  In Utero
```

## Cover art

Find an album and download its front cover from the Cover Art Archive.

```sh
uv run python examples/cover_art.py "Nirvana" "Nevermind" --size 500 -o covers/
```

```text
Found: Nirvana - Nevermind (1991-09-24)
Saved 23 KiB to covers/Nevermind.jpg
```

## Identify a CD

Read the disc in your drive and list the matching releases, with dates,
countries and catalogue numbers. Reading the drive needs the
[`discid`](https://pypi.org/project/discid/) package and the libdiscid C
library; `--disc-id` looks up an ID you already have instead.

```sh
uv run --with discid python examples/find_disc.py
uv run python examples/find_disc.py --disc-id 2y0jFMwYQP7gb9iSUKagmoT6DIw-
```

```text
Disc ID: 2y0jFMwYQP7gb9iSUKagmoT6DIw-
  Nirvana - Nevermind  [1991-09-24 US GED24425]
  Nirvana - Nevermind  [1996-03-12 US UDCD 666]
```

## ISRC report (async)

Look up several ISRCs as concurrent tasks with `AsyncMusicBrainz`. The rate
limiter still spaces the requests about a second apart.

```sh
uv run python examples/isrc_report.py USGF19942501 GBAYE0601498 USRC17607839
```

```text
USGF19942501  Nirvana - Smells Like Teen Spirit
GBAYE0601498  The Beatles - Yellow Submarine
USRC17607839  Daryl Hall & John Oates - Crazy Eyes
```

## Export a collection

Write every release in an editor's collections to CSV. With credentials and
`--private`, private collections are included too.

```sh
uv run python examples/export_collection.py some-editor > releases.csv
```
