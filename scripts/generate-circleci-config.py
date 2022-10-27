#!/usr/bin/env python3


import logging

logger = logging.getLogger()

try:
    import yaml
except:
    logger.warning("Yaml not found")


def main():
    # type (None) -> None
    output = """
version: 2.1

default_resource_class: &default_resource_class medium
cimg_base_image: &cimg_base_image cimg/base:stable
python310_image: &python310_image cimg/python:3.10
ddtrace_dev_image: &ddtrace_dev_image datadog/dd-trace-py:buster
redis_image: &redis_image redis:4.0-alpine
memcached_image: &memcached_image memcached:1.5-alpine
cassandra_image: &cassandra_image cassandra:3.11.7
consul_image: &consul_image consul:1.6.0
moto_image: &moto_image palazzem/moto:1.0.1
mysql_image: &mysql_image mysql:5.7
postgres_image: &postgres_image postgres:11-alpine
mongo_image: &mongo_image mongo:3.6
httpbin_image: &httpbin_image kennethreitz/httpbin@sha256:2c7abc4803080c22928265744410173b6fea3b898872c01c5fd0f0f9df4a59fb
vertica_image: &vertica_image sumitchawla/vertica:latest
rabbitmq_image: &rabbitmq_image rabbitmq:3.7-alpine

orbs:
  win: circleci/windows@5.0

machine_executor: &machine_executor
  machine:
    image: ubuntu-2004:current
  environment:
    - BOTO_CONFIG: /dev/null
    # https://support.circleci.com/hc/en-us/articles/360045268074-Build-Fails-with-Too-long-with-no-output-exceeded-10m0s-context-deadline-exceeded-
    - PYTHONUNBUFFERED: 1
  steps:
    - &pyenv-set-global
      run:
        name: Set global pyenv
        command: |
          pyenv global 3.9.4

contrib_job: &contrib_job
  executor: ddtrace_dev
  parallelism: 4

contrib_job_small: &contrib_job_small
  executor: ddtrace_dev_small
  parallelism: 1

commands:
  save_coverage:
    description: "Save coverage.py results to workspace"
    steps:
      - run: |
          set -ex
          mkdir coverage
          if [ -f .coverage ];
          then
            cp .coverage ./coverage/$CIRCLE_BUILD_NUM-$CIRCLE_JOB-$CIRCLE_NODE_INDEX.coverage
          fi
      - persist_to_workspace:
          root: coverage
          paths:
            - "*.coverage"
      - store_artifacts:
          path: coverage

  setup_tox:
    description: "Install tox"
    steps:
      - run: pip install -U tox

  setup_riot:
    description: "Install riot"
    steps:
      # Make sure we install and run riot on Python 3
      - run: pip3 install riot

  restore_tox_cache:
    description: "Restore .tox directory from previous runs for faster installs"
    steps:
      - restore_cache:
          # In the cache key:
          #   - .Environment.CIRCLE_JOB: We do separate tox environments by job name, so caching and restoring is
          #                              much faster.
          key: tox-cache-{{ .Environment.CIRCLE_JOB }}-{{ checksum "tox.ini" }}-{{ checksum "setup.py" }}

  save_tox_cache:
    description: "Save .tox directory into cache for faster installs next time"
    steps:
      - save_cache:
          # In the cache key:
          #   - .Environment.CIRCLE_JOB: We do separate tox environments by job name, so caching and restoring is
          #                              much faster.
          key: tox-cache-{{ .Environment.CIRCLE_JOB }}-{{ checksum "tox.ini" }}-{{ checksum "setup.py" }}
          paths:
            - ".tox"

  save_pip_cache:
    description: "Save pip cache directory"
    steps:
      - save_cache:
          # DEV: Cache misses can still occur as venvs are not necessarily always created on the same node index
          key: pip-cache-{{ .Environment.CIRCLE_JOB }}-{{ .Environment.CIRCLE_NODE_INDEX }}-{{ checksum "riotfile.py" }}-{{ checksum "setup.py" }}
          paths:
            - ".cache/pip"

  restore_pip_cache:
    description: "Restore pip cache directory"
    steps:
      - restore_cache:
          key: pip-cache-{{ .Environment.CIRCLE_JOB }}-{{ .Environment.CIRCLE_NODE_INDEX }}-{{ checksum "riotfile.py" }}-{{ checksum "setup.py" }}

  start_docker_services:
    description: "Start Docker services"
    parameters:
      env:
        type: string
        default: ""
      services:
        type: string
        default: ""
    steps:
      # Retry pulls in case they fail
      - run: for i in {1..3}; do docker-compose pull -q << parameters.services >> && break || sleep 3; done
      - run: << parameters.env >> docker-compose up -d << parameters.services >>
      - run:
          command: docker-compose logs -f
          background: true

  run_test:
    description: "Run tests matching a pattern"
    parameters:
      pattern:
        type: string
        default: ""
      wait:
        type: string
        default: ""
      snapshot:
        type: boolean
        default: false
      docker_services:
        type: string
        default: ""
      store_coverage:
        type: boolean
        default: true
      riotfile:
        type: string
        default: "riotfile.py"
    steps:
      - attach_workspace:
          at: .
      - checkout
      - restore_pip_cache
      - when:
          condition:
              << parameters.snapshot >>
          steps:
            - setup_riot
            - start_docker_services:
                env: SNAPSHOT_CI=1
                services: testagent << parameters.docker_services >>
            - run:
                environment:
                  DD_TRACE_AGENT_URL: http://localhost:9126

                command: |
                  mv .riot .ddriot
                  riot -f '<<parameters.riotfile>>' list -i '<<parameters.pattern>>' | circleci tests split | xargs -I PY ./scripts/ddtest riot -f '<<parameters.riotfile>>' -v run --python=PY --exitfirst --pass-env -s '<< parameters.pattern >>'
      - unless:
          condition:
              << parameters.snapshot >>
          steps:
            - when:
                condition:
                  << parameters.wait >>
                steps:
                  - setup_tox
                  - run:
                      name: "Waiting for << parameters.wait >>"
                      command: tox -e 'wait' << parameters.wait >>
            - setup_riot
            - run:
                command: "riot list -i '<<parameters.pattern>>' | circleci tests split | xargs -I PY riot -v run --python=PY --exitfirst --pass-env -s '<< parameters.pattern >>'"
      - save_pip_cache
      - when:
          condition:
            << parameters.store_coverage >>
          steps:
            - save_coverage
      - store_test_results:
          path: test-results
      - store_artifacts:
          path: test-results

  run_tox_scenario_with_testagent:
    description: Run scripts/run-tox-scenario with setup, caching persistence and the testagent
    parameters:
      pattern:
        type: string
      wait:
        type: string
        default: ""
    steps:
      - checkout
      - restore_tox_cache
      - when:
          condition:
            << parameters.wait >>
          steps:
            - run:
                name: "Waiting for << parameters.wait >>"
                command: tox -e 'wait' << parameters.wait >>
      - start_docker_services:
          env: SNAPSHOT_CI=1
          services: memcached redis testagent
      - run:
          name: "Run scripts/run-tox-scenario"
          environment:
            DD_TRACE_AGENT_URL: http://localhost:9126
          command: ./scripts/ddtest scripts/run-tox-scenario '<< parameters.pattern >>'
      - save_tox_cache

  run_tox_scenario:
    description: "Run scripts/run-tox-scenario with setup, caching and persistence"
    parameters:
      pattern:
        type: string
      wait:
        type: string
        default: ""
      store_coverage:
        type: boolean
        default: true
    steps:
      - checkout
      - setup_tox
      - restore_tox_cache
      - when:
          condition:
            << parameters.wait >>
          steps:
            - run:
                name: "Waiting for << parameters.wait >>"
                command: tox -e 'wait' << parameters.wait >>
      - run:
          name: "Run scripts/run-tox-scenario"
          command: scripts/run-tox-scenario '<< parameters.pattern >>'
      - save_tox_cache
      - when:
          condition:
            << parameters.store_coverage >>
          steps:
            - save_coverage
      - store_test_results:
          path: test-results
      - store_artifacts:
          path: test-results

executors:
  cimg_base:
    docker:
      - image: *cimg_base_image
    resource_class: medium
  python310:
    docker:
      - image: *python310_image
    resource_class: large
  ddtrace_dev:
    docker:
      - image: *ddtrace_dev_image
    resource_class: *default_resource_class
  ddtrace_dev_small:
    docker:
      - image: *ddtrace_dev_image
    resource_class: small

# Common configuration blocks as YAML anchors
# See: https://circleci.com/blog/circleci-hacks-reuse-yaml-in-your-circleci-config-with-yaml/
httpbin_local: &httpbin_local
  image: *httpbin_image
  name: httpbin.org

mysql_server: &mysql_server
  image: *mysql_image
  environment:
    - MYSQL_ROOT_PASSWORD=admin
    - MYSQL_PASSWORD=test
    - MYSQL_USER=test
    - MYSQL_DATABASE=test

postgres_server: &postgres_server
  image: *postgres_image
  environment:
    - POSTGRES_PASSWORD=postgres
    - POSTGRES_USER=postgres
    - POSTGRES_DB=postgres

jobs:
  run_tox_contrib_job:
    parameters:
      pattern:
        type: string
        default: ""
    executor: ddtrace_dev
    parallelism: 4
    steps:
      - run_tox_scenario:
          pattern: <<parameters.pattern>>

  run_tox_contrib_job_small:
    parameters:
      pattern:
        type: string
        default: ""
    executor: ddtrace_dev_small
    parallelism: 1
    steps:
      - run_tox_scenario:
          pattern: <<parameters.pattern>>

  run_test_machine_executor:
    parameters:
      pattern:
        type: string
        default: ""
    machine:
      image: ubuntu-2004:current
    environment:
      - BOTO_CONFIG: /dev/null
    steps:
      - run_test:
          pattern: <<parameters.pattern>>
          snapshot: true

  run_test_contrib_job:
    parameters:
      pattern:
        type: string
        default: ""
    executor: ddtrace_dev
    parallelism: 4
    steps:
      - run_test:
          pattern: <<parameters.pattern>>

  run_test_contrib_job_small:
    parameters:
      pattern:
        type: string
        default: ""
    executor: ddtrace_dev_small
    parallelism: 1
    steps:
      - run_test:
          pattern: <<parameters.pattern>>

  pre_check:
    executor: python310
    steps:
      - checkout
      - setup_riot
      - run:
          name: "Formatting check"
          command: riot run -s fmt && git diff --exit-code
      - run:
          name: "Flake8 check"
          command: riot run -s flake8
      - run:
          name: "Slots check"
          command: riot run -s slotscheck
      - run:
          name: "Mypy check"
          command: riot run -s mypy
      - run:
          name: "Codespell check"
          command: riot run -s codespell
      - run:
          name: "Test agent snapshot check"
          command: riot run -s snapshot-fmt && git diff --exit-code

  ccheck:
    executor: cimg_base
    steps:
      - checkout
      - run: sudo apt-get update
      - run: sudo apt-get install --yes clang-format gcc-10 g++-10 python3 python3-setuptools python3-pip cppcheck
      - run: scripts/cformat.sh
      - run: scripts/cppcheck.sh
      - run: DD_COMPILE_DEBUG=1 DD_TESTING_RAISE=1 CC=gcc-10 CXX=g++-10 pip -vvv install .

  coverage_report:
    executor: python310
    steps:
      - checkout
      - attach_workspace:
          at: .
      - run: pip install coverage codecov diff_cover
      - run: ls -hal *.coverage
      # Combine all job coverage reports into one
      - run: coverage combine *.coverage
      # Upload coverage report to Codecov
      # DEV: Do not use the bash uploader, it cannot be trusted
      - run: codecov
      # Generate and save xml report
      # DEV: "--ignore-errors" to skip over files that are missing
      - run: coverage xml --ignore-errors
      - store_artifacts:
          path: coverage.xml
      # Generate and save JSON report
      # DEV: "--ignore-errors" to skip over files that are missing
      - run: coverage json --ignore-errors
      - store_artifacts:
          path: coverage.json
      # Print ddtrace/ report to stdout
      # DEV: "--ignore-errors" to skip over files that are missing
      - run: coverage report --ignore-errors --omit=tests/
      # Print tests/ report to stdout
      # DEV: "--ignore-errors" to skip over files that are missing
      - run: coverage report --ignore-errors --omit=ddtrace/
      # Print diff-cover report to stdout (compares against origin/1.x)
      - run: diff-cover --compare-branch $(git rev-parse --abbrev-ref origin/HEAD) coverage.xml

  build_base_venvs:
    resource_class: large
    docker:
      - image: *ddtrace_dev_image
    parallelism: 7
    steps:
      - checkout
      - setup_riot
      - run:
          name: "Run riotfile.py tests"
          command: riot run -s riot-helpers
      - run:
          name: "Run scripts/*.py tests"
          command: riot run -s scripts
      - run:
          name: "Generate base virtual environments."
          # DEV: riot list -i tracer lists all supported Python versions
          command: "riot list -i tracer | circleci tests split | xargs -I PY riot -v generate --python=PY"
      - persist_to_workspace:
          root: .
          paths:
            - "."

  build_latest_venvs:
    resource_class: large
    docker:
      - image: *ddtrace_dev_image
    parallelism: 7
    steps:
      - checkout
      - setup_riot
      - run:
          name: "Run riotfile.py tests"
          command: riot -f riotfile-latest.py run -s riot-helpers
      - run:
          name: "Run scripts/*.py tests"
          command: riot -f riotfile-latest.py run -s scripts
      - run:
          name: "Generate base virtual environments."
          # DEV: riot list -i tracer lists all supported Python versions
          command: "riot -f riotfile-latest.py list -i tracer | circleci tests split | xargs -I PY riot -f riotfile-latest.py -v generate --python=PY"
      - persist_to_workspace:
          root: .
          paths:
            - "."

  appsec:
    <<: *machine_executor
    steps:
      - run_test:
          pattern: 'appsec'
          snapshot: true

  tracer:
    <<: *contrib_job
    parallelism: 7
    steps:
      - run_test:
          pattern: "tracer"
      - run_tox_scenario:
          # Riot venvs break with Py 3.11 importlib, specifically with hypothesis (test_http.py).
          # We skip the test_http.py tests in riot and run the test_http.py tests through tox.
          pattern: '^py.\+-tracer_test_http'

  telemetry:
    <<: *machine_executor
    steps:
      - run_test:
          pattern: "telemetry"
          snapshot: true
          store_coverage: false

  debugger:
    <<: *contrib_job
    parallelism: 7
    steps:
      - run_test:
          pattern: "debugger"

  opentracer:
    <<: *contrib_job
    parallelism: 7
    steps:
      - run_tox_scenario:
          pattern: '^py.\+-opentracer'

  # Building gevent (for which we never want wheels because they crash)
  # on Python 2.7 requires Microsoft Visual C++ 9.0 which is not installed. :(
  # which is not installed.
  # profile-windows-27:
  #   executor:
  #     name: win/default
  #     shell: bash.exe
  #   steps:
  #     - run: choco install python2
  #     - run_tox_scenario:
  #         store_coverage: false
  #         pattern: '^py27-profile'

  profile-windows-35:
    executor:
      name: win/default
      shell: bash.exe
    steps:
      - run: choco install -y python --version=3.5.4 --side-by-side
      - run_tox_scenario:
          store_coverage: false
          pattern: '^py35-profile'

  profile-windows-36:
    executor:
      name: win/default
      shell: bash.exe
    steps:
      - run: choco install -y python --version=3.6.8 --side-by-side
      - run_tox_scenario:
          store_coverage: false
          pattern: '^py36-profile'

  # For whatever reason, choco does not install Python 3.7 correctly on Windows
  # profile-windows-37:
  #   executor:
  #     name: win/default
  #     shell: bash.exe
  #   steps:
  #     - run: choco install python --version=3.7.9
  #     - run_tox_scenario:
  #         store_coverage: false
  #         pattern: '^py37-profile'

  profile-windows-38:
    executor:
      name: win/default
      shell: bash.exe
    steps:
      - run: choco install -y python --version=3.8.10 --side-by-side
      - run_tox_scenario:
          store_coverage: false
          pattern: '^py38-profile'

  profile-windows-39:
    executor:
      name: win/default
      shell: bash.exe
    steps:
      - run: choco install -y python --version=3.9.12 --side-by-side
      - run_tox_scenario:
          store_coverage: false
          pattern: '^py39-profile'

  profile-windows-310:
    executor:
      name: win/default
      shell: bash.exe
    steps:
      # circleci/windows@5.0 orb includes python 3.10.6
      - run_tox_scenario:
          store_coverage: false
          pattern: '^py310-profile'

  profile:
    <<: *contrib_job
    resource_class: large
    parallelism: 7
    steps:
      - run_tox_scenario:
          store_coverage: false
          pattern: '^py.\+-profile'

  integration_agent5:
    <<: *machine_executor
    steps:
      - attach_workspace:
          at: .
      - checkout
      - start_docker_services:
          services: ddagent5
      - run:
          command: |
            mv .riot .ddriot
            ./scripts/ddtest riot -v run --pass-env -s 'integration-v5'

  integration_agent:
    <<: *machine_executor
    steps:
      - attach_workspace:
          at: .
      - checkout
      - start_docker_services:
          services: ddagent
      - run:
          command: |
            mv .riot .ddriot
            ./scripts/ddtest riot -v run --pass-env -s 'integration-latest'

  integration_testagent:
    <<: *machine_executor
    steps:
      - attach_workspace:
          at: .
      - checkout
      - start_docker_services:
          env: SNAPSHOT_CI=1
          services: testagent
      - run:
          environment:
            DD_TRACE_AGENT_URL: http://localhost:9126
          command: |
            mv .riot .ddriot
            ./scripts/ddtest riot -v run --pass-env -s 'integration-snapshot'


  boto:
    <<: *machine_executor
    parallelism: 4
    steps:
      - run_test:
          pattern: '^boto'  # run boto and botocore
          snapshot: true
          docker_services: "localstack"

  ddtracerun:
    <<: *contrib_job
    docker:
      - image: *ddtrace_dev_image
      - image: *redis_image
    steps:
      - run_test:
          store_coverage: false
          pattern: 'ddtracerun'

  asyncpg:
    <<: *machine_executor
    steps:
      - run_test:
          pattern: 'asyncpg'
          snapshot: true
          docker_services: 'postgres'

  aiohttp:
    <<: *machine_executor
    parallelism: 6
    steps:
      - run_test:
          pattern: 'aiohttp'  # includes aiohttp_jinja2
          snapshot: true
          docker_services: 'httpbin_local'

  aiohttp_latest:
    <<: *machine_executor
    parallelism: 5
    steps:
      - run_test:
          pattern: 'aiohttp'  # includes aiohttp_jinja2
          riotfile: 'riotfile-latest.py'
          snapshot: true
          docker_services: 'httpbin_local'


  cassandra:
    <<: *contrib_job
    docker:
      - image: *ddtrace_dev_image
        environment:
          CASS_DRIVER_NO_EXTENSIONS: 1
      - image: *cassandra_image
        environment:
          - MAX_HEAP_SIZE=512M
          - HEAP_NEWSIZE=256M
    steps:
      - run_test:
          wait: cassandra
          pattern: 'cassandra'

  celery:
    <<: *contrib_job
    parallelism: 7
    docker:
      - image: *ddtrace_dev_image
      - image: redis:4.0-alpine
      - image: *rabbitmq_image
    steps:
      - run_test:
          pattern: 'celery'

  cherrypy:
    <<: *machine_executor
    parallelism: 6
    steps:
      - run_test:
          pattern: 'cherrypy'
          snapshot: true

  consul:
    <<: *contrib_job
    docker:
      - image: *ddtrace_dev_image
      - image: *consul_image
    steps:
      - run_tox_scenario:
          pattern: '^consul_contrib-'

  elasticsearch:
    <<: *machine_executor
    parallelism: 4
    steps:
      - run_test:
          pattern: 'elasticsearch'
          snapshot: true
          docker_services: 'elasticsearch'

  django:
    <<: *machine_executor
    parallelism: 6
    steps:
      - run_test:
          pattern: 'django$'
          snapshot: true
          docker_services: "memcached redis postgres"

  djangorestframework:
    <<: *machine_executor
    parallelism: 6
    steps:
      - run_test:
          pattern: 'djangorestframework'
          snapshot: true
          docker_services: "memcached redis"


  flask:
    <<: *machine_executor
    parallelism: 7
    steps:
      - run_test:
          # Run both flask and flask_cache test suites
          # TODO: Re-enable coverage for Flask tests
          store_coverage: false
          snapshot: true
          pattern: "flask"
          docker_services: "memcached redis"

  gevent:
    <<: *contrib_job
    parallelism: 7
    steps:
      - run_tox_scenario:
          pattern: '^gevent_contrib-'

  grpc:
    <<: *machine_executor
    parallelism: 7
    steps:
      - run_test:
          pattern: "grpc"
          snapshot: true

  httplib:
    <<: *machine_executor
    steps:
      - run_test:
          pattern: "httplib"
          snapshot: true
          docker_services: 'httpbin_local'

  httpx:
    <<: *machine_executor
    steps:
      - run_test:
          pattern: 'httpx'
          snapshot: true
          docker_services: 'httpbin_local'

  mariadb:
    <<: *machine_executor
    steps:
      - run_test:
          pattern: 'mariadb$'
          snapshot: true
          docker_services: "mariadb"

  mysqlconnector:
    <<: *contrib_job
    docker:
      - image: *ddtrace_dev_image
      - *mysql_server
    steps:
      - run_test:
          wait: mysql
          pattern: 'mysql'

  mysqlpython:
    <<: *contrib_job
    docker:
      - image: *ddtrace_dev_image
      - *mysql_server
    steps:
      - run_tox_scenario:
          wait: mysql
          pattern: '^mysqldb_contrib-.*-mysqlclient'

  pymysql:
    <<: *contrib_job
    docker:
      - image: *ddtrace_dev_image
      - *mysql_server
    steps:
      - run_test:
          wait: mysql
          pattern: 'pymysql'

  pylibmc:
    <<: *contrib_job
    docker:
      - image: *ddtrace_dev_image
      - image: *memcached_image
    steps:
      - run_tox_scenario:
          pattern: '^pylibmc_contrib-'

  pymemcache:
    <<: *contrib_job
    docker:
      - image: *ddtrace_dev_image
      - image: *memcached_image
    steps:
      - run_test:
          pattern: "pymemcache"

  mongoengine:
    <<: *machine_executor
    parallelism: 1
    steps:
      - run_test:
          pattern: 'mongoengine'
          snapshot: true
          docker_services: 'mongo'

  pymongo:
    <<: *contrib_job
    docker:
      - image: *ddtrace_dev_image
      - image: *mongo_image
    steps:
      - run_test:
          pattern: "pymongo"

  pyramid_latest:
    <<: *machine_executor
    steps:
      - run_test:
          pattern: 'pyramid'
          riotfile: "riotfile-latest.py"
          snapshot: true

  requests:
    <<: *contrib_job
    docker:
      - image: *ddtrace_dev_image
      - *httpbin_local
    steps:
      - run_test:
          pattern: "requests"

  snowflake:
    <<: *machine_executor
    parallelism: 4
    steps:
      - run_test:
          pattern: "snowflake"
          snapshot: true

  sqlalchemy:
    <<: *contrib_job
    docker:
      - image: *ddtrace_dev_image
      - *postgres_server
      - *mysql_server
    steps:
      - run_test:
          wait: postgres mysql
          pattern: "sqlalchemy"

  psycopg:
    <<: *machine_executor
    parallelism: 4
    steps:
      - run_test:
          pattern: "psycopg"
          snapshot: true
          docker_services: "postgres"

  aiobotocore:
    <<: *contrib_job
    docker:
      - image: *ddtrace_dev_image
      - image: *moto_image
    steps:
       - run_test:
          pattern: 'aiobotocore'

  aiobotocore_latest:
    <<: *contrib_job
    docker:
      - image: *ddtrace_dev_image
      - image: *moto_image
    steps:
       - run_test:
          riotfile: 'riotfile-latest.py'
          pattern: 'aiobotocore'

  aiomysql:
    <<: *machine_executor
    steps:
      - run_test:
          docker_services: 'mysql'
          wait: mysql
          pattern: 'aiomysql'
          snapshot: true

  aiopg:
    <<: *contrib_job
    docker:
      - image: *ddtrace_dev_image
      - *postgres_server
    steps:
      - run_test:
          wait: postgres
          pattern: 'aiopg'

  aioredis:
    <<: *machine_executor
    parallelism: 4
    steps:
      - run_test:
          docker_services: 'redis'
          pattern: 'aioredis$'
          snapshot: true

  aredis:
    <<: *machine_executor
    parallelism: 4
    steps:
      - run_test:
          docker_services: 'redis'
          pattern: 'aredis$'
          snapshot: true

  yaaredis:
    <<: *machine_executor
    parallelism: 4
    steps:
      - run_test:
          docker_services: 'redis'
          pattern: 'yaaredis$'
          snapshot: true

  redis:
    <<: *machine_executor
    parallelism: 4
    steps:
      - run_test:
          docker_services: 'redis'
          pattern: 'redis$'
          snapshot: true

  rediscluster:
    <<: *machine_executor
    steps:
      - run_test:
          pattern: 'rediscluster'
          docker_services: 'rediscluster'
          snapshot: true

  rq:
    <<: *machine_executor
    parallelism: 4
    steps:
      - run_test:
          pattern: "rq"
          snapshot: true
          docker_services: "redis"

  urllib3:
    <<: *machine_executor
    steps:
      - run_test:
          pattern: 'urllib3'
          snapshot: true
          docker_services: "httpbin_local"

  vertica:
    <<: *contrib_job
    docker:
      - image: *ddtrace_dev_image
      - image: *vertica_image
        environment:
          - VP_TEST_USER=dbadmin
          - VP_TEST_PASSWORD=abc123
          - VP_TEST_DATABASE=docker
    steps:
      - run_tox_scenario:
          wait: vertica
          pattern: '^vertica_contrib-'

  kombu:
    <<: *contrib_job
    parallelism: 7
    docker:
      - image: *ddtrace_dev_image
      - image: *rabbitmq_image
    steps:
      - run_tox_scenario:
          wait: rabbitmq
          pattern: '^kombu_contrib-'

  benchmarks:
    <<: *contrib_job
    steps:
      - run_test:
          store_coverage: false
          pattern: '^benchmarks'

  build_docs:
    # build documentation and store as an artifact
    executor: ddtrace_dev
    steps:
      - setup_riot
      - checkout
      - run:
          command: |
             riot -v run docs
             mkdir -p /tmp/docs
             cp -r docs/_build/html/* /tmp/docs
      - store_artifacts:
          path: /tmp/docs

requires_pre_check: &requires_pre_check
  requires:
    - pre_check
    - ccheck

requires_base_venvs: &requires_base_venvs
  requires:
    - pre_check
    - ccheck
    - build_base_venvs

requires_latest_venvs: &requires_latest_venvs
  requires:
    - ccheck
    - build_latest_venvs

requires_tests: &requires_tests
  requires:
    - run_test_contrib_job
    - run_test_contrib_job_small
    - run_test_machine_executor
    - run_tox_contrib_job
    - run_tox_contrib_job_small
    - aiopg
    - aioredis
    - asyncpg
    - benchmarks
    - boto
    - cassandra
    - celery
    - cherrypy
    - consul
    - ddtracerun
    - django
    - djangorestframework
    - elasticsearch
    - flask
    - gevent
    - grpc
    - httplib
    - httpx
    - integration_agent5
    - integration_agent
    - integration_testagent
    - profile
    - kombu
    - mariadb
    - mongoengine
    - mysqlconnector
    - mysqlpython
    - opentracer
    - psycopg
    - pylibmc
    - pymemcache
    - pymongo
    - pymysql
    - aredis
    - yaaredis
    - redis
    - rediscluster
    - requests
    - rq
    - snowflake
    - sqlalchemy
    - tracer
    - telemetry
    - debugger
    - appsec
    - urllib3
    - vertica
    # - profile-windows-27
    - profile-windows-35
    - profile-windows-36
    # - profile-windows-37
    - profile-windows-38
    - profile-windows-39
    - profile-windows-310

workflows:
  version: 2
  test: &workflow_test
    jobs:
      # Pre-checking before running all jobs
      - pre_check
      - ccheck

      # Build necessary base venvs for integration tests
      - build_base_venvs

      # Docs
      - build_docs: *requires_pre_check

      - run_test_contrib_job:
          requires:
            - pre_check
            - ccheck
            - build_base_venvs
          matrix:
            parameters:
              pattern:
                - 'jinja2'
                - "pynamodb"
                - 'falcon'
                - 'internal'

      - run_test_contrib_job_small:
          requires:
            - pre_check
            - ccheck
            - build_base_venvs
          matrix:
            parameters:
              pattern:
                - 'vendor'
                - 'test_logging'
                - 'pylons'
                - 'mako'
                - 'asgi$'
                - 'pytest$'
                - 'pytest-bdd'

      - run_test_machine_executor:
          requires:
            - pre_check
            - ccheck
            - build_base_venvs
          matrix:
            parameters:
              pattern:
                - "fastapi"
                - "django_hosts$"
                - "graphene"
                - "graphql"
                - 'pyramid'
                - "sanic"
                - "starlette"
                - "wsgi"

      - run_tox_contrib_job:
          requires:
            - pre_check
            - ccheck
            - build_base_venvs
          matrix:
            parameters:
              pattern:
                - '^sqlite3_contrib-'
                - '^algoliasearch_contrib-'
                - '^requests_gevent_contrib-'
                - '^molten_contrib-'
                - '^dogpile_contrib-'
                - '^bottle_contrib\(_autopatch\)\?-'
                - '^tornado_contrib-'
                - '^pyodbc_contrib-'
                - '^dbapi_contrib-'

      - run_tox_contrib_job_small:
          requires:
            - pre_check
            - ccheck
            - build_base_venvs
          matrix:
            parameters:
              pattern:
                - '^futures_contrib-'
                - '^asyncio_contrib-'

       # Integration test suites
      - aiobotocore: *requires_base_venvs
      - aiohttp: *requires_base_venvs
      - aiomysql: *requires_base_venvs
      - aiopg: *requires_base_venvs
      - aioredis: *requires_base_venvs
      - asyncpg: *requires_base_venvs
      - benchmarks: *requires_base_venvs
      - boto: *requires_base_venvs
      - cassandra: *requires_base_venvs
      - celery: *requires_base_venvs
      - cherrypy: *requires_base_venvs
      - consul: *requires_base_venvs
      - ddtracerun: *requires_base_venvs
      - django: *requires_base_venvs
      - djangorestframework: *requires_base_venvs
      - elasticsearch: *requires_base_venvs
      - flask: *requires_base_venvs
      - gevent: *requires_base_venvs
      - grpc: *requires_base_venvs
      - httplib: *requires_base_venvs
      - httpx: *requires_base_venvs
      - integration_agent5: *requires_base_venvs
      - integration_agent: *requires_base_venvs
      - integration_testagent: *requires_base_venvs
      - profile: *requires_base_venvs
      - kombu: *requires_base_venvs
      - mariadb: *requires_base_venvs
      - mongoengine: *requires_base_venvs
      - mysqlconnector: *requires_base_venvs
      - mysqlpython: *requires_base_venvs
      - opentracer: *requires_base_venvs
      - psycopg: *requires_base_venvs
      - pylibmc: *requires_base_venvs
      - pymemcache: *requires_base_venvs
      - pymongo: *requires_base_venvs
      - pymysql: *requires_base_venvs
      - aredis: *requires_base_venvs
      - yaaredis: *requires_base_venvs
      - redis: *requires_base_venvs
      - rediscluster: *requires_base_venvs
      - requests: *requires_base_venvs
      - rq: *requires_base_venvs
      - snowflake: *requires_base_venvs
      - sqlalchemy: *requires_base_venvs
      - tracer: *requires_base_venvs
      - telemetry: *requires_base_venvs
      - debugger: *requires_base_venvs
      - appsec: *requires_base_venvs
      - urllib3: *requires_base_venvs
      - vertica: *requires_base_venvs

      # - profile-windows-27: *requires_pre_check
      - profile-windows-35: *requires_pre_check
      - profile-windows-36: *requires_pre_check
      # - profile-windows-37: *requires_pre_check
      - profile-windows-38: *requires_pre_check
      - profile-windows-39: *requires_pre_check
      - profile-windows-310: *requires_pre_check
      # Final reports
      - coverage_report: *requires_tests

  test_latest:
    # requires:
    #   - test
    jobs:
      # Integration test suites
      - ccheck
      - build_latest_venvs
      - aiobotocore_latest: *requires_latest_venvs
      - aiohttp_latest: *requires_latest_venvs
      - pyramid_latest: *requires_latest_venvs

  test_nightly:
    <<: *workflow_test
    triggers:
      - schedule:
          cron: "0 0 * * *"
          filters:
            branches:
              only:
                - 0.x
                - 1.x

    """
    print(output)


if __name__ == "__main__":
    main()
