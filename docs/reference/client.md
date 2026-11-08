# Clients

::: pymusicbrainz.client.MusicBrainz

::: pymusicbrainz.client.AsyncMusicBrainz

## Entity namespaces

The attributes of a client (`mb.artists`, `mb.releases`, …) are instances of
these classes. The async client uses `Async`-prefixed twins with the same
methods, awaited.

::: pymusicbrainz.client.EntityResource

::: pymusicbrainz.client.UrlResource
    options:
      inherited_members: false

::: pymusicbrainz.client.GenreResource
    options:
      inherited_members: false

::: pymusicbrainz.client.CollectionResource
    options:
      inherited_members: false

::: pymusicbrainz.client.CoverArtResource

::: pymusicbrainz.client.SearchOnlyResource
    options:
      members: []

## Rate limiters

::: pymusicbrainz.RateLimiter

::: pymusicbrainz.AsyncRateLimiter
