# Collections & submissions

Most of the API is read-only. Through the API you can add tags, ratings,
barcodes and ISRCs, and manage your collections. All of these need
authentication.

## Authentication

=== "Application token (digest)"

    Create a token on your
    [MusicBrainz applications page](https://musicbrainz.org/account/applications)
    and pass it as the password:

    ```python
    mb = MusicBrainz(
        "MyApp",
        "1.0",
        "me@example.com",
        username="my-username",
        password="<application token>",
    )
    ```

    MusicBrainz has announced that digest authentication will be removed in
    August 2027.

=== "OAuth2"

    ```python
    mb = MusicBrainz("MyApp", "1.0", "me@example.com", oauth_token="<access token>")
    ```

    Obtaining the token is up to your application; see the
    [MusicBrainz OAuth2 documentation](https://musicbrainz.org/doc/Development/OAuth2).

=== "Custom"

    Any `httpx2.Auth` implementation works:

    ```python
    mb = MusicBrainz("MyApp", "1.0", auth=my_auth)
    ```

Pass only one of these. A submission or `user-*` include without credentials
raises [`AuthenticationRequiredError`][musicbrainz.errors.AuthenticationRequiredError]
before anything is sent.

!!! warning "Client version"

    Submissions identify your application as `client=<app>-<version>`, and
    the API rejects versions that contain a hyphen. Use `"1.0"`, not
    `"1.0-beta"`, when submitting.

## Tags

```python
mb.submit_tags("artist", {artist_mbid: ["grunge", "rock"]}, vote="upvote")
```

`vote` is `"upvote"`, `"downvote"` or `"withdraw"`.

!!! danger "Without `vote`, tags replace"

    If `vote` is omitted, the given tags *replace* all of your upvoted tags on
    each entity. Your downvotes are unaffected.

Several entities of the same type can be tagged in one request by adding more
keys to the mapping. Genres are tags too: tag with a genre name to vote for
it.

## Ratings

```python
mb.submit_ratings("recording", {recording_mbid: 80})
```

Ratings run from 0 to 100, 20 per star; 0 removes your rating. Artists,
events, labels, recordings, release groups and works can be rated.

## Barcodes and ISRCs

```python
mb.submit_barcodes({release_mbid: "4006381333931"})
mb.submit_isrcs({recording_mbid: ["USGF19942501"]})
```

Barcodes must be valid GTIN-8, 12, 13 or 14 codes; the checksum is verified
before sending. Barcode submissions create edits, which are applied
automatically unless someone votes against them.

## Collections

```python
# Collections of an editor (private ones need you to be that editor)
for collection in mb.collections.of_editor("my-username", include_private=True):
    print(collection.name, collection.entity_type, collection.item_count)

# What's in one
for release in mb.collections.iter_items(collection_mbid, "release"):
    print(release.title)

# Change it
mb.collections.add(collection_mbid, "release", [release_mbid, ...])
mb.collections.remove(collection_mbid, "release", [release_mbid])
```

`add` and `remove` split large lists into requests of 400 entities.
`summary(mbid, entity_type)` returns the collection's description with an
item count. Entity types may be singular (`"release"`) or plural
(`"releases"`).
