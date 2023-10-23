#!/bin/bash
rm -rf /tmp/wheels
mkdir -p /tmp/wheels

docker compose run --build wheel_builder
