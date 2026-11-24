#!/usr/bin/env bash
set -e

COUNTRY="${1:-Spain}"
COUNT="${2:-20}"

# python3 start.py \
#   --country "$COUNTRY" \
#   --country-code es \
#   --lang es \
#   --cities-count "$COUNT" \
#   --depth 5 \
#   --model qwen2.5:7b

python3 get_email_insta_fb_country.py \
  --country "$COUNTRY" \
  --country-code es \
  --lang es \
  --cities-count "$COUNT" \
  --depth 5 \
  --model qwen2.5:7b
rm result*
