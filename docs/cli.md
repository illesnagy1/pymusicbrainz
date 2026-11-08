# Command line

The `pymusicbrainz` command comes with the `cli` extra:

```sh
pip install 'pymusicbrainz[cli]'      # or: uv tool install 'pymusicbrainz[cli]'
```

It prints results as JSON: pretty-printed in a terminal, plain when piped, for example into `jq`.

```sh
pymusicbrainz search artist "Daft Punk" --limit 3
pymusicbrainz lookup artist 5b11f4ce-a62d-471e-81fc-a69a8278c7da -i aliases,url-rels
pymusicbrainz browse release-group --artist 5b11f4ce-a62d-471e-81fc-a69a8278c7da --type album --all
pymusicbrainz isrc USGF19942501
pymusicbrainz iswc T-070.243.691-6
pymusicbrainz discid 2y0jFMwYQP7gb9iSUKagmoT6DIw-
pymusicbrainz url https://www.discogs.com/artist/125246
pymusicbrainz cover-art 0fb1bdd3-1443-4569-943f-fc69c6424f99 -o cover.jpg --size 500
pymusicbrainz genres
```

`pymusicbrainz --help` and `pymusicbrainz <command> --help` list every option.

## Commands

| Command | Does |
|---|---|
| `lookup ENTITY MBID` | Look up one entity. Accepts `-i/--inc`, `--type` and `--status`. |
| `search ENTITY QUERY` | Search with a Lucene query. Accepts `--limit` (default 10), `--offset` and `--dismax`. |
| `browse ENTITY --LINK MBID` | List entities linked to another one, e.g. `--artist`, `--release-group`, `--editor NAME`. Add `--all` to fetch every page. |
| `isrc CODE`, `iswc CODE` | Recordings with an ISRC, works with an ISWC. |
| `discid DISCID` | Releases for a disc ID. Accepts `--toc`, `--no-cdstubs` and `--all-formats`. |
| `url URL...` | URL entities for one or more URLs. |
| `cover-art MBID` | A release's cover art listing (`--release-group` for a release group). With `-o FILE`, downloads one image instead; pick it with `--image front\|back\|ID` and `--size 250\|500\|1200`. |
| `genres` | All genre names. |

Includes can be repeated (`-i aliases -i tags`) or comma-separated
(`-i aliases,tags`).

## Global options

| Option | Environment variable | Default |
|---|---|---|
| `--app` | `MUSICBRAINZ_APP` | `pymusicbrainz-cli` |
| `--contact` | `MUSICBRAINZ_CONTACT` | none |
| `--base-url` | `MUSICBRAINZ_URL` | `https://musicbrainz.org/ws/2/` |
| `-v`, `-vv` | | Log retries (`-v`) or every request (`-vv`) to stderr. |

Global options go before the command: `pymusicbrainz -vv --contact me@example.com lookup …`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | An API or validation error. The message goes to stderr. |
| 2 | A usage error, such as an unknown command or entity. |

Shell completion: `pymusicbrainz --install-completion`.
