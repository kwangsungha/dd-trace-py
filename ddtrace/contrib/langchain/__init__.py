"""
The LangChain integration instruments the LangChain Python library to emit metrics,
traces, and logs (logs are disabled by default) for requests made to the LLMs,
chat models, embeddings, chains, and vector store interfaces.

All metrics, logs, and traces submitted from the LangChain integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- ``langchain.request.provider``: LLM provider used in the request.
- ``langchain.request.model``: LLM/Chat/Embeddings model used in the request.
- ``langchain.request.api_key``: LLM provider API key used to make the request (obfuscated into the format ``...XXXX`` where ``XXXX`` is the last 4 digits of the key).

Metrics
~~~~~~~

The following metrics are collected by default by the LangChain integration.

.. important::
    If the Agent is configured to use a non-default Statsd hostname or port, use ``DD_DOGSTATSD_URL`` to configure
    ``ddtrace`` to use it.


.. py:data:: langchain.request.duration

   The duration of the LangChain request in seconds.

   Type: ``distribution``


.. py:data:: langchain.request.error

   The number of errors from requests made with LangChain.

   Type: ``count``


.. py:data:: langchain.tokens.prompt

   The number of tokens used in the prompt of a LangChain request.

   Type: ``distribution``


.. py:data:: langchain.tokens.completion

   The number of tokens used in the completion of a LangChain response.

   Type: ``distribution``


.. py:data:: langchain.tokens.total

   The total number of tokens used in the prompt and completion of a LangChain request/response.

   Type: ``distribution``


.. py:data:: langchain.tokens.total_cost

   The estimated cost in USD based on token usage.

   Type: ``count``


(beta) Prompt and Completion Sampling
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The following data is collected in span tags with a default sampling rate of ``1.0``:

- Prompt inputs and completions for the ``LLM`` interface.
- Message inputs and completions for the ``ChatModel`` interface.
- Embedding inputs for the ``Embeddings`` interface.
- Prompt inputs, chain inputs, and outputs for the ``Chain`` interface.
- Query inputs and document outputs for the ``VectorStore`` interface.

Prompt and message inputs and completions can also be emitted as log data.
Logs are **not** emitted by default. When logs are enabled they are sampled at ``0.1``.

Read the **Global Configuration** section for information about enabling logs and configuring sampling
rates.

.. important::

    To submit logs, you must set the ``DD_API_KEY`` environment variable.

    Set ``DD_SITE`` to send logs to a Datadog site such as ``datadoghq.eu``. The default is ``datadoghq.com``.


Enabling
~~~~~~~~

The LangChain integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Note that these commands also enable the ``requests`` and ``aiohttp``
integrations which trace HTTP requests to LLM providers, as well as the
``openai`` integration which traces requests to the OpenAI library.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the LangChain integration::

    from ddtrace import config, patch

    # Note: be sure to configure the integration before calling ``patch()``!
    # eg. config.langchain["logs_enabled"] = True

    patch(langchain=True)

    # to trace synchronous HTTP requests
    # patch(langchain=True, requests=True)

    # to trace asynchronous HTTP requests (to the OpenAI library)
    # patch(langchain=True, aiohttp=True)

    # to include underlying OpenAI spans from the OpenAI integration
    # patch(langchain=True, openai=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.langchain["service"]

   The service name reported by default for LangChain requests.

   Alternatively, you can set this option with the ``DD_SERVICE`` or ``DD_LANGCHAIN_SERVICE`` environment
   variables.

   Default: ``DD_SERVICE``


.. py:data:: ddtrace.config.langchain["logs_enabled"]

   Enable collection of prompts and completions as logs. You can adjust the rate of prompts and completions collected
   using the sample rate configuration described below.

   Alternatively, you can set this option with one of the following environment variables:

   - ``DD_LLMOBS_LOGS_ENABLED``
   - ``DD_LANGCHAIN_LOGS_ENABLED``, which takes precedence over ``DD_LLMOBS_LOGS_ENABLED``.


   Note that you must set the ``DD_API_KEY`` environment variable to enable sending logs.

   Default: ``False``


.. py:data:: ddtrace.config.langchain["metrics_enabled"]

   Enable collection of LangChain metrics.

   If the Datadog Agent is configured to use a non-default Statsd hostname
   or port, use ``DD_DOGSTATSD_URL`` to configure ``ddtrace`` to use it.

   Alternatively, you can set this option with one of the following environment variables:

   - ``DD_LLMOBS_METRICS_ENABLED``
   - ``DD_LANGCHAIN_METRICS_ENABLED``, which takes precedence over ``DD_LLMOBS_METRICS_ENABLED``.

   Default: ``True``


.. py:data:: (beta) ddtrace.config._llmobs_span_char_limit

   Configure the maximum number of characters for the following data within span tags:

   - Prompt inputs and completions
   - Message inputs and completions
   - Embedding inputs

   Text exceeding the maximum number of characters is truncated to the character limit
   and has ``...`` appended to the end.

   Alternatively, you can set this option with the ``DD_LLMOBS_SPAN_CHAR_LIMIT`` environment
   variable.

   Default: ``128``


.. py:data:: (beta) ddtrace.config._llmobs_span_prompt_completion_sample_rate

   Configure the sample rate for the collection of prompts and completions as span tags.

   Alternatively, you can set this option with the ``DD_LLMOBS_SPAN_PROMPT_COMPLETION_SAMPLE_RATE`` environment
   variable.

   Default: ``1.0``


.. py:data:: (beta) ddtrace.config._llmobs_log_prompt_completion_sample_rate

   Configure the sample rate for the collection of prompts and completions as logs.

   Alternatively, you can set this option with the ``DD_LLMOBS_LOG_PROMPT_COMPLETION_SAMPLE_RATE`` environment
   variable.

   Default: ``0.1``

"""  # noqa: E501
from ...internal.utils.importlib import require_modules


required_modules = ["langchain"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from . import patch as _patch

        patch = _patch.patch
        unpatch = _patch.unpatch
        get_version = _patch.get_version

        __all__ = ["patch", "unpatch", "get_version"]
