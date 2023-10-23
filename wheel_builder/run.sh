#!/bin/sh

cd /app/dd-trace-py/
git config --global --add safe.directory /app/dd-trace-py
python -m build --wheel --outdir /tmp/wheels/
for whl in /tmp/wheels/*.whl; do
  auditwheel repair "${whl}" -w /tmp/wheels --plat manylinux2014_x86_64 && \
  rm -rf $whl
done
echo "Done building wheel I guess"
