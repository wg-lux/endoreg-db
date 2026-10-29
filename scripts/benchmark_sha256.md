# Candidate search with a known Salt

This script is testing possible entries and measures runtime. It tries to brute-force attack SHA-256 with various entries. To run it succesfully, the entry has to be in the search space.

The brute force vulnerability of SHA-256 on sensitive data is the reason for choosing **HMAC-SHA-256**, using Salt as a key. The payload is a UTF-8-JSON: `["patient-identity-v1",first_name,last_name,dob,center_id]`.
`dob` is a ISO-Date, `center_key` a JSON-Number. The current implementation instead
uses a string center key and trims whitespace. Exact serialization and the
correct revision are essential.

Reference: : [Anonymization.yml](../feature-tracking/Anonymization.yml),
Criterion: `sensitive_metadata_resolution`.
## Examples

Test with a freely selected test key and a four digit number:

```sh
python scripts/benchmark_sha256.py --salt benchmark-only --plaintext 9876 \
  --min-length 4 --max-length 4 --max-seconds 30
```

To look for a known hash, it should be set in the call (`TARGET_HASH` and `KNOWN_SALT`):

```sh
python scripts/benchmark_sha256.py --target "$TARGET_HASH" --salt "$KNOWN_SALT" \
  --charset abcdef0123456789 --min-length 1 --max-length 8 \
  --max-seconds 60 --max-attempts 5000000
```


Additional modes: `--algorithm sha256 --salt ''`, `--algorithm salt-prefix`
for `SHA256(salt + payload)`, and `--algorithm salt-suffix` for
`SHA256(payload + salt)`. `--help` lists all parameters.

## Interpretation

The JSON output contains the status, matching variable candidate, attempts,
search-space size, seconds, candidates per second, and an extrapolated duration
for the entire search space. This estimate is not a measured exhaustive search;
it depends on the machine, algorithm, input length, and Python overhead. It is
particularly inaccurate with few attempts. If an existing match has a uniformly
distributed position, the average attempt count is approximately half the search
space. This is not a practical inversion method for unknown arbitrary 256-bit
preimages.

The search runs sequentially and stores no candidate list. Order: increasing
length, then character order in `--charset`. Length 0 permits empty input.
Limits are always positive; the time limit is checked between candidates.
Statuses: `found`, `exhausted`, `attempt_limit`, `time_limit`, or `interrupted`.
Exit code 0 means a match, 1 no match, and 2 a parameter error. Only `exhausted`
rules out the entire specified search space.

There is no database, network, or file access and no change to production hash
functions. The script deliberately reproduces historical algorithms in isolation;
it creates no persisted application identities. Salt and payload are not loaded
automatically from Django. Command-line arguments can appear in process lists or
shell history; output contains the match. The explicitly generated test values
above are suitable for measurements.
