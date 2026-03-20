import os
import sys
import time
from collections.abc import Callable

import google.auth
from google.adk.tools import VertexAiSearchTool
from google.genai import types
from googleapiclient import discovery

def create_search_tool(
    data_store_path: str,
) -> VertexAiSearchTool | Callable[[str], str]:
    """Create a Vertex AI Search tool or mock for testing.

    Args:
        data_store_path: Full resource path of the datastore.

    Returns:
        VertexAiSearchTool instance or mock function for testing.
    """
    # For integration tests, return a mock function instead of the real tool
    if os.getenv("INTEGRATION_TEST") == "TRUE":

        def mock_search(query: str) -> str:
            """Mock Vertex AI Search for integration tests."""
            return "Mock search result for testing purposes."

        return mock_search

    return VertexAiSearchTool(data_store_id=data_store_path)

def _build_service(location: str, project_id: str):
    """Build a Discovery Engine v1alpha service client."""
    credentials, _ = google.auth.default()
    credentials = credentials.with_quota_project(project_id)
    if location == "global":
        endpoint = "https://discoveryengine.googleapis.com"
    else:
        endpoint = f"https://{location}-discoveryengine.googleapis.com"
    return discovery.build(
        "discoveryengine",
        "v1alpha",
        credentials=credentials,
        discoveryServiceUrl=f"{endpoint}/$discovery/rest?version=v1alpha",
    )

def download_and_ingest_content(url: str, title: str) -> str:
    """Downloads content from a given URL and saves it to the staging bucket, then triggers ingestion.

    Args:
        url: The URL of the content to download.
        title: The title of the content, which will be used as the filename.

    Returns:
        A string indicating the success or failure of the operation.
    """
    import urllib.request
    from google.cloud import storage

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("DATA_STORE_REGION", "global")
    # use project_name if available, else default to ednrag
    project_name = os.environ.get("PROJECT_NAME", "ednrag")
    # Use the suffix '-collection' for the collection_id as defined in TF.
    collection_id = f"{project_name}-collection"
    
    # Try getting the staging bucket from environment, or use the terraform default logic
    staging_bucket_name = os.environ.get("STAGING_BUCKET_NAME")
    if not staging_bucket_name:
        # Fallback to deriving bucket name similarly to how TF might or simply use project_id
        staging_bucket_name = f"{project_id}-ednrag-docs"

    try:
        # 1. Download content
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            content = response.read()

        # 2. Upload to GCS staging bucket
        storage_client = storage.Client()
        try:
             bucket = storage_client.get_bucket(staging_bucket_name)
        except Exception:
             return f"Error: Bucket {staging_bucket_name} not found."
             
        safe_title = "".join([c if c.isalnum() else "_" for c in title])
        blob = bucket.blob(f"new_content/{safe_title}.html")
        
        blob.upload_from_string(content, content_type="text/html")
        gcs_uri = f"gs://{staging_bucket_name}/new_content/{safe_title}.html"

        # 3. Trigger ingestion
        service = _build_service(location, project_id)
        connector_name = f"projects/{project_id}/locations/{location}/collections/{collection_id}/dataConnector"
        
        connector = service.projects().locations().collections().getDataConnector(name=connector_name).execute()
        entities = connector.get("entities", [])
        if not entities:
            return "Error: No entities found on the data connector."
            
        entity = entities[0]
        data_store = entity.get("dataStore", "")
        data_schema = entity.get("params", {}).get("data_schema", "content")
        
        branch = f"{data_store}/branches/default_branch"
        
        operation = (
            service.projects()
            .locations()
            .collections()
            .dataStores()
            .branches()
            .documents()
            .import_(
                parent=branch,
                body={
                    "gcsSource": {
                        "inputUris": [gcs_uri],
                        "dataSchema": data_schema,
                    },
                    "reconciliationMode": "INCREMENTAL",
                },
            )
            .execute()
        )
        lro_name = operation.get("name", "")

        return f"Successfully downloaded '{title}' to {gcs_uri} and triggered ingestion (LRO: {lro_name})."

    except Exception as e:
        return f"Failed to download or save content: {str(e)}"

def get_kb_table_of_contents() -> str:
    """Retrieves a table of contents of all the documents in the Knowledge Base.
    
    Returns:
        A formatted string listing the documents in the datastore.
    """
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("DATA_STORE_REGION", "global")
    # use project_name if available, else default to ednrag
    project_name = os.environ.get("PROJECT_NAME", "ednrag")
    # Use the suffix '-collection' for the collection_id as defined in TF.
    collection_id = f"{project_name}-collection"
    
    try:
        service = _build_service(location, project_id)
        connector_name = f"projects/{project_id}/locations/{location}/collections/{collection_id}/dataConnector"
        
        connector = service.projects().locations().collections().getDataConnector(name=connector_name).execute()
        entities = connector.get("entities", [])
        if not entities:
             return "No datastore configured for this collection."
        
        entity = entities[0]
        data_store = entity.get("dataStore", "")
        if not data_store:
             return "Datastore ID not found."
             
        branch = f"{data_store}/branches/default_branch"
        
        request = service.projects().locations().collections().dataStores().branches().documents().list(parent=branch)
        
        documents = []
        while request is not None:
             response = request.execute()
             for doc in response.get('documents', []):
                 # For unstructured files, the original GCS URI is usually stored in the document metadata
                 uri = doc.get("content", {}).get("uri") or doc.get("id")
                 title = doc.get("name") or doc.get("id")
                 if title and uri:
                      documents.append(f"- {title} ({uri})")
                 elif title:
                      documents.append(f"- {title}")
                 elif uri:
                      documents.append(f"- {uri}")
                 else:
                      documents.append(f"- Document ID: {doc.get('id')}")
                      
             request = service.projects().locations().collections().dataStores().branches().documents().list_next(previous_request=request, previous_response=response)
        
        if not documents:
            return "The Knowledge Base is currently empty."
            
        return "Knowledge Base Table of Contents:\n" + "\n".join(documents)
        
    except Exception as e:
        return f"Error retrieving table of contents: {str(e)}"
