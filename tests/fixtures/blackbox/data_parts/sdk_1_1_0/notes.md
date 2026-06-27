# SDK 1.1.0 Data-Part Fixtures

These sanitized fixtures use the official `a2a-sdk 1.1.0` `Part` shape: data parts are encoded as `{"data": ...}` with no `kind` or `type` discriminator.

Observed official SDK model behavior:

- JSON object data parts parse.
- JSON array data parts parse.
- Text plus data parts parse.
- Data artifacts and streaming artifact updates parse when their parts use `data` without `kind`.
- Scalar data values also parse at the SDK protobuf layer because `data` is a `google.protobuf.Value`; Hermes intentionally keeps its local subset to JSON objects and arrays.
- `kind` and `type` are not SDK `Part` fields and are rejected by `ParseDict`.

The response and stream fixtures are sanitized captures from the local optional SDK harness. They are harness fixtures, not public-network SDK service captures.
