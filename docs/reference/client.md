# Clients

::: musicbrainz.client.MusicBrainz

::: musicbrainz.client.AsyncMusicBrainz

## Entity namespaces

The attributes of a client (`mb.artists`, `mb.releases`, …) are instances of
these classes. The async client uses `Async`-prefixed twins with the same
methods, awaited.

::: musicbrainz.client.EntityResource

::: musicbrainz.client.UrlResource
    options:
      inherited_members: false

::: musicbrainz.client.GenreResource
    options:
      inherited_members: false

::: musicbrainz.client.CollectionResource
    options:
      inherited_members: false

::: musicbrainz.client.CoverArtResource

::: musicbrainz.client.SearchOnlyResource
    options:
      members: []

## Rate limiters

::: musicbrainz.RateLimiter

::: musicbrainz.AsyncRateLimiter
