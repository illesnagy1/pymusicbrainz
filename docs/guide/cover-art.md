# Cover art

The [Cover Art Archive](https://coverartarchive.org/) (CAA) stores cover
images for MusicBrainz releases. The client reaches it through
`mb.cover_art`:

```python
art = mb.cover_art.release("0fb1bdd3-1443-4569-943f-fc69c6424f99")

for image in art.images:
    print(image.id, image.types, image.image)

front = art.front  # the image chosen as the main front cover, or None
if front:
    print(front.thumbnail(500))  # URL of the 500 px thumbnail
```

A listing is a [`CoverArt`][musicbrainz.models.CoverArt] with a list of
[`CoverArtImage`][musicbrainz.models.CoverArtImage]s. Each image has its
`types` (`"Front"`, `"Back"`, `"Booklet"`, …), the `front`/`back` flags, an
`approved` flag, a `comment`, and the URL of the original `image`.
`thumbnail(size)` returns the URL of the 250, 500 or 1200 px version, or `None`
if that size doesn't exist.

## Downloading

```python
img = mb.cover_art.front(release_mbid, size=500)
Path(f"cover.{img.content_type.split('/')[-1]}").write_bytes(img.data)

back = mb.cover_art.back(release_mbid)
booklet = mb.cover_art.image(release_mbid, image="45224054828")  # by image ID
```

Downloads return an [`ImageData`][musicbrainz.models.ImageData] with the bytes,
the `content_type` and the final `url` after the redirect to archive.org.
`size=` picks a thumbnail (250, 500 or 1200 px) instead of the original.

If you only need a link, for example for an `<img>` tag, `url()` builds it
without making a request:

```python
mb.cover_art.url(release_mbid, size=250)
# 'https://coverartarchive.org/release/…/front-250'
```

## Release groups

A release group's cover art is the art of one of its releases, chosen by the
community:

```python
art = mb.cover_art.release_group(release_group_mbid)
art.release  # URL of the release the images come from

img = mb.cover_art.front(release_group_mbid, release_group=True, size=250)
```

Release groups only have a front image; asking for `"back"` or an image ID
raises `ValidationError`.

## Errors and limits

- A release or release group without cover art raises
  [`NotFoundError`][musicbrainz.errors.NotFoundError], with the archive's
  message ("No cover art found for release …").
- The archive has no rate limit of its own, so these requests **don't wait**
  for the MusicBrainz rate limiter. Retries on 503 and network errors still
  apply.
- Your MusicBrainz credentials are never sent to the archive or to
  archive.org.
- A mirror can be used with `MusicBrainz(..., caa_url="https://…")`.
