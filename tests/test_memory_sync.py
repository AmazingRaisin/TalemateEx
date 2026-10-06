"""
Long term memory sync: scene loads bring the memory in line with the scene
without embedding unchanged documents again (which is slow on CPU).
"""

import types
import uuid

import chromadb
import pytest
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

from talemate.agents.memory import ChromaDBMemoryAgent


class RecordingEmbeddings(EmbeddingFunction):
    def __init__(self):
        self.embedded: list[str] = []

    def __call__(self, input: Documents) -> Embeddings:
        self.embedded.extend(input)
        return [[float(len(text)), 1.0, 0.5] for text in input]


@pytest.fixture
def agent():
    embeddings = RecordingEmbeddings()
    client = chromadb.EphemeralClient()
    agent = ChromaDBMemoryAgent()
    agent.scene = types.SimpleNamespace(memory_session_id="session-1")
    agent.db = client.create_collection(
        f"test-{uuid.uuid4().hex[:8]}", embedding_function=embeddings
    )
    agent.embeddings_recorder = embeddings
    return agent


def doc(doc_id, text, **meta):
    return {"id": doc_id, "text": text, "meta": meta}


def stored(agent):
    result = agent.db.get(include=["documents", "metadatas"])
    return {
        doc_id: (document, meta)
        for doc_id, document, meta in zip(
            result["ids"], result["documents"], result["metadatas"]
        )
    }


def test_unchanged_documents_are_not_embedded_again(agent):
    documents = [
        doc("Alice.age", "Alice's age: 19", character="Alice", typ="base_attribute"),
        doc("w1", "The city of Vel.", typ="world_state"),
    ]

    agent._sync(documents)
    assert len(agent.embeddings_recorder.embedded) == 2

    agent.scene.memory_session_id = "session-2"
    agent._sync(documents)
    assert len(agent.embeddings_recorder.embedded) == 2
    # kept documents keep the session they were stored in
    assert stored(agent)["w1"][1]["session"] == "session-1"


def test_changed_text_is_embedded(agent):
    agent._sync([doc("w1", "The city of Vel."), doc("w2", "A river.")])
    agent._sync([doc("w1", "The ruined city of Vel."), doc("w2", "A river.")])

    assert agent.embeddings_recorder.embedded[2:] == ["The ruined city of Vel."]
    assert stored(agent)["w1"][0] == "The ruined city of Vel."


def test_metadata_changes_are_stored_without_embedding(agent):
    agent._sync([doc("Alice.age", "Alice's age: 19", character="Alice")])
    agent._sync(
        [
            doc(
                "Alice.age",
                "Alice's age: 19",
                character="Alice",
                visibility="private",
                section="attributes",
            )
        ]
    )

    assert len(agent.embeddings_recorder.embedded) == 1
    meta = stored(agent)["Alice.age"][1]
    assert (meta["visibility"], meta["section"]) == ("private", "attributes")


def test_documents_no_longer_in_the_scene_are_removed(agent):
    agent._sync([doc("w1", "Vel."), doc("w2", "A river.")])
    agent._sync([doc("w1", "Vel.")])

    assert set(stored(agent)) == {"w1"}


def test_scoped_sync_only_touches_its_scope(agent):
    agent._sync(
        [
            doc(
                "Alice.age", "Alice's age: 19", character="Alice", typ="base_attribute"
            ),
            doc(
                "Alice.hair",
                "Alice's hair: red",
                character="Alice",
                typ="base_attribute",
            ),
            doc("Bob.age", "Bob's age: 30", character="Bob", typ="base_attribute"),
            doc("w1", "Vel.", typ="world_state"),
        ]
    )

    agent._sync(
        [doc("Alice.age", "Alice's age: 19", character="Alice", typ="base_attribute")],
        scopes=[{"character": "Alice", "typ": "base_attribute"}],
    )

    assert set(stored(agent)) == {"Alice.age", "Bob.age", "w1"}
    assert len(agent.embeddings_recorder.embedded) == 4
