# SDK 1.1.0 File-Part Rejection Fixture

These fixtures describe the optional isolated SDK client probe against Hermes. SDK model fields `raw`, `filename`, `url`, and multimodal media types parse at the SDK layer, but Hermes rejects them with `unsupported_part_type`.

The Agent Card fixture keeps only text and JSON modes. It does not advertise file, image, audio, or video input support.
