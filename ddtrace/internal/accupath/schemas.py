import json

from ddtrace.internal.accupath.stats import _checkpoint_diff, _submit_service_metrics
from ddtrace.internal.accupath.checkpoints import _time_checkpoint
from ddtrace.internal.accupath.context_propagation import inject_context, extract_context
from ddtrace.internal.accupath.generators import generate_time
from ddtrace.internal.accupath.node_info import NodeInfo
from ddtrace.internal.accupath.path_info import generate_request_pathway_id, generate_response_pathway_id


_service_schema = {
    "name": "service",
    "observation_generators": [
        ("_time_checkpoint", _time_checkpoint),
    ],
    "metric_generators": [
        ("_checkpoint_diff", _checkpoint_diff),
    ],
    "propagation_functions": [
        ("inject_context", inject_context),
        ("extract_context", extract_context),
    ],
    "context_propagation": {
        "request": [
            (
                "http.request.header.injection",  # Triggering event (inject)
                "inject_context",
                generate_time,  # Default value to inject (None - do not inject if value does not exist)
                True, # Whether to use the existing value if it exists
                "http.request.header.extraction",  # Triggering event (extract)
                "extract_context",
                generate_time,  # Default value to set to on extraction, if the value isn't present
                int, # Casting function on extraction
                "root_out",  # Name of the observation in the core context (for both src and dst)
            ),  
            (
                "http.request.header.injection",  # Triggering event (inject)
                "inject_context",  # Function to call
                generate_time,  # Default value to inject (None - do not inject if value does not exist)
                False, # Whether to use the existing value if it exists
                "http.request.header.extraction",  # Triggering event (extract)
                "extract_context",  # Function to call
                generate_time,  # Default value to set to on extraction, if the value isn't present
                int, # Casting function on extraction
                "upstream_out",  # Name of the observation in the core context (for both src and dst)
            ),  
            (
                "http.request.header.injection",  # Triggering event (inject)
                "inject_context",  # Function to call
                lambda: NodeInfo.from_local_env().to_string_dict(),  # Default value to inject (None - do not inject if value does not exist)
                True, # Whether to use the existing value if it exists
                "http.request.header.extraction",  # Triggering event (extract)
                "extract_context",  # Function to call
                lambda: None,  # Default value to set to on extraction, if the value isn't present
                str,  # Casting function on extraction
                "root_node_info",  # Name of the observation in the core context (for both src and dst)
            ),  
            (
                "http.request.header.injection",  # Triggering event (inject)
                "inject_context",  # Function to call
                lambda: NodeInfo.from_local_env().to_string_dict(),  # Default value to inject (None - do not inject if value does not exist)
                False, # Whether to use the existing value if it exists
                "http.request.header.extraction",  # Triggering event (extract)
                "extract_context",  # Function to call
                lambda: None,  # Default value to set to on extraction, if the value isn't present
                str,  # Casting function on extraction
                "request_parent_node_info",  # Name of the observation in the core context (for both src and dst)
            ),  
            (
                "http.request.header.injection",  # Triggering event (inject)
                "inject_context",  # Function to call
                generate_request_pathway_id,  # Default value to inject (None - do not inject if value does not exist)
                False, # Whether to use the existing value if it exists
                "http.request.header.extraction",  # Triggering event (extract)
                "extract_context",  # Function to call
                lambda: None,  # Default value to set to on extraction, if the value isn't present
                int,  # Casting function on extraction
                "request_path_info",  # Name of the observation in the core context (for both src and dst)
            ),  
            (
                "http.response.header.injection",  # Triggering event (inject)
                "inject_context",  # Function to call
                lambda: NodeInfo.from_local_env().to_string_dict(),  # Default value to inject (None - do not inject if value does not exist)
                True, # Whether to use the existing value if it exists
                "http.response.header.extraction",  # Triggering event (extract)
                "extract_context",  # Function to call
                lambda: None,  # Default value to set to on extraction, if the value isn't present
                str,  # Casting function on extraction
                "root_node_info",  # Name of the observation in the core context (for both src and dst)
            ),  
            (
                "http.response.header.injection",  # Triggering event (inject)
                "inject_context",  # Function to call
                lambda: NodeInfo.from_local_env().to_string_dict(),  # Default value to inject (None - do not inject if value does not exist)
                False, # Whether to use the existing value if it exists
                "http.response.header.extraction",  # Triggering event (extract)
                "extract_context",  # Function to call
                lambda: None,  # Default value to set to on extraction, if the value isn't present
                str,  # Casting function on extraction
                "parent_node_info",  # Name of the observation in the core context (for both src and dst)
            ),  
            (
                "http.response.header.injection",  # Triggering event (inject)
                "inject_context",  # Function to call
                generate_response_pathway_id,  # Default value to inject (None - do not inject if value does not exist)
                False, # Whether to use the existing value if it exists
                "http.response.header.extraction",  # Triggering event (extract)
                "extract_context",  # Function to call
                lambda: None,  # Default value to set to on extraction, if the value isn't present
                int,  # Casting function on extraction
                "response_path_info",  # Name of the observation in the core context (for both src and dst)
            ),  
        ]
    },
    "observations": [
        ("http.request.header.extraction", "request_in", '_time_checkpoint'),  # (triggering event, checkpoint name, checkpoint action)
        ("http.request.header.injection", "request_out", '_time_checkpoint'),
        ("http.response.header.extraction", "response_in", '_time_checkpoint'),
        ("http.response.header.injection", "response_out", '_time_checkpoint'),
    ],
    "metrics": [
        (
            ("request_in", '_time_checkpoint'), # Triggering checkpoint ID  (checkpoint name, checkpoint action name)
            "root_to_request_in_latency",  # Name of metric
            '_checkpoint_diff',  # The metric function to call
            ("root_out", "request_in"),  # Parameters (core value names) to use as args to the metric calculator
            None,  # Event id to emit after
        ),
        (
            ("request_in", "_time_checkpoint"),
            "upstream_to_request_in_latency",
            '_checkpoint_diff',
            ("upstream_out", "request_in"),
            None,  # Event id to emit after
        ),
        (
            ("request_out", "_time_checkpoint"),
            "request_in_to_request_out_latency",
            '_checkpoint_diff',
            ("request_in", "request_out"),
            None,  # Event id to emit after
        ),
        (
            ("response_in", "_time_checkpoint"),
            "request_out_to_response_in_latency",
            "_checkpoint_diff",
            ("request_out", "response_in"),
            "submit_metrics",  # Event id to emit after
        ),
        (
            ("response_out", "_time_checkpoint"),
            "response_in_to_response_out_latency",
            "_checkpoint_diff",
            ("response_in", "response_out"),
            None,  # Event id to emit after
        )
    ],
    "metric_submissions": [
        (
            (
                "submit_metrics",  # Triggering Event
                "_checkpoint_diff",  # Triggering generator function
            ),
            _submit_service_metrics,  # Function to call
        ),
    ]
}

_schemas = [_service_schema]