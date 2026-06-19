#!/usr/bin/env bash
#
# bootstrap-buckets.sh — create the two phantom-ink buckets and a
# per-profile IAM user/policy pair scoped to that profile's prefix.
#
# Run once after `docker compose up -d`. Idempotent; re-running for an
# existing profile rotates nothing — pass --rotate to force a new
# access key.
#
# Buckets:
#   phantom-vault      — Obsidian markdown, mounted via s3fs-mac
#   phantom-artifacts  — Loop templates / instances / iterations /
#                        assist outputs / session recordings
#
# Prefix scheme inside each bucket:
#   phantom-artifacts/<profile>/<kind>/<name>/<sha256>
#   phantom-vault/<profile>/<...vault tree...>
#
# Per-profile policies restrict the user to its own prefix; cross-
# profile reads return 403.

set -euo pipefail

PROFILE="${1:-}"
ROTATE="${2:-}"

if [[ -z "$PROFILE" ]]; then
  cat <<'USAGE'
Usage: bootstrap-buckets.sh <profile-name> [--rotate]

  profile-name   e.g. "personal", "work" — must match the workspace
                 profile slug. The IAM user gets named
                 "phantom-<profile>" and scoped to that prefix in both
                 buckets.
  --rotate       Force rotation of the access key. By default a
                 re-run on an existing profile preserves the key.

Environment (mc must be configured against your local MinIO):
  MC_ALIAS=local       # mc alias to use (default: local)
  PHANTOM_BUCKET_VAULT=phantom-vault
  PHANTOM_BUCKET_ARTIFACTS=phantom-artifacts

Example:
  mc alias set local http://localhost:9090 admin admin-password
  ./bootstrap-buckets.sh personal
USAGE
  exit 1
fi

MC_ALIAS="${MC_ALIAS:-local}"
BUCKET_VAULT="${PHANTOM_BUCKET_VAULT:-phantom-vault}"
BUCKET_ARTIFACTS="${PHANTOM_BUCKET_ARTIFACTS:-phantom-artifacts}"
IAM_USER="phantom-${PROFILE}"
POLICY_NAME="phantom-${PROFILE}-policy"

command -v mc >/dev/null || {
  echo "error: 'mc' (MinIO client) not installed. brew install minio/stable/mc" >&2
  exit 1
}

mc admin info "$MC_ALIAS" >/dev/null || {
  echo "error: mc alias '$MC_ALIAS' not configured or unreachable." >&2
  echo "       mc alias set $MC_ALIAS http://<host>:9090 <root-user> <root-password>" >&2
  exit 1
}

# 1. Buckets — idempotent
for bucket in "$BUCKET_VAULT" "$BUCKET_ARTIFACTS"; do
  if mc ls "$MC_ALIAS/$bucket" >/dev/null 2>&1; then
    echo "  bucket exists: $bucket"
  else
    mc mb "$MC_ALIAS/$bucket"
    echo "  bucket created: $bucket"
  fi
done

# 2. Profile-scoped policy. Allow read/write only under the profile's
# prefix in both buckets. ListBucket needs a prefix condition so cross-
# profile listings return 403 even though the bucket-level perm is
# implied by the resource list.
POLICY_JSON=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": [
        "arn:aws:s3:::${BUCKET_VAULT}/${PROFILE}/*",
        "arn:aws:s3:::${BUCKET_ARTIFACTS}/${PROFILE}/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::${BUCKET_VAULT}",
        "arn:aws:s3:::${BUCKET_ARTIFACTS}"
      ],
      "Condition": {
        "StringLike": {
          "s3:prefix": ["${PROFILE}/*", "${PROFILE}"]
        }
      }
    }
  ]
}
EOF
)

TMPFILE=$(mktemp -t phantom-policy.XXXXXX)
trap 'rm -f "$TMPFILE"' EXIT
echo "$POLICY_JSON" > "$TMPFILE"

mc admin policy create "$MC_ALIAS" "$POLICY_NAME" "$TMPFILE" >/dev/null 2>&1 \
  || mc admin policy update "$MC_ALIAS" "$POLICY_NAME" "$TMPFILE" >/dev/null
echo "  policy applied: $POLICY_NAME"

# 3. IAM user — create or rotate
USER_EXISTS=$(mc admin user info "$MC_ALIAS" "$IAM_USER" 2>/dev/null && echo yes || echo no)

if [[ "$USER_EXISTS" == "yes" && "$ROTATE" != "--rotate" ]]; then
  echo "  user exists: $IAM_USER (use --rotate to issue a new key)"
  ACCESS_KEY=""
  SECRET_KEY=""
else
  ACCESS_KEY="phantom-${PROFILE}-$(openssl rand -hex 6)"
  SECRET_KEY=$(openssl rand -base64 32 | tr -d '/+=' | cut -c1-32)
  if [[ "$USER_EXISTS" == "yes" ]]; then
    mc admin user remove "$MC_ALIAS" "$IAM_USER" >/dev/null
  fi
  mc admin user add "$MC_ALIAS" "$ACCESS_KEY" "$SECRET_KEY" >/dev/null
  IAM_USER="$ACCESS_KEY"  # MinIO uses access key as the user name
  echo "  user created: $IAM_USER"
fi

mc admin policy attach "$MC_ALIAS" "$POLICY_NAME" --user "$IAM_USER" >/dev/null 2>&1 || true
echo "  policy attached to user: $IAM_USER"

# 4. Print the credentials block on success. Stored OUT-OF-BAND — never
# echo SECRET_KEY into a file the user might commit.
if [[ -n "$ACCESS_KEY" ]]; then
  cat <<RESULT

────────────────────────────────────────────────────────────
profile: $PROFILE
access_key: $ACCESS_KEY
secret_key: $SECRET_KEY

Add to ~/.config/phantom-ink/brainbox/brainbox.env:
  CL_MINIO__ENDPOINT=http://<minio-host>:9090
  CL_MINIO__ACCESS_KEY=$ACCESS_KEY
  CL_MINIO__SECRET_KEY=$SECRET_KEY
  CL_MINIO__BUCKET_ARTIFACTS=$BUCKET_ARTIFACTS
  CL_MINIO__BUCKET_VAULT=$BUCKET_VAULT
────────────────────────────────────────────────────────────
RESULT
fi
