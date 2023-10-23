#!/bin/bash
rm -rf /tmp/wheels
mkdir -p /tmp/wheels

if [[ ! -d "vendored/ddprof" ]]; then
  cd vendored
  ./get_ddprof.sh
fi
docker compose run --build wheel_builder
