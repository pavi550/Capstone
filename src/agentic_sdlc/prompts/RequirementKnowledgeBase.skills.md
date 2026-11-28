# RequirementKnowledgeBase skills

How agents use the requirement knowledge base:

1. Every requirement (from Confluence/documents and Jira) is stored with its ID, title,
   description, acceptance criteria and human clarifications.
2. Before coding, testing or reviewing, retrieve the top-k most relevant entries and
   include them in the prompt as context.
3. When failure triage concludes the requirement is incorrect, the human-approved update
   is written back to the knowledge base and the requirement is re-implemented.
4. The knowledge base is persisted as `knowledge_base.json` (or a ChromaDB collection).
