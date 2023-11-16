from . import context_propagation
from . import processor

import time
from functools import partial

from ddtrace.internal.logger import get_logger
from ddtrace.internal.accupath.schemas import _schemas
from ddtrace.internal import core

log = get_logger('accupath')

def _enabled():
    # Can later read from an env var
    return True

NAMESPACE = "accupath"

OBSERVATION_STORAGE_COORDINATE_FORMAT = "{NAMESPACE}.{schema_name}.{observation_name}"
OBSERVATION_GENERATED_EVENT_FORMAT = "{NAMESPACE}.{schema_name}.{observation_generator_name}.{event_observation_name}.generated"
METRIC_GENERATED_EVENT_FORMAT = "{NAMESPACE}.{schema_name}.{metric_generator_name}.{dispatch_event_id}.generated"

if _enabled():
    function_map = {}
    for schema in _schemas:
        schema_name = schema["name"]

        # Load observation generators
        for name, generator in schema['observation_generators']:
            function_map[name] = generator

        # Loca context propagation injectors/extractors:
        for name, method in schema["propagation_functions"]:
            function_map[name] = method

        # Load Context Propagation
        for (
            injection_trigger_event,
            injection_method_name,
            default_injection_value,
            use_existing,
            extraction_trigger_event,
            extraction_method_name,
            default_extraction_value,
            extraction_cast_func,
            copy_on_extract_var,
            value_variable
        ) in schema["context_propagation"]["request"]:
            partial_injection_func = partial(function_map[injection_method_name], f"accupath.service.{value_variable}", default_injection_value, use_existing)
            partial_extract_func = partial(
                function_map[extraction_method_name],
                f"accupath.service.{value_variable}",
                default_extraction_value,
                extraction_cast_func,
                f"accupath.service.{copy_on_extract_var}"
            )
            core.on(injection_trigger_event, partial_injection_func)  # Register the injection method
            core.on(extraction_trigger_event, partial_extract_func)  # Register the extraction method

        # Load observations
        for trigger_event_name, observation_name, observation_generator_name in schema["observations"]:
            event_observation_name = observation_name
            observation_storage_name = OBSERVATION_STORAGE_COORDINATE_FORMAT.format(**locals())
            observation_generated_event_name = OBSERVATION_GENERATED_EVENT_FORMAT.format(**locals())
            observation_generator_function = function_map[observation_generator_name]
            partial_func = partial(observation_generator_function, observation_storage_name, observation_generated_event_name)
            core.on(trigger_event_name, partial_func)

        # Load metric generators
        for metric_generator_name, metric_generator in schema["metric_generators"]:
            function_map[metric_generator_name] = metric_generator

        # Load metrics
        for (event_observation_name, observation_generator_name), metric_name, metric_generator_name, observation_names, dispatch_event_id in schema["metrics"]:
            observation_coordinates = []
            metric_generation_trigger_event_name = OBSERVATION_GENERATED_EVENT_FORMAT.format(**locals())
            for observation_name in observation_names:
                observation_coordinate = OBSERVATION_STORAGE_COORDINATE_FORMAT.format(**locals())
                observation_coordinates.append(observation_coordinate)

            metric_generator_func = function_map[metric_generator_name]
            dispatch_event_full_id = None if not dispatch_event_id else METRIC_GENERATED_EVENT_FORMAT.format(**locals())
            partial_func = partial(metric_generator_func, metric_name, observation_coordinates, dispatch_event_full_id)
            core.on(metric_generation_trigger_event_name, partial_func)

        # Load metric submissions
        for (dispatch_event_id, metric_generator_name), submission_func in schema["metric_submissions"]:
            #partial_func = partial(submission_func, )
            dispatch_event_full_id = METRIC_GENERATED_EVENT_FORMAT.format(**locals())
            log.debug(f"Registering {dispatch_event_full_id} to be processed by {submission_func}")
            core.on(dispatch_event_full_id, submission_func)
