# Command line

Installing the package adds a `musicbrainz` command. It prints results as
JSON: pretty-printed in a terminal, plain when piped, for example into `jq`.

```sh
musicbrainz search artist "Daft Punk" --limit 3
musicbrainz lookup artist 5b11f4ce-a62d-471e-81fc-a69a8278c7da -i aliases,url-rels
musicbrainz browse release-group --artist 5b11f4ce-a62d-471e-81fc-a69a8278c7da --type album --all
musicbrainz isrc USGF19942501
musicbrainz iswc T-070.243.691-6
musicbrainz discid 2y0jFMwYQP7gb9iSUKagmoT6DIw-
musicbrainz url https://www.discogs.com/artist/125246
musicbrainz genres
```

`musicbrainz --help` and `musicbrainz <command> --help` list every option.

## Commands

| Command | Does |
|---|---|
| `lookup ENTITY MBID` | Look up one entity. Accepts `-i/--inc`, `--type` and `--status`. |
| `search ENTITY QUERY` | Search with a Lucene query. Accepts `--limit` (default 10), `--offset` and `--dismax`. |
| `browse ENTITY --LINK MBID` | List entities linked to another one, e.g. `--artist`, `--release-group`, `--editor NAME`. Add `--all` to fetch every page. |
| `isrc CODE`, `iswc CODE` | Recordings with an ISRC, works with an ISWC. |
| `discid DISCID` | Releases for a disc ID. Accepts `--toc`, `--no-cdstubs` and `--all-formats`. |
| `url URL...` | URL entities for one or more URLs. |
| `genres` | All genre names. |

Includes can be repeated (`-i aliases -i tags`) or comma-separated
(`-i aliases,tags`).

## Global options

| Option | Environment variable | Default |
|---|---|---|
| `--app` | `MUSICBRAINZ_APP` | `musicbrainz-cli` |
| `--contact` | `MUSICBRAINZ_CONTACT` | none |
| `--base-url` | `MUSICBRAINZ_URL` | `https://musicbrainz.org/ws/2/` |
| `-v`, `-vv` | | Log retries (`-v`) or every request (`-vv`) to stderr. |

Global options go before the command: `musicbrainz -vv --contact me@example.com lookup …`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | An API or validation error. The message goes to stderr. |
| 2 | A usage error, such as an unknown command or entity. |

Shell completion: `musicbrainz --install-completion`.
