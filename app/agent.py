# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os

import google
import vertexai
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools import google_search
from google.genai import types

from app.retrievers import create_search_tool, download_and_ingest_content, get_kb_table_of_contents

LLM_LOCATION = "global"
LOCATION = "us-central1"
LLM = "gemini-3-flash-preview"

credentials, project_id = google.auth.default()
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = LLM_LOCATION
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

vertexai.init(project=project_id, location=LOCATION)


data_store_region = os.getenv("DATA_STORE_REGION", "global")
data_store_id = os.getenv(
    "DATA_STORE_ID", "ednrag-collection_documents"
)
data_store_path = (
    f"projects/{project_id}/locations/{data_store_region}"
    f"/collections/default_collection/dataStores/{data_store_id}"
)

vertex_search_tool = create_search_tool(data_store_path)

def get_user_preferences() -> dict[str, list[dict[str, any]]]:
    """Retrieves user preferences (topics and ranks) from the agent state.
    
    Returns:
        A dictionary containing the user's topics of interest and their ranks.
    """
    # In a real implementation, this would access a state object
    # For now, returning a mock based on the schema requested.
    return {
        "preferences": [
            {"topic": "Artificial Intelligence", "Rank": 1},
            {"topic": "Space Exploration", "Rank": 2},
            {"topic": "Renewable Energy", "Rank": 3}
        ]
    }

root_instructions = """You are the master coordinator agent for a knowledge discovery, curation, and analysis system.
Your primary role is to understand the user's intent and delegate tasks to your specialized sub-agents:
1. Curator Agent: If the user wants to find new information, research a topic on the web, discover content, or add new content to the Knowledge Base (KB), delegate to the Curator Agent.
2. Q&A Agent: If the user wants to ask questions, analyze, query the existing curated Knowledge Base, or get a list of the documents currently in the Knowledge Base, delegate to the Q&A Agent.

Do not attempt to answer questions about the knowledge base or search the web directly. Always route to the appropriate sub-agent based on the user's request.
"""

curator_instructions = """You are a specialized Curator Agent responsible for discovering and curating new knowledge.
Your tasks include:
1. Using the `get_user_preferences` tool to retrieve the user's topics of interest and their corresponding ranks (1 to 5).
2. Using the built-in Google Search tool to find relevant content based on user queries or their specified topics of interest.
   - You must specifically seek out long-form content formats, such as: podcasts, videos, blog posts, published papers, and code repositories.
   - When searching, use keywords that are likely to surface these long-form formats.
3. Presenting the findings to the user and asking if they consider the content relevant and if they want to add it to the Knowledge Base (KB).
4. If the user confirms they want to add the content to the KB, use the `download_and_ingest_content` tool to download the content from the URL and save it to the staging bucket for ingestion.

Always be proactive in finding high-quality, relevant information based on the user's preferences, prioritizing comprehensive, long-form resources.
"""

qna_instructions = """You are a specialized Q&A Agent responsible for answering questions based on the curated Knowledge Base.
Answer to the best of your ability using the context retrieved from the Knowledge Base via your tools.
Leverage the provided search tools to query the Knowledge Base and formulate comprehensive answers.
If you need to know what documents are in the Knowledge Base, use the `get_kb_table_of_contents` tool.
If you already know the answer to a question from general knowledge but it is not in the KB, prioritize the KB's perspective or clearly distinguish between general knowledge and curated knowledge.
"""

curator_agent = Agent(
    name="curator_agent",
    model=Gemini(
        model=LLM,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=curator_instructions,
    tools=[google_search, get_user_preferences, download_and_ingest_content],
)

qna_agent = Agent(
    name="qna_agent",
    model=Gemini(
        model=LLM,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=qna_instructions,
    tools=[vertex_search_tool, get_kb_table_of_contents],
)

root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model=LLM,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=root_instructions,
    tools=[],
    sub_agents=[curator_agent, qna_agent]
)

app = App(
    root_agent=root_agent,
    name="app",
)
