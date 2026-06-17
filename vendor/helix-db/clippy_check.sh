#!/bin/sh
set -eu

cargo clippy --workspace -- -D warnings
