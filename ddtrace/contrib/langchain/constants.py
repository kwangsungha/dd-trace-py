text_embedding_models = (
    "OpenAIEmbeddings",
    "HuggingFaceEmbeddings",
    "CohereEmbeddings",
    "ElasticsearchEmbeddings",
    "JinaEmbeddings",
    "LlamaCppEmbeddings",
    "HuggingFaceHubEmbeddings",
    "ModelScopeEmbeddings",
    "TensorflowHubEmbeddings",
    "SagemakerEndpointEmbeddings",
    "HuggingFaceInstructEmbeddings",
    "MosaicMLInstructorEmbeddings",
    "SelfHostedEmbeddings",
    "SelfHostedHuggingFaceEmbeddings",
    "SelfHostedHuggingFaceInstructEmbeddings",
    "FakeEmbeddings",
    "AlephAlphaAsymmetricSemanticEmbedding",
    "AlephAlphaSymmetricSemanticEmbedding",
    "SentenceTransformerEmbeddings",
    "GooglePalmEmbeddings",
    "MiniMaxEmbeddings",
    "VertexAIEmbeddings",
    "BedrockEmbeddings",
    "DeepInfraEmbeddings",
    "DashScopeEmbeddings",
    "EmbaasEmbeddings",
)

vectorstore_classes = (
    "AzureSearch",
    "Redis",
    "ElasticVectorSearch",
    "FAISS",
    "VectorStore",
    "Pinecone",
    "Weaviate",
    "Qdrant",
    "Milvus",
    "Zilliz",
    "SingleStoreDB",
    "Chroma",
    "OpenSearchVectorSearch",
    "AtlasDB",
    "DeepLake",
    "Annoy",
    "MongoDBAtlasVectorSearch",
    "MyScale",
    "SKLearnVectorStore",
    "SupabaseVectorStore",
    "AnalyticDB",
    "Vectara",
    "Tair",
    "LanceDB",
    "DocArrayHnswSearch",
    "DocArrayInMemorySearch",
    "Typesense",
    "Hologres",
    "Clickhouse",
    "Tigris",
    "MatchingEngine",
    "AwaDB",
)

agent_output_parser_classes = {
    "chat": {"output_parser": "ChatOutputParser"},
    "conversational": {"output_parser": "ConvoOutputParser"},
    "conversational_chat": {"output_parser": "ConvoOutputParser"},
    "mrkl": {"output_parser": "MRKLOutputParser"},
    "output_parsers": {
        "json": "JSONAgentOutputParser",
        "openai_functions": "OpenAIFunctionsAgentOutputParser",
        "react_json_single_input": "ReActJsonSingleInputOutputParser",
        "react_single_input": "ReActSingleInputOutputParser",
        "self_ask": "SelfAskOutputParser",
        "xml": "XMLAgentOutputParser",
    },
    "react": {"output_parser": "ReActOutputParser"},
    "self_ask_with_search": {"output_parser": "SelfAskOutputParser"},
    "structured_chat": {"output_parser": "StructuredChatOutputParser"},
}

API_KEY = "langchain.request.api_key"
PROVIDER = "langchain.request.provider"
MODEL = "langchain.request.model"
TYPE = "langchain.request.type"
COMPLETION_TOKENS = "langchain.tokens.completion_tokens"
PROMPT_TOKENS = "langchain.tokens.prompt_tokens"
TOTAL_COST = "langchain.tokens.total_cost"
