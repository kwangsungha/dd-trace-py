# How this works
* A path consists of a root node, a set of "upstream" nodes a request follows, 
* A path coordinate is (root node id, upstream path id, current node id) 
    * We break this down further in order to get stats to questions we want to answer
* A checkpoint we 

# Assumptions we need to fix/validate eventually
* We should be able to acquire the datastreams info here too, but the backend info might look different
* rewrite the "how this works"
* We can make "pathway classes" for future iterations or customization by customers
* use an environment variable to enable/disable
* Every tracer supports these headers (especially upstream)
* Efficiency of data transmitted (hashing/unhashing)
* Context propagation only woks through the 'datadog' propagator (others not supported -_-)
* core is implemented and will always find the right items
* How is information back-propagated?
* Make the metrics a periodic service (instead of synchronous)
* Actually aggregate metrics
* Add testing

# Notes
* With span links we may have multiple paths act as one graph (fan-in)
* What do we do if there's an error downstream as far as the downstream stats?
* We're grouping by time, so we're going to end up with not reporting the "from upstream" stats for a bit
    * Does the time bucket matter?
* I am sending partial path information at a time, so a single path may be part of multiple buckets
* the "from upstream" and "from downstream" are a bit restrictive, it works with the "service" (see excalidraw) model but not others.
    * is this true if we use the edge "name" ?
    * Is there a way to give customers customizability?  There has to be a way to make this generic.
        * Yes, the edge name should make this possible, we can generalize for the API and vend templates like "service"

log = get_logger(__name__)

_accupath_processor = processor._processor_singleton

from ddtrace.internal.accupath.checkpoints import _time_checkpoint
from ddtrace.internal.accupath.stats import _checkpoint_diff

## Context Propagation:
* Protocols generate points to send data and extract data
    * Injection message
    * Extraction message
* On event, add in information or remove information
    * We pass in information type, protocol handles encoding the information


* On receipt of injection event, return information to the protocol to propagate
    * which event?
    * method to add in information
* On receipt of extraction event, grab information from the provided information
    * which event?
    * method to remove information


## Observation recording:
* Observations are recorded facts about the world at a given point in time, like "time this thing occurred"
* When an event happens, we want to perform some action to record information

* When an event happens, perform some action
    * What event?
    * When the event happens, what do I do?


## Metrics Generation:
* Metrics are generated values based on observations
* When do I calculate it?

* When is hard => but for now "when the last observation is recorded"
* What do I do? => a method which knows which arguments to use

## Coordinates:
* Coordinates of a core event is the core event name.
* Coordinates of an observation in AccuPath is "{system}.pathway_class.checkpoint_name
    * system: "accupath"
    * pathway_class: "service"
    * checkpoint_name: method used to generate a checkpoint
* Coordinates of a metric are 

## More:
* Traces are essentially pathways through execution
* Observations within a pathway are part of a pathway (the execution context), and have a time
    * Observations need to know that something happened to occur
* We have to propagate/link information about what happened before.
    * In python, we can use "core context" to act within a context within a given process 
* A metric needs to know:
    * Pathway: (provided by python core as context)
    * (system - class): namespace within the context
    * variable: variables to draw in, which live within the namespace
    * trigger: when to calculate the metric
    * destination: where to send the metric to (we can infer this from system - always send to system processor)


## Pathway Processor
* We want to bucketize on two things:
    * A unique ID for the pathway
    * A timeslot (for when a metric is emitted)

* For service name, one possible unique path ID is (upstream path hash, downstream path hash)
    * An issue is we don't know the downstream path hash until it is returned
        * its also possible that something downstream loses the context (and then what?)
    * Its a nice idea if everything works, because if we can emit metrics here based on the full path, we can emit the metrics for both
        * if everything doesn't work, we can't attribute the request metrics with the same path as the downstream metrics
        * this is 
    * (time_bucket, unique_path_id):
        * time_from_root
        * time_from_parent
        * time in service during request path
        * time in service during response path


## TODO
* We need to get more accurate processing times.  Right now tracing the middleware on the response path is only the time for our response, not the rest of the middleware
    * we can calculate/emit the time more correctly, but the header sent in the response will have the wrong timestamp
    * multiple requests?