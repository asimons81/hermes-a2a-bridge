# Remote URL File Reference Fixtures

These fixtures describe URL references recorded by the local file command as metadata only. Hermes does not fetch, inspect, cache, or serve remote bytes for this shape.

Remote references are marked with `metadataOnly: true` and `bytesAvailable: false`; byte requests return `file_bytes_unavailable`.
