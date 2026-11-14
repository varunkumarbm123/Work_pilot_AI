# WorkPilot project status

The current application is a business workspace for knowledge documents, sales records, customer enquiries, and settings. Chatbot, AI-agent, model-provider, approval, connector, and task workflow features have been removed.

The backend includes a migration that removes legacy assistant tables while preserving business workspace data. Backend tests and frontend production build must pass before deployment. Deployment requires authenticated access to the configured hosting services and database.
