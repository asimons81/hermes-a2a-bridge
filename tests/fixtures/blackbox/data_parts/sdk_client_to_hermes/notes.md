# SDK Client to Hermes Data-Part Fixtures

These sanitized fixtures represent the official SDK client shape sent to Hermes: data parts use the `data` field directly and omit `kind`/`type`.

Observed locally with the optional SDK harness:

- The SDK client can send data-only messages to Hermes.
- The SDK client can send mixed text plus data messages to Hermes.
- The SDK client can receive Hermes structured data artifacts when the executor output is JSON.
- The SDK client can receive Hermes streaming data artifact updates.
- Hermes still rejects file/raw/url/blob/multimodal parts with a structured A2A 1.0 error.
